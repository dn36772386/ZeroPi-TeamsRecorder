#!/bin/bash
# Raspberry Pi録音コントローラー用の依存関係をインストール

echo "録音コントローラーの依存関係をインストールします..."

# vorbis-tools (oggenc) のインストール
if ! command -v oggenc &> /dev/null; then
    echo "vorbis-toolsをインストール中..."
    sudo apt-get update
    sudo apt-get install -y vorbis-tools
else
    echo "vorbis-toolsは既にインストールされています"
fi

# PyAudioの依存関係
echo "PyAudio依存関係をインストール中..."
sudo apt-get install -y python3-pyaudio portaudio19-dev

# Pythonパッケージ
echo "Pythonパッケージをインストール中..."
pip3 install flask pyaudio

echo "インストール完了！"