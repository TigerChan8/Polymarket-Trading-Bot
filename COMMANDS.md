# Terminal Commands Guide

## 🚀 Bot Execution

### Run Bot
```bash
# Basic execution (foreground)
python3 bot.py

# Background execution
python3 bot.py &

# Background execution and get process ID
python3 bot.py &
echo $!
```

### Stop Bot
```bash
# Find process
ps aux | grep "python3 bot.py"

# Stop bot
pkill -f "python3 bot.py"

# Force stop
pkill -9 -f "python3 bot.py"
```

### Test Execution
```bash
# Run test script (30 seconds)
python3 test_bot.py

# Run strategy pipeline dry-run (no real trading)
python3 strategy_test.py --duration 60 --markets 10
```

## 📊 Data Analysis

### Basic Analysis
```bash
# Analyze last 24 hours of data
python3 analyze_data.py

# Analyze last 1 hour of data
python3 analyze_data.py 1

# Analyze last 12 hours of data
python3 analyze_data.py 12

# Analyze last 48 hours of data
python3 analyze_data.py 48
```

### CSV Export
```bash
# Analysis + CSV export
python3 analyze_data.py 24 --export
```

## 💾 Database Check

### Direct SQLite DB Query
```bash
# Check database file
ls -lh logs/price_data.db

# Check total record count
python3 -c "import sqlite3; conn = sqlite3.connect('logs/price_data.db'); cursor = conn.cursor(); cursor.execute('SELECT COUNT(*) FROM price_data'); print('Total records:', cursor.fetchone()[0]); conn.close()"

# Check last 5 records
python3 -c "import sqlite3; conn = sqlite3.connect('logs/price_data.db'); cursor = conn.cursor(); cursor.execute('SELECT timestamp, market_id, yes_price, no_price, total_cost, arbitrage_opportunity FROM price_data ORDER BY timestamp DESC LIMIT 5'); [print(r) for r in cursor.fetchall()]; conn.close()"

# Check arbitrage opportunity count
python3 -c "import sqlite3; conn = sqlite3.connect('logs/price_data.db'); cursor = conn.cursor(); cursor.execute('SELECT COUNT(*) FROM price_data WHERE arbitrage_opportunity = 1'); print('Arbitrage opportunities:', cursor.fetchone()[0]); conn.close()"

# Market statistics
python3 -c "import sqlite3; conn = sqlite3.connect('logs/price_data.db'); cursor = conn.cursor(); cursor.execute('SELECT market_id, COUNT(*) as cnt, AVG(total_cost) as avg_cost FROM price_data GROUP BY market_id ORDER BY cnt DESC LIMIT 10'); [print(f'Market: {r[0]}, Records: {r[1]}, Avg Cost: {r[2]:.4f}') for r in cursor.fetchall()]; conn.close()"
```

### Using SQLite CLI
```bash
# SQLite interactive mode
sqlite3 logs/price_data.db

# Useful SQLite queries:
# .tables                    # List tables
# .schema price_data         # Table structure
# SELECT COUNT(*) FROM price_data;
# SELECT * FROM price_data ORDER BY timestamp DESC LIMIT 10;
# SELECT * FROM price_data WHERE arbitrage_opportunity = 1;
# .quit                      # Exit
```

## 📁 File Check

### CSV File Check
```bash
# Check CSV file size
ls -lh logs/price_data.csv

# Check first 10 lines of CSV file
head -n 10 logs/price_data.csv

# Check last 10 lines of CSV file
tail -n 10 logs/price_data.csv

# Check total line count of CSV file
wc -l logs/price_data.csv

# Filter only arbitrage opportunities from CSV
grep ",1," logs/price_data.csv | head -n 10
```

### Log Directory Check
```bash
# Check log directory contents
ls -lh logs/

# Check log file size
du -sh logs/
```

## 🔍 Process Monitoring

