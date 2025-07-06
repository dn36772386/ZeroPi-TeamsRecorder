# ZeroPi-TeamsRecorder System Monitoring Script (Simple Version)
# Usage: .\check_system_simple.ps1

param(
    [string]$PI = "192.168.0.16",
    [string]$USER = "nakajima",
    [string]$SSHKey = "",  # SSH秘密鍵のパス（オプション）
    [switch]$SetupSSHKey   # SSH鍵をセットアップするオプション
)

# Function for colored output
function Write-ColorOutput($ForegroundColor, $Message) {
    $originalColor = $host.UI.RawUI.ForegroundColor
    $host.UI.RawUI.ForegroundColor = $ForegroundColor
    Write-Output $Message
    $host.UI.RawUI.ForegroundColor = $originalColor
}

# SSH鍵のセットアップ
function Setup-SSHKey {
    Write-ColorOutput Cyan "======================================================"
    Write-ColorOutput Cyan "SSH Key Setup for Password-less Access"
    Write-ColorOutput Cyan "======================================================"
    
    $sshDir = "$env:USERPROFILE\.ssh"
    $keyPath = "$sshDir\id_rsa_pi"
    
    # .sshディレクトリの作成
    if (-not (Test-Path $sshDir)) {
        New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
    }
    
    # 既存の鍵を確認
    if (Test-Path $keyPath) {
        Write-ColorOutput Yellow "SSH key already exists at: $keyPath"
        $overwrite = Read-Host "Do you want to overwrite it? (Y/N)"
        if ($overwrite -notmatch '^[Yy]') {
            Write-ColorOutput Green "Using existing key."
        } else {
            # 新しい鍵を生成
            Write-ColorOutput Yellow "Generating new SSH key..."
            ssh-keygen -t rsa -b 4096 -f $keyPath -N '""' -q
            Write-ColorOutput Green "SSH key generated successfully!"
        }
    } else {
        # 新しい鍵を生成
        Write-ColorOutput Yellow "Generating new SSH key..."
        ssh-keygen -t rsa -b 4096 -f $keyPath -N '""' -q
        Write-ColorOutput Green "SSH key generated successfully!"
    }
    
    # 公開鍵をRaspberry Piにコピー
    Write-ColorOutput Yellow "Copying public key to Raspberry Pi..."
    Write-ColorOutput Cyan "Please enter your password when prompted:"
    
    # 公開鍵の内容を取得
    $publicKey = Get-Content "${keyPath}.pub"
    
    # SSHコマンドで公開鍵を追加
    $setupCommand = @"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo '$publicKey' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
echo 'SSH key added successfully!'
"@
    
    $result = $setupCommand | ssh -o StrictHostKeyChecking=no "$USER@$PI" "bash -s" 2>&1
    
    if ($result -match "SSH key added successfully!") {
        Write-ColorOutput Green "`nSSH key setup completed successfully!"
        Write-ColorOutput Green "You can now use the script without entering a password."
        Write-ColorOutput Cyan "`nUsage: .\check_system_simple.ps1 -SSHKey '$keyPath'"
        Write-ColorOutput Cyan "Or simply: .\check_system_simple.ps1 (will auto-detect the key)"
    } else {
        Write-ColorOutput Red "Failed to setup SSH key: $result"
    }
    
    return $keyPath
}

# SSH接続オプションを構築
function Get-SSHOptions {
    $options = @("-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes")
    
    # SSH鍵の自動検出または指定された鍵を使用
    if ($script:SSHKey) {
        $options += @("-i", $script:SSHKey)
    } else {
        # デフォルトの鍵パスを確認
        $defaultKey = "$env:USERPROFILE\.ssh\id_rsa_pi"
        if (Test-Path $defaultKey) {
            $options += @("-i", $defaultKey)
            Write-ColorOutput Green "Using SSH key: $defaultKey"
        }
    }
    
    return $options
}

