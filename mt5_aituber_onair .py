
#!/usr/bin/env python3
"""
MetaTrader 5 → AItuber on Air 統合システム (/direct-speech対応版)
"""

import asyncio
import json
import logging
import websockets
from dataclasses import dataclass, field
from typing import Dict, Set, Optional
import MetaTrader5 as mt5
from datetime import datetime
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== 設定 ====================
@dataclass
class Config:
    watch_symbols: Dict[str, Dict] = field(default_factory=lambda: {
        "USDJPY": {"digits": 3, "jp_name": "どるえん"},
        "EURUSD": {"digits": 5, "jp_name": "ユーロドル"},
        "GBPUSD": {"digits": 5, "jp_name": "ポンドル"},
        "EURJPY": {"digits": 3, "jp_name": "ユーロえん"},
        "GBPJPY": {"digits": 3, "jp_name": "ポンドえん"},
    })
    update_interval: float = 2.0
    small_threshold: float = 5.0
    medium_threshold: float = 16.0
    large_threshold: float = 30.0
    msg_small: str = "📊 すこしのうごきがあったわ"
    msg_medium: str = "⚠️ ちゅうくらいのうごきがあったわ"
    msg_large: str = "🚨 おい！なんかあっただろ"
    ws_host: str = "0.0.0.0"
    ws_port: int = 8000
    http_port: int = 8080

config = Config()

# ==================== メッセージブローカー ====================
class MessageBroker:
    def __init__(self):
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.dashboard_clients: Set[websockets.WebSocketServerProtocol] = set()
    
    def add_client(self, ws: websockets.WebSocketServerProtocol, is_dashboard=False):
        if is_dashboard:
            self.dashboard_clients.add(ws)
            logger.info(f"✓ ダッシュボード接続 (合計: {len(self.dashboard_clients)})")
        else:
            self.clients.add(ws)
            logger.info(f"✓ AITuber接続 (合計: {len(self.clients)})")
    
    def remove_client(self, ws: websockets.WebSocketServerProtocol, is_dashboard=False):
        if is_dashboard:
            self.dashboard_clients.discard(ws)
            logger.info(f"✗ ダッシュボード切断 (残り: {len(self.dashboard_clients)})")
        else:
            self.clients.discard(ws)
            logger.info(f"✗ AITuber切断 (残り: {len(self.clients)})")
    
    async def broadcast(self, message_data):
        if not self.clients:
            return
        
        # AITuber on Air形式に変換
        if isinstance(message_data, str):
            message_to_send = json.dumps({
                "type": "chat",
                "text": message_data
            }, ensure_ascii=False)
        elif isinstance(message_data, dict):
            # すでに正しい形式ならそのまま、違えば変換
            if "type" in message_data and "text" in message_data:
                message_to_send = json.dumps(message_data, ensure_ascii=False)
            else:
                message_to_send = json.dumps({
                    "type": "chat",
                    "text": message_data.get("text", str(message_data))
                }, ensure_ascii=False)
        else:
            message_to_send = json.dumps({
                "type": "chat",
                "text": str(message_data)
            }, ensure_ascii=False)
        
        dead = set()
        
        for client in self.clients:
            try:
                await client.send(message_to_send)
                # ログ用に元のテキストを抽出
                if isinstance(message_data, str):
                    display_text = message_data
                else:
                    display_text = message_data.get('text', str(message_data))
                logger.info(f"✓ 送信: {display_text[:50]}")
            except websockets.exceptions.ConnectionClosed:
                dead.add(client)
        
        for client in dead:
            self.remove_client(client)
    
    async def broadcast_dashboard(self, data: Dict):
        if not self.dashboard_clients:
            return
        
        message_json = json.dumps(data, ensure_ascii=False)
        dead = set()
        
        for client in self.dashboard_clients:
            try:
                await client.send(message_json)
            except websockets.exceptions.ConnectionClosed:
                dead.add(client)
        
        for client in dead:
            self.remove_client(client, is_dashboard=True)

broker = MessageBroker()