### Bot Process Check
```bash
# Check running bot process
ps aux | grep "python3 bot.py" | grep -v grep

# Process detailed information
ps -p $(pgrep -f "python3 bot.py") -o pid,ppid,cmd,%mem,%cpu,etime

# Real-time process monitoring
watch -n 1 'ps aux | grep "python3 bot.py" | grep -v grep'
```

### System Resource Check
```bash
# CPU and memory usage
top -p $(pgrep -f "python3 bot.py")

# Or use htop (if installed)
htop -p $(pgrep -f "python3 bot.py")
```

## 📈 Real-time Data Monitoring

### Real-time Database Monitoring
```bash
# Check record count in real-time (every 5 seconds)
watch -n 5 'python3 -c "import sqlite3; conn = sqlite3.connect(\"logs/price_data.db\"); cursor = conn.cursor(); cursor.execute(\"SELECT COUNT(*) FROM price_data\"); print(\"Total records:\", cursor.fetchone()[0]); conn.close()"'

# Real-time arbitrage opportunity monitoring
watch -n 5 'python3 -c "import sqlite3; conn = sqlite3.connect(\"logs/price_data.db\"); cursor = conn.cursor(); cursor.execute(\"SELECT COUNT(*) FROM price_data WHERE arbitrage_opportunity = 1\"); print(\"Arbitrage opportunities:\", cursor.fetchone()[0]); conn.close()"'
```

### CSV File Real-time Monitoring
```bash
# Real-time CSV file monitoring (tail -f)
tail -f logs/price_data.csv

# Continuously view last 20 lines
tail -n 20 -f logs/price_data.csv
```

## 🧹 Cleanup Commands

### Log File Cleanup
```bash
# Backup and delete old CSV files (30+ days)
find logs/ -name "*.csv" -mtime +30 -exec mv {} logs/backup/ \;

# Database backup
cp logs/price_data.db logs/backup/price_data_$(date +%Y%m%d_%H%M%S).db

# Delete empty log files
find logs/ -type f -empty -delete
```

## 🔧 Utility Commands

### Python Environment Check
```bash
# Check Python version
python3 --version

# Check installed packages
pip3 list | grep -E "(pandas|requests|web3|sqlite3)"

# Reinstall packages
pip3 install -r requirements.txt
```

### Network Test
```bash
# Test Polymarket API connection
curl -s "https://gamma-api.polymarket.com/markets?limit=1" | head -n 20

# Measure API response time
time curl -s "https://gamma-api.polymarket.com/markets?limit=1" > /dev/null
```

## 🏆 Leaderboard

### Fetch Trader Leaderboard
```bash
# Top traders by daily PnL (overall)
python3 leaderboard.py --category OVERALL --time-period DAY --order-by PNL --limit 25

# Weekly crypto leaderboard by volume
python3 leaderboard.py --category CRYPTO --time-period WEEK --order-by VOL --limit 25

# Filter by wallet address
python3 leaderboard.py --user 0x56687bf447db6ffa42ffe2204a05edaa20f55839

# Export to CSV and JSON
python3 leaderboard.py --category OVERALL --time-period MONTH --order-by PNL --limit 50 --out-csv logs/leaderboard.csv --out-json logs/leaderboard.json

# Save snapshot to SQLite for trend analytics
python3 leaderboard.py --category OVERALL --time-period DAY --order-by PNL --limit 50 --save-db logs/leaderboard.db

# Compare latest vs previous snapshot and show rank movers
python3 leaderboard_analytics.py --db logs/leaderboard.db --category OVERALL --time-period DAY --order-by PNL --movers 15

# Track one wallet across snapshots
python3 leaderboard_analytics.py --db logs/leaderboard.db --user 0x56687bf447db6ffa42ffe2204a05edaa20f55839
```

## 🌦️ Weather Intelligence

### Browse Weather Markets
```bash
# Quick overview: active + recent closed weather markets
python3 weather_markets.py
```

