# backend/config.py
import os
from pathlib import Path

# Base directory
BASE_DIR = Path(__file__).parent

class Config:
    # Server
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', 5000))
    DEBUG = os.getenv('DEBUG', 'True').lower() == 'true'
    
    # Trading
    SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT']
    TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1d']
    
    # WebSocket
    BINANCE_WS = 'wss://stream.binance.com:9443/ws'
    BINANCE_REST = 'https://api.binance.com'
    
    # Database
    DATA_DIR = BASE_DIR / 'data'
    DATA_DIR.mkdir(exist_ok=True)
    DB_PATH = DATA_DIR / 'trades.db'
    
    # Logging
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    
    # CORS
    CORS_ORIGINS = ['http://localhost:3000', 'http://localhost:5000']

config = Config()
