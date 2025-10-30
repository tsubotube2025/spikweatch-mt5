#!/usr/bin/env python3
"""
MetaTrader 5 → AItuber Kit 統合システム (改善版)
- セキュリティ強化
- 日本語読み対応
- Webダッシュボード機能付き
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

# ==================== 設定 (Config) ====================
@dataclass
class Config:
    """アプリケーションの設定を管理するクラス"""
    # 監視する通貨ペア（MT5のシンボル名）、小数点以下の桁数、日本語読み
    watch_symbols: Dict[str, Dict] = field(default_factory=lambda: {
        "USDJPY": {"digits": 3, "jp_name": "どるえん"},
        "EURUSD": {"digits": 5, "jp_name": "ユーロドル"},
        "GBPUSD": {"digits": 5, "jp_name": "ポンドル"},
        "EURJPY": {"digits": 3, "jp_name": "ユーロえん"},
        "GBPJPY": {"digits": 3, "jp_name": "ポンドえん"},
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
    # HTTPサーバー設定
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
            logger.info(f"✓ クライアント接続 (合計: {len(self.clients)})")
    
    def remove_client(self, ws: websockets.WebSocketServerProtocol, is_dashboard=False):
        if is_dashboard:
            self.dashboard_clients.discard(ws)
            logger.info(f"✗ ダッシュボード切断 (残り: {len(self.dashboard_clients)})")
        else:
            self.clients.discard(ws)
            logger.info(f"✗ クライアント切断 (残り: {len(self.clients)})")
    
    async def broadcast(self, message_data: Dict):
        if not self.clients:
            return
        
        message_json = json.dumps(message_data, ensure_ascii=False)
        dead = set()
        
        for client in self.clients:
            try:
                await client.send(message_json)
                logger.info(f"✓ 送信: {message_data.get('text', '')[:50]}")
            except websockets.exceptions.ConnectionClosed:
                dead.add(client)
        
        for client in dead:
            self.remove_client(client)
    
    async def broadcast_dashboard(self, data: Dict):
        """ダッシュボード用の状態更新送信"""
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
            
            message = {
                "text": f"{jp_name} が {pips_change:.1f} pips {direction} しました\n{level_msg}",
                "role": "assistant",
                "emotion": emotion,
                "type": "message"
            }
            
            logger.info(f"★ 通知: {symbol}({jp_name}) {pips_change:.1f} pips {direction}")
            await broker.broadcast(message)
            
            self.symbol_data[symbol]["base_price"] = price
            logger.info(f"  → 基準価格リセット: {price:.{digits}f}")
        
        self.symbol_data[symbol]["last_price"] = price
        
        # ダッシュボードに価格更新を送信
        await broker.broadcast_dashboard({
            "type": "price_update",
            "symbol": symbol,
            "jp_name": jp_name,
            "price": price,
            "base_price": base_price,
            "pips_change": pips_change
        })
    
    def get_status(self):
        """現在の監視状態を取得"""
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
        """MT5に接続"""
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
        
        # セキュリティ強化：口座情報は非表示
        logger.info("✓ MT5接続成功")
        logger.info(f"  サーバー: {account_info.server}")
        logger.info("  口座情報: [セキュリティのため非表示]")
        
        logger.info("\n監視シンボルの確認:")
        self.available_symbols = []
        for symbol in config.watch_symbols.keys():
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                logger.warning(f"  ⚠ {symbol}: 利用不可（スキップ）")
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
        """価格監視開始"""
        if not self.connected:
            logger.error("✗ MT5未接続")
            return
        
        logger.info("=" * 60)
        logger.info("価格監視開始")
        logger.info("=" * 60)
        
        jp_names = [config.watch_symbols[s]["jp_name"] for s in self.available_symbols]
        await broker.broadcast({
            "text": f"MT5 FX価格監視開始: {', '.join(jp_names)}",
            "role": "system",
            "emotion": "happy",
            "type": "message"
        })
        
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
        """MT5切断"""
        if self.connected:
            mt5.shutdown()
            logger.info("✓ MT5切断")

# ==================== WebSocketサーバー ====================
async def websocket_handler(websocket: websockets.WebSocketServerProtocol):
    """AItuber Kit用WebSocketクライアント接続処理"""
    broker.add_client(websocket)
    
    try:
        welcome = {
            "text": "MT5 FX価格監視システムに接続しました",
            "role": "system",
            "emotion": "happy",
            "type": "message"
        }
        await websocket.send(json.dumps(welcome, ensure_ascii=False))
        
        async for message in websocket:
            pass
            
    except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError):
        pass
    finally:
        broker.remove_client(websocket)

async def dashboard_websocket_handler(websocket: websockets.WebSocketServerProtocol):
    """ダッシュボード用WebSocket接続処理"""
    broker.add_client(websocket, is_dashboard=True)
    
    try:
        # 初期状態を送信
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
            # 設定変更メッセージを受信
            try:
                data = json.loads(message)
                if data.get("type") == "update_config":
                    await handle_config_update(data.get("config", {}))
                    # 更新完了を通知
                    await websocket.send(json.dumps({"type": "config_updated", "success": True}, ensure_ascii=False))
            except json.JSONDecodeError:
                logger.error("✗ 不正なJSON受信")
            
    except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError):
        pass
    finally:
        broker.remove_client(websocket, is_dashboard=True)

async def handle_config_update(new_config: Dict):
    """設定を更新"""
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
        logger.info(f"✓ 小変動メッセージ変更")
    
    if "msg_medium" in new_config:
        config.msg_medium = new_config["msg_medium"]
        logger.info(f"✓ 中変動メッセージ変更")
    
    if "msg_large" in new_config:
        config.msg_large = new_config["msg_large"]
        logger.info(f"✓ 大変動メッセージ変更")
    
    # 設定をファイルに保存
    save_config_to_file()

def save_config_to_file():
    """設定をJSONファイルに保存"""
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
        logger.info("✓ 設定をファイルに保存しました")
    except Exception as e:
        logger.error(f"✗ 設定保存エラー: {e}")

def load_config_from_file():
    """設定をJSONファイルから読み込み"""
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
        
        # watch_symbolsは既存のものとマージ
        if "watch_symbols" in config_data:
            for symbol, data in config_data["watch_symbols"].items():
                if symbol in config.watch_symbols:
                    config.watch_symbols[symbol].update(data)
        
        logger.info("✓ 保存された設定を読み込みました")
        return True
    except FileNotFoundError:
        logger.info("  設定ファイルが見つかりません。デフォルト設定を使用します。")
        return False
    except Exception as e:
        logger.error(f"✗ 設定読み込みエラー: {e}")
        return False

async def start_websocket_server():
    """WebSocketサーバー起動"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            external_ip = s.getsockname()[0]
    except Exception:
        external_ip = "N/A"

    logger.info("=" * 60)
    logger.info("WebSocketサーバー起動")
    logger.info(f"  - AItuber Kit用: ws://localhost:{config.ws_port}")
    logger.info(f"  - ダッシュボード用: ws://localhost:{config.ws_port + 1}")
    if config.ws_host == "0.0.0.0":
        logger.info(f"  - ネットワーク: ws://{external_ip}:{config.ws_port}")
    logger.info("=" * 60)
    
    # 2つのWebSocketサーバーを起動
    async with websockets.serve(websocket_handler, config.ws_host, config.ws_port), \
               websockets.serve(dashboard_websocket_handler, config.ws_host, config.ws_port + 1):
        await asyncio.Future()

