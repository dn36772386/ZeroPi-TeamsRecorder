# ZeroPi-TeamsRecorder

Raspberry Piとスマートフォン（iPhone）を使用して、Bluetooth経由での高音質録音をWebブラウザからコントロールするためのアプリケーションです。

*(注: 上記はUIのイメージです)*

## ✨ 主な機能

  * **リモート録音:** スマートフォンのWebブラウザから録音の開始・停止を操作できます。
  * **Bluetooth音声入力:** iPhoneなどのスマートフォンを高品質なワイヤレスマイクとして利用し、その音声をRaspberry Piで直接録音します。
  * **ファイル管理:** 録音されたファイル（OGG形式）の一覧表示、ダウンロード、削除がWeb上で行えます。
  * **ネットワーク設定機能:** Raspberry Piが未知のでも、スマートフォンからWi-Fi（テザリングなど）への接続設定が可能です。
  * **自動起動:** `systemd`サービスとして登録することで、Raspberry Piの起動と同時にバックグラウンドでサーバーが自動起動します。

## 📂 ディレクトリ構造

```
.
├── recorder_web.py         # Flaskで構築されたメインのWebサーバー
├── recorder_worker.py        # 実際に録音処理を行うバックグラウンドワーカー
├── recorder_status.json      # Webとワーカー間の状態共有ファイル
├── recorder_command.json     # Webからワーカーへの命令ファイル
├── recorder_config.json      # 選択されたデバイス設定の保存ファイル
|
├── templates/
│   ├── index.html          # 通常利用時のメイン画面 (GitHub風UI)
│   ├── setup.html          # Wi-Fi設定用のWebページ
│   └── connect_status.html   # Wi-Fi接続結果を表示するページ
|
├── install_deps.sh         # 依存パッケージをインストールするスクリプト
├── recorder.service        # systemd用のサービス設定ファイル（サンプル）
└── README.md               # このファイル
```

## ⚙️ 技術的な仕組み

このシステムは、Webサーバーの応答性を維持しつつ、重い録音処理を安定して行うために、役割の異なる2つのプロセスで構成されています。

1.  **Webサーバー (`recorder_web.py`)**

      * PythonのWebフレームワーク**Flask**を使用。
      * ユーザーからのHTTPリクエスト（録音開始/停止など）を受け付けます。
      * システムの「リモコン」として機能し、録音命令を`recorder_command.json`に書き込みます。

2.  **録音ワーカー (`recorder_worker.py`)**

      * 独立したPythonプロセスとしてバックグラウンドで常時実行。
      * `recorder_command.json`を監視し、命令を受け取ると録音処理を開始・停止します。
      * 現在の状態（待機中、録音中など）を`recorder_status.json`に書き込み、Webサーバーに伝えます。

また、**Wi-Fi**と**Bluetooth**はそれぞれ以下の異なる役割を担っています。

  * **Wi-Fi:** WebブラウザとWebサーバー間の\*\*操作命令（コントロール）\*\*の通信に使われます。
  * **Bluetooth:** スマートフォンから送られてくる**音声データ**の通信に使われます。

## 🚀 セットアップと実行方法

### 前提条件

  * Raspberry Pi Zero W / 3 / 4 など (Wi-FiとBluetooth機能が必須)
  * Raspberry Pi OS (Bullseye以降を推奨)
  * スマートフォン（iPhoneなど）

### 1\. インストール

まず、PCからSSHでRaspberry Piに接続し、リポジトリをクローンします。

```bash
# 適切な場所にプロジェクトをクローン
git clone https://github.com/your-username/ZeroPi-TeamsRecorder.git
cd ZeroPi-TeamsRecorder
```

次に、依存するソフトウェアをインストールします。

```bash
# スクリプトに実行権限を付与
chmod +x install_deps.sh

# スクリプトを実行して依存パッケージをインストール
./install_deps.sh
```

### 2\. `systemd`サービスのセットアップ

システムの自動起動を設定します。

1.  **サービスファイルの配置**
    `recorder.service`ファイルを `/etc/systemd/system/` ディレクトリにコピー（または新規作成）します。
    ```bash
    sudo cp recorder.service /etc/systemd/system/recorder.service
    ```
2.  **パスの編集【重要】**
    配置したサービスファイルを開き、`WorkingDirectory`と`ExecStart`のパスを、あなたがプロジェクトをクローンした**絶対パス**に必ず修正してください。
    ```bash
    sudo nano /etc/systemd/system/recorder.service
    ```
3.  **サービスの有効化**
    以下のコマンドでサービスを有効化し、OS起動時に自動で立ち上がるようにします。
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable recorder.service
    ```

### 3\. 実行方法

#### A. 通常利用 (設定済みWi-Fi環境)

1.  Raspberry Piの電源を入れ、1〜2分待ちます。
2.  iPhoneをRaspberry Piと同じWi-Fiに接続します。
3.  iPhoneのブラウザで、Raspberry PiのIPアドレス（例: `http://192.168.1.10`）にアクセスします。
      * IPアドレスは、Web画面のコントロールパネルに表示されます。

#### B. 新規Wi-Fiへの接続設定 (外出先など)

1.  **設定モードへの移行**
    PCやSSHアプリからPiに接続し、現在実行中の通常サービスを停止後、`--setup`オプション付きでプログラムを手動起動します。

    ```bash
    sudo systemctl stop recorder.service
    sudo python3 /path/to/your/recorder_web.py --setup
    ```

2.  **iPhoneからの設定**

    1.  iPhoneのWi-Fi設定画面を開き、Piが発しているアクセスポイント（例: `raspberrypi`）に接続します。
    2.  ブラウザで `http://(PiのIPアドレス)` にアクセスします。（通常は `http://192.168.4.1` など）
    3.  表示された設定ページで、接続したいWi-Fi（スマートフォンのテザリングなど）を選択し、パスワードを入力して接続します。
    4.  Piが自動的に再起動します。

3.  **通常モードでの利用再開**

    1.  Piの再起動後、iPhoneを先ほど設定したWi-Fi（テザリングなど）に接続し直します。
    2.  PCやSSHアプリから `sudo systemctl start recorder.service` コマンドで通常サービスを再開します。
    3.  これで、新しいネットワーク上でPiにアクセスできるようになります。