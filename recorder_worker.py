#!/usr/bin/env python3
"""
録音ワーカープロセス
recorder_web.pyからの指示に基づき、実際の録音処理を担当する。
"""

# --- このブロックを丸ごと追加してください ---
import logging
import os

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
# --- ここまで追加 ---

import pyaudio
import time
import json
import subprocess
import signal
import threading
from datetime import datetime

# --- 定数（絶対パスで指定） ---
# このスクリプト自身の場所を基準に、絶対パスを生成します
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# Webアプリと状態を共有するためのファイルを、絶対パスで指定します
STATUS_FILE = os.path.join(APP_ROOT, "recorder_status.json")
COMMAND_FILE = os.path.join(APP_ROOT, "recorder_command.json")
RECORDINGS_DIR = os.path.join(APP_ROOT, "recordings")

ASOUNDRC_PATH = os.path.expanduser("~/.asoundrc")

# 録音設定
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100

# --- ロギング設定 ---
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- グローバル変数 ---
status = {
    'recording': False,
    'status': 'idle', # idle, recording, error
    'start_time': None,
    'filename': None,
    'error_message': None
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

def find_bluetooth_source(device_mac):
    """PulseAudioからBluetoothソースを検索"""
    try:
        # PulseAudioのソース一覧を取得
        result = subprocess.run(['pactl', 'list', 'sources'], 
                              capture_output=True, text=True)
        
        # MACアドレスを正規化（:を_に変換）
        normalized_mac = device_mac.replace(':', '_')
        
        sources = result.stdout.split('Source #')
        for source in sources[1:]:  # 最初の空要素をスキップ
            if normalized_mac in source:
                # ソース名を抽出
                for line in source.split('\n'):
                    if line.strip().startswith('Name:'):
                        source_name = line.split('Name:')[1].strip()
                        worker_logger.info(f"Bluetoothソースを発見: {source_name}")
                        return source_name
        
        # A2DPシンクのモニターを探す（音声ループバック用）
        for source in sources[1:]:
            if 'monitor' in source and 'bluez' in source:
                for line in source.split('\n'):
                    if line.strip().startswith('Name:'):
                        source_name = line.split('Name:')[1].strip()
                        worker_logger.info(f"Bluetoothモニターソースを発見: {source_name}")
                        return source_name
        
        return None
    except Exception as e:
        worker_logger.error(f"Bluetoothソース検索エラー: {e}")
        return None

def record_audio_thread(device_mac, filename_base):
    """ffmpegを使用した録音スレッド"""
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
        
        # ステータスを先に更新
        update_status({
            'recording': True,
            'status': 'recording',
            'start_time': time.time(),
            'filename': os.path.basename(final_ogg_filename),
            'error_message': None
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
        
        while not stop_recording_flag.is_set():
            # プロセスの生存確認
            if process.poll() is not None:
                worker_logger.warning("録音プロセスが予期せず終了")
                break
            
            # ファイルサイズで進捗確認
            if os.path.exists(final_ogg_filename):
                current_size = os.path.getsize(final_ogg_filename)
                if current_size > last_size:
                    last_size = current_size
                    # 録音継続中
                elif time.time() - start_time > 10:
                    # 10秒以上サイズが変わらない
                    worker_logger.warning("録音が停止している可能性")
            
            # ステータス更新
            update_status()
            time.sleep(0.5)

        # 適切な停止処理
        if process.poll() is None:
            worker_logger.info("録音を停止します")
            # ffmpegにqキーを送信（正常終了）
            process.stdin.write(b'q')
            process.stdin.flush()
            
            # 5秒待機
            for _ in range(10):
                if process.poll() is not None:
                    break
                time.sleep(0.5)
            
            # まだ終了していなければSIGTERM
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

        worker_logger.info("録音が正常に終了しました")

    except subprocess.TimeoutExpired:
        worker_logger.error("プロセスの終了がタイムアウト")
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
            'filename': None
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
        
        action = command_data.get('action')  # 'action'フィールドを使用
        worker_logger.info(f"コマンドを受信: {action}")

        if action == 'start':
            if status['recording']:
                worker_logger.warning("すでに録音中のため、新しい録音は開始しません。")
                return
            
            device = command_data.get('device')
            if not device:
                update_status({'status': 'error', 'error_message': 'デバイスが指定されていません。'})
                return
            
            # デバイスのMACアドレスを取得
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
                time.sleep(2) # 録音スレッドの終了を待つ
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