# ==================== HTTPサーバー（ダッシュボード） ====================
async def http_handler(request):
    """ダッシュボードHTMLを返す"""
    return web.Response(text=DASHBOARD_HTML, content_type='text/html', charset='utf-8')

async def start_http_server():
    """HTTPサーバー起動"""
    app = web.Application()
    app.router.add_get('/', http_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.ws_host, config.http_port)
    await site.start()
    
    logger.info(f"ダッシュボード: http://localhost:{config.http_port}")

# ==================== ダッシュボードHTML ====================
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
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        h1 {
            color: #ff6b9d;
            text-align: center;
            margin-bottom: 30px;
            font-size: 2.8em;
            text-shadow: 3px 3px 0px #ffc4d6, 6px 6px 10px rgba(255,107,157,0.3);
            animation: bounce 2s infinite;
        }
        @keyframes bounce {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-10px); }
        }
        .sparkle {
            display: inline-block;
            animation: sparkle 1.5s infinite;
        }
        @keyframes sparkle {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.5; transform: scale(1.2); }
        }
        .card {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 25px;
            padding: 25px;
            margin-bottom: 25px;
            box-shadow: 0 8px 32px rgba(255, 107, 157, 0.3);
            border: 3px solid #ffc4d6;
            position: relative;
            overflow: hidden;
        }
        .card::before {
            content: '';
            position: absolute;
            top: -50%;
            left: -50%;
            width: 200%;
            height: 200%;
            background: linear-gradient(45deg, transparent, rgba(255, 255, 255, 0.3), transparent);
            transform: rotate(45deg);
            animation: shine 3s infinite;
        }
        @keyframes shine {
            0% { transform: translateX(-100%) translateY(-100%) rotate(45deg); }
            100% { transform: translateX(100%) translateY(100%) rotate(45deg); }
        }
        .card h2 {
            color: #ff6b9d;
            margin-bottom: 20px;
            padding-bottom: 15px;
            font-size: 1.8em;
            position: relative;
            z-index: 1;
        }
        .card h2::after {
            content: '♡';
            position: absolute;
            right: 0;
            color: #ffb3d9;
            animation: heartbeat 1.5s infinite;
        }
        @keyframes heartbeat {
            0%, 100% { transform: scale(1); }
            25% { transform: scale(1.2); }
            50% { transform: scale(1); }
        }
        .price-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
            position: relative;
            z-index: 1;
        }
        .price-item {
            background: linear-gradient(135deg, #fff5f7 0%, #ffe0eb 100%);
            padding: 20px;
            border-radius: 20px;
            border: 3px solid #ffb3d9;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        .price-item::before {
            content: '✨';
            position: absolute;
            top: 10px;
            right: 10px;
            font-size: 1.5em;
            opacity: 0.5;
        }
        .price-item:hover {
            transform: translateY(-5px) scale(1.02);
            box-shadow: 0 10px 25px rgba(255, 107, 157, 0.4);
        }
        .price-item.positive { 
            border-color: #a8e6cf;
            background: linear-gradient(135deg, #f0fff4 0%, #c6f6d5 100%);
        }
        .price-item.positive::before { content: '💚'; }
        .price-item.negative { 
            border-color: #ffabab;
            background: linear-gradient(135deg, #fff5f5 0%, #fed7d7 100%);
        }
        .price-item.negative::before { content: '💔'; }
        .price-item h3 { 
            font-size: 1.3em; 
            margin-bottom: 8px;
            color: #ff6b9d;
        }
        .price-item .jp-name { 
            color: #ff9ec9; 
            font-size: 1em;
            font-weight: bold;
            margin-bottom: 10px;
        }
        .price-item .price { 
            font-size: 1.8em; 
            font-weight: bold; 
            color: #5a3f37;
            text-shadow: 1px 1px 2px rgba(255,255,255,0.8);
        }
        .price-item .pips { 
            font-size: 1em; 
            color: #8b7355;
            margin-top: 5px;
            font-weight: bold;
        }
        .form-group {
            margin-bottom: 20px;
            position: relative;
            z-index: 1;
        }
        .form-group label {
            display: block;
            margin-bottom: 8px;
            font-weight: bold;
            color: #ff6b9d;
            font-size: 1.1em;
        }
        .form-group label::before {
            content: '🌸 ';
        }
        .form-group input, .form-group textarea {
            width: 100%;
            padding: 12px 15px;
            border: 3px solid #ffc4d6;
            border-radius: 15px;
            font-size: 1em;
            font-family: 'M PLUS Rounded 1c', sans-serif;
            background: rgba(255, 255, 255, 0.9);
            transition: all 0.3s ease;
        }
        .form-group input:focus, .form-group textarea:focus {
            outline: none;
            border-color: #ff6b9d;
            box-shadow: 0 0 15px rgba(255, 107, 157, 0.3);
            background: white;
        }
        .btn {
            background: linear-gradient(135deg, #ff6b9d 0%, #ffa8c5 100%);
            color: white;
            padding: 15px 40px;
            border: none;
            border-radius: 50px;
            font-size: 1.2em;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 5px 15px rgba(255, 107, 157, 0.4);
            font-family: 'M PLUS Rounded 1c', sans-serif;
            position: relative;
            z-index: 1;
        }
        .btn::before {
            content: '💝 ';
        }
        .btn::after {
            content: ' 💝';
        }
        .btn:hover {
            background: linear-gradient(135deg, #ff8cb3 0%, #ffc4d6 100%);
            transform: translateY(-3px);
            box-shadow: 0 8px 25px rgba(255, 107, 157, 0.6);
        }
        .btn:active {
            transform: translateY(0);
        }
        .status {
            display: inline-block;
            padding: 8px 20px;
            border-radius: 50px;
            font-size: 1em;
            font-weight: bold;
            border: 3px solid;
            position: relative;
            z-index: 1;
        }
        .status::before {
            content: '●';
            margin-right: 8px;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .status.connected {
            background: linear-gradient(135deg, #a8e6cf 0%, #c6f6d5 100%);
            color: #2d5a3a;
            border-color: #a8e6cf;
        }
        .status.disconnected {
            background: linear-gradient(135deg, #ffabab 0%, #ffc9c9 100%);
            color: #7d3838;
            border-color: #ffabab;
        }
        .grid-2col {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        @media (max-width: 768px) {
            .grid-2col { grid-template-columns: 1fr; }
            h1 { font-size: 2em; }
        }
        .cute-decoration {
            text-align: center;
            font-size: 2em;
            margin: 20px 0;
            animation: float 3s ease-in-out infinite;
        }
        @keyframes float {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-10px); }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1><span class="sparkle">✨</span> コロンの通貨監視❤ <span class="sparkle">✨</span></h1>
        
        <div class="card">
            <h2>接続状態</h2>
            <p>WebSocket: <span class="status disconnected" id="status">切断</span></p>
        </div>
        
        <div class="cute-decoration">🌸 💖 🌸</div>
        
        <div class="card">
            <h2>現在の価格</h2>
            <div class="price-grid" id="priceGrid">
                <p>データ読み込み中...</p>
            </div>
        </div>
        
        <div class="cute-decoration">🎀 ✨ 🎀</div>
        
        <div class="card">
            <h2>設定変更</h2>
            <form id="configForm">
                <div class="grid-2col">
                    <div class="form-group">
                        <label>更新間隔（秒）</label>
                        <input type="number" id="updateInterval" step="0.1" min="0.1">
                    </div>
                    <div class="form-group">
                        <label>小変動閾値（pips）</label>
                        <input type="number" id="smallThreshold" step="0.1" min="0">
                    </div>
                    <div class="form-group">
                        <label>中変動閾値（pips）</label>
                        <input type="number" id="mediumThreshold" step="0.1" min="0">
                    </div>
                    <div class="form-group">
                        <label>大変動閾値（pips）</label>
                        <input type="number" id="largeThreshold" step="0.1" min="0">
                    </div>
                </div>
                
                <div class="form-group">
                    <label>小変動メッセージ</label>
                    <textarea id="msgSmall" rows="2"></textarea>
                </div>
                <div class="form-group">
                    <label>中変動メッセージ</label>
                    <textarea id="msgMedium" rows="2"></textarea>
                </div>
                <div class="form-group">
                    <label>大変動メッセージ</label>
                    <textarea id="msgLarge" rows="2"></textarea>
                </div>
                
                <div style="text-align: center;">
                    <button type="submit" class="btn">設定を保存</button>
                </div>
            </form>
        </div>
        
        <div class="cute-decoration">💕 🌟 💕</div>
    </div>
    
    <script>
        const ws = new WebSocket('ws://localhost:8001');
        const statusEl = document.getElementById('status');
        const priceGrid = document.getElementById('priceGrid');
        const form = document.getElementById('configForm');
        
        let currentConfig = {};
        
        ws.onopen = () => {
            statusEl.textContent = '接続中';
            statusEl.className = 'status connected';
        };
        
        ws.onclose = () => {
            statusEl.textContent = '切断';
            statusEl.className = 'status disconnected';
        };
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            if (data.type === 'init') {
                currentConfig = data.config;
                updateForm(data.config);
                updatePriceGrid(data.status);
            } else if (data.type === 'price_update') {
                updatePrice(data);
            } else if (data.type === 'config_updated') {
                // 可愛いアラート
                const alertDiv = document.createElement('div');
                alertDiv.style.cssText = `
                    position: fixed;
                    top: 50%;
                    left: 50%;
                    transform: translate(-50%, -50%);
                    background: linear-gradient(135deg, #ff6b9d 0%, #ffa8c5 100%);
                    color: white;
                    padding: 30px 50px;
                    border-radius: 25px;
                    font-size: 1.5em;
                    font-weight: bold;
                    box-shadow: 0 10px 40px rgba(255, 107, 157, 0.6);
                    z-index: 9999;
                    border: 4px solid white;
                    animation: popIn 0.3s ease;
                `;
                alertDiv.textContent = '💖 設定を保存しました！ ✨';
                document.body.appendChild(alertDiv);
                
                setTimeout(() => {
                    alertDiv.style.animation = 'popOut 0.3s ease';
                    setTimeout(() => alertDiv.remove(), 300);
                }, 2000);
            }
        };
        
        function updateForm(config) {
            document.getElementById('updateInterval').value = config.update_interval;
            document.getElementById('smallThreshold').value = config.small_threshold;
            document.getElementById('mediumThreshold').value = config.medium_threshold;
            document.getElementById('largeThreshold').value = config.large_threshold;
            document.getElementById('msgSmall').value = config.msg_small;
            document.getElementById('msgMedium').value = config.msg_medium;
            document.getElementById('msgLarge').value = config.msg_large;
        }
        
        function updatePriceGrid(status) {
            priceGrid.innerHTML = status.map(item => {
                const pips = item.price && item.base_price 
                    ? ((item.price - item.base_price) / 0.01).toFixed(1)
                    : '0.0';
                const direction = parseFloat(pips) >= 0 ? 'positive' : 'negative';
                const arrow = parseFloat(pips) >= 0 ? '📈' : '📉';
                
                return `
                    <div class="price-item ${direction}" id="price-${item.symbol}">
                        <h3>${item.symbol}</h3>
                        <div class="jp-name">${item.jp_name}</div>
                        <div class="price">${item.price ? item.price.toFixed(3) : '---'}</div>
                        <div class="pips">${arrow} ${pips} pips</div>
                    </div>
                `;
            }).join('');
        }
        
        function updatePrice(data) {
            const el = document.getElementById(`price-${data.symbol}`);
            if (!el) return;
            
            const direction = data.pips_change >= 0 ? 'positive' : 'negative';
            const arrow = data.pips_change >= 0 ? '📈' : '📉';
            el.className = `price-item ${direction}`;
            el.querySelector('.price').textContent = data.price.toFixed(3);
            el.querySelector('.pips').textContent = `${arrow} ${data.pips_change.toFixed(1)} pips`;
            
            // キラキラエフェクト
            el.style.animation = 'none';
            setTimeout(() => {
                el.style.animation = 'pulse 0.5s ease';
            }, 10);
        }
        
        form.onsubmit = (e) => {
            e.preventDefault();
            
            const newConfig = {
                update_interval: parseFloat(document.getElementById('updateInterval').value),
                small_threshold: parseFloat(document.getElementById('smallThreshold').value),
                medium_threshold: parseFloat(document.getElementById('mediumThreshold').value),
                large_threshold: parseFloat(document.getElementById('largeThreshold').value),
                msg_small: document.getElementById('msgSmall').value,
                msg_medium: document.getElementById('msgMedium').value,
                msg_large: document.getElementById('msgLarge').value
            };
            
            ws.send(JSON.stringify({
                type: 'update_config',
                config: newConfig
            }));
        };
        
        // アニメーション定義
        const style = document.createElement('style');
        style.textContent = `
            @keyframes popIn {
                from { transform: translate(-50%, -50%) scale(0); }
                to { transform: translate(-50%, -50%) scale(1); }
            }
            @keyframes popOut {
                from { transform: translate(-50%, -50%) scale(1); opacity: 1; }
                to { transform: translate(-50%, -50%) scale(0); opacity: 0; }
            }
        `;
        document.head.appendChild(style);
    </script>
</body>
</html>
"""

# ==================== 準備 (Setup) ====================
def initial_setup():
    """起動前の依存関係チェック"""
    print("\n依存関係チェック:")
    try:
        import MetaTrader5
        print("  ✓ MetaTrader5 OK")
    except ImportError:
        print("  ✗ 'pip install MetaTrader5' を実行してください。")
        return False
    
    try:
        import websockets
        print("  ✓ websockets OK")
    except ImportError:
        print("  ✗ 'pip install websockets' を実行してください。")
        return False
    
    try:
        import aiohttp
        print("  ✓ aiohttp OK")
    except ImportError:
        print("  ✗ 'pip install aiohttp' を実行してください。")
        return False
    
    return True

# ==================== メイン ====================
async def main():
    print("\n" + "=" * 60)
    print("MT5 → AItuber Kit 統合システム (改善版)")
    print("=" * 60)
    
    # 保存された設定を読み込み
    load_config_from_file()
    
    print(f"監視通貨: {', '.join([f'{s}({info['jp_name']})' for s, info in config.watch_symbols.items()])}")
    print(f"更新間隔: {config.update_interval}秒")
    print(f"小変動: {config.small_threshold} pips")
    print(f"中変動: {config.medium_threshold} pips")
    print(f"大変動: {config.large_threshold} pips")
    print("=" * 60)
    print()
    
    client = MT5Client()
    
    if not client.connect():
        logger.error("\nMT5接続失敗。プログラムを終了します。")
        return
    
    try:
        # HTTPサーバー、WebSocketサーバー、価格監視を並行実行
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
    if initial_setup():
        print("\n【重要】実行前に確認:")
        print("  1. MetaTrader 5アプリケーションが起動していること")
        print("  2. 口座にログインしていること")
        print("  ※ aiohttp未インストールの場合: pip install aiohttp")
        input("\n準備ができたらEnterで起動...")
        print()
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n✓ 停止")