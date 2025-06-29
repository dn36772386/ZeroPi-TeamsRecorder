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

def setup_asoundrc(device_mac):
    """指定されたBluetoothデバイスを使用するようにALSAを設定する"""
    asoundrc_content = f"""
pcm.!default {{
    type asym
    playback.pcm "bluetooth"
    capture.pcm "bluetooth"
}}

pcm.bluetooth {{
    type bluealsa
    device "{device_mac}"
    profile "a2dp"
}}

ctl.!default {{
    type bluealsa
}}
"""
    try:
        with open(ASOUNDRC_PATH, 'w') as f:
            f.write(asoundrc_content)
        worker_logger.info(f".asoundrcをデバイス {device_mac} 用に設定しました。")
        return True
    except Exception as e:
        worker_logger.error(f".asoundrcの設定に失敗: {e}")
        return False

def record_audio_thread(device_mac, filename_base):
    """録音を実行するスレッド"""
    global status
    
    if not setup_asoundrc(device_mac):
        update_status({'status': 'error', 'error_message': '.asoundrcの設定に失敗'})
        return

    # bluealsaデバイスが準備できるまで少し待つ
    time.sleep(2)

    p = pyaudio.PyAudio()
    stream = None
    
    temp_wav_filename = os.path.join(RECORDINGS_DIR, f"{filename_base}.wav")
    final_ogg_filename = os.path.join(RECORDINGS_DIR, f"{filename_base}.ogg")

    try:
        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
        
        worker_logger.info("録音を開始します...")
        update_status({
            'recording': True,
            'status': 'recording',
            'start_time': time.time(),
            'filename': os.path.basename(final_ogg_filename),
            'error_message': None
        })

        frames = []
        while not stop_recording_flag.is_set():
            data = stream.read(CHUNK)
            frames.append(data)

        worker_logger.info("録音を停止しています...")

    except Exception as e:
        worker_logger.error(f"PyAudioエラー: {e}")
        update_status({'status': 'error', 'error_message': f"録音デバイスエラー: {e}"})
        return
    finally:
        if stream:
            stream.stop_stream()
            stream.close()
        p.terminate()
        worker_logger.info("PyAudioを終了しました。")

    # --- ファイル変換 ---
    try:
        # WAVファイルとして一時保存
        import wave
        with wave.open(temp_wav_filename, 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
        
        # FFmpegでOGGに変換
        worker_logger.info(f"'{temp_wav_filename}' を '{final_ogg_filename}' に変換中...")
        subprocess.run(
            ['ffmpeg', '-i', temp_wav_filename, '-acodec', 'libvorbis', final_ogg_filename, '-y'],
            check=True, capture_output=True, text=True
        )
        worker_logger.info("変換が完了しました。")
        
        # 一時WAVファイルを削除
        os.remove(temp_wav_filename)

    except subprocess.CalledProcessError as e:
        worker_logger.error(f"FFmpegエラー: {e.stderr}")
        update_status({'status': 'error', 'error_message': f"ファイル変換失敗: {e.stderr}"})
        # 変換に失敗した場合でもWAVファイルは残す
    except Exception as e:
        worker_logger.error(f"ファイル保存エラー: {e}")
        update_status({'status': 'error', 'error_message': f"ファイル保存エラー: {e}"})

    update_status({'recording': False, 'status': 'idle', 'start_time': None, 'filename': None})
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
        
        command = command_data.get('command')
        worker_logger.info(f"コマンドを受信: {command}")

        if command == 'start':
            if status['recording']:
                worker_logger.warning("すでに録音中のため、新しい録音は開始しません。")
                return
            
            device = command_data.get('device')
            if not device:
                update_status({'status': 'error', 'error_message': 'デバイスが指定されていません。'})
                return
            
            filename_base = f"recording_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"
            
            stop_recording_flag.clear()
            thread = threading.Thread(target=record_audio_thread, args=(device, filename_base))
            thread.daemon = True
            thread.start()

        elif command == 'stop':
            if status['recording']:
                stop_recording_flag.set()
            else:
                worker_logger.warning("録音中ではないため、停止コマンドは無視します。")

        elif command == 'exit':
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