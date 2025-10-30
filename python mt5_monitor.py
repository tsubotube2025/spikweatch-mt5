#!/usr/bin/env python3
"""
MetaTrader 5 → AItuber Kit 統合システム
簡単・確実に動作します
"""

import asyncio
import json
import logging
import websockets

from dataclasses import dataclass, field
from typing import Dict, Set, Optional
import MetaTrader5 as mt5
from datetime import datetime
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

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

# ==================== メッセージブローカー ====================
class MessageBroker:
    def __init__(self):
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
    
    def add_client(self, ws: websockets.WebSocketServerProtocol):
        self.clients.add(ws)
        logger.info(f"✓ クライアント接続 (合計: {len(self.clients)})")
    
    def remove_client(self, ws: websockets.WebSocketServerProtocol):
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

broker = MessageBroker()

# ==================== 価格監視 ====================
class PriceMonitor:
    def __init__(self):
        self.symbol_data = {}
        for symbol, info in config.watch_symbols.items():
            self.symbol_data[symbol] = {
                "base_price": None, # type: Optional[float]
                "last_price": None,
                "digits": info["digits"]
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
        
        if self.symbol_data[symbol]["base_price"] is None:
            self.symbol_data[symbol]["base_price"] = price
            self.symbol_data[symbol]["last_price"] = price
            logger.info(f"✓ {symbol} 初期価格: {price:.{digits}f}")
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
                "text": f"{symbol} が {pips_change:.1f} pips {direction} しました\n{level_msg}",
                "role": "assistant",
                "emotion": emotion,
                "type": "message"
            }
            
            logger.info(f"★ 通知: {symbol} {pips_change:.1f} pips {direction}")
            await broker.broadcast(message)
            
            self.symbol_data[symbol]["base_price"] = price
            logger.info(f"  → 基準価格リセット: {price:.{digits}f}")
        
        self.symbol_data[symbol]["last_price"] = price

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
        
        # MT5を初期化
        if not mt5.initialize():
            logger.error("✗ MT5初期化失敗")
            logger.error(f"  エラー: {mt5.last_error()}")
            logger.error("\n確認事項:")
            logger.error("  1. MetaTrader 5がインストールされていますか？")
            logger.error("  2. MT5アプリケーションが起動していますか？")
            logger.error("  3. デモ口座にログインしていますか？")
            return False
        
        # MT5情報を取得
        account_info = mt5.account_info()
        if account_info is None:
            logger.error("✗ 口座情報取得失敗")
            return False
        
        logger.info("✓ MT5接続成功")
        logger.info(f"  口座番号: {account_info.login}")
        logger.info(f"  口座名: {account_info.name}")
        logger.info(f"  残高: {account_info.balance} {account_info.currency}")
        logger.info(f"  サーバー: {account_info.server}")
        
        # 監視シンボルの確認
        logger.info("\n監視シンボルの確認:")
        self.available_symbols = []
        for symbol in config.watch_symbols.keys():
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                logger.warning(f"  ⚠ {symbol}: 利用不可（スキップ）")
            else:
                logger.info(f"  ✓ {symbol}: 利用可能")
                self.available_symbols.append(symbol)
                
                # シンボルを有効化
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
        
        await broker.broadcast({
            "text": f"MT5 FX価格監視開始: {', '.join(self.available_symbols)}",
            "role": "system",
            "emotion": "happy",
            "type": "message"
        })
        
        self.running = True
        
        while self.running:
            try:
                for symbol in self.available_symbols:
                    # 現在のティック価格を取得
                    tick = mt5.symbol_info_tick(symbol)
                    
                    if tick is None:
                        continue
                    
                    # Bid価格を使用
                    price = tick.bid
                    digits = config.watch_symbols[symbol]["digits"]
                    
                    logger.debug(f"💹 {symbol} = {price:.{digits}f}")
                    
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
    """WebSocketクライアント接続処理"""
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

async def start_websocket_server():
    """WebSocketサーバー起動"""
    # 外部からアクセス可能なIPアドレスを取得試行
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            external_ip = s.getsockname()[0]
    except Exception:
        external_ip = "N/A"

    logger.info("=" * 60)
    logger.info("WebSocketサーバー起動")
    logger.info(f"  - ローカル: ws://localhost:{config.ws_port}")
    if config.ws_host == "0.0.0.0":
        logger.info(f"  - ネットワーク: ws://{external_ip}:{config.ws_port}")
    logger.info("=" * 60)
    
    async with websockets.serve(websocket_handler, config.ws_host, config.ws_port):
        await asyncio.Future()

# ==================== 準備 (Setup) ====================
def initial_setup():
    """起動前の依存関係チェックとユーザーへの案内"""
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
    return True

# ==================== メイン ====================
async def main():
    print("\n" + "=" * 60)
    print("MT5 → AItuber Kit 統合システム")
    print("=" * 60)
    print(f"監視通貨: {', '.join(config.watch_symbols.keys())}")
    print(f"更新間隔: {config.update_interval}秒")
    print(f"小変動: {config.small_threshold} pips")
    print(f"中変動: {config.medium_threshold} pips")
    print(f"大変動: {config.large_threshold} pips")
    print("=" * 60)
    print()
    
    # MT5クライアント作成
    client = MT5Client()
    
    # MT5に接続
    if not client.connect():
        logger.error("\nMT5接続失敗。プログラムを終了します。")
        return
    
    try:
        # WebSocketサーバーと価格監視を並行実行
        await asyncio.gather(
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
        input("\n準備ができたらEnterで起動...")
        print()
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            print("\n✓ 停止")