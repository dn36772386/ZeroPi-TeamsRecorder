#!/usr/bin/env python3
"""
録音ワーカープロセス
Webサーバーとは独立して動作する録音専用プロセス
"""

import pyaudio
import wave
import datetime
import os
import subprocess
import time
import json
import sys
import signal
import logging

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('recorder_worker.log'),
        logging.StreamHandler()
    ]
)

# 録音設定
CHUNK = 2048
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 22050
SAMPLE_WIDTH = 2  # paInt16のサンプル幅

# ステータスファイル
STATUS_FILE = "recorder_status.json"
COMMAND_FILE = "recorder_command.json"

class RecorderWorker:
    def __init__(self):
        self.recording = False
        self.stop_requested = False
        self.current_filename = None
        self.start_time = None
        
    def update_status(self, status, filename=None, error=None):
        """ステータスファイルを更新"""
        try:
            data = {
                'recording': self.recording,
                'filename': filename or self.current_filename,
                'start_time': self.start_time,
                'status': status,
                'error': error,
                'pid': os.getpid(),
                'updated_at': time.time()
            }
            
            # アトミックな書き込み
            temp_file = STATUS_FILE + '.tmp'
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_file, STATUS_FILE)
            
        except Exception as e:
            logging.error(f"ステータス更新エラー: {e}")
    
    def check_command(self):
        """コマンドファイルをチェック"""
        if not os.path.exists(COMMAND_FILE):
            return None
            
        try:
            with open(COMMAND_FILE, 'r') as f:
                command = json.load(f)
            
            # コマンドファイルを削除（処理済み）
            os.remove(COMMAND_FILE)
            return command
            
        except Exception as e:
            logging.error(f"コマンド読み取りエラー: {e}")
            return None
    
    def record_audio(self, duration_seconds):
        """録音処理"""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_temp_file = f"temp_{timestamp}.wav"
        ogg_output_file = f"recording_{timestamp}.ogg"
        
        self.current_filename = ogg_output_file
        self.start_time = time.time()
        self.recording = True
        self.stop_requested = False
        
        self.update_status('recording', ogg_output_file)
        
        p = None
        stream = None
        frames = []
        
        try:
            # PyAudioの初期化
            p = pyaudio.PyAudio()
            
            # ストリームを開く
            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,
                frames_per_buffer=CHUNK
            )
            
            logging.info(f"録音開始: {ogg_output_file}")
            
            total_chunks = int(RATE / CHUNK * duration_seconds)
            
            # 録音ループ
            for i in range(total_chunks):
                # 停止コマンドをチェック
                command = self.check_command()
                if command and command.get('action') == 'stop':
                    self.stop_requested = True
                    logging.info("停止コマンドを受信")
                    break
                
                if self.stop_requested:
                    break
                
                try:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(data)
                except Exception as e:
                    logging.error(f"録音中のエラー: {e}")
                    break
                
                # 定期的にステータスを更新
                if i % 50 == 0:
                    self.update_status('recording', ogg_output_file)
            
            logging.info(f"録音ループ終了: {len(frames)}フレーム")
            
            # ストリームを閉じる
            if stream:
                stream.stop_stream()
                stream.close()
            
            # WAVファイル保存
            if len(frames) > 0:
                wf = wave.open(wav_temp_file, 'wb')
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))
                wf.close()
                
                logging.info(f"WAVファイル保存完了: {wav_temp_file}")
                
                # OGG変換
                self.update_status('converting', ogg_output_file)
                
                result = subprocess.run([
                    'oggenc', 
                    '-q', '4',
                    '-o', ogg_output_file,
                    wav_temp_file
                ], capture_output=True, text=True, timeout=1800)
                
                if result.returncode == 0:
                    if os.path.exists(wav_temp_file):
                        os.remove(wav_temp_file)
                    logging.info(f"録音完了: {ogg_output_file}")
                    self.update_status('completed', ogg_output_file)
                else:
                    logging.error(f"OGG変換失敗: {result.stderr}")
                    self.update_status('error', ogg_output_file, 'OGG変換失敗')
            
        except Exception as e:
            logging.error(f"録音エラー: {e}")
            self.update_status('error', self.current_filename, str(e))
            
        finally:
            # PyAudioの終了
            if p:
                try:
                    p.terminate()
                except:
                    pass
            
            self.recording = False
            self.current_filename = None
            self.start_time = None
    
    def run(self):
        """メインループ"""
        logging.info("録音ワーカー起動")
        self.update_status('idle')
        
        while True:
            try:
                command = self.check_command()
                
                if command:
                    action = command.get('action')
                    
                    if action == 'start' and not self.recording:
                        duration = command.get('duration', 120) * 60
                        self.record_audio(duration)
                        
                    elif action == 'stop' and self.recording:
                        self.stop_requested = True
                        
                    elif action == 'shutdown':
                        logging.info("シャットダウンコマンドを受信")
                        break
                
                # CPU使用率を抑える
                time.sleep(0.1)
                
            except KeyboardInterrupt:
                logging.info("KeyboardInterrupt を受信")
                break
            except Exception as e:
                logging.error(f"メインループエラー: {e}")
                time.sleep(1)
        
        self.update_status('shutdown')
        logging.info("録音ワーカー終了")

def signal_handler(signum, frame):
    """シグナルハンドラー"""
    logging.info(f"シグナル {signum} を受信")
    sys.exit(0)

if __name__ == '__main__':
    # シグナルハンドラーを設定
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # ワーカーを起動
    worker = RecorderWorker()
    worker.run()