# ==================== 価格監視 ====================
class PriceMonitor:
    def __init__(self):
        self.symbol_data = {}
        for symbol, info in config.watch_symbols.items():
            self.symbol_data[symbol] = {
                "base_price": None,
                "last_price": None,
                "digits": info["digits"],
                "jp_name": info["jp_name"]
            }
    
    def calculate_pips(self, symbol, price_change):
        digits = self.symbol_data[symbol]["digits"]
        
        if digits == 3 or digits == 5:
            pip_value = 0.1 ** (digits - 1)
        else:
            pip_value = 0.1 ** (digits - 2)
        
        return abs(price_change) / pip_value
    
    async def update_price(self, symbol, price):
        if symbol not in config.watch_symbols:
            return
        
        digits = self.symbol_data[symbol]["digits"]
        jp_name = self.symbol_data[symbol]["jp_name"]
        
        if self.symbol_data[symbol]["base_price"] is None:
            self.symbol_data[symbol]["base_price"] = price
            self.symbol_data[symbol]["last_price"] = price
            logger.info(f"✓ {symbol}({jp_name}) 初期価格: {price:.{digits}f}")
            return
        
        base_price = self.symbol_data[symbol]["base_price"]
        price_change = price - base_price
        pips_change = self.calculate_pips(symbol, price_change)
        
        level_msg = None
        emotion = "neutral"
        
        if pips_change >= config.large_threshold:
            level_msg = config.msg_large
            emotion = "surprised"
        elif pips_change >= config.medium_threshold:
            level_msg = config.msg_medium
            emotion = "happy" if price_change > 0 else "sad"
        elif pips_change >= config.small_threshold:
            level_msg = config.msg_small
            emotion = "happy" if price_change > 0 else "sad"
        
        if level_msg:
            direction = "上昇" if price_change > 0 else "下降"
            
            # 感情タグを追加
            if pips_change >= config.large_threshold:
                emotion_tag = "[surprised]"
            elif price_change > 0:
                emotion_tag = "[happy]"
            else:
                emotion_tag = "[sad]"
            
            # メッセージを作成（感情タグ付き）
            message_text = f"{emotion_tag} {jp_name} が {pips_change:.1f} pips {direction} した。{level_msg}"
            
            logger.info(f"★ 通知: {symbol}({jp_name}) {pips_change:.1f} pips {direction}")
            
            # 送信
            await broker.broadcast(message_text)
            
            self.symbol_data[symbol]["base_price"] = price
            logger.info(f"  → 基準価格リセット: {price:.{digits}f}")
        
        self.symbol_data[symbol]["last_price"] = price
        
        await broker.broadcast_dashboard({
            "type": "price_update",
            "symbol": symbol,
            "jp_name": jp_name,
            "price": price,
            "base_price": base_price,
            "pips_change": pips_change
        })
    
    def get_status(self):
        status = []
        for symbol, data in self.symbol_data.items():
            status.append({
                "symbol": symbol,
                "jp_name": data["jp_name"],
                "price": data["last_price"],
                "base_price": data["base_price"]
            })
        return status

monitor = PriceMonitor()

# ==================== MT5クライアント ====================
class MT5Client:
    def __init__(self):
        self.running = False
        self.connected = False
    
    def connect(self):
        logger.info("=" * 60)
        logger.info("MetaTrader 5 接続開始")
        logger.info("=" * 60)
        
        if not mt5.initialize():
            logger.error("✗ MT5初期化失敗")
            logger.error(f"  エラー: {mt5.last_error()}")
            return False
        
        account_info = mt5.account_info()
        if account_info is None:
            logger.error("✗ 口座情報取得失敗")
            return False
        
        logger.info("✓ MT5接続成功")
        logger.info(f"  サーバー: {account_info.server}")
        
        logger.info("\n監視シンボルの確認:")
        self.available_symbols = []
        for symbol in config.watch_symbols.keys():
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                logger.warning(f"  ⚠ {symbol}: 利用不可")
            else:
                jp_name = config.watch_symbols[symbol]["jp_name"]
                logger.info(f"  ✓ {symbol}({jp_name}): 利用可能")
                self.available_symbols.append(symbol)
                
                if not symbol_info.visible:
                    mt5.symbol_select(symbol, True)
        
        if not self.available_symbols:
            logger.error("✗ 利用可能なシンボルがありません")
            mt5.shutdown()
            return False
        
        self.connected = True
        return True
    
    async def start_monitoring(self):
        if not self.connected:
            logger.error("✗ MT5未接続")
            return
        
        logger.info("=" * 60)
        logger.info("価格監視開始")
        logger.info("=" * 60)
        
        jp_names = [config.watch_symbols[s]["jp_name"] for s in self.available_symbols]
        message = f"[happy] MT5 FX価格監視開始。{', '.join(jp_names)}を監視します"
        await broker.broadcast(message)
        
        self.running = True
        
        while self.running:
            try:
                for symbol in self.available_symbols:
                    tick = mt5.symbol_info_tick(symbol)
                    
                    if tick is None:
                        continue
                    
                    price = tick.bid
                    await monitor.update_price(symbol, price)
                
                await asyncio.sleep(config.update_interval)
                
            except Exception as e:
                logger.error(f"✗ 価格取得エラー: {e}")
                await asyncio.sleep(5.0)
    
    def disconnect(self):
        if self.connected:
            mt5.shutdown()
            logger.info("✓ MT5切断")

