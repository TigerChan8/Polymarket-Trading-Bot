"""
Polymarket Arbitrage Bot Configuration File

Telegram: @qntrade
"""
import os
from dotenv import load_dotenv

load_dotenv()

# API endpoints
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"

# WebSocket endpoints (for real-time data)
WS_CLOB_URL = "wss://clob-ws.polymarket.com"

# Bot settings
MIN_PROFIT_MARGIN = float(os.getenv("MIN_PROFIT_MARGIN", "0.05"))  # Minimum 5% net profit margin (after 2% taker fee)
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "1.0"))  # Scan interval (seconds)
MAX_MARKETS_TO_MONITOR = int(os.getenv("MAX_MARKETS_TO_MONITOR", "100"))  # Number of markets to monitor simultaneously

# Web3 settings (for actual trading)
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")  # Wallet private key (loaded from environment variable)
POLYGON_RPC_URL = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")

# Data logger settings
ENABLE_DATA_LOGGING = os.getenv("ENABLE_DATA_LOGGING", "true").lower() == "true"
LOG_DIR = os.getenv("LOG_DIR", "./logs")
CSV_LOG_FILE = os.path.join(LOG_DIR, "price_data.csv")
DB_LOG_FILE = os.path.join(LOG_DIR, "price_data.db")

# Trading settings
MIN_TRADE_SIZE = float(os.getenv("MIN_TRADE_SIZE", "0.01"))  # Minimum trade amount
MAX_SLIPPAGE = float(os.getenv("MAX_SLIPPAGE", "0.01"))  # Maximum slippage (1%)

# Strategy extension settings (for indicator/rule experimentation in data mode)
ENABLE_STRATEGY_PIPELINE = os.getenv("ENABLE_STRATEGY_PIPELINE", "false").lower() == "true"

# Weather Intelligence settings
WEATHER_MIN_NOTIONAL = float(os.getenv("WEATHER_MIN_NOTIONAL", "5000"))           # Min USD notional for whale alerts
WEATHER_CONSENSUS_N = int(os.getenv("WEATHER_CONSENSUS_N", "3"))                   # Min traders for consensus burst
WEATHER_CONSENSUS_WINDOW_MINUTES = int(os.getenv("WEATHER_CONSENSUS_WINDOW_MINUTES", "60"))  # Burst detection window
WEATHER_RANK_VELOCITY_MIN_JUMP = int(os.getenv("WEATHER_RANK_VELOCITY_MIN_JUMP", "10"))       # Min rank jump for velocity alert
WEATHER_TOP_TRADERS_LIMIT = int(os.getenv("WEATHER_TOP_TRADERS_LIMIT", "50"))      # Leaderboard traders to watch