# Execute SSH command
function Invoke-SSHCommand {
    param([string]$Command)
    
    $sshOptions = Get-SSHOptions
    $sshCommand = @("ssh", "-T") + $sshOptions + @("$USER@$PI", $Command)
    
    try {
        $result = & $sshCommand[0] $sshCommand[1..($sshCommand.Length-1)] 2>&1 | 
            Where-Object { $_ -notmatch "Pseudo-terminal|Warning: Permanently added" }
        return $result
    } catch {
        Write-ColorOutput Red "SSH command failed: $_"
        return $null
    }
}

# Test SSH connection
function Test-SSHConnection {
    Write-ColorOutput Yellow "Testing SSH connection..."
    
    $testResult = Invoke-SSHCommand "echo 'Connection Test: OK'"
    
    if ($testResult -match "Connection Test: OK") {
        Write-ColorOutput Green "SSH connection successful (password-less access confirmed)"
        return $true
    } elseif ($testResult -match "Permission denied|password:") {
        Write-ColorOutput Red "SSH connection requires password."
        Write-ColorOutput Yellow "Run with -SetupSSHKey option to setup password-less access:"
        Write-ColorOutput Cyan ".\check_system_simple.ps1 -SetupSSHKey"
        return $false
    } else {
        Write-ColorOutput Red "SSH connection failed: $testResult"
        return $false
    }
}

# Check system status
function Get-SystemStatus {
    Write-ColorOutput Yellow "Checking system status..."
    
    Write-Output "======================================================"
    Write-Output "ZeroPi-TeamsRecorder System Status Check"
    Write-Output "======================================================"
    
    # Date
    $date = Invoke-SSHCommand "date"
    Write-Output "Date: $date"
    Write-Output ""
    
    # Service Status
    Write-Output "Service Status:"
    $services = @("recorder.service", "redis-server.service", "avahi-daemon.service", "bluetooth.service")
    foreach ($service in $services) {
        $isActive = Invoke-SSHCommand "systemctl is-active $service 2>/dev/null || echo inactive"
        $isEnabled = Invoke-SSHCommand "systemctl is-enabled $service 2>/dev/null || echo disabled"
        Write-Output "  ${service}: $isActive ($isEnabled)"
    }
    Write-Output ""
    
    # Running Processes
    Write-Output "Running Processes:"
    $recorderWeb = Invoke-SSHCommand "pgrep -f recorder_web.py | wc -l"
    $recorderWorker = Invoke-SSHCommand "pgrep -f recorder_worker.py | wc -l"
    $redisServer = Invoke-SSHCommand "pgrep -f redis-server | wc -l"
    Write-Output "  recorder_web: $recorderWeb processes"
    Write-Output "  recorder_worker: $recorderWorker processes"
    Write-Output "  redis-server: $redisServer processes"
    Write-Output ""
    
    # Port Status
    Write-Output "Port Status:"
    $port8080 = Invoke-SSHCommand "sudo netstat -tulpn 2>/dev/null | grep -q ':8080' && echo 'Open' || echo 'Closed'"
    $port6379 = Invoke-SSHCommand "sudo netstat -tulpn 2>/dev/null | grep -q ':6379' && echo 'Open' || echo 'Closed'"
    Write-Output "  Port 8080: $port8080"
    Write-Output "  Port 6379: $port6379"
    Write-Output ""
    
    # Redis Connection Test
    Write-Output "Redis Connection Test:"
    $redisTest = Invoke-SSHCommand "redis-cli ping 2>/dev/null || echo 'Connection failed'"
    if ($redisTest -eq "PONG") {
        Write-Output "  Redis: Connection successful"
    } else {
        Write-Output "  Redis: Connection failed"
    }
    Write-Output ""
    
    # System Resources
    Write-Output "System Resources:"
    $cpuUsage = Invoke-SSHCommand "top -bn1 | grep 'Cpu(s)' | awk '{print \$2}' | cut -d'%' -f1"
    Write-Output "  CPU Usage: ${cpuUsage}%"
    
    $memUsage = Invoke-SSHCommand "free | grep Mem | awk '{print int(\$3/\$2 * 100)}'"
    Write-Output "  Memory Usage: ${memUsage}%"
    
    $diskUsage = Invoke-SSHCommand "df -h / | awk 'NR==2{print \$5}'"
    Write-Output "  Disk Usage: $diskUsage"
    Write-Output ""
    
    # Access Information
    Write-Output "Access URL:"
    Write-Output "  http://raspberrypi.local:8080"
    $ipAddress = Invoke-SSHCommand "hostname -I | awk '{print \$1}'"
    Write-Output "  http://${ipAddress}:8080"
    Write-Output ""
    
    # Recording Files Information
    Write-Output "Recording Files:"
    $recordingsExist = Invoke-SSHCommand "test -d /home/nakajima/recordings && echo 'yes' || echo 'no'"
    if ($recordingsExist -eq "yes") {
        $fileCount = Invoke-SSHCommand "ls -1 /home/nakajima/recordings/*.ogg 2>/dev/null | wc -l"
        if ([int]$fileCount -gt 0) {
            Write-Output "  Recording files count: $fileCount"
            $latestFile = Invoke-SSHCommand "ls -t /home/nakajima/recordings/*.ogg 2>/dev/null | head -1 | xargs basename"
            Write-Output "  Latest file: $latestFile"
        } else {
            Write-Output "  No recording files found."
        }
    } else {
        Write-Output "  Recordings directory not found."
    }
    
    Write-Output "======================================================"
}

