#!/usr/bin/env python3
"""
Raspberry Pi Web録音コントローラー（デバイス選択・ネットワーク設定機能付き）
iPhoneからアクセスできるWebインターフェース付き録音アプリ
"""

from flask import Flask, render_template, jsonify, request, send_file, redirect, url_for
import os
import subprocess
import threading
import signal
import sys
import time
import logging
import json
import socket
import psutil
import argparse

# Flaskアプリの設定
app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 設定ファイル
CONFIG_FILE = "recorder_config.json"
STATUS_FILE = "recorder_status.json"
COMMAND_FILE = "recorder_command.json"
WORKER_SCRIPT = "recorder_worker.py"

# --- ここから大幅な変更・追加 ---

# === ネットワーク関連の機能を追加 ===
def get_ip_address():
    """サーバーのIPアドレスを取得"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        # APモードの場合などはこちら
        try:
            result = subprocess.check_output("hostname -I", shell=True).decode().strip()
            return result.split()[0]
        except Exception:
            return "127.0.0.1"

def is_wifi_connected():
    """Wi-Fiに接続されているか確認"""
    try:
        # `iwgetid` は接続中のWi-Fi名(SSID)を返す。接続してなければ空。
        result = subprocess.check_output("iwgetid -r", shell=True).decode().strip()
        return bool(result)
    except subprocess.CalledProcessError:
        return False

def scan_wifi_networks():
    """利用可能なWi-Fiネットワークをスキャン"""
    networks = []
    try:
        # `nmcli` を使ってデバイス一覧を取得し、Wi-Fiデバイス名を探す
        result = subprocess.check_output("nmcli -t -f DEVICE,TYPE device", shell=True).decode()
        wifi_device = None
        for line in result.strip().split('\n'):
            dev, dev_type = line.split(':')
            if dev_type == 'wifi':
                wifi_device = dev
                break
        
        if not wifi_device:
            logging.warning("Wi-Fiデバイスが見つかりません。")
            return []

        # Wi-Fiネットワークをスキャン
        scan_cmd = f"sudo nmcli --get-values SSID,SIGNAL,SECURITY device wifi list ifname {wifi_device}"
        result = subprocess.check_output(scan_cmd, shell=True).decode()
        
        seen_ssids = set()
        for line in result.strip().split('\n\n'):
            parts = line.split(':')
            if len(parts) >= 3:
                ssid, signal, security = parts[0], parts[1], parts[2]
                if ssid and ssid not in seen_ssids:
                    networks.append({'ssid': ssid, 'signal': int(signal), 'security': security})
                    seen_ssids.add(ssid)
        
        return sorted(networks, key=lambda x: x['signal'], reverse=True)

    except Exception as e:
        logging.error(f"Wi-Fiスキャンエラー: {e}")
        return []

def connect_to_wifi(ssid, password):
    """指定されたWi-Fiに接続"""
    try:
        connect_cmd = f'sudo nmcli device wifi connect "{ssid}" password "{password}"' if password else f'sudo nmcli device wifi connect "{ssid}"'
        subprocess.check_output(connect_cmd, shell=True, stderr=subprocess.STDOUT)
        return True, f"{ssid}への接続に成功しました。システムを再起動します。"
    except subprocess.CalledProcessError as e:
        return False, f"Wi-Fiへの接続に失敗しました: {e.output.decode()}"
# グローバル変数
selected_device = None
selected_adapter = None
worker_process = None

def send_command(command):
    """ワーカープロセスにコマンドを送信"""
    try:
        with open(COMMAND_FILE, 'w') as f:
            json.dump(command, f)
        return True
    except Exception as e:
        logging.error(f"コマンド送信エラー: {e}")
        return False

def get_worker_status():
    """ワーカープロセスのステータスを取得"""
    try:
        if not os.path.exists(STATUS_FILE):
            return None
            
        with open(STATUS_FILE, 'r') as f:
            status = json.load(f)
            
        # ステータスが古すぎる場合はワーカーが死んでいる可能性
        if time.time() - status.get('updated_at', 0) > 10:
            return None
            
        return status
    except Exception as e:
        logging.error(f"ステータス取得エラー: {e}")
        return None

def start_worker_process():
    """ワーカープロセスを起動"""
    global worker_process
    
    try:
        # 既存のワーカーを確認
        status = get_worker_status()
        if status and status.get('pid'):
            try:
                # プロセスが生きているか確認
                if psutil.pid_exists(status['pid']):
                    logging.info(f"ワーカープロセスは既に起動中: PID {status['pid']}")
                    return True
            except:
                pass
        
        # 新しいワーカーを起動
        worker_process = subprocess.Popen(
            [sys.executable, WORKER_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True  # 親プロセスから独立
        )
        
        logging.info(f"ワーカープロセスを起動: PID {worker_process.pid}")
        
        # ワーカーの起動を待つ
        for i in range(30):
            if get_worker_status():
                return True
            time.sleep(0.1)
        
        return False
        
    except Exception as e:
        logging.error(f"ワーカー起動エラー: {e}")
        return False

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

@app.route('/')
def index():
    """メインページ"""
    if is_setup_mode:
        return redirect(url_for('setup'))
    return render_template('index.html')

@app.route('/setup', methods=['GET'])
def setup():
    """Wi-Fi設定ページ"""
    networks = scan_wifi_networks()
    return render_template('setup.html', networks=networks)

@app.route('/connect', methods=['POST'])
def connect():
    """Wi-Fi接続処理"""
    ssid = request.form.get('ssid')
    password = request.form.get('password')
    success, message = connect_to_wifi(ssid, password)
    
    if success:
        def reboot_pi():
            time.sleep(3)
            os.system("sudo reboot")
        threading.Thread(target=reboot_pi).start()
        
    return render_template('connect_status.html', success=success, message=message)

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
    global selected_device
    
    # ワーカーのステータスを確認
    status = get_worker_status()
    if not status:
        # ワーカーが起動していない場合は起動
        if not start_worker_process():
            return jsonify({
                'success': False,
                'message': 'ワーカープロセスの起動に失敗しました'
            })
    
    # 既に録音中か確認
    status = get_worker_status()
    if status and status.get('recording'):
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
    
    # ワーカーにコマンドを送信
    command = {
        'action': 'start',
        'duration': duration_minutes,
        'device': device_info
    }
    
    if not send_command(command):
        return jsonify({
            'success': False,
            'message': 'コマンドの送信に失敗しました'
        })
    
    logging.info(f"録音開始コマンド送信: {duration_minutes}分間, デバイス: {device_info['name']}")
    
    return jsonify({
        'success': True,
        'message': f'{duration_minutes}分間の録音を開始しました'
    })

@app.route('/stop_recording', methods=['POST'])
def stop_recording():
    """録音停止API"""
    status = get_worker_status()
    if not status or not status.get('recording'):
        return jsonify({
            'success': False,
            'message': '録音中ではありません'
        })
    
    try:
        # 停止コマンドを送信
        if not send_command({'action': 'stop'}):
            return jsonify({
                'success': False,
                'message': 'コマンドの送信に失敗しました'
            })
        
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
    status = get_worker_status()
    response_data = {}
    
    if status:
        response_data = status
    else:
        response_data = {
            'recording': False,
            'status': 'offline'
        }
    
    response_data['ip_address'] = get_ip_address()
    return jsonify(response_data)

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
        
        return send_file(filepath, as_attachment=True, attachment_filename=filename)
    
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

def cleanup():
    """クリーンアップ処理"""
    global worker_process
    
    # ワーカープロセスに終了コマンドを送信
    send_command({'action': 'shutdown'})
    
    if worker_process:
        try:
            worker_process.terminate()
        except:
            pass

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Raspberry Pi Web Recorder")
    parser.add_argument('--setup', action='store_true', help='Run in Wi-Fi setup mode')
    args = parser.parse_args()
    
    is_setup_mode = args.setup

    if not is_setup_mode:
        load_config()
        if not start_worker_process():
            print("エラー: ワーカープロセスの起動に失敗しました")
            sys.exit(1)

    print("=" * 50)
    print("Raspberry Pi Web録音コントローラー")
    if is_setup_mode:
        print("--- Wi-Fi設定モード ---")
    print("=" * 50)
    
    try:
        # ポートを80番に固定して、ブラウザでポート番号入力を不要にする
        app.run(host='0.0.0.0', port=80, debug=False)
    except KeyboardInterrupt:
        print("サーバーを停止します...")
    except Exception as e:
        print(f"サーバーエラー: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if not is_setup_mode:
            cleanup()
        print("サーバーを終了しました")