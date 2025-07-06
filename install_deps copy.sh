#!/bin/bash
# Raspberry Pi録音コントローラー用の依存関係をインストール（完全版）

echo "録音コントローラーの依存関係をインストールします..."

# システムパッケージの更新
echo "システムを更新中..."
sudo apt-get update

# 基本的な録音ツール
echo "録音ツールをインストール中..."
sudo apt-get install -y vorbis-tools portaudio19-dev python3-pyaudio pulseaudio-utils

# Redis
echo "Redisをインストール中..."
sudo apt-get install -y redis-server
# Redisを有効化
sudo systemctl enable redis-server
sudo systemctl start redis-server

# mDNS (Avahi)
echo "Avahiをインストール中..."
sudo apt-get install -y avahi-daemon avahi-utils
# Avahiを有効化
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon

# Python依存関係
echo "Python依存関係をインストール中..."
sudo apt-get install -y python3-pip python3-numpy

# Pythonパッケージ
echo "Pythonパッケージをインストール中..."
pip3 install flask flask-socketio python-socketio redis numpy

# tmpディレクトリの作成（SDカード保護）
echo "一時ディレクトリを作成中..."
sudo mkdir -p /tmp/recorder
sudo chmod 777 /tmp/recorder

# tmpfsの設定（RAM上にマウント）
echo "tmpfsを設定中..."
if ! grep -q "/tmp/recorder" /etc/fstab; then
    echo "tmpfs /tmp/recorder tmpfs defaults,noatime,size=100M 0 0" | sudo tee -a /etc/fstab
    sudo mount /tmp/recorder
fi

# Bluetoothオーディオの最適化
echo "Bluetoothオーディオを最適化中..."
# PulseAudioのBluetooth設定
if [ ! -f /etc/pulse/default.pa.backup ]; then
    sudo cp /etc/pulse/default.pa /etc/pulse/default.pa.backup
fi

# A2DP高品質プロファイルを有効化
sudo tee -a /etc/pulse/default.pa > /dev/null <<EOF

# Bluetooth A2DP高品質設定
.ifexists module-bluetooth-discover.so
load-module module-bluetooth-discover a2dp_config="ldac_eqmid=hq"
.endif
EOF

# PulseAudioの再起動
systemctl --user restart pulseaudio

echo "================================"
echo "インストール完了！"
echo "================================"
echo ""
echo "次のステップ:"
echo "1. recorder_web.pyとrecorder_worker.pyをホームディレクトリに配置"
echo "2. sudo systemctl daemon-reload"
echo "3. sudo systemctl enable recorder.service"
echo "4. sudo systemctl start recorder.service"
echo ""
echo "アクセス方法:"
echo "- http://raspberrypi.local:8080"
echo "- http://[Raspberry PiのIP]:8080"
echo ""