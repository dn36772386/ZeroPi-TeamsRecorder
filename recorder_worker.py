#!/usr/bin/env python3
"""
録音ワーカープロセス（Redis/音声レベル対応版）
改善版：Pub/Sub方式とパフォーマンス最適化
"""

import logging
import os
import time
import json
import subprocess
import signal
import threading
import redis
import shutil
import numpy as np
from datetime import datetime

# tmpディレクトリ（SDカード保護）
TEMP_DIR = "/tmp/recorder"
LOG_FILE = os.path.join(TEMP_DIR, "worker.log")

# ディレクトリ作成
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# ロガーの設定
worker_logger = logging.getLogger('WorkerLogger')
worker_logger.setLevel(logging.INFO)
handler = logging.FileHandler(LOG_FILE)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
worker_logger.addHandler(handler)

worker_logger.info("--- ワーカーログ開始 ---")

# 定数
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(APP_ROOT, "recordings")
CHUNK = 1024
CHANNELS = 1
RATE = 44100

# Redis接続（接続プール使用）
try:
    pool = redis.ConnectionPool(host='localhost', port=6379, decode_responses=True)
    redis_client = redis.Redis(connection_pool=pool)
    redis_client.ping()
    worker_logger.info("Redis接続成功")
except Exception as e:
    worker_logger.error(f"Redis接続失敗: {e}")
    exit(1)

# グローバル変数
stop_recording_flag = threading.Event()
main_loop_running = True
recording_process = None
audio_monitor_thread = None
command_thread = None

def initialize_redis_status():
    """起動時にRedisステータスを初期化"""
    try:
        # 既存のステータスをクリア
        redis_client.delete("recorder:status")
        
        # 初期ステータスを設定
        initial_status = {
            'recording': 'false',
            'status': 'idle',
            'pid': str(os.getpid()),
            'updated_at': str(time.time()),
            'start_time': 'null',
            'filename': 'null',
            'device': 'null',
            'error_message': 'null',
            'recording_info': 'null'
        }
        
        # パイプライン使用で高速化
        pipe = redis_client.pipeline()
        pipe.hmset("recorder:status", initial_status)
        pipe.delete("recorder:command_queue")  # 古いコマンドをクリア
        pipe.execute()
        
        worker_logger.info("Redisステータスを初期化しました")
        
    except Exception as e:
        worker_logger.error(f"Redis初期化エラー: {e}")

def update_status(status_dict):
    """Redisにステータスを更新（最適化版）"""
    try:
        # 更新用の辞書を作成
        processed_dict = {}
        
        for key, value in status_dict.items():
            if value is None:
                processed_dict[key] = 'null'
            elif key in ['device', 'recording_info']:
                if isinstance(value, dict):
                    processed_dict[key] = json.dumps(value)
                else:
                    processed_dict[key] = str(value)
            else:
                processed_dict[key] = str(value)
        
        # 更新時刻を追加
        processed_dict['updated_at'] = str(time.time())
        
        # パイプラインで一括実行
        pipe = redis_client.pipeline()
        pipe.hset("recorder:status", mapping=processed_dict)
        pipe.publish("recorder:status_update", "update")
        pipe.publish("recorder:status_changed", json.dumps(processed_dict))
        pipe.execute()
        
    except Exception as e:
        worker_logger.error(f"ステータス更新エラー: {e}")