# ==================== WebSocketサーバー ====================
async def websocket_handler(websocket):
    """AItuber on Air用WebSocket接続処理"""
    broker.add_client(websocket)
    
    try:
        # AITuber on Air形式で送信
        welcome_msg = {
            "type": "chat",
            "text": "[happy] FX価格監視システムに接続しました"
        }
        await websocket.send(json.dumps(welcome_msg, ensure_ascii=False))
        
        async for message in websocket:
            pass
            
    except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError):
        pass
    finally:
        broker.remove_client(websocket)

async def websocket_router(websocket):
    """パスに応じて適切なハンドラーにルーティング"""
    try:
        # websocketsバージョンに応じてパスを取得
        if hasattr(websocket, 'request'):
            path = websocket.request.path
        elif hasattr(websocket, 'path'):
            path = websocket.path
        else:
            path = "/"
        
        logger.info(f"🔌 接続要求: {path}")
        
        if path in ["/", "/direct-speech", "/direct"]:
            # AItuber on Air用
            await websocket_handler(websocket)
        else:
            logger.warning(f"⚠️ 未対応のパス: {path}")
            await websocket.close()
    except Exception as e:
        logger.error(f"✗ ルーターエラー: {e}")
        import traceback
        traceback.print_exc()

async def dashboard_websocket_handler(websocket):
    """ダッシュボード用WebSocket接続処理"""
    broker.add_client(websocket, is_dashboard=True)
    
    try:
        initial_state = {
            "type": "init",
            "config": {
                "update_interval": config.update_interval,
                "small_threshold": config.small_threshold,
                "medium_threshold": config.medium_threshold,
                "large_threshold": config.large_threshold,
                "msg_small": config.msg_small,
                "msg_medium": config.msg_medium,
                "msg_large": config.msg_large,
                "watch_symbols": config.watch_symbols
            },
            "status": monitor.get_status()
        }
        await websocket.send(json.dumps(initial_state, ensure_ascii=False))
        
        async for message in websocket:
            try:
                data = json.loads(message)
                if data.get("type") == "update_config":
                    await handle_config_update(data.get("config", {}))
                    await websocket.send(json.dumps({"type": "config_updated", "success": True}, ensure_ascii=False))
            except json.JSONDecodeError:
                logger.error("✗ 不正なJSON受信")
            
    except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError):
        pass
    finally:
        broker.remove_client(websocket, is_dashboard=True)

async def handle_config_update(new_config: Dict):
    if "update_interval" in new_config:
        config.update_interval = float(new_config["update_interval"])
        logger.info(f"✓ 更新間隔変更: {config.update_interval}秒")
    
    if "small_threshold" in new_config:
        config.small_threshold = float(new_config["small_threshold"])
        logger.info(f"✓ 小変動閾値変更: {config.small_threshold} pips")
    
    if "medium_threshold" in new_config:
        config.medium_threshold = float(new_config["medium_threshold"])
        logger.info(f"✓ 中変動閾値変更: {config.medium_threshold} pips")
    
    if "large_threshold" in new_config:
        config.large_threshold = float(new_config["large_threshold"])
        logger.info(f"✓ 大変動閾値変更: {config.large_threshold} pips")
    
    if "msg_small" in new_config:
        config.msg_small = new_config["msg_small"]
    
    if "msg_medium" in new_config:
        config.msg_medium = new_config["msg_medium"]
    
    if "msg_large" in new_config:
        config.msg_large = new_config["msg_large"]
    
    save_config_to_file()

