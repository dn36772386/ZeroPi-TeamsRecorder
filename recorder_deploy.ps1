# 録音コントローラー デプロイスクリプト
param(
    [Parameter(Position=0)]
    [string]$Action = "upload"
)

$RaspberryPiIP = "192.168.0.6"
$User = "nakajima"

Write-Host "=== Raspberry Pi Recorder Deploy Script ===" -ForegroundColor Cyan

switch ($Action) {
    "download" {
        Write-Host "Downloading files from Raspberry Pi..." -ForegroundColor Green
        
        # サービスファイルをダウンロード
        Write-Host "Downloading recorder.service..." -ForegroundColor Yellow
        scp ${User}@${RaspberryPiIP}:~/recorder.service ./recorder.service
        
        # Pythonファイルもダウンロード（オプション）
        Write-Host "Downloading Python files..." -ForegroundColor Yellow
        scp ${User}@${RaspberryPiIP}:~/recorder_web.py ./
        scp ${User}@${RaspberryPiIP}:~/recorder_worker.py ./
        
        Write-Host "Download completed!" -ForegroundColor Green
    }
    
    "upload" {
        Write-Host "Uploading files to Raspberry Pi..." -ForegroundColor Green
        
        # Pythonファイルとテンプレートをアップロード
        scp -r templates recorder_web.py recorder_worker.py ${User}@${RaspberryPiIP}:~/
        
        # サービスファイルがあればアップロード
        if (Test-Path "./recorder.service") {
            Write-Host "Uploading recorder.service..." -ForegroundColor Yellow
            scp ./recorder.service ${User}@${RaspberryPiIP}:~/
            
            Write-Host "Installing service file..." -ForegroundColor Yellow
            ssh ${User}@${RaspberryPiIP} "sudo cp ~/recorder.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart recorder.service"
        }
        
        Write-Host "Upload completed!" -ForegroundColor Green
    }
    
    "restart" {
        Write-Host "Restarting recorder service..." -ForegroundColor Yellow
        ssh ${User}@${RaspberryPiIP} "sudo systemctl restart recorder.service"
        Write-Host "Service restarted!" -ForegroundColor Green
    }
    
    "status" {
        Write-Host "Checking service status..." -ForegroundColor Yellow
        ssh ${User}@${RaspberryPiIP} "sudo systemctl status recorder.service"
    }
    
    "logs" {
        Write-Host "Showing recent logs..." -ForegroundColor Yellow
        ssh ${User}@${RaspberryPiIP} "sudo journalctl -u recorder.service -n 50 --no-pager"
    }
    
    default {
        Write-Host "Usage: ./recorder_deploy.ps1 [action]" -ForegroundColor Red
        Write-Host "Actions: download, upload, restart, status, logs" -ForegroundColor Yellow
    }
}