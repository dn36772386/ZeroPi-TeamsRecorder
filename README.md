# ZeroPi-TeamsRecorder

Raspberry PiとiPhoneを使用して、Bluetooth経由で高音質録音を行うWebアプリケーションです。

![録音画面](https://via.placeholder.com/800x400?text=Recording+Interface)

## ✨ 主な機能

- **🎙️ Bluetooth録音** - iPhoneを高品質ワイヤレスマイクとして使用
- **🌐 Webブラウザ操作** - スマホやPCから録音をコントロール
- **📊 リアルタイム音声レベル** - WebSocketによる音声レベルメーター表示
- **📁 ファイル管理** - 録音ファイル（OGG形式）の一覧・ダウンロード・削除
- **🔍 自動検出** - mDNS対応で`raspberrypi.local`でアクセス可能
- **🚀 自動起動** - systemdサービスとして登録、起動時に自動実行

## 🆕 v2.0の新機能

- **WebSocket通信** - リアルタイムステータス更新（ポーリング廃止）
- **Redis導入** - 高速なメモリ内データ管理（JSONファイル廃止）
- **音声レベルメーター** - 録音中の音声レベルをリアルタイム表示
- **mDNS対応** - IPアドレス不要で`http://raspberrypi.local:8080`でアクセス
- **SDカード保護** - tmpfsによる一時ファイル管理

## 📋 必要なもの

- Raspberry Pi Zero W / 3 / 4（Wi-Fi・Bluetooth搭載）
- Raspberry Pi OS（Bullseye以降）
- iPhone（またはBluetoothオーディオ対応スマートフォン）

## 🚀 かんたんインストール

### Windows PCから（VSCode使用）

1. **プロジェクトをダウンロード**
   ```powershell
   git clone https://github.com/your-username/ZeroPi-TeamsRecorder.git
   cd ZeroPi-TeamsRecorder
   ```

2. **設定を変更**（`pi.ps1`の先頭部分）
   ```powershell
   $PI = "192.168.0.16"  # あなたのRaspberry PiのIP
   $USER = "pi"          # ユーザー名
   ```

3. **インストール実行**
   ```powershell
   .\pi.ps1 install
   ```

### Raspberry Piで直接インストール

```bash
# プロジェクトをクローン
git clone https://github.com/your-username/ZeroPi-TeamsRecorder.git
cd ZeroPi-TeamsRecorder

# インストール実行
chmod +x install_deps.sh
./install_deps.sh

# サービス登録
sudo cp recorder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable recorder.service
sudo systemctl start recorder.service
```

## 📱 使い方

1. **アクセス**
   - `http://raspberrypi.local:8080` または
   - `http://[Raspberry PiのIP]:8080`

2. **Bluetoothデバイス選択**
   - 右側のデバイスリストからiPhoneを選択
   - 自動的に接続を試行

3. **録音**
   - 「録音開始」ボタンをクリック
   - リアルタイムで音声レベルを確認
   - 「録音停止」で終了

4. **ファイル管理**
   - 録音ファイルは自動的にリストに表示
   - ダウンロード・削除が可能

## 🏗️ システム構成

```
Raspberry Pi
├── recorder_web.py      # Webサーバー（Flask + SocketIO）
├── recorder_worker.py   # 録音ワーカー（バックグラウンド）
├── Redis               # データ管理・プロセス間通信
└── templates/
    └── index.html      # Webインターフェース
```

### 通信フロー

```
iPhone ─[Bluetooth音声]→ Raspberry Pi
  ↑                           ↓
  └──[WebSocket/HTTP]── ブラウザ
```

## 🛠️ 開発・デバッグ

### コード更新
```powershell
.\pi.ps1 update
```

### ログ確認
```powershell
.\pi.ps1 log
```

### サービス状態
```powershell
.\pi.ps1 status
```

## 📝 トラブルシューティング

### 接続できない場合
- mDNS名が解決できない → IPアドレス直接指定
- ファイアウォール確認 → `sudo ufw allow 8080`

### Bluetoothデバイスが表示されない
- iPhoneのBluetooth設定でペアリング確認
- `sudo systemctl restart bluetooth`

### 録音が開始されない
- Redisが起動しているか確認: `redis-cli ping`
- ワーカープロセス確認: `ps aux | grep recorder_worker`

## 🔧 詳細設定

### 録音品質の変更
`recorder_worker.py`内の設定を編集：
```python
'-ab', '128k',  # ビットレート（64k〜320k）
```

### ポート番号の変更
`recorder_web.py`の最下部：
```python
socketio.run(app, host='0.0.0.0', port=8080)
```

## 📄 ライセンス

MIT License

## 🤝 貢献

プルリクエスト歓迎です！

1. Fork it
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request