### Weather Leaderboard Snapshot Daemon (Idea 3 — rank velocity)
```bash
# Start daemon in background (snapshots every 6h → logs/leaderboard.db)
python3 weather_snapshot_daemon.py &

# Custom interval
python3 weather_snapshot_daemon.py --interval-hours 4 &

# Stop daemon
pkill -f "weather_snapshot_daemon.py"
```

### Rank Velocity Alert (Idea 3)
```bash
# Who climbed fastest between last two WEATHER snapshots?  (requires ≥2 snapshots in DB)
python3 leaderboard_analytics.py --db logs/leaderboard.db --category WEATHER --time-period ALL --velocity

# Lower the jump threshold (surface more movers)
python3 leaderboard_analytics.py --db logs/leaderboard.db --category WEATHER --time-period ALL --velocity --min-jump 5

# Skip live position fetch (faster, offline)
python3 leaderboard_analytics.py --db logs/leaderboard.db --category WEATHER --time-period ALL --velocity --no-fetch-positions
```

### Weather Whale Monitor + Consensus Burst (Ideas 1 & 2)
```bash
# Monitor top-20 WEATHER traders for large trades (6 loops × 60s poll)
python3 weather_whale_monitor.py --loops 6 --poll-seconds 60 --min-notional 5000

# Continuous monitoring until Ctrl+C (loops=0)
python3 weather_whale_monitor.py --loops 0 --poll-seconds 120 --leaderboard-limit 30

# Lower notional threshold + enable Discord notifications
python3 weather_whale_monitor.py --loops 12 --min-notional 2000 --notify-discord

# Custom DB / CSV output
python3 weather_whale_monitor.py --loops 6 --db logs/weather_alerts.db --csv logs/weather_alerts.csv
```

### Trader Accuracy Scorer (Idea 5)
```bash
# Score top-50 WEATHER traders by historical accuracy on resolved markets
python3 weather_accuracy.py --top-n 50

# Export results to CSV
python3 weather_accuracy.py --top-n 50 --out-csv logs/weather_accuracy.csv

# Only print top-10 in table (still scores all 50)
python3 weather_accuracy.py --top-n 50 --print-top 10
```

### DB Quick-Checks
```bash
# View whale alerts
sqlite3 logs/weather_alerts.db "SELECT detected_at, title, outcome, notional FROM whale_alerts ORDER BY notional DESC LIMIT 10;"

# View consensus bursts
sqlite3 logs/weather_alerts.db "SELECT detected_at, title, outcome, trader_count, total_notional FROM consensus_bursts ORDER BY total_notional DESC LIMIT 10;"

# View accuracy scores
sqlite3 logs/weather_accuracy.db "SELECT user_name, total_trades, win_rate, weighted_accuracy, confidence_warning FROM trader_accuracy ORDER BY weighted_accuracy DESC LIMIT 20;"
```

## 📝 Useful Combined Commands

### Bot Status Overview
```bash
# Bot status + database status
echo "=== Bot Process ===" && ps aux | grep "python3 bot.py" | grep -v grep && echo -e "\n=== Database ===" && python3 -c "import sqlite3; conn = sqlite3.connect('logs/price_data.db'); cursor = conn.cursor(); cursor.execute('SELECT COUNT(*) FROM price_data'); print('Total records:', cursor.fetchone()[0]); cursor.execute('SELECT COUNT(*) FROM price_data WHERE arbitrage_opportunity = 1'); print('Arbitrage opportunities:', cursor.fetchone()[0]); conn.close()"
```

### Quick Analysis
```bash
# Quick analysis of last 1 hour of data
python3 analyze_data.py 1
```

### Database Size Check
```bash
# Database file size
du -h logs/price_data.db

# Database internal statistics
sqlite3 logs/price_data.db "SELECT COUNT(*) as total, COUNT(DISTINCT market_id) as markets, MIN(timestamp) as first, MAX(timestamp) as last FROM price_data;"
```
