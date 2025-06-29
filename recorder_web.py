#!/usr/bin/env python3
"""
Raspberry Pi Web録音コントローラー（デバイス選択機能付き）
iPhoneからアクセスできるWebインターフェース付き録音アプリ
"""

from flask import Flask, render_template, jsonify, request, send_file
import pyaudio
import wave
import datetime
import os
import subprocess
import threading
import signal
import sys
import time
import logging
import json
import socket

# Flaskアプリの設定
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 録音設定（vorbis-tools対応）
CHUNK = 2048
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 22050
RECORD_SECONDS = 7200  # 2時間（デフォルト）

# 設定ファイル
CONFIG_FILE = "recorder_config.json"

# グローバル変数
recording_thread = None
stop_recording = False
recording_active = False
current_filename = None
recording_start_time = None
selected_device = None
selected_adapter = None

def load_config():
    """設定ファイルを読み込む"""
    global selected_device, selected_adapter
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                selected_device = config.get('selected_device')
                selected_adapter = config.get('selected_adapter')
                logging.info(f"設定を読み込みました: {selected_device}")
        except Exception as e:
            logging.error(f"設定ファイルの読み込みエラー: {e}")

def save_config():
    """設定ファイルに保存"""
    try:
        config = {
            'selected_device': selected_device,
            'selected_adapter': selected_adapter
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logging.info(f"設定を保存しました: {selected_device}")
    except Exception as e:
        logging.error(f"設定ファイルの保存エラー: {e}")

def get_bluetooth_devices():
    """すべてのBluetoothアダプタからペアリング済みデバイスを取得"""
    devices = []
    
    try:
        # 利用可能なアダプタを取得
        hci_result = subprocess.run(['hciconfig'], 
                                  capture_output=True, text=True, timeout=10)
        
        logging.info("hciconfig output:")
        logging.info(hci_result.stdout)
        
        adapters = []
        current_adapter = None
        
        for line in hci_result.stdout.split('\n'):
            if line.startswith('hci'):
                # アダプタ名を取得
                adapter_name = line.split(':')[0]
                current_adapter = {'name': adapter_name}
            elif current_adapter and 'BD Address:' in line:
                # BDアドレスを取得
                bd_addr = line.split('BD Address:')[1].split()[0]
                current_adapter['address'] = bd_addr
                adapters.append(current_adapter)
                current_adapter = None
        
        logging.info(f"Found adapters: {adapters}")
        
        # 各アダプタのデバイスを取得
        for adapter in adapters:
            logging.info(f"Checking adapter: {adapter}")
            
            # bluetoothctlで各アダプタを選択してデバイスを取得
            cmd = f'select {adapter["address"]}\ndevices\nexit\n'
            result = subprocess.run(['bluetoothctl'], 
                                  input=cmd,
                                  capture_output=True, text=True, timeout=10)
            
            logging.info(f"bluetoothctl devices output for {adapter['name']}:")
            logging.info(result.stdout)
            
            if result.returncode == 0:
                # デバイス行を探す
                in_device_list = False
                for line in result.stdout.split('\n'):
                    # "Device"で始まる行を探す
                    if line.strip().startswith('Device '):
                        parts = line.strip().split(None, 2)
                        if len(parts) >= 3:
                            mac = parts[1]
                            name = parts[2]
                            
                            logging.info(f"Found device: {name} ({mac})")
                            
                            # デバイスの詳細情報を取得
                            info_cmd = f'select {adapter["address"]}\ninfo {mac}\nexit\n'
                            info_result = subprocess.run(['bluetoothctl'],
                                                       input=info_cmd,
                                                       capture_output=True, text=True, timeout=10)
                            
                            info_lower = info_result.stdout.lower()
                            connected = 'connected: yes' in info_lower
                            paired = 'paired: yes' in info_lower
                            trusted = 'trusted: yes' in info_lower
                            
                            if paired:  # ペアリング済みのデバイスのみ
                                devices.append({
                                    'mac': mac,
                                    'name': name,
                                    'adapter': adapter['address'],
                                    'adapter_name': adapter['name'],
                                    'connected': connected,
                                    'paired': paired,
                                    'trusted': trusted
                                })
                                logging.info(f"Added device: {name} (connected: {connected})")
        
        # 現在のアダプタのデバイスも確認（フォールバック）
        if len(devices) == 0:
            logging.info("No devices found with adapter selection, trying default adapter...")
            
            # デフォルトアダプタでデバイスを取得
            result = subprocess.run(['bluetoothctl', 'devices'], 
                                  capture_output=True, text=True, timeout=10)
            
            logging.info("Default adapter devices:")
            logging.info(result.stdout)
            
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.strip().startswith('Device '):
                        parts = line.strip().split(None, 2)
                        if len(parts) >= 3:
                            mac = parts[1]
                            name = parts[2]
                            
                            # デバイスの詳細情報を取得
                            info_result = subprocess.run(['bluetoothctl', 'info', mac],
                                                       capture_output=True, text=True, timeout=10)
                            
                            info_lower = info_result.stdout.lower()
                            connected = 'connected: yes' in info_lower
                            paired = 'paired: yes' in info_lower
                            trusted = 'trusted: yes' in info_lower
                            
                            if paired:
                                # どのアダプタか判定
                                adapter_addr = "unknown"
                                adapter_name = "unknown"
                                
                                # アダプタを特定する
                                for adapter in adapters:
                                    check_cmd = f'select {adapter["address"]}\ninfo {mac}\nexit\n'
                                    check_result = subprocess.run(['bluetoothctl'],
                                                                input=check_cmd,
                                                                capture_output=True, text=True, timeout=5)
                                    if 'Device' in check_result.stdout and mac in check_result.stdout:
                                        adapter_addr = adapter['address']
                                        adapter_name = adapter['name']
                                        break
                                
                                devices.append({
                                    'mac': mac,
                                    'name': name,
                                    'adapter': adapter_addr,
                                    'adapter_name': adapter_name,
                                    'connected': connected,
                                    'paired': paired,
                                    'trusted': trusted
                                })
                                logging.info(f"Added device from default: {name} (adapter: {adapter_name})")
        
    except Exception as e:
        logging.error(f"デバイス一覧取得エラー: {e}")
        import traceback
        logging.error(traceback.format_exc())
    
    logging.info(f"Total devices found: {len(devices)}")
    return devices

def check_device_connection(device_info):
    """特定のデバイスの接続状態をチェック"""
    try:
        # アダプタを選択してデバイス情報を取得
        cmd = f'select {device_info["adapter"]}\ninfo {device_info["mac"]}\nexit\n'
        result = subprocess.run(['bluetoothctl'],
                              input=cmd,
                              capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            info_output = result.stdout.lower()
            connected = 'connected: yes' in info_output
            
            if connected:
                return True, "デバイスは正常に接続されています"
            elif 'paired: yes' in info_output:
                # 自動接続を試行
                connect_cmd = f'select {device_info["adapter"]}\nconnect {device_info["mac"]}\nexit\n'
                connect_result = subprocess.run(['bluetoothctl'],
                                              input=connect_cmd,
                                              capture_output=True, text=True, timeout=15)
                
                if connect_result.returncode == 0:
                    time.sleep(3)
                    # 再度確認
                    verify_result = subprocess.run(['bluetoothctl'],
                                                 input=cmd,
                                                 capture_output=True, text=True, timeout=10)
                    if 'connected: yes' in verify_result.stdout.lower():
                        return True, "デバイスへの接続に成功しました"
                    else:
                        return False, "接続コマンドは成功しましたが、接続状態が確認できません"
                else:
                    return False, "デバイスへの接続に失敗しました"
            else:
                return False, "デバイスがペアリングされていません"
        else:
            return False, "デバイスの情報を取得できませんでした"
            
    except subprocess.TimeoutExpired:
        return False, "Bluetooth操作がタイムアウトしました"
    except Exception as e:
        return False, f"Bluetoothチェック中にエラーが発生しました: {e}"

def record_audio_worker(duration_seconds):
    """録音処理を行うワーカー関数"""
    global stop_recording, recording_active, current_filename
    
    # ファイル名を最初に設定
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    wav_temp_file = f"temp_{timestamp}.wav"
    ogg_output_file = f"recording_{timestamp}.ogg"
    current_filename = ogg_output_file
    
    # 変数の初期化
    p = None
    stream = None
    stop_recording = False  # グローバル変数を初期化
    
    try:
        # ALSAの警告を抑制
        os.environ['PYTHONWARNINGS'] = 'ignore'
        devnull = open(os.devnull, 'w')
        old_stderr = os.dup(2)
        os.dup2(devnull.fileno(), 2)
        
        # PyAudioの初期化
        p = pyaudio.PyAudio()
        
        # 標準エラー出力を復元
        os.dup2(old_stderr, 2)
        devnull.close()
        
        # ストリームを開く
        stream = p.open(format=FORMAT,
                       channels=CHANNELS,
                       rate=RATE,
                       input=True,
                       frames_per_buffer=CHUNK)
        
        frames = []
        recording_active = True
        
        total_chunks = int(RATE / CHUNK * duration_seconds)
        
        # 録音ループ
        for i in range(total_chunks):
            if stop_recording:
                break
            
            try:
                # ストリームが有効か確認
                if stream and stream.is_active():
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    frames.append(data)
                else:
                    logging.warning("ストリームが無効です")
                    break
            except Exception as e:
                logging.error(f"録音中のエラー: {e}")
                break
        
        logging.info(f"録音ループ終了: {len(frames)}フレーム")
        
        # 録音終了前にフラグを更新
        recording_active = False
        stop_recording = False
                
        # ストリームを適切に閉じる
        if stream:
            try:
                if stream.is_active():
                    stream.stop_stream()
                stream.close()
            except Exception as e:
                logging.error(f"ストリームクローズエラー: {e}")
        
        # PyAudioを先に終了
        if p:
            try:
                p.terminate()
                p = None
            except Exception as e:
                logging.error(f"PyAudio終了エラー: {e}")
        
        if len(frames) == 0:
            logging.warning("録音データがありません")
            return
        
        # WAVファイル保存
        try:
            wf = wave.open(wav_temp_file, 'wb')
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(p.get_sample_size(FORMAT))
            wf.setframerate(RATE)
            wf.writeframes(b''.join(frames))
            wf.close()
            
            logging.info(f"WAVファイル保存完了: {wav_temp_file}")
        except Exception as e:
            logging.error(f"WAVファイル保存エラー: {e}")
            return
        
        # OGG変換
        try:
            # oggencが存在するか確認
            check_oggenc = subprocess.run(['which', 'oggenc'], capture_output=True, text=True)
            if check_oggenc.returncode != 0:
                logging.error("oggencがインストールされていません。vorbis-toolsをインストールしてください。")
                return
            
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
            else:
                logging.error(f"OGG変換失敗: {result.stderr}")
        
        except subprocess.TimeoutExpired:
            logging.error("OGG変換がタイムアウトしました")
        except Exception as e:
            logging.error(f"OGG変換エラー: {e}")
        
    except Exception as e:
        logging.error(f"録音エラー: {e}")
        import traceback
        logging.error(traceback.format_exc())
        recording_active = False
    
    finally:
        # クリーンアップ
        recording_active = False
        stop_recording = False
        current_filename = None
        
        # PyAudioの終了
        if p:
            try:
                p.terminate()
            except Exception as e:
                logging.error(f"PyAudio終了エラー: {e}")
        
        # 少し待つ
        time.sleep(0.5)

@app.route('/')
def index():
    """メインページ"""
    return render_template('index.html')

@app.route('/get_devices')
def get_devices():
    """Bluetoothデバイス一覧を取得"""
    logging.info("=== /get_devices API called ===")
    
    try:
        devices = get_bluetooth_devices()
        logging.info(f"Found {len(devices)} devices")
        
        # デバイスの詳細をログに出力
        for device in devices:
            logging.info(f"Device: {device['name']} ({device['mac']}) on {device['adapter_name']}")
        
        # 現在の選択デバイスも返す
        current_device = None
        if selected_device:
            current_device = {
                'mac': selected_device.get('mac'),
                'name': selected_device.get('name'),
                'adapter': selected_device.get('adapter')
            }
            logging.info(f"Current device: {current_device}")
        
        response_data = {
            'success': True,
            'devices': devices,
            'current_device': current_device
        }
        
        logging.info(f"Returning {len(devices)} devices to client")
        return jsonify(response_data)
        
    except Exception as e:
        logging.error(f"Error in /get_devices: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'devices': [],
            'error': str(e)
        })

@app.route('/save_device', methods=['POST'])
def save_device():
    """選択したデバイスを保存"""
    global selected_device, selected_adapter
    
    data = request.get_json()
    selected_device = data
    selected_adapter = data.get('adapter')
    
    save_config()
    
    return jsonify({
        'success': True,
        'message': '設定を保存しました'
    })

@app.route('/check_connection', methods=['POST'])
def check_connection():
    """接続状態確認API"""
    data = request.get_json()
    
    if not data:
        return jsonify({
            'connected': False,
            'message': 'デバイスが選択されていません'
        })
    
    connected, message = check_device_connection(data)
    return jsonify({
        'connected': connected,
        'message': message
    })

@app.route('/start_recording', methods=['POST'])
def start_recording():
    """録音開始API"""
    global recording_thread, recording_start_time, selected_device, current_filename
    
    if recording_active:
        return jsonify({
            'success': False,
            'message': '既に録音中です'
        })
    
    # リクエストからデバイス情報を取得
    data = request.get_json()
    device_info = data.get('device')
    
    if not device_info:
        return jsonify({
            'success': False,
            'message': 'デバイスが選択されていません'
        })
    
    # デバイスの接続確認
    connected, message = check_device_connection(device_info)
    if not connected:
        return jsonify({
            'success': False,
            'message': message
        })
    
    # 録音時間を取得（デフォルト120分）
    duration_minutes = data.get('duration', 120)
    duration_seconds = duration_minutes * 60
    
    recording_start_time = time.time()
    
    # ファイル名を事前に生成
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_filename = f"recording_{timestamp}.ogg"
    
    # 録音スレッドを開始
    recording_thread = threading.Thread(
        target=record_audio_worker,
        args=(duration_seconds,)
    )
    recording_thread.daemon = True
    recording_thread.start()
    
    # 少し待ってcurrent_filenameが設定されるのを待つ
    time.sleep(0.1)
    
    logging.info(f"録音開始: {duration_minutes}分間, デバイス: {device_info['name']}, ファイル名: {current_filename or temp_filename}")
    
    return jsonify({
        'success': True,
        'message': f'{duration_minutes}分間の録音を開始しました',
        'filename': current_filename or temp_filename
    })

@app.route('/stop_recording', methods=['POST'])
def stop_recording():
    """録音停止API"""
    global stop_recording, recording_active, recording_thread
    
    if not recording_active:
        return jsonify({
            'success': False,
            'message': '録音中ではありません'
        })
    
    try:
        # 停止フラグを設定
        stop_recording = True
        logging.info("録音停止要求を受信")
        
        # 録音スレッドの終了を待つ（最大10秒）
        if recording_thread and recording_thread.is_alive():
            recording_thread.join(timeout=10)
            if recording_thread.is_alive():
                logging.warning("録音スレッドが時間内に終了しませんでした")
        
        # フラグをリセット
        stop_recording = False
        recording_active = False
        recording_thread = None
        
        logging.info("録音停止完了")
        return jsonify({
            'success': True,
            'message': '録音を停止しました'
        })
    
    except Exception as e:
        logging.error(f"録音停止エラー: {e}")
        return jsonify({
            'success': False,
            'message': f'録音停止中にエラーが発生しました: {str(e)}'
        })

@app.route('/get_status')
def get_status():
    """録音状態取得API"""
    return jsonify({
        'recording': recording_active,
        'filename': current_filename if recording_active else None,
        'start_time': recording_start_time
    })

@app.route('/get_files')
def get_files():
    """録音ファイル一覧取得API"""
    logging.info("=== /get_files API called ===")
    try:
        files = [f for f in os.listdir('.') if f.endswith('.ogg')]
        files.sort(reverse=True)  # 新しい順
        logging.info(f"Found {len(files)} OGG files")
        return jsonify({
            'success': True,
            'files': files[:10]  # 最新10件
        })
    except Exception as e:
        logging.error(f"Error in /get_files: {e}")
        return jsonify({
            'success': False,
            'files': [],
            'error': str(e)
        })

@app.route('/download/<filename>')
def download_file(filename):
    """ファイルダウンロードAPI"""
    try:
        # セキュリティ：ディレクトリトラバーサル対策
        if '..' in filename or '/' in filename:
            return jsonify({'error': '不正なファイル名です'}), 400
        
        # .oggファイルのみ許可
        if not filename.endswith('.ogg'):
            return jsonify({'error': 'OGGファイルのみダウンロード可能です'}), 400
        
        filepath = os.path.join(os.getcwd(), filename)
        if not os.path.exists(filepath):
            return jsonify({'error': 'ファイルが見つかりません'}), 404
        
        return send_file(filepath, as_attachment=True, download_name=filename)
    
    except Exception as e:
        logging.error(f"ダウンロードエラー: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete/<filename>', methods=['POST'])
def delete_file(filename):
    """ファイル削除API"""
    try:
        # セキュリティ：ディレクトリトラバーサル対策
        if '..' in filename or '/' in filename:
            return jsonify({
                'success': False,
                'message': '不正なファイル名です'
            }), 400
        
        # .oggファイルのみ許可
        if not filename.endswith('.ogg'):
            return jsonify({
                'success': False,
                'message': 'OGGファイルのみ削除可能です'
            }), 400
        
        filepath = os.path.join(os.getcwd(), filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            logging.info(f"ファイル削除: {filename}")
            return jsonify({'success': True, 'message': 'ファイルを削除しました'})
        else:
            return jsonify({'success': False, 'message': 'ファイルが見つかりません'}), 404
    
    except Exception as e:
        logging.error(f"削除エラー: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/debug_bluetooth')
def debug_bluetooth():
    """Bluetoothのデバッグ情報を取得"""
    debug_info = {
        'hciconfig': '',
        'bluetoothctl_list': '',
        'bluetoothctl_devices': '',
        'error': None
    }
    
    try:
        # hciconfig
        result = subprocess.run(['hciconfig'], capture_output=True, text=True, timeout=10)
        debug_info['hciconfig'] = result.stdout
        
        # bluetoothctl list
        result = subprocess.run(['bluetoothctl'], input='list\nexit\n', 
                              capture_output=True, text=True, timeout=10)
        debug_info['bluetoothctl_list'] = result.stdout
        
        # bluetoothctl devices
        result = subprocess.run(['bluetoothctl'], input='devices\nexit\n',
                              capture_output=True, text=True, timeout=10)
        debug_info['bluetoothctl_devices'] = result.stdout
        
    except Exception as e:
        debug_info['error'] = str(e)
    
    return jsonify(debug_info)

if __name__ == '__main__':
    # 設定を読み込む
    load_config()
    
    # 利用可能なポートを探す関数
    def find_available_port(start_port=5000, max_attempts=10):
        for port in range(start_port, start_port + max_attempts):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(('', port))
                sock.close()
                return port
            except OSError:
                continue
        return None
    
    # 利用可能なポートを探す
    port = find_available_port()
    if port is None:
        print("エラー: 利用可能なポートが見つかりません")
        sys.exit(1)
    
    # サーバー起動時のメッセージ
    print("=" * 50)
    print("Raspberry Pi Web録音コントローラー")
    print("=" * 50)
    if selected_device:
        print(f"保存済みデバイス: {selected_device.get('name')}")
        print(f"MACアドレス: {selected_device.get('mac')}")
    else:
        print("デバイスが選択されていません")
    print("=" * 50)
    print("Webサーバーを起動中...")
    print(f"アクセスURL: http://[RaspberryPiのIPアドレス]:{port}")
    print("Ctrl+C で終了")
    print("=" * 50)
    
    # エラーが発生してもサーバーを継続実行
    while True:
        try:
            # Flaskアプリを起動（同じネットワーク内からアクセス可能）
            app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
        except KeyboardInterrupt:
            print("\n\nサーバーを停止します...")
            break
        except Exception as e:
            print(f"\nエラーが発生しました: {e}")
            print("5秒後に再起動します...")
            time.sleep(5)