# Get system logs
function Get-SystemLogs {
    Write-ColorOutput Yellow "Fetching system logs (last 50 entries)..."
    
    Write-Output "======================================================"
    Write-Output "ZeroPi-TeamsRecorder System Logs - Last 50 entries"
    Write-Output "======================================================"
    Write-Output ""
    
    # Recorder service log
    Write-Output "recorder.service Log:"
    $recorderLog = Invoke-SSHCommand "sudo journalctl -u recorder.service -n 25 --no-pager -o short 2>&1 || echo 'No logs available'"
    Write-Output $recorderLog
    Write-Output ""
    
    # Redis server log
    Write-Output "redis-server.service Log:"
    $redisLog = Invoke-SSHCommand "sudo journalctl -u redis-server.service -n 10 --no-pager -o short 2>&1 || echo 'No logs available'"
    Write-Output $redisLog
    Write-Output ""
    
    # Bluetooth service log
    Write-Output "bluetooth.service Log:"
    $bluetoothLog = Invoke-SSHCommand "sudo journalctl -u bluetooth.service -n 10 --no-pager -o short 2>&1 || echo 'No logs available'"
    Write-Output $bluetoothLog
    Write-Output ""
    
    # Avahi daemon log
    Write-Output "avahi-daemon.service Log:"
    $avahiLog = Invoke-SSHCommand "sudo journalctl -u avahi-daemon.service -n 5 --no-pager -o short 2>&1 || echo 'No logs available'"
    Write-Output $avahiLog
    
    Write-Output "======================================================"
}

# Main process
function Main {
    # SSH鍵のセットアップが要求された場合
    if ($SetupSSHKey) {
        $keyPath = Setup-SSHKey
        if ($keyPath) {
            $script:SSHKey = $keyPath
        }
        Write-Output ""
    }
    
    $header = @"
====================================================
ZeroPi-TeamsRecorder System Monitoring Tool
====================================================
Target: $USER@$PI
Time: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
====================================================
"@
    Write-ColorOutput Cyan $header
    
    # Test SSH connection
    if (-not (Test-SSHConnection)) {
        exit 1
    }
    
    # Check system status
    try {
        Get-SystemStatus
    } catch {
        Write-ColorOutput Red "System status check failed: $_"
    }
    
    Write-ColorOutput Cyan ("="*50)
    
    # Get logs
    try {
        Get-SystemLogs
    } catch {
        Write-ColorOutput Red "Log fetching failed: $_"
    }
    
    $footer = @"

Monitoring Complete!

Hints:
- Periodic execution: .\check_system_simple.ps1
- Setup SSH key for password-less access: .\check_system_simple.ps1 -SetupSSHKey
- If there is a problem: .\pi.ps1 status
- Real-time monitoring: .\pi.ps1 log

====================================================
"@
    Write-ColorOutput Green $footer
}

# Execute script
Main