def save_config_to_file():
    config_data = {
        "update_interval": config.update_interval,
        "small_threshold": config.small_threshold,
        "medium_threshold": config.medium_threshold,
        "large_threshold": config.large_threshold,
        "msg_small": config.msg_small,
        "msg_medium": config.msg_medium,
        "msg_large": config.msg_large,
        "watch_symbols": config.watch_symbols
    }
    try:
        with open("mt5_config.json", "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        logger.info("✓ 設定をファイルに保存")
    except Exception as e:
        logger.error(f"✗ 設定保存エラー: {e}")

def load_config_from_file():
    try:
        with open("mt5_config.json", "r", encoding="utf-8") as f:
            config_data = json.load(f)
        
        config.update_interval = config_data.get("update_interval", config.update_interval)
        config.small_threshold = config_data.get("small_threshold", config.small_threshold)
        config.medium_threshold = config_data.get("medium_threshold", config.medium_threshold)
        config.large_threshold = config_data.get("large_threshold", config.large_threshold)
        config.msg_small = config_data.get("msg_small", config.msg_small)
        config.msg_medium = config_data.get("msg_medium", config.msg_medium)
        config.msg_large = config_data.get("msg_large", config.msg_large)
        
        if "watch_symbols" in config_data:
            for symbol, data in config_data["watch_symbols"].items():
                if symbol in config.watch_symbols:
                    config.watch_symbols[symbol].update(data)
        
        logger.info("✓ 保存された設定を読み込み")
        return True
    except FileNotFoundError:
        logger.info("  デフォルト設定を使用")
        return False
    except Exception as e:
        logger.error(f"✗ 設定読み込みエラー: {e}")
        return False

async def start_websocket_server():
    """WebSocketサーバー起動"""
    logger.info("=" * 60)
    logger.info("WebSocketサーバー起動")
    logger.info(f"  - AItuber on Air用:")
    logger.info(f"    ws://localhost:{config.ws_port}/direct")
    logger.info(f"    ws://localhost:{config.ws_port}/direct-speech")
    logger.info(f"    ws://localhost:{config.ws_port}/")
    logger.info(f"  - ダッシュボード用:")
    logger.info(f"    ws://localhost:{config.ws_port + 1}")
    logger.info("=" * 60)
    
    # ルーター付きのWebSocketサーバー
    async with websockets.serve(websocket_router, config.ws_host, config.ws_port), \
               websockets.serve(dashboard_websocket_handler, config.ws_host, config.ws_port + 1):
        await asyncio.Future()

# ==================== HTTPサーバー ====================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>💖 コロンの通貨監視❤</title>
    <link href="https://fonts.googleapis.com/css2?family=M+PLUS+Rounded+1c:wght@400;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'M PLUS Rounded 1c', sans-serif;
            background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 50%, #ff9a9e 100%);
            background-attachment: fixed;
            color: #5a3f37;
            padding: 20px;
            min-height: 100vh;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 {
            color: #ff6b9d;
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.8em;
            text-shadow: 3px 3px 0px #ffc4d6;
        }
        .card {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 25px;
            padding: 25px;
            margin-bottom: 25px;
            box-shadow: 0 8px 32px rgba(255, 107, 157, 0.3);
        }
        .status {
            display: inline-block;
            padding: 8px 20px;
            border-radius: 50px;
            font-weight: bold;
        }
        .status.connected {
            background: #a8e6cf;
            color: #2d5a3a;
        }
        .status.disconnected {
            background: #ffabab;
            color: #7d3838;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>✨ コロンの通貨監視❤ ✨</h1>
        <div class="card">
            <h2>接続状態</h2>
            <p>WebSocket: <span class="status disconnected" id="status">切断</span></p>
        </div>
    </div>
    <script>
        const ws = new WebSocket('ws://localhost:8001');
        const statusEl = document.getElementById('status');
        
        ws.onopen = () => {
            statusEl.textContent = '接続中';
            statusEl.className = 'status connected';
        };
        
        ws.onclose = () => {
            statusEl.textContent = '切断';
            statusEl.className = 'status disconnected';
        };
    </script>
</body>
</html>
"""

async def http_handler(request):
    return web.Response(text=DASHBOARD_HTML, content_type='text/html', charset='utf-8')

async def start_http_server():
    app = web.Application()
    app.router.add_get('/', http_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.ws_host, config.http_port)
    await site.start()
    
    logger.info(f"ダッシュボード: http://localhost:{config.http_port}")

# ==================== メイン ====================
async def main():
    print("\n" + "=" * 60)
    print("MT5 → AItuber on Air 統合システム")
    print("=" * 60)
    
    load_config_from_file()
    
    print(f"監視通貨: {', '.join([f'{s}({info['jp_name']})' for s, info in config.watch_symbols.items()])}")
    print("=" * 60)
    print()
    
    client = MT5Client()
    
    if not client.connect():
        logger.error("\nMT5接続失敗")
        return
    
    try:
        await asyncio.gather(
            start_http_server(),
            start_websocket_server(),
            client.start_monitoring()
        )
    except KeyboardInterrupt:
        logger.info("\n✓ 停止")
    finally:
        client.disconnect()

if __name__ == '__main__':
    print("\n【重要】実行前に確認:")
    print("  1. MetaTrader 5が起動していること")
    print("  2. 口座にログインしていること")
    input("\n準備ができたらEnterで起動...")
    print()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✓ 停止")