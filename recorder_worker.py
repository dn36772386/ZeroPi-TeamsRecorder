#!/usr/bin/env python3
"""
録音ワーカープロセス
recorder_web.pyからの指示に基づき、実際の録音処理を担当する。
音声インジケーター機能を追加
"""

import logging
import os
import pyaudio
import time
import json
import subprocess
import signal
import threading
from datetime import datetime

# このスクリプトの場所にログファイルを作成
log_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'worker.log')

# ロガーの設定
worker_logger = logging.getLogger('WorkerLogger')
worker_logger.setLevel(logging.INFO)
handler = logging.FileHandler(log_file_path)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
worker_logger.addHandler(handler)

worker_logger.info("--- ワーカーログ開始 ---")

# --- 定数（絶対パスで指定） ---
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# Webアプリと状態を共有するためのファイル
STATUS_FILE = os.path.join(APP_ROOT, "recorder_status.json")
COMMAND_FILE = os.path.join(APP_ROOT, "recorder_command.json")
RECORDINGS_DIR = os.path.join(APP_ROOT, "recordings")

# 録音設定
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

# 音声インジケーター用の更新間隔（秒）
AUDIO_STATUS_UPDATE_INTERVAL = 0.5

# --- グローバル変数 ---
status = {
    'recording': False,
    'status': 'idle',  # idle, recording, error
    'start_time': None,
    'filename': None,
    'device': None,
    'error_message': None,
    'recording_info': None
}
stop_recording_flag = threading.Event()
main_loop_running = True

# --- 関数 ---

def update_status(new_status=None):
    """現在の状態をJSONファイルに書き出す"""
    global status
    if new_status:
        status.update(new_status)
    
    # 常に最新のタイムスタンプを追加する
    status['updated_at'] = time.time()
    try:
        with open(STATUS_FILE, 'w') as f:
            json.dump(status, f)
    except Exception as e:
        worker_logger.error(f"ステータスファイルの書き込みに失敗: {e}")

def find_pulse_audio_device(device_mac):
    """PulseAudioから適切なデバイス（sourceまたはsink.monitor）を検索"""
    try:
        # MACアドレスを正規化（:を_に変換） 
        normalized_mac = device_mac.replace(':', '_')
        
        # pactl list sourcesでソース一覧を取得
        result = subprocess.run(['pactl', 'list', 'sources', 'short'], 
                              capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                parts = line.split('\t')
                if len(parts) >= 2:
                    source_name = parts[1]
                    # sourceまたはsink.monitorでMACアドレスが含まれているものを探す
                    if normalized_mac in source_name:
                        worker_logger.info(f"PulseAudioデバイスを発見: {source_name}")
                        return source_name
        
        worker_logger.warning(f"MACアドレス {device_mac} に対応するPulseAudioデバイスが見つかりません")
        return None
        
    except Exception as e:
        worker_logger.error(f"PulseAudioデバイス検索エラー: {e}")
        return None

def record_audio_thread(device_mac, filename_base):
    """ffmpegを使用した録音スレッド（音声インジケーター対応版）"""
    global status
    
    final_ogg_filename = os.path.join(RECORDINGS_DIR, f"{filename_base}.ogg")
    process = None

    try:
        # PulseAudioデバイスを検索
        source_name = find_pulse_audio_device(device_mac)
        if not source_name:
            raise Exception(f"Bluetoothデバイス {device_mac} が見つかりません")

        # ffmpegで直接OGG録音
        cmd = [
            'ffmpeg',
            '-f', 'pulse',
            '-i', source_name,
            '-acodec', 'libvorbis',
            '-ab', '128k',  # ビットレート
            '-y',  # 上書き許可
            final_ogg_filename
        ]

        worker_logger.info(f"録音開始: {' '.join(cmd)}")
        
        # デバイス情報を含めてステータスを更新
        update_status({
            'recording': True,
            'status': 'recording',
            'start_time': time.time(),
            'filename': os.path.basename(final_ogg_filename),
            'device': status.get('device'),  # グローバル変数から取得
            'error_message': None,
            'recording_info': {
                'duration': 0,
                'file_size': 0,
                'format': 'OGG Vorbis 128kbps'
            }
        })

        # プロセス開始（stderrは破棄）
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,  # SIGINTを送るため
            stderr=subprocess.DEVNULL  # バッファ溢れ防止
        )
        
        # 録音監視ループ
        start_time = time.time()
        last_size = 0
        last_status_update = time.time()
        no_audio_count = 0  # 音声なしカウンター
        
        while not stop_recording_flag.is_set():
            # プロセスの生存確認
            if process.poll() is not None:
                worker_logger.warning("録音プロセスが予期せず終了")
                break
            
            # 現在の時間と経過時間
            current_time = time.time()
            duration = int(current_time - start_time)
            
            # ファイルサイズで進捗確認
            file_size = 0
            if os.path.exists(final_ogg_filename):
                file_size = os.path.getsize(final_ogg_filename)
                
                # 音声検出の簡易判定
                if file_size > last_size:
                    # ファイルサイズが増加している = 音声あり
                    last_size = file_size
                    no_audio_count = 0
                else:
                    # ファイルサイズが変わらない = 音声なしの可能性
                    no_audio_count += 1
                    
                    # 10秒以上音声なしの場合は警告
                    if no_audio_count > 20 and duration > 10:
                        worker_logger.warning(f"音声が検出されない可能性があります（{no_audio_count * 0.5}秒間）")
            
            # ステータス更新（より頻繁に）
            if current_time - last_status_update >= AUDIO_STATUS_UPDATE_INTERVAL:
                update_status({
                    'recording_info': {
                        'duration': duration,
                        'file_size': file_size,
                        'format': 'OGG Vorbis 128kbps',
                        'last_update': current_time  # 更新タイムスタンプ追加
                    }
                })
                last_status_update = current_time
            
            time.sleep(AUDIO_STATUS_UPDATE_INTERVAL)

        # 適切な停止処理
        if process.poll() is None:
            worker_logger.info("録音を停止します")
            # ffmpegにqキーを送信（正常終了）
            try:
                process.stdin.write(b'q')
                process.stdin.flush()
            except:
                pass  # stdin書き込みエラーは無視
            
            # 5秒待機
            for _ in range(10):
                if process.poll() is not None:
                    break
                time.sleep(0.5)
            
            # まだ終了していなければSIGTERM
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

        worker_logger.info(f"録音が正常に終了しました。最終ファイルサイズ: {file_size} bytes")

    except subprocess.TimeoutExpired:
        worker_logger.error("プロセスの終了がタイムアウト")
        if process:
            process.kill()
        
    except Exception as e:
        worker_logger.error(f"録音エラー: {e}")
        update_status({'status': 'error', 'error_message': str(e)})
        if process and process.poll() is None:
            process.kill()
    finally:
        # クリーンアップ
        update_status({
            'recording': False,
            'status': 'idle',
            'start_time': None,
            'filename': None,
            'device': None,
            'recording_info': None
        })
        stop_recording_flag.clear()
        worker_logger.info("録音処理が完了しました。")

