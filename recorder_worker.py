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
    """録音を実行するスレッド（PulseAudio対応）"""
    global status
    
    temp_wav_filename = os.path.join(RECORDINGS_DIR, f"{filename_base}.wav")
    final_ogg_filename = os.path.join(RECORDINGS_DIR, f"{filename_base}.ogg")
    process = None

    try:
        # Bluetoothソースを探す
        bluetooth_source = find_bluetooth_source(device_mac)
        
        if bluetooth_source:
            # PulseAudioのBluetoothソースから録音
            cmd = ['parec', '-d', bluetooth_source, '--file-format=wav', temp_wav_filename]
            worker_logger.info(f"PulseAudioで録音: {bluetooth_source}")
        else:
            # arecordのデバイスを確認
            check_result = subprocess.run(['arecord', '-l'], capture_output=True, text=True)
            worker_logger.info(f"利用可能なデバイス:\n{check_result.stdout}")
            
            if 'no soundcards found' in check_result.stdout:
                # PulseAudioを使用
                worker_logger.warning("arecordでデバイスが見つかりません。PulseAudioを使用します。")
                cmd = ['parec', '--file-format=wav', temp_wav_filename]
            else:
                # arecordを使用（プラグデバイスを指定）
                worker_logger.warning("Bluetoothソースが見つかりません。デフォルトデバイスを使用します。")
                # -D pulse を使用してPulseAudio経由で録音
                cmd = ['arecord', '-D', 'pulse', '-f', 'cd', '-t', 'wav', temp_wav_filename]
        
        update_status({
            'recording': True,
            'status': 'recording',
            'start_time': time.time(),
            'filename': os.path.basename(final_ogg_filename),
            'error_message': None
        })
        
        # 録音プロセスを開始
        # shell=Trueを削除し、stderr出力を捕捉
        process = subprocess.Popen(cmd, stderr=subprocess.PIPE, preexec_fn=os.setsid)
        worker_logger.info(f"録音を開始しました: {' '.join(cmd)}")
        
        # 停止フラグを監視
        while not stop_recording_flag.is_set():
            if process.poll() is not None:
                # プロセスが終了した場合
                stderr_output = process.stderr.read().decode() if process.stderr else ""
                if stderr_output:
                    worker_logger.error(f"録音プロセスエラー: {stderr_output}")
                break
            time.sleep(0.1)
        
        # 録音を停止
        if process.poll() is None:
            worker_logger.info("録音を停止しています...")
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                # プロセスが既に終了している場合
                pass
            except subprocess.TimeoutExpired:
                # 強制終了
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait()
        
        worker_logger.info("録音が停止されました")
        
    except Exception as e:
        worker_logger.error(f"録音エラー: {e}")
        update_status({'status': 'error', 'error_message': str(e)})
        if process and process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)

    # --- ファイル変換 ---
    try:
        if os.path.exists(temp_wav_filename) and os.path.getsize(temp_wav_filename) > 0:
            # FFmpegでOGGに変換
            worker_logger.info(f"'{temp_wav_filename}' を '{final_ogg_filename}' に変換中...")
            subprocess.run(
                ['ffmpeg', '-i', temp_wav_filename, '-acodec', 'libvorbis', final_ogg_filename, '-y'],
                check=True, capture_output=True, text=True
            )
            worker_logger.info("変換が完了しました。")
        
            # 一時WAVファイルを削除
            os.remove(temp_wav_filename)
        else:
            raise Exception("録音ファイルが空または存在しません")

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