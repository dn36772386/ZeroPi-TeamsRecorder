#!/usr/bin/env python3
"""
Raspberry Pi Web録音コントローラー（WebSocket/Redis/mDNS対応版）
"""

from flask import Flask, render_template, jsonify, request, send_file, redirect, url_for
from flask_socketio import SocketIO, emit
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
import redis
import numpy as np
from datetime import datetime

# Flaskアプリの設定
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
socketio = SocketIO(app, cors_allowed_origins="*")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# このスクリプト自身の場所を基準に、絶対パスを生成します
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# 録音ディレクトリ（tmpfsを使用してSDカード保護）
RECORDINGS_DIR = os.path.join(APP_ROOT, "recordings")
TEMP_DIR = "/tmp/recorder"

# Redis接続
try:
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    redis_client.ping()
    logging.info("Redis接続成功")
except:
    logging.error("Redis接続失敗 - Redisサーバーが起動していることを確認してください")
    sys.exit(1)

# グローバル変数
connected_clients = set()
worker_status_thread = None
audio_level_thread = None

def get_ip_address():
    """サーバーのIPアドレスを取得"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            result = subprocess.check_output("hostname -I", shell=True).decode().strip()
            return result.split()[0]
        except Exception:
            return "127.0.0.1"

def get_mdns_address():
    """mDNSアドレスを取得（無効化時はIPアドレスを返す）"""
    return get_ip_address()

def setup_avahi():
    """Avahiサービスの設定（無効化）"""
    logging.info("mDNS設定をスキップします")
    return

def load_config():
    """Redisから設定を読み込む"""
    try:
        config = redis_client.hgetall("recorder:config")
        if config:
            return config
        return {}
    except Exception as e:
        logging.error(f"設定読み込みエラー: {e}")
        return {}

def save_config(config):
    """Redisに設定を保存"""
    try:
        redis_client.hmset("recorder:config", config)
        logging.info("設定を保存しました")
    except Exception as e:
        logging.error(f"設定保存エラー: {e}")

def send_command(command):
    """ワーカープロセスにコマンドを送信"""
    try:
        redis_client.lpush("recorder:commands", json.dumps(command))
        return True
    except Exception as e:
        logging.error(f"コマンド送信エラー: {e}")
        return False

def get_worker_status():
    """ワーカープロセスのステータスを取得"""
    try:
        status = redis_client.hgetall("recorder:status")
        if status:
            # 文字列をJSONとしてパース
            for key in ['recording', 'device', 'recording_info']:
                if key in status and isinstance(status[key], str):
                    try:
                        status[key] = json.loads(status[key])
                    except:
                        pass
        return status
    except Exception as e:
        logging.error(f"ステータス取得エラー: {e}")
        return None

def monitor_worker_status():
    """ワーカーステータスの監視とWebSocket配信"""
    pubsub = redis_client.pubsub()
    pubsub.subscribe('recorder:status_update')
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            status = get_worker_status()
            if status:
                socketio.emit('status_update', status)

def monitor_audio_levels():
    """音声レベルの監視とWebSocket配信"""
    pubsub = redis_client.pubsub()
    pubsub.subscribe('recorder:audio_level')
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            try:
                data = json.loads(message['data'])
                socketio.emit('audio_level', data)
            except:
                pass

# --- WebSocketイベントハンドラー ---

@socketio.on('connect')
def handle_connect():
    """クライアント接続時"""
    connected_clients.add(request.sid)
    logging.info(f"クライアント接続: {request.sid}")
    
    # 現在のステータスを送信
    status = get_worker_status()
    if status:
        emit('status_update', status)

@socketio.on('disconnect')
def handle_disconnect():
    """クライアント切断時"""
    connected_clients.discard(request.sid)
    logging.info(f"クライアント切断: {request.sid}")

# --- ルートハンドラー ---

@app.route('/')
def index():
    """メインページ"""
    if is_setup_mode:
        return redirect(url_for('setup'))
    return render_template('index.html', 
                         mdns_address=None,
                         ip_address=get_ip_address())

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
    try:
        devices = get_bluetooth_devices()
        config = load_config()
        current_device = None
        
        if 'selected_device' in config:
            current_device = json.loads(config['selected_device'])
        
        return jsonify({
            'success': True,
            'devices': devices,
            'current_device': current_device
        })
    except Exception as e:
        logging.error(f"Error in /get_devices: {e}")
        return jsonify({
            'success': False,
            'devices': [],
            'error': str(e)
        })

@app.route('/save_device', methods=['POST'])
def save_device():
    """選択したデバイスを保存"""
    data = request.get_json()
    
    config = {
        'selected_device': json.dumps(data),
        'selected_adapter': data.get('adapter', '')
    }
    save_config(config)
    
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
    status = get_worker_status()
    if status and status.get('recording'):
        return jsonify({
            'success': False,
            'message': '既に録音中です'
        })
    
    data = request.get_json()
    device_info = data.get('device')
    
    if not device_info:
        return jsonify({
            'success': False,
            'message': 'デバイスが選択されていません'
        })
    
    connected, message = check_device_connection(device_info)
    if not connected:
        return jsonify({
            'success': False,
            'message': message
        })
    
    duration_minutes = data.get('duration', 120)
    
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
    
    if not send_command({'action': 'stop'}):
        return jsonify({
            'success': False,
            'message': 'コマンドの送信に失敗しました'
        })
    
    return jsonify({
        'success': True,
        'message': '録音を停止しました'
    })

@app.route('/get_status')
def get_status():
    """録音状態取得API（後方互換性のため残す）"""
    status = get_worker_status() or {
        'recording': False,
        'status': 'offline'
    }
    status['ip_address'] = get_ip_address()
    status['mdns_address'] = get_mdns_address()
    return jsonify(status)

@app.route('/get_files')
def get_files():
    """録音ファイル一覧取得API"""
    try:
        if not os.path.exists(RECORDINGS_DIR):
            os.makedirs(RECORDINGS_DIR)
        files = [f for f in os.listdir(RECORDINGS_DIR) if f.endswith('.ogg')]
        files.sort(reverse=True)
        return jsonify({
            'success': True,
            'files': files[:10]
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
        if '..' in filename or '/' in filename:
            return jsonify({'error': '不正なファイル名です'}), 400
        
        if not filename.endswith('.ogg'):
            return jsonify({'error': 'OGGファイルのみダウンロード可能です'}), 400
        
        filepath = os.path.join(RECORDINGS_DIR, filename)
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
        if '..' in filename or '/' in filename:
            return jsonify({
                'success': False,
                'message': '不正なファイル名です'
            }), 400
        
        if not filename.endswith('.ogg'):
            return jsonify({
                'success': False,
                'message': 'OGGファイルのみ削除可能です'
            }), 400
        
        filepath = os.path.join(RECORDINGS_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            logging.info(f"ファイル削除: {filename}")
            return jsonify({'success': True, 'message': 'ファイルを削除しました'})
        else:
            return jsonify({'success': False, 'message': 'ファイルが見つかりません'}), 404
    
    except Exception as e:
        logging.error(f"削除エラー: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# --- 既存の関数（変更なし） ---

def is_wifi_connected():
    """Wi-Fiに接続されているか確認"""
    try:
        result = subprocess.check_output("iwgetid -r", shell=True).decode().strip()
        return bool(result)
    except subprocess.CalledProcessError:
        return False

def scan_wifi_networks():
    """利用可能なWi-Fiネットワークをスキャン"""
    networks = []
    try:
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

        scan_cmd = f"sudo nmcli --get-values SSID,SIGNAL,SECURITY device wifi list ifname {wifi_device}"
        result = subprocess.check_output(scan_cmd, shell=True).decode()
        
        seen_ssids = set()
        for line in result.strip().split('\n'):
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

def get_bluetooth_devices():
    """すべてのBluetoothアダプタからペアリング済みデバイスを取得"""
    devices = []
    
    try:
        hci_result = subprocess.run(['hciconfig'], 
                                  capture_output=True, text=True, timeout=10)
        
        adapters = []
        current_adapter = None
        
        for line in hci_result.stdout.split('\n'):
            if line.startswith('hci'):
                adapter_name = line.split(':')[0]
                current_adapter = {'name': adapter_name}
            elif current_adapter and 'BD Address:' in line:
                bd_addr = line.split('BD Address:')[1].split()[0]
                current_adapter['address'] = bd_addr
                adapters.append(current_adapter)
                current_adapter = None
        
        for adapter in adapters:
            cmd = f'select {adapter["address"]}\ndevices\nexit\n'
            result = subprocess.run(['bluetoothctl'], 
                                  input=cmd,
                                  capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.strip().startswith('Device '):
                        parts = line.strip().split(None, 2)
                        if len(parts) >= 3:
                            mac = parts[1]
                            name = parts[2]
                            
                            info_cmd = f'select {adapter["address"]}\ninfo {mac}\nexit\n'
                            info_result = subprocess.run(['bluetoothctl'],
                                                       input=info_cmd,
                                                       capture_output=True, text=True, timeout=10)
                            
                            info_lower = info_result.stdout.lower()
                            connected = 'connected: yes' in info_lower
                            paired = 'paired: yes' in info_lower
                            trusted = 'trusted: yes' in info_lower
                            
                            if paired:
                                devices.append({
                                    'mac': mac,
                                    'name': name,
                                    'adapter': adapter['address'],
                                    'adapter_name': adapter['name'],
                                    'connected': connected,
                                    'paired': paired,
                                    'trusted': trusted
                                })
        
    except Exception as e:
        logging.error(f"デバイス一覧取得エラー: {e}")
    
    return devices

def check_device_connection(device_info):
    """特定のデバイスの接続状態をチェック"""
    try:
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
                connect_cmd = f'select {device_info["adapter"]}\nconnect {device_info["mac"]}\nexit\n'
                connect_result = subprocess.run(['bluetoothctl'],
                                              input=connect_cmd,
                                              capture_output=True, text=True, timeout=15)
                
                if connect_result.returncode == 0:
                    time.sleep(3)
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

def cleanup():
    """クリーンアップ処理"""
    send_command({'action': 'shutdown'})
    logging.info("サーバーをクリーンアップしました")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Raspberry Pi Web Recorder")
    parser.add_argument('--setup', action='store_true', help='Run in Wi-Fi setup mode')
    args = parser.parse_args()
    
    is_setup_mode = args.setup

    if not is_setup_mode:
        # tmpディレクトリ作成
        if not os.path.exists(TEMP_DIR):
            os.makedirs(TEMP_DIR)
        
        # mDNS設定
        setup_avahi()
        
        # ワーカー監視スレッド開始        
        worker_status_thread = threading.Thread(target=monitor_worker_status, daemon=True)
        worker_status_thread.start()
        
        audio_level_thread = threading.Thread(target=monitor_audio_levels, daemon=True)
        audio_level_thread.start()

    print("=" * 50)
    print("Raspberry Pi Web録音コントローラー")
    if is_setup_mode:
        print("--- Wi-Fi設定モード ---")
    else:
        print(f"アクセス: http://{get_mdns_address()}:8080")
        print(f"         http://{get_ip_address()}:8080")
    print("=" * 50)
    
    try:
        socketio.run(app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)
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