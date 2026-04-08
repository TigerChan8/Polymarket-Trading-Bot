"""
Polymarket Arbitrage Bot Main Code
Detects and executes arbitrage opportunities when Yes/No ticket price sum is less than 1.0

Telegram: @qntrade
"""
import time
import requests
import asyncio
import json
from typing import Optional, List, Dict, Any
from datetime import datetime
from web3 import Web3
from eth_account import Account

from config import (
    GAMMA_API_URL,
    CLOB_API_URL,
    MIN_PROFIT_MARGIN,
    SCAN_INTERVAL,
    MAX_MARKETS_TO_MONITOR,
    PRIVATE_KEY,
    POLYGON_RPC_URL,
    ENABLE_DATA_LOGGING,
    CSV_LOG_FILE,
    DB_LOG_FILE,
    MIN_TRADE_SIZE,
    MAX_SLIPPAGE,
    ENABLE_STRATEGY_PIPELINE
)
from data_logger import DataLogger
from strategy_engine import StrategyPipeline


class PolyArbitrageBot:
    """Polymarket Arbitrage Bot"""
    
    def __init__(self, market_ids: Optional[List[str]] = None):
        """
        Args:
            market_ids: List of market IDs to monitor. If None, automatically discovers active markets
        """
        self.market_ids = market_ids or []
        self.min_profit_margin = MIN_PROFIT_MARGIN
        self.scan_interval = SCAN_INTERVAL
        
        # Initialize data logger
        self.logger = None
        if ENABLE_DATA_LOGGING:
            self.logger = DataLogger(CSV_LOG_FILE, DB_LOG_FILE)

        # Optional strategy pipeline for indicator/rule experimentation
        self.strategy_pipeline = StrategyPipeline() if ENABLE_STRATEGY_PIPELINE else None
        
        # Initialize Web3 (for actual trading)
        self.web3 = None
        self.account = None
        if PRIVATE_KEY:
            try:
                self.web3 = Web3(Web3.HTTPProvider(POLYGON_RPC_URL))
                self.account = Account.from_key(PRIVATE_KEY)
                print(f"[✓] Wallet connected: {self.account.address}")
            except Exception as e:
                print(f"[!] Web3 initialization failed: {e}")
                print("[!] Running in data logging mode only.")
    
    def get_active_markets(self, limit: int = MAX_MARKETS_TO_MONITOR) -> List[Dict[str, Any]]:
        """Query active market list"""
        try:
            # Query active markets via Gamma API
            params = {
                'active': 'true',
                'closed': 'false',
                'limit': limit
            }
            response = requests.get(f"{GAMMA_API_URL}/markets", params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # API response may be returned directly as a list
            if isinstance(data, dict):
                market_list = data.get('data', [])
            else:
                market_list = data
            
            markets = []
            for market in market_list:
                # Handle default values if active and closed fields are missing
                is_active = market.get('active', True)
                is_closed = market.get('closed', False)
                
                if is_active and not is_closed:
                    markets.append({
                        'id': str(market.get('id', '')),
                        'question': market.get('question', ''),
                        'slug': market.get('slug', '')
                    })
            
            print(f"[✓] Found {len(markets)} active markets")
            return markets[:limit]
        
        except Exception as e:
            print(f"[✗] Failed to query market list: {e}")
            return []
    
    def get_market_orderbook(self, token_id: str) -> Optional[Dict[str, Any]]:
        """Query token orderbook data (CLOB API)"""
        try:
            # Query orderbook via CLOB API
            response = requests.get(
                f"{CLOB_API_URL}/book",
                params={'token_id': token_id},
                timeout=5
            )
            response.raise_for_status()
            return response.json()
        
        except Exception as e:
            print(f"[✗] Failed to query orderbook ({token_id}): {e}")
            return None
    
    def get_market_prices(self, market_id: str) -> Optional[Dict[str, float]]:
        """Query Yes/No ticket prices for a market"""
        try:
            # Query market information via Gamma API
            response = requests.get(
                f"{GAMMA_API_URL}/markets/{market_id}",
                timeout=5
            )
            response.raise_for_status()
            market_data = response.json()
            
            def _load_json_if_needed(value: Any):
                if isinstance(value, str):
                    try:
                        return json.loads(value)
                    except json.JSONDecodeError:
                        return None
                return value

            def _best_ask(book: Optional[Dict[str, Any]]) -> Optional[float]:
                if not book:
                    return None
                asks = book.get('asks', [])
                prices = []
                for ask in asks:
                    try:
                        prices.append(float(ask.get('price')))
                    except (TypeError, ValueError, AttributeError):
                        continue
                return min(prices) if prices else None

            def _best_bid(book: Optional[Dict[str, Any]]) -> Optional[float]:
                if not book:
                    return None
                bids = book.get('bids', [])
                prices = []
                for bid in bids:
                    try:
                        prices.append(float(bid.get('price')))
                    except (TypeError, ValueError, AttributeError):
                        continue
                return max(prices) if prices else None
            
            # Extract Yes/No ticket prices
            yes_price = None
            no_price = None
            yes_ask = None
            no_ask = None
            yes_bid = None
            no_bid = None
            yes_token_id = None
            no_token_id = None
            
            # Try extracting prices from Gamma API
            if 'outcomePrices' in market_data and 'outcomes' in market_data:
                # Parse if outcomePrices/outcomes are JSON strings
                prices = _load_json_if_needed(market_data.get('outcomePrices'))
                outcomes = _load_json_if_needed(market_data.get('outcomes'))
                clob_token_ids = _load_json_if_needed(market_data.get('clobTokenIds'))
                
                if isinstance(prices, list) and isinstance(outcomes, list) and len(prices) >= 2 and len(outcomes) >= 2:
                    # Find Yes/No indices in outcomes list
                    for i, outcome in enumerate(outcomes):
                        if outcome == 'Yes' and i < len(prices):
                            yes_price = float(prices[i])
                            if isinstance(clob_token_ids, list) and i < len(clob_token_ids):
                                yes_token_id = str(clob_token_ids[i])
                        elif outcome == 'No' and i < len(prices):
                            no_price = float(prices[i])
                            if isinstance(clob_token_ids, list) and i < len(clob_token_ids):
                                no_token_id = str(clob_token_ids[i])

            # Query orderbooks with CLOB token IDs (if available)
            yes_orderbook = self.get_market_orderbook(yes_token_id) if yes_token_id else None
            no_orderbook = self.get_market_orderbook(no_token_id) if no_token_id else None

            yes_ask = _best_ask(yes_orderbook)
            yes_bid = _best_bid(yes_orderbook)
            no_ask = _best_ask(no_orderbook)
            no_bid = _best_bid(no_orderbook)
            
            # Use default values if prices are missing
            if yes_price is None or no_price is None:
                # Alternative: calculate directly from market data
                if 'tokens' in market_data:
                    tokens = market_data['tokens']
                    for token in tokens:
                        if token.get('outcome') == 'Yes':
                            yes_price = float(token.get('price', 0.5))
                        elif token.get('outcome') == 'No':
                            no_price = float(token.get('price', 0.5))
            
            if yes_price is None or no_price is None:
                return None
            
            return {
                'yes_price': yes_price,
                'no_price': no_price,
                'yes_ask': yes_ask or yes_price,
                'no_ask': no_ask or no_price,
                'yes_bid': yes_bid or yes_price,
                'no_bid': no_bid or no_price,
                'yes_token_id': yes_token_id,
                'no_token_id': no_token_id
            }
        
        except Exception as e:
            print(f"[✗] Failed to query prices ({market_id}): {e}")
            return None
    
    def check_arbitrage(self, yes_ask: float, no_ask: float) -> tuple[bool, float]:
        """
        Check for arbitrage opportunity using ask-side prices.
        Deducts 2% taker fee (Polymarket standard) from gross profit.
        BUG-1 fix: mid-prices always sum to ~1.0; ask prices expose the real cost.
        BUG-2 fix: fee is now subtracted so edge is never overstated.

        Returns:
            (opportunity_exists, net_profit_after_fees)
        """
        TAKER_FEE  = 0.02  # 2% per-side Polymarket taker fee
        total_cost = yes_ask + no_ask
        net_profit = 1.0 - total_cost - (total_cost * TAKER_FEE)
        if net_profit >= self.min_profit_margin:
            return True, net_profit
        return False, 0.0
    
    def execute_trade(self, market_id: str, yes_price: float, no_price: float) -> bool:
        """
        Execute arbitrage trade
        
        For actual implementation:
        1. Sign and send orders via CLOB API
        2. Send transactions via Web3
        3. Check order status and slippage
        
        Currently in simulation mode
        """
        if not self.account or not self.web3:
            print("[!] Wallet not connected. Cannot execute trades.")
            return False
        
        try:
            # Actual implementation example:
            # 1. Create Yes ticket buy order
            # 2. Create No ticket buy order
            # 3. Send both orders simultaneously (Atomic Arbitrage)
            # 4. Check order status
            
            print(f"[*] Trade execution simulation:")
            print(f"    Market ID: {market_id}")
            print(f"    Yes ticket buy: ${yes_price:.4f}")
            print(f"    No ticket buy: ${no_price:.4f}")
            print(f"    Total cost: ${yes_price + no_price:.4f}")
            print(f"    Expected profit: ${1.0 - (yes_price + no_price):.4f}")
            
            # Add CLOB API call code here for actual implementation
            # from py_clob_client.client import ClobClient
            # client = ClobClient(...)
            # client.create_order(...)
            
            return True
        
        except Exception as e:
            print(f"[✗] Trade execution failed: {e}")
            return False
    
    def monitor_market(self, market_id: str, market_question: str = ""):
        """Monitor single market"""
        prices = self.get_market_prices(market_id)
        
        if not prices:
            return False
        
        yes_price = prices['yes_price']
        no_price = prices['no_price']
        
        # Data logging
        if self.logger:
            self.logger.log_price_data(
                market_id=market_id,
                market_question=market_question,
                yes_price=yes_price,
                no_price=no_price,
                yes_ask=prices.get('yes_ask'),
                no_ask=prices.get('no_ask'),
                yes_bid=prices.get('yes_bid'),
                no_bid=prices.get('no_bid'),
                min_profit_margin=self.min_profit_margin
            )
        
        # Check for arbitrage opportunity using ask prices (not mid-prices)
        # BUG-1 fix: mid-prices always sum to ~1.0 by market design
        # BUG-2 fix: check_arbitrage now deducts 2% taker fee
        has_opportunity, profit = self.check_arbitrage(
            prices['yes_ask'], prices['no_ask']
        )

        if self.strategy_pipeline:
            snapshot = {
                'market_id': market_id,
                'market_question': market_question,
                'timestamp': datetime.utcnow().isoformat(),
                'yes_price': yes_price,
                'no_price': no_price,
                'yes_ask': prices.get('yes_ask', yes_price),
                'no_ask': prices.get('no_ask', no_price),
                'yes_bid': prices.get('yes_bid', yes_price),
                'no_bid': prices.get('no_bid', no_price),
                'yes_token_id': prices.get('yes_token_id'),
                'no_token_id': prices.get('no_token_id')
            }
            strategy_result = self.strategy_pipeline.evaluate(snapshot)
            if strategy_result['signals']:
                print(f"[🧪] Strategy signals ({market_id}):")
                for signal in strategy_result['signals']:
                    print(
                        f"    - {signal['name']} | score={signal['score']:.4f} | {signal['reason']}"
                    )
        
        if has_opportunity:
            print(f"\n{'='*60}")
            print(f"[🎯] Arbitrage opportunity found!")
            print(f"    Market: {market_question or market_id}")
            print(f"    Yes ask:  ${prices['yes_ask']:.4f}  (mid: ${yes_price:.4f})")
            print(f"    No ask:   ${prices['no_ask']:.4f}  (mid: ${no_price:.4f})")
            print(f"    Total ask cost: ${prices['yes_ask'] + prices['no_ask']:.4f}")
            print(f"    Net profit (after 2% fee): ${profit:.4f} ({profit*100:.2f}%)")
            print(f"{'='*60}\n")
            
            # Execute trade
            if self.account:
                self.execute_trade(market_id, yes_price, no_price)
        
        return has_opportunity
    
    def run(self):
        """Bot execution main loop"""
        print("="*60)
        print("Polymarket Arbitrage Bot Starting")
        print("="*60)
        
        # Get market list
        if not self.market_ids:
            print("[*] Searching for active markets...")
            markets = self.get_active_markets()
            self.market_ids = [m['id'] for m in markets]
            market_questions = {m['id']: m['question'] for m in markets}
        else:
            market_questions = {mid: "" for mid in self.market_ids}
        
        if not self.market_ids:
            print("[✗] No markets to monitor.")
            return
        
        print(f"[✓] Starting to monitor {len(self.market_ids)} markets")
        print(f"[*] Minimum profit rate: {self.min_profit_margin*100:.1f}%")
        print(f"[*] Scan interval: {self.scan_interval} seconds")
        print(f"[*] Data logging: {'Enabled' if ENABLE_DATA_LOGGING else 'Disabled'}")
        print("-"*60)
        
        try:
            while True:
                opportunities_found = 0
                
                for market_id in self.market_ids:
                    try:
                        if self.monitor_market(market_id, market_questions.get(market_id, "")):
                            opportunities_found += 1
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        print(f"[✗] Market monitoring error ({market_id}): {e}")
                        continue
                
                # Output statistics (periodically)
                if self.logger and opportunities_found == 0:
                    # Output statistics every 10 minutes
                    if int(time.time()) % 600 == 0:
                        stats = self.logger.get_arbitrage_statistics(hours=24)
                        if stats['total_opportunities'] > 0:
                            print(f"\n[📊] Last 24 hours statistics:")
                            print(f"    Arbitrage opportunities: {stats['total_opportunities']}")
                            print(f"    Average profit rate: {stats['avg_profit']*100:.2f}%")
                            print(f"    Maximum profit rate: {stats['max_profit']*100:.2f}%")
                            print(f"    Unique markets: {stats['unique_markets']}\n")
                
                time.sleep(self.scan_interval)
        
        except KeyboardInterrupt:
            print("\n\n[*] Shutting down bot...")
            if self.logger:
                stats = self.logger.get_arbitrage_statistics(hours=24)
                print(f"\n[📊] Final statistics:")
                print(f"    Arbitrage opportunities: {stats['total_opportunities']}")
                print(f"    Average profit rate: {stats['avg_profit']*100:.2f}%")
            print("[✓] Bot shutdown complete")


if __name__ == "__main__":
    # Usage example
    # To monitor specific markets only:
    # bot = PolyArbitrageBot(market_ids=["market-id-1", "market-id-2"])
    
    # Monitor all active markets:
    bot = PolyArbitrageBot()
    bot.run()
