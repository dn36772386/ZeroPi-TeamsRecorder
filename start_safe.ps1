# 安定版起動スクリプト
Write-Host "Starting Safe Recorder..." -ForegroundColor Green

# ファイルコピー
scp -r templates recorder_web.py nakajima@192.168.0.6:~/

# 依存関係の確認
Write-Host "Checking dependencies..." -ForegroundColor Yellow
ssh nakajima@192.168.0.6 @"
    # 必要なパッケージをインストール
    which oggenc || sudo apt-get update && sudo apt-get install -y vorbis-tools
    
    # PyAudioの修正版をインストール
    pip3 install --upgrade pyaudio
"@

# 安全な起動スクリプトを作成
$safeScript = @'
#!/bin/bash
cd /home/nakajima

# 既存プロセスを停止
pkill -f recorder_web.py
sleep 2

# ログファイルを作成
LOG_FILE="recorder_$(date +%Y%m%d_%H%M%S).log"

# Pythonバッファリングを無効化して起動
export PYTHONUNBUFFERED=1

# 無限ループで実行（クラッシュ時に自動再起動）
while true; do
    echo "[$(date)] Starting recorder..." | tee -a $LOG_FILE
    python3 -u recorder_web.py 2>&1 | tee -a $LOG_FILE
    
    echo "[$(date)] Recorder stopped. Restarting in 5 seconds..." | tee -a $LOG_FILE
    sleep 5
done
'@

# スクリプトを転送して実行
Write-Host "Starting server..." -ForegroundColor Green
$safeScript | ssh nakajima@192.168.0.6 "cat > start_recorder.sh && chmod +x start_recorder.sh"

# nohupで実行
ssh nakajima@192.168.0.6 @"
    nohup ./start_recorder.sh > /dev/null 2>&1 &
    sleep 3
    ps aux | grep recorder_web.py | grep -v grep
    echo ''
    echo '================================'
    echo 'サーバーが起動しました！'
    echo 'アクセスURL: http://192.168.0.6:5000'
    echo ''
    echo 'ログ確認: tail -f recorder_*.log'
    echo 'プロセス確認: ps aux | grep recorder'
    echo '停止: pkill -f start_recorder.sh'
    echo '================================'
"@