def find_pulse_audio_device(device_mac):
    """PulseAudioから適切なデバイスを検索"""
    try:
        normalized_mac = device_mac.replace(':', '_')
        worker_logger.info(f"デバイスを検索中: MAC={device_mac}, normalized={normalized_mac}")
        
        result = subprocess.run(['pactl', 'list', 'sources', 'short'], 
                              capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            worker_logger.info(f"利用可能なソース:")
            for line in result.stdout.strip().split('\n'):
                parts = line.split('\t')
                if len(parts) >= 2:
                    source_name = parts[1]
                    worker_logger.info(f"  - {source_name}")
                    
                    # より柔軟なマッチング
                    if normalized_mac.lower() in source_name.lower():
                        worker_logger.info(f"PulseAudioデバイスを発見: {source_name}")
                        return source_name
            
            # Bluetoothソースのフォールバック
            for line in result.stdout.strip().split('\n'):
                parts = line.split('\t')
                if len(parts) >= 2:
                    source_name = parts[1]
                    if 'bluez' in source_name.lower():
                        worker_logger.info(f"Bluetoothソースを発見（フォールバック）: {source_name}")
                        worker_logger.warning(f"警告: MACアドレス {device_mac} と完全一致しないが、Bluetoothソース {source_name} を使用します")
                        return source_name
        
        worker_logger.warning(f"MACアドレス {device_mac} に対応するPulseAudioデバイスが見つかりません")
        return None
        
    except Exception as e:
        worker_logger.error(f"PulseAudioデバイス検索エラー: {e}")
        return None

def monitor_audio_levels(source_name):
    """音声レベルをモニタリングしてRedisに送信（最適化版）"""
    try:
        # parecordで音声データを取得
        cmd = [
            'parec',
            '--device=' + source_name,
            '--format=s16le',
            '--rate=' + str(RATE),
            '--channels=' + str(CHANNELS),
            '--latency-msec=10'
        ]
        
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        worker_logger.info("音声レベルモニタリング開始")
        
        # 更新頻度制御
        last_update_time = 0
        update_interval = 0.1  # 100ms = 10Hz
        
        # ピークホールド機能
        peak_level = 0
        peak_decay = 0.95
        
        while not stop_recording_flag.is_set():
            # 音声データを読み取り
            data = process.stdout.read(CHUNK * 2)  # 16bit = 2bytes
            if not data:
                break
            
            # numpy配列に変換してRMS計算
            audio_array = np.frombuffer(data, dtype=np.int16)
            if len(audio_array) > 0:
                rms = np.sqrt(np.mean(audio_array.astype(np.float32)**2))
                # 正規化（0-1の範囲）
                normalized_level = min(rms / 32768.0, 1.0)
                
                # ピークレベル更新
                if normalized_level > peak_level:
                    peak_level = normalized_level
                else:
                    peak_level *= peak_decay
                
                # dB変換（無音時は-60dB）
                if normalized_level > 0:
                    db_level = 20 * np.log10(normalized_level)
                else:
                    db_level = -60
                
                # 更新頻度制限
                current_time = time.time()
                if current_time - last_update_time >= update_interval:
                    # Redisに送信
                    level_data = {
                        'level': float(normalized_level),
                        'peak': float(peak_level),
                        'db': float(db_level),
                        'timestamp': current_time
                    }
                    redis_client.publish('recorder:audio_level', json.dumps(level_data))
                    last_update_time = current_time
        
        process.terminate()
        worker_logger.info("音声レベルモニタリング終了")
        
    except Exception as e:
        worker_logger.error(f"音声レベルモニタリングエラー: {e}")

def cleanup_temp_files():
    """起動時に残っている一時ファイルをクリーンアップ"""
    try:
        temp_files_removed = 0
        for f in os.listdir(TEMP_DIR):
            if f.endswith('.ogg') or f.endswith('.wav'):
                try:
                    file_path = os.path.join(TEMP_DIR, f)
                    # ファイルが空でない場合は保存
                    if os.path.getsize(file_path) > 0:
                        # recordingsディレクトリに移動
                        final_path = os.path.join(RECORDINGS_DIR, f)
                        shutil.move(file_path, final_path)
                        worker_logger.info(f"未完了の録音ファイルを保存: {f}")
                    else:
                        # 空ファイルは削除
                        os.remove(file_path)
                        temp_files_removed += 1
                except Exception as e:
                    worker_logger.error(f"一時ファイル処理エラー ({f}): {e}")
        
        if temp_files_removed > 0:
            worker_logger.info(f"{temp_files_removed}個の一時ファイルを削除しました")
            
    except Exception as e:
        worker_logger.error(f"一時ファイルクリーンアップエラー: {e}")

def record_audio_thread(device_mac, filename_base):
    """録音処理スレッド（音声レベル対応）"""
    global recording_process, audio_monitor_thread
    
    # 一時ファイルパス（tmpfs使用）
    temp_ogg_path = os.path.join(TEMP_DIR, f"{filename_base}.ogg")
    final_ogg_path = os.path.join(RECORDINGS_DIR, f"{filename_base}.ogg")
    
    try:
        # PulseAudioデバイスを検索
        source_name = find_pulse_audio_device(device_mac)
        if not source_name:
            # フォールバック: デフォルトのソースを使用
            worker_logger.warning("特定のBluetoothデバイスが見つかりません。デフォルトソースを使用します。")
            cmd_check = ['pactl', 'info']
            result = subprocess.run(cmd_check, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'Default Source:' in line:
                        source_name = line.split(':', 1)[1].strip()
                        worker_logger.info(f"デフォルトソースを使用: {source_name}")
                        break
            
            if not source_name:
                raise Exception(f"Bluetoothデバイス {device_mac} が見つかりません。デバイスが接続されていることを確認してください。")
        
        # 音声レベルモニタリングスレッド開始
        audio_monitor_thread = threading.Thread(
            target=monitor_audio_levels, 
            args=(source_name,), 
            daemon=True
        )
        audio_monitor_thread.start()
        
        # ffmpegで録音（tmpfsに保存）
        cmd = [
            'ffmpeg',
            '-f', 'pulse',
            '-i', source_name,
            '-acodec', 'libvorbis',
            '-ab', '128k',
            '-y',
            temp_ogg_path
        ]
        
        worker_logger.info(f"録音開始: {' '.join(cmd)}")
        
        # ステータス更新
        device_info = {
            'name': redis_client.hget("recorder:config", "selected_device_name") or "Unknown",
            'mac': device_mac,
            'type': 'Bluetooth Audio Source'
        }
        
        update_status({
            'recording': 'true',
            'status': 'recording',
            'start_time': time.time(),
            'filename': os.path.basename(final_ogg_path),
            'device': device_info,
            'error_message': None,
            'recording_info': {
                'duration': 0,
                'file_size': 0,
                'format': 'OGG Vorbis 128kbps'
            }
        })
        
        # プロセス開始
        recording_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        
        # 録音監視ループ
        start_time = time.time()
        last_update_time = 0
        update_interval = 1.0  # ステータス更新は1秒ごと
        
        while not stop_recording_flag.is_set():
            if recording_process.poll() is not None:
                worker_logger.warning("録音プロセスが予期せず終了")
                break
            
            current_time = time.time()
            if current_time - last_update_time >= update_interval:
                duration = int(current_time - start_time)
                
                # ファイルサイズ確認
                file_size = 0
                if os.path.exists(temp_ogg_path):
                    file_size = os.path.getsize(temp_ogg_path)
                
                # ステータス更新
                update_status({
                    'recording_info': {
                        'duration': duration,
                        'file_size': file_size,
                        'format': 'OGG Vorbis 128kbps'
                    }
                })
                last_update_time = current_time
            
            time.sleep(0.1)  # CPU使用率を抑える
        
        # 録音停止
        if recording_process.poll() is None:
            worker_logger.info("録音を停止します")
            try:
                recording_process.stdin.write(b'q')
                recording_process.stdin.flush()
            except:
                pass
            
            recording_process.wait(timeout=5)
        
        # ファイルを最終保存場所に移動
        if os.path.exists(temp_ogg_path) and os.path.getsize(temp_ogg_path) > 0:
            shutil.move(temp_ogg_path, final_ogg_path)
            worker_logger.info(f"録音ファイルを保存: {final_ogg_path}")
        
    except subprocess.TimeoutExpired:
        worker_logger.error("プロセスの終了がタイムアウト")
        if recording_process:
            recording_process.kill()
    except Exception as e:
        worker_logger.error(f"録音エラー: {e}")
        update_status({'status': 'error', 'error_message': str(e)})
    finally:
        # クリーンアップ
        update_status({
            'recording': 'false',
            'status': 'idle',
            'start_time': None,
            'filename': None,
            'device': None,
            'recording_info': None
        })
        stop_recording_flag.clear()
        worker_logger.info("録音処理が完了しました")

def process_command(command_data):
    """コマンドを処理"""
    global main_loop_running
    
    try:
        action = command_data.get('action')
        worker_logger.info(f"コマンドを受信: {action}")
        
        if action == 'start':
            if redis_client.hget("recorder:status", "recording") == 'true':
                worker_logger.warning("すでに録音中のため、新しい録音は開始しません")
                return
            
            # pending ステータスは既にWeb側で設定済み
            
            device = command_data.get('device')
            if not device:
                update_status({'status': 'error', 'error_message': 'デバイスが指定されていません'})
                return
            
            # デバイス情報をログに記録
            worker_logger.info(f"デバイス情報: {device}")
            
            device_mac = device.get('mac') if isinstance(device, dict) else device
            device_name = device.get('name', 'Unknown') if isinstance(device, dict) else 'Unknown'
            
            worker_logger.info(f"録音開始: デバイス名={device_name}, MAC={device_mac}")
            
            # デバイス名を設定に保存
            redis_client.hset("recorder:config", "selected_device_name", device_name)
            
            filename_base = f"recording_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            
            stop_recording_flag.clear()
            thread = threading.Thread(
                target=record_audio_thread, 
                args=(device_mac, filename_base),
                daemon=True
            )
            thread.start()
            
        elif action == 'stop':
            if redis_client.hget("recorder:status", "recording") == 'true':
                stop_recording_flag.set()
            else:
                worker_logger.warning("録音中ではないため、停止コマンドは無視します")
                
        elif action == 'shutdown' or action == 'exit':
            if redis_client.hget("recorder:status", "recording") == 'true':
                stop_recording_flag.set()
                time.sleep(2)
            main_loop_running = False
            worker_logger.info("終了コマンドを受信しました")
            
    except Exception as e:
        worker_logger.error(f"コマンド処理エラー: {e}")

def command_listener():
    """Pub/Subでコマンドを待機（新方式）"""
    pubsub = redis_client.pubsub()
    pubsub.subscribe('recorder:commands')
    
    worker_logger.info("Pub/Subコマンドリスナー開始")
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            try:
                command_data = json.loads(message['data'])
                process_command(command_data)
            except Exception as e:
                worker_logger.error(f"コマンドパースエラー: {e}")

def legacy_command_processor():
    """レガシーコマンド処理（後方互換性）"""
    while main_loop_running:
        try:
            # ブロッキングでコマンドを待機
            result = redis_client.brpop("recorder:command_queue", timeout=1)
            if result:
                _, command_json = result
                command_data = json.loads(command_json)
                process_command(command_data)
        except Exception as e:
            worker_logger.error(f"レガシーコマンド処理エラー: {e}")
        
        # 定期的に生存確認
        if int(time.time()) % 10 == 0:
            update_status({'pid': os.getpid()})

def cleanup():
    """終了処理"""
    update_status({
        'recording': 'false',
        'status': 'offline'
    })
    worker_logger.info("ワーカープロセスをクリーンアップしました")

if __name__ == '__main__':
    if not os.path.exists(RECORDINGS_DIR):
        os.makedirs(RECORDINGS_DIR)
    
    try:
        # 起動時の初期化
        initialize_redis_status()
        cleanup_temp_files()
        
        # 起動時にステータスを設定
        update_status({
            'recording': 'false',
            'status': 'idle',
            'pid': os.getpid()
        })
        
        # Pub/Subコマンドリスナー開始
        command_thread = threading.Thread(target=command_listener, daemon=True)
        command_thread.start()
        
        worker_logger.info("ワーカープロセス起動完了")
        
        # レガシーコマンド処理（メインループ）
        legacy_command_processor()
                
    except KeyboardInterrupt:
        worker_logger.info("キーボード割り込みにより終了します")
    except Exception as e:
        worker_logger.error(f"ワーカーのメイン処理で致命的なエラーが発生: {e}", exc_info=True)
    finally:
        main_loop_running = False
        cleanup()
        worker_logger.info("録音ワーカーがシャットダウンしました")