# MT5 Price-Change Notifier for AItuber Kit

MetaTrader 5 (MT5) に接続し、指定した通貨ペアの価格変動をリアルタイムで監視するPythonスクリプトです。
価格が設定した閾値（pips）を超えて変動すると、WebSocketを通じてJSON形式のメッセージをブロードキャストします。

このシステムは、[AItuber Kit](https://github.com/tegnike/AItuber-Kit) のようなアプリケーションと連携し、為替市場の動きに応じてAIキャラクターがリアルタイムで反応する、といった用途を想定して設計されています。

## ✨ 主な機能

-   **リアルタイム価格監視**: MetaTrader 5 と直接連携し、最新のティック価格を取得します。
-   **複数通貨ペア対応**: 監視したい通貨ペアを簡単に追加・変更できます。
-   **pipsベースの変動通知**: 小・中・大の3段階で変動閾値（pips）を設定し、変動レベルに応じたメッセージを送信します。
-   **WebSocketサーバー内蔵**: 複数のクライアント（AItuber Kitなど）が同時に接続し、価格変動の通知を受け取ることができます。
-   **柔軟なメッセージ形式**: 通知メッセージには、テキスト、感情、役割などが含まれており、AIキャラクターのリアクション制御に最適です。

## ⚙️ 動作要件

-   Python 3.7以上
-   [MetaTrader 5](https://www.metatrader5.com/) デスクトップアプリケーション
    -   MT5がPCにインストールされ、実行中である必要があります。
    -   デモ口座またはリアル口座にログインしている必要があります。
-   Pythonライブラリ (依存関係)
    -   `MetaTrader5`
    -   `websockets`

## 📦 インストール

1.  プロジェクトをクローンまたはダウンロードします。

2.  必要なPythonライブラリをインストールします。
    ```bash
    pip install MetaTrader5 websockets
    ```

## 🔧 設定

スクリプト `mt5_monitor.py` の冒頭にある `設定` セクションを編集することで、動作をカスタマイズできます。

```python
# ==================== 設定 (Config) ====================
@dataclass
class Config:
    """アプリケーションの設定を管理するクラス"""
    # 監視する通貨ペア（MT5のシンボル名）と小数点以下の桁数
    watch_symbols: Dict[str, Dict[str, int]] = field(default_factory=lambda: {
        "USDJPY": {"digits": 3},
        "EURUSD": {"digits": 5},
        "GBPUSD": {"digits": 5},
        "EURJPY": {"digits": 3},
        "GBPJPY": {"digits": 3},
    })
    # 更新間隔（秒）
    update_interval: float = 1.0
    # 変動閾値（pips）
    small_threshold: float = 5.0
    medium_threshold: float = 16.0
    large_threshold: float = 30.0
    # メッセージ
    msg_small: str = "📊 すこしのうごきがありましたです"
    msg_medium: str = "⚠️ ちゅうくらいのうごきがありましたです"
    msg_large: str = "🚨 えええっ～びっくりです。大変です。"
    # WebSocketサーバー設定
    ws_host: str = "0.0.0.0"
    ws_port: int = 8000

config = Config()
```

-   `watch_symbols`: 監視する通貨ペアとその桁数（`digits`）を指定します。`digits`はpips計算に用いられます（例: JPYペアは3、EURUSDなどは5）。
-   `update_interval`: MT5へ価格を問い合わせる間隔（秒）です。
-   `*_threshold`: `small`, `medium`, `large` の変動を検知するpips数を設定します。
-   `msg_*`: 各変動レベルで送信されるメッセージのテンプレートです。
-   `ws_host`, `ws_port`: WebSocketサーバーが待機するホストとポートです。`0.0.0.0` を指定すると、ローカルネットワーク内の他のPCからもアクセス可能になります。

## 🚀 実行方法

1.  **MetaTrader 5を起動**: PCでMT5アプリケーションを起動し、口座にログインしておきます。

2.  **スクリプトを実行**: ターミナルまたはコマンドプロンプトで以下のコマンドを実行します。
    ```bash
    python mt5_monitor.py
    ```

3.  **クライアントから接続**: WebSocketクライアント（AItuber Kitなど）から `ws://localhost:8000` （ホスト名を変更した場合はそのアドレス）に接続します。

## 🔌 WebSocket API

このサーバーは、価格が設定した閾値を超えて変動した際に、以下の形式のJSONメッセージを接続中の全クライアントに送信します。

### メッセージ形式

```json
{
    "text": "USDJPY が 5.1 pips 上昇 しました\n📊 すこしのうごきがありましたです",
    "role": "assistant",
    "emotion": "happy",
    "type": "message"
}
```

---
**免責事項**: 本ソフトウェアは情報提供を目的としており、投資助言を構成するものではありません。本ソフトウェアの使用によって生じたいかなる損害についても、開発者は一切の責任を負いません。