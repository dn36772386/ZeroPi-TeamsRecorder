# 録音コントローラー起動スクリプト
Write-Host "Starting Raspberry Pi Recorder..." -ForegroundColor Green

# ファイルをコピー
scp -r templates recorder_web.py recorder_worker.py nakajima@192.168.0.6:~/

# 依存関係の確認とインストール（初回のみ）
# ssh nakajima@192.168.0.6 "which oggenc || sudo apt-get update && sudo apt-get install -y vorbis-tools"

