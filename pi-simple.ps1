# Simple Raspberry Pi Deploy
param([string]$cmd = "help")

#$PI = "172.20.10.13"
$PI = "raspberrypi.local"
$USER = "nakajima"

if ($cmd -eq "install") {
    Write-Host "Installing..." -ForegroundColor Green
    
    # Copy files
    scp *.py *.sh *.txt *.service ${USER}@${PI}:~/
    scp -r templates ${USER}@${PI}:~/
    
    # Install
    ssh ${USER}@${PI} "chmod +x install_deps.sh"
    ssh ${USER}@${PI} "./install_deps.sh"
    ssh ${USER}@${PI} "mkdir -p recordings"
    ssh ${USER}@${PI} "sudo cp recorder.service /etc/systemd/system/"
    ssh ${USER}@${PI} "sudo systemctl daemon-reload"
    ssh ${USER}@${PI} "sudo systemctl enable recorder.service"
    ssh ${USER}@${PI} "sudo systemctl start recorder.service"
    
    Write-Host "Done! http://${PI}:8080" -ForegroundColor Green
}
elseif ($cmd -eq "update") {
    Write-Host "Updating..." -ForegroundColor Yellow
    scp *.py ${USER}@${PI}:~/
    scp -r templates ${USER}@${PI}:~/
    ssh ${USER}@${PI} "sudo systemctl restart recorder.service"
    Write-Host "Done!" -ForegroundColor Green
}
elseif ($cmd -eq "log") {
    Write-Host "Logs (Ctrl+C to exit)" -ForegroundColor Cyan
    ssh ${USER}@${PI} "sudo journalctl -u recorder.service -f"
}
elseif ($cmd -eq "status") {
    ssh ${USER}@${PI} "sudo systemctl status recorder.service"
}
else {
    Write-Host "Usage: .\pi-simple.ps1 [install|update|log|status]" -ForegroundColor Cyan
}