def check_command():
    """コマンドファイルを確認し、処理を実行する"""
    global main_loop_running
    if not os.path.exists(COMMAND_FILE):
        return

    try:
        with open(COMMAND_FILE, 'r') as f:
            command_data = json.load(f)
        
        # 読み込んだらすぐにファイルを削除
        os.remove(COMMAND_FILE)
        
        action = command_data.get('action')
        worker_logger.info(f"コマンドを受信: {action}")

        if action == 'start':
            if status['recording']:
                worker_logger.warning("すでに録音中のため、新しい録音は開始しません。")
                return
            
            device = command_data.get('device')
            if not device:
                update_status({'status': 'error', 'error_message': 'デバイスが指定されていません。'})
                return
            
            # デバイス情報を保存（音声インジケーター用）
            device_info = {
                'name': device.get('name', 'Unknown Device'),
                'mac': device.get('mac') if isinstance(device, dict) else device,
                'type': 'Bluetooth Audio Source',
                'adapter': device.get('adapter', 'unknown')
            }
            
            # グローバル変数に保存
            status['device'] = device_info
            
            device_mac = device.get('mac') if isinstance(device, dict) else device
            
            filename_base = f"recording_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            
            stop_recording_flag.clear()
            thread = threading.Thread(target=record_audio_thread, args=(device_mac, filename_base))
            thread.daemon = True
            thread.start()

        elif action == 'stop':
            if status['recording']:
                stop_recording_flag.set()
            else:
                worker_logger.warning("録音中ではないため、停止コマンドは無視します。")

        elif action == 'shutdown' or action == 'exit':
            if status['recording']:
                stop_recording_flag.set()
                time.sleep(2)  # 録音スレッドの終了を待つ
            main_loop_running = False
            worker_logger.info("終了コマンドを受信しました。")

    except Exception as e:
        worker_logger.error(f"コマンド処理エラー: {e}")
        if os.path.exists(COMMAND_FILE):
            os.remove(COMMAND_FILE)

def cleanup():
    """終了処理"""
    update_status({'recording': False, 'status': 'offline'})
    if os.path.exists(COMMAND_FILE):
        os.remove(COMMAND_FILE)
    worker_logger.info("ワーカープロセスをクリーンアップしました。")

if __name__ == '__main__':
    if not os.path.exists(RECORDINGS_DIR):
        os.makedirs(RECORDINGS_DIR)

    try:
        # 起動時にステータスを初期化
        worker_logger.info(f"初期ステータスファイル書き込み試行: {STATUS_FILE}")
        update_status({'recording': False, 'status': 'idle'})
        worker_logger.info("初期ステータスファイル書き込み成功。")

        worker_logger.info("コマンド待機ループを開始します...")
        while main_loop_running:
            check_command()
            # Webサーバーに生存を知らせるため、ステータスを定期的に更新する
            update_status()
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        worker_logger.info("キーボード割り込みにより終了します。")
    except Exception as e:
        # ループ開始前の致命的なエラーをログに記録
        worker_logger.error(f"ワーカーのメイン処理で致命的なエラーが発生: {e}", exc_info=True)
    finally:
        cleanup()
        worker_logger.info("録音ワーカーがシャットダウンしました。")