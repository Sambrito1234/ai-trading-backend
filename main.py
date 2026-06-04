from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime
from collections import defaultdict

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
INTRADAY_PROFIT_TARGET_PCT = 2.0   # sell intraday at +2%
EOD_MIN_PROFIT_PCT         = 0.5   # sell at EOD if >= +0.5%
MAX_HOLDINGS               = 10    # max 10 stocks at once
MAX_BUYS_PER_SCAN          = 3     # buy only top 3 signals per scan
STARTING_CASH              = 100000.0

# Smart position sizing based on confidence
# confidence >= 85 → allocate 20% of cash
# confidence >= 75 → allocate 15% of cash
# confidence >= 65 → allocate 10% of cash
# confidence >= 55 → allocate 7% of cash
def get_position_size(confidence, cash):
    if confidence >= 85:
        pct = 0.20
    elif confidence >= 75:
        pct = 0.15
    elif confidence >= 65:
        pct = 0.10
    else:
        pct = 0.07
    return round(cash * pct, 2)

LARGE_CAP = {
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","SBIN.NS",
    "ICICIBANK.NS","KOTAKBANK.NS","LT.NS","BAJFINANCE.NS","HINDUNILVR.NS",
    "AXISBANK.NS","MARUTI.NS","TITAN.NS","SUNPHARMA.NS","TATAMOTORS.NS",
    "NESTLEIND.NS","ULTRACEMCO.NS","ASIANPAINT.NS","AAPL","MSFT",
    "GOOGL","AMZN","NVDA","TSLA","META","JPM","V","JNJ","WMT","UNH","MA","HD"
}

STRONG_CONF = 70
DECENT_CONF = 55

# --------------------------------------------------
# DATABASE
# --------------------------------------------------
DB_PATH = "trading.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS portfolio (
        id INTEGER PRIMARY KEY, cash REAL NOT NULL)""")

    c.execute("""CREATE TABLE IF NOT EXISTS holdings (
        symbol TEXT PRIMARY KEY,
        quantity INTEGER NOT NULL,
        avg_buy_price REAL DEFAULT 0,
        invested_amount REAL DEFAULT 0)""")

    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        price REAL NOT NULL,
        quantity INTEGER NOT NULL,
        invested REAL DEFAULT 0,
        confidence INTEGER DEFAULT 0,
        mode TEXT NOT NULL,
        reason TEXT DEFAULT '',
        pnl REAL DEFAULT 0,
        time TEXT NOT NULL)""")

    c.execute("""CREATE TABLE IF NOT EXISTS watchlist (
        symbol TEXT PRIMARY KEY)""")

    c.execute("""CREATE TABLE IF NOT EXISTS daily_pnl (
        date TEXT PRIMARY KEY,
        portfolio_value REAL NOT NULL,
        cash REAL NOT NULL,
        realized_pnl_today REAL DEFAULT 0,
        cumulative_pnl REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0)""")

    c.execute("""CREATE TABLE IF NOT EXISTS loss_cooldown (
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        PRIMARY KEY (symbol, date))""")

    c.execute("SELECT COUNT(*) FROM portfolio")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO portfolio (id, cash) VALUES (1, ?)", (STARTING_CASH,))

    c.execute("SELECT COUNT(*) FROM watchlist")
    if c.fetchone()[0] == 0:
        default = [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","SBIN.NS",
            "WIPRO.NS","ICICIBANK.NS","BAJFINANCE.NS","HINDUNILVR.NS",
            "AXISBANK.NS","KOTAKBANK.NS","LT.NS","MARUTI.NS","TITAN.NS",
            "SUNPHARMA.NS","TATAMOTORS.NS","ADANIENT.NS","ULTRACEMCO.NS",
            "ASIANPAINT.NS","NESTLEIND.NS","ONGC.NS","NTPC.NS","POWERGRID.NS",
            "COALINDIA.NS","BAJAJFINSV.NS","HCLTECH.NS","TECHM.NS","DRREDDY.NS",
            "DIVISLAB.NS","CIPLA.NS","EICHERMOT.NS","HEROMOTOCO.NS","BPCL.NS",
            "GRASIM.NS","INDUSINDBK.NS","SBILIFE.NS","HDFCLIFE.NS","BRITANNIA.NS",
            "TATACONSUM.NS","APOLLOHOSP.NS","ADANIPORTS.NS","JSWSTEEL.NS",
            "TATASTEEL.NS","HINDALCO.NS",
            "AAPL","MSFT","GOOGL","AMZN","NVDA","TSLA","META",
            "JPM","V","JNJ","WMT","UNH","MA","HD","BAC",
            "PFE","KO","PEP","NFLX","AMD","INTC","CRM","ORCL",
            "BTC-USD","ETH-USD","BNB-USD","SOL-USD","ADA-USD","XRP-USD"
        ]
        for sym in default:
            c.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (sym,))

    conn.commit()
    conn.close()

init_db()

# --------------------------------------------------
# DB HELPERS
# --------------------------------------------------
def get_cash():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT cash FROM portfolio WHERE id=1")
    cash = c.fetchone()[0]
    conn.close()
    return cash

def set_cash(amount):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE portfolio SET cash=? WHERE id=1", (amount,))
    conn.commit()
    conn.close()

def get_holdings():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol, quantity, avg_buy_price, invested_amount FROM holdings WHERE quantity > 0")
    rows = c.fetchall()
    conn.close()
    return {r[0]: {"quantity": r[1], "avg_buy_price": r[2], "invested_amount": r[3]} for r in rows}

def update_holding(symbol, quantity, avg_buy_price=0, invested_amount=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if quantity <= 0:
        c.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
    else:
        c.execute("""INSERT INTO holdings (symbol, quantity, avg_buy_price, invested_amount)
            VALUES (?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
            quantity=?, avg_buy_price=?, invested_amount=?""",
            (symbol, quantity, avg_buy_price, invested_amount,
             quantity, avg_buy_price, invested_amount))
    conn.commit()
    conn.close()

def add_trade(type_, symbol, price, quantity, invested, confidence, mode, reason="", pnl=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO trades
        (type,symbol,price,quantity,invested,confidence,mode,reason,pnl,time)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (type_, symbol, price, quantity, invested, confidence, mode, reason, pnl,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_all_trades():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT type,symbol,price,quantity,invested,confidence,mode,reason,pnl,time
        FROM trades ORDER BY id DESC LIMIT 500""")
    rows = c.fetchall()
    conn.close()
    return [{"type":r[0],"symbol":r[1],"price":r[2],"quantity":r[3],
             "invested":round(r[4],2),"confidence":r[5],"mode":r[6],
             "reason":r[7],"pnl":round(r[8],2),"time":r[9]} for r in rows]

def get_todays_realized_pnl():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("""SELECT SUM(pnl) FROM trades
        WHERE type='SELL' AND time LIKE ?""", (f"{today}%",))
    result = c.fetchone()[0]
    conn.close()
    return round(result or 0, 2)

def get_watchlist_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol FROM watchlist ORDER BY symbol")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def add_to_watchlist_db(symbol):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol,))
    conn.commit()
    conn.close()

def remove_from_watchlist_db(symbol):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM watchlist WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()

def add_loss_cooldown(symbol):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("INSERT OR IGNORE INTO loss_cooldown (symbol, date) VALUES (?, ?)", (symbol, today))
    conn.commit()
    conn.close()

def is_on_cooldown(symbol):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT 1 FROM loss_cooldown WHERE symbol=? AND date=?", (symbol, today))
    result = c.fetchone()
    conn.close()
    return result is not None

def clear_old_cooldowns():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("DELETE FROM loss_cooldown WHERE date < ?", (today,))
    conn.commit()
    conn.close()

def save_daily_snapshot():
    """Save daily snapshot with cumulative P&L carrying forward"""
    cash = get_cash()
    holdings = get_holdings()
    total_value = cash
    for symbol, data in holdings.items():
        price = get_price(symbol)
        if price:
            total_value += price * data["quantity"]

    today = datetime.now().strftime("%Y-%m-%d")
    realized_today = get_todays_realized_pnl()

    # Get yesterday's cumulative PnL to carry forward
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT cumulative_pnl FROM daily_pnl
        WHERE date < ? ORDER BY date DESC LIMIT 1""", (today,))
    row = c.fetchone()
    prev_cumulative = row[0] if row else 0

    cumulative_pnl = prev_cumulative + realized_today
    pnl_pct = (cumulative_pnl / STARTING_CASH) * 100

    c.execute("""INSERT INTO daily_pnl
        (date, portfolio_value, cash, realized_pnl_today, cumulative_pnl, pnl_pct)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
        portfolio_value=?, cash=?, realized_pnl_today=?, cumulative_pnl=?, pnl_pct=?""",
        (today, total_value, cash, realized_today, cumulative_pnl, pnl_pct,
         total_value, cash, realized_today, cumulative_pnl, pnl_pct))
    conn.commit()
    conn.close()
    print(f"[SNAPSHOT] {today} | Value:₹{total_value:.2f} | Today:₹{realized_today:.2f} | Cumulative:₹{cumulative_pnl:.2f} ({pnl_pct:+.2f}%)")

def get_daily_pnl_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT date, portfolio_value, realized_pnl_today, cumulative_pnl, pnl_pct
        FROM daily_pnl ORDER BY date DESC LIMIT 30""")
    rows = c.fetchall()
    conn.close()
    return [{"date":r[0], "portfolio_value":round(r[1],2),
             "realized_pnl_today":round(r[2],2),
             "cumulative_pnl":round(r[3],2),
             "pnl_pct":round(r[4],2)} for r in rows]

# --------------------------------------------------
# Signal log
# --------------------------------------------------
signal_log = []

# --------------------------------------------------
# INDICATORS
# --------------------------------------------------
def calculate_rsi(close, window=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(close):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal, macd - signal

def get_volume_signal(data):
    try:
        volume = data["Volume"]
        if isinstance(volume, pd.DataFrame):
            volume = volume.iloc[:, 0]
        avg_vol = float(volume.rolling(20).mean().iloc[-1])
        cur_vol = float(volume.iloc[-1])
        return cur_vol > avg_vol * 1.2
    except Exception:
        return False

def get_news_sentiment(symbol):
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news:
            return 0
        positive = ["surge","rise","gain","profit","growth","beat","record",
                    "strong","bullish","upgrade","buy","rally","high","boost"]
        negative = ["fall","drop","loss","decline","miss","weak","bearish",
                    "downgrade","sell","crash","cut","warning","low","risk"]
        score = 0
        for article in news[:8]:
            title = article.get("title","").lower()
            for w in positive:
                if w in title: score += 1
            for w in negative:
                if w in title: score -= 1
        if score > 1: return 1
        elif score < -1: return -1
        return 0
    except Exception:
        return 0

def get_price(symbol: str):
    try:
        data = yf.download(symbol, period="1d", progress=False)
        if data.empty:
            return None
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        price = float(close.iloc[-1])
        return None if np.isnan(price) else round(price, 2)
    except Exception:
        return None

# --------------------------------------------------
# ANALYZE STOCK
# --------------------------------------------------
def analyze_symbol(symbol: str):
    try:
        data = yf.download(symbol, period="6mo", progress=False)
        if data.empty or len(data) < 50:
            return None

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()

        current_price = float(close.iloc[-1])

        rsi = float(calculate_rsi(close).iloc[-1])
        if np.isnan(rsi): rsi = 50.0

        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else sma50
        if np.isnan(sma20): sma20 = current_price
        if np.isnan(sma50): sma50 = current_price
        if np.isnan(sma200): sma200 = current_price

        macd, macd_sig, macd_hist = calculate_macd(close)
        macd_val = float(macd.iloc[-1])
        macd_sig_val = float(macd_sig.iloc[-1])
        macd_hist_val = float(macd_hist.iloc[-1])
        macd_bullish = macd_val > macd_sig_val and macd_hist_val > 0

        sma20_bb = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_upper = float((sma20_bb + 2 * std20).iloc[-1])
        bb_lower = float((sma20_bb - 2 * std20).iloc[-1])
        bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5

        volume_confirms = get_volume_signal(data)
        sentiment = get_news_sentiment(symbol)

        buy_score = 0
        if rsi < 25: buy_score += 4
        elif rsi < 35: buy_score += 3
        elif rsi < 45: buy_score += 1
        if sma20 > sma50: buy_score += 2
        if current_price > sma200: buy_score += 1
        if macd_bullish: buy_score += 3
        if bb_position < 0.2: buy_score += 2
        elif bb_position < 0.35: buy_score += 1
        if volume_confirms: buy_score += 1
        if sentiment == 1: buy_score += 2
        elif sentiment == -1: buy_score -= 3

        confidence = min(95, int(50 + (buy_score / 15) * 50))
        signal = "BUY" if buy_score >= 4 else "WATCH"

        return {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "RSI": round(rsi, 2),
            "SMA20": round(sma20, 2),
            "SMA50": round(sma50, 2),
            "SMA200": round(sma200, 2),
            "MACD": round(macd_val, 4),
            "MACD_bullish": macd_bullish,
            "BB_upper": round(bb_upper, 2),
            "BB_lower": round(bb_lower, 2),
            "BB_position": round(bb_position * 100, 1),
            "volume_confirms": volume_confirms,
            "sentiment": sentiment,
            "buy_score": buy_score,
            "signal": signal,
            "confidence": confidence,
            "is_large_cap": symbol in LARGE_CAP,
            "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        return None

# --------------------------------------------------
# INTRADAY PROFIT CHECK — NEVER sells at loss
# --------------------------------------------------
def check_intraday_profit():
    holdings = get_holdings()
    for symbol, data in holdings.items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        invested = data["invested_amount"]
        if avg_buy == 0 or qty == 0:
            continue
        current_price = get_price(symbol)
        if current_price is None:
            continue
        pnl_pct = ((current_price - avg_buy) / avg_buy) * 100
        pnl_amount = (current_price - avg_buy) * qty

        if pnl_pct >= INTRADAY_PROFIT_TARGET_PCT:
            # ✅ SELL — profit target hit
            cash = get_cash()
            sell_value = current_price * qty
            set_cash(cash + sell_value)
            update_holding(symbol, 0)
            add_trade("SELL", symbol, current_price, qty, invested,
                100, "AUTO",
                f"✅ INTRADAY +{pnl_pct:.2f}% | Invested:₹{invested:.0f} → ₹{sell_value:.0f}",
                pnl_amount)
            print(f"[PROFIT] {symbol} @ ₹{current_price} | +{pnl_pct:.2f}% | P&L:₹{pnl_amount:.2f}")

        elif pnl_pct < 0:
            # 🔒 HOLD — never sell at loss
            print(f"[HOLD] {symbol} | {pnl_pct:.2f}% loss | ₹{pnl_amount:.2f} | WILL NOT SELL")

        else:
            # ⏳ WAIT — small profit, wait for target or EOD
            print(f"[WAIT] {symbol} | +{pnl_pct:.2f}% | waiting for +{INTRADAY_PROFIT_TARGET_PCT}% or EOD")

# --------------------------------------------------
# END OF DAY PROFIT BOOKING — 3:15 PM IST
# Sells all profitable positions (>= +0.5%)
# NEVER sells losses — marks them with cooldown
# --------------------------------------------------
def end_of_day_profit_booking():
    print(f"\n[EOD] ===== EOD Booking at {datetime.now().strftime('%H:%M:%S')} =====")
    holdings = get_holdings()
    sold = 0
    held = 0
    total_profit = 0

    for symbol, data in holdings.items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        invested = data["invested_amount"]
        if avg_buy == 0 or qty == 0:
            continue
        current_price = get_price(symbol)
        if current_price is None:
            continue
        pnl_pct = ((current_price - avg_buy) / avg_buy) * 100
        pnl_amount = (current_price - avg_buy) * qty

        if pnl_pct >= EOD_MIN_PROFIT_PCT:
            # ✅ SELL — book the profit
            cash = get_cash()
            sell_value = current_price * qty
            set_cash(cash + sell_value)
            update_holding(symbol, 0)
            add_trade("SELL", symbol, current_price, qty, invested,
                100, "AUTO",
                f"📅 EOD PROFIT +{pnl_pct:.2f}% | ₹{invested:.0f} → ₹{sell_value:.0f}",
                pnl_amount)
            total_profit += pnl_amount
            sold += 1
            print(f"[EOD SELL] {symbol} @ ₹{current_price} | +{pnl_pct:.2f}% | ₹{pnl_amount:.2f}")
        else:
            # 🔒 HOLD overnight — add cooldown so bot won't rebuy tomorrow
            add_loss_cooldown(symbol)
            held += 1
            print(f"[EOD HOLD] {symbol} | {pnl_pct:.2f}% | holding overnight")

    save_daily_snapshot()
    print(f"[EOD] Sold:{sold} | Held:{held} | Profit booked:₹{total_profit:.2f}")

# --------------------------------------------------
# AUTO-TRADING BOT
# Scans all stocks, picks TOP 3 best signals, buys smart
# --------------------------------------------------
def auto_trade():
    print(f"\n[BOT] ===== Scan at {datetime.now().strftime('%H:%M:%S')} =====")
    global signal_log

    clear_old_cooldowns()
    check_intraday_profit()

    holdings = get_holdings()
    cash = get_cash()

    # If already at max holdings, skip buying
    if len(holdings) >= MAX_HOLDINGS:
        print(f"[BOT] Max holdings reached ({MAX_HOLDINGS}). Skipping buys.")
        return

    slots_available = MAX_HOLDINGS - len(holdings)
    buys_this_scan = min(MAX_BUYS_PER_SCAN, slots_available)

    # Scan all watchlist stocks
    watchlist = get_watchlist_db()
    all_signals = []

    print(f"[BOT] Scanning {len(watchlist)} stocks...")
    for symbol in watchlist:
        result = analyze_symbol(symbol)
        if result is None:
            continue
        all_signals.append(result)

    signal_log = all_signals

    # Filter: only BUY signals, not already holding, not on cooldown
    candidates = [
        s for s in all_signals
        if s["signal"] == "BUY"
        and s["symbol"] not in holdings
        and not is_on_cooldown(s["symbol"])
        and s["sentiment"] != -1
        and s["current_price"] <= cash * 0.25  # single stock max 25% of cash
    ]

    # Apply confidence threshold per type
    candidates = [
        s for s in candidates
        if (s["is_large_cap"] and s["confidence"] >= STRONG_CONF)
        or (not s["is_large_cap"] and s["confidence"] >= DECENT_CONF)
    ]

    # Sort by confidence + buy_score — pick TOP 3
    candidates.sort(key=lambda x: (x["confidence"], x["buy_score"]), reverse=True)
    top_picks = candidates[:buys_this_scan]

    print(f"[BOT] {len(candidates)} candidates → buying top {len(top_picks)}")

    for pick in top_picks:
        symbol = pick["symbol"]
        price = pick["current_price"]
        confidence = pick["confidence"]
        cash = get_cash()

        # Smart position sizing
        position_size = get_position_size(confidence, cash)

        # Calculate how many shares we can buy
        if price <= 0 or position_size < price:
            print(f"[SKIP] {symbol} — price ₹{price} > position size ₹{position_size:.0f}")
            continue

        qty = int(position_size / price)
        if qty < 1:
            qty = 1

        total_cost = price * qty

        if cash < total_cost:
            print(f"[SKIP] {symbol} — not enough cash (need ₹{total_cost:.0f}, have ₹{cash:.0f})")
            continue

        set_cash(cash - total_cost)
        update_holding(symbol, qty, price, total_cost)

        target = round(price * (1 + INTRADAY_PROFIT_TARGET_PCT / 100), 2)
        eod_target = round(price * (1 + EOD_MIN_PROFIT_PCT / 100), 2)

        add_trade("BUY", symbol, price, qty, total_cost, confidence, "AUTO",
            f"{'STRONG' if pick['is_large_cap'] else 'DECENT'} | conf:{confidence}% | score:{pick['buy_score']} | RSI:{pick['RSI']} | MACD:{'▲' if pick['MACD_bullish'] else '▼'} | target:₹{target} | eod:₹{eod_target}",
            0)

        print(f"[BUY] {symbol} | qty:{qty} | ₹{total_cost:.0f} invested | conf:{confidence}% | target:₹{target}")

    remaining = get_cash()
    print(f"[BOT] Done | Holdings:{len(get_holdings())}/{MAX_HOLDINGS} | Cash:₹{remaining:.2f}")

# --------------------------------------------------
# SCHEDULER
# --------------------------------------------------
scheduler = BackgroundScheduler()
scheduler.add_job(auto_trade, "interval", minutes=5, id="auto_trade")
# NSE EOD — 3:15 PM IST = 09:45 UTC
scheduler.add_job(end_of_day_profit_booking, "cron", hour=9, minute=45, id="eod_nse", timezone="UTC")
# NYSE EOD — 9:55 PM IST = 16:25 UTC
scheduler.add_job(end_of_day_profit_booking, "cron", hour=16, minute=25, id="eod_nyse", timezone="UTC")
# Snapshots
scheduler.add_job(save_daily_snapshot, "cron", hour=10, minute=5, id="snap_nse")
scheduler.add_job(save_daily_snapshot, "cron", hour=21, minute=5, id="snap_nyse")
scheduler.start()

# --------------------------------------------------
# ROUTES
# --------------------------------------------------
@app.get("/")
def home():
    holdings = get_holdings()
    return {
        "status": "AI Trading Bot Running",
        "strategy": f"Top {MAX_BUYS_PER_SCAN} signals per scan | Sell at +{INTRADAY_PROFIT_TARGET_PCT}% | EOD sell at +{EOD_MIN_PROFIT_PCT}% | Hold losses",
        "current_holdings": len(holdings),
        "max_holdings": MAX_HOLDINGS,
        "watchlist_size": len(get_watchlist_db()),
        "cash": round(get_cash(), 2)
    }

@app.get("/stock/{symbol}")
def get_stock(symbol: str):
    result = analyze_symbol(symbol)
    return result if result else {"error": f"Could not fetch {symbol}"}

@app.get("/history/{symbol}")
def stock_history(symbol: str):
    try:
        data = yf.download(symbol, period="3mo", progress=False)
        if data.empty:
            return {"dates": [], "prices": []}
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.ffill()
        return {
            "dates": [str(d.date()) for d in close.index],
            "prices": [round(float(p), 2) for p in close.tolist()]
        }
    except Exception:
        return {"dates": [], "prices": []}

@app.post("/buy/{symbol}")
def buy_stock(symbol: str, quantity: int = 1):
    price = get_price(symbol)
    if price is None:
        return {"error": "Invalid symbol"}
    cash = get_cash()
    total_cost = price * quantity
    if cash < total_cost:
        return {"error": f"Not enough cash. Need ₹{total_cost}, have ₹{cash:.0f}"}
    holdings = get_holdings()
    existing_qty = holdings.get(symbol, {}).get("quantity", 0)
    existing_avg = holdings.get(symbol, {}).get("avg_buy_price", 0)
    new_qty = existing_qty + quantity
    new_avg = ((existing_avg * existing_qty) + (price * quantity)) / new_qty
    set_cash(cash - total_cost)
    update_holding(symbol, new_qty, new_avg, total_cost)
    target = round(price * (1 + INTRADAY_PROFIT_TARGET_PCT / 100), 2)
    eod_min = round(price * (1 + EOD_MIN_PROFIT_PCT / 100), 2)
    add_trade("BUY", symbol, price, quantity, total_cost, 0, "MANUAL",
              f"Manual buy | target:₹{target} | eod_min:₹{eod_min}", 0)
    return {
        "message": f"Bought {quantity} share(s) of {symbol}",
        "price": price,
        "invested": total_cost,
        "intraday_target": target,
        "eod_min_sell": eod_min,
        "cash_remaining": round(get_cash(), 2)
    }

@app.post("/sell/{symbol}")
def sell_stock(symbol: str, quantity: int = 1):
    holdings = get_holdings()
    if symbol not in holdings or holdings[symbol]["quantity"] < quantity:
        return {"error": "Not enough shares"}
    price = get_price(symbol)
    if price is None:
        return {"error": "Invalid symbol"}
    avg_buy = holdings[symbol]["avg_buy_price"]
    invested = holdings[symbol]["invested_amount"]
    pnl = (price - avg_buy) * quantity
    pnl_pct = ((price - avg_buy) / avg_buy) * 100
    cash = get_cash()
    sell_value = price * quantity
    set_cash(cash + sell_value)
    new_qty = holdings[symbol]["quantity"] - quantity
    update_holding(symbol, new_qty, avg_buy, invested)
    add_trade("SELL", symbol, price, quantity, invested, 0, "MANUAL",
              f"Manual sell | P&L:{'+' if pnl>=0 else ''}₹{pnl:.2f} ({pnl_pct:+.2f}%)", pnl)
    return {
        "message": f"Sold {quantity} share(s) of {symbol}",
        "price": price,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "cash_balance": round(get_cash(), 2)
    }

@app.post("/eod-sell")
def manual_eod_sell():
    end_of_day_profit_booking()
    return {"message": "EOD profit booking complete", "realized_today": get_todays_realized_pnl()}

@app.get("/portfolio/pnl")
def portfolio_pnl():
    holdings = get_holdings()
    cash = get_cash()
    pnl_details = {}
    total_invested = 0
    total_current = 0

    for symbol, data in holdings.items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        invested = data["invested_amount"]
        if qty == 0:
            continue
        current_price = get_price(symbol)
        if current_price is None:
            continue
        current_value = current_price * qty
        pnl = current_value - invested
        pnl_pct = ((current_price - avg_buy) / avg_buy * 100) if avg_buy > 0 else 0
        total_invested += invested
        total_current += current_value

        target_price = round(avg_buy * (1 + INTRADAY_PROFIT_TARGET_PCT / 100), 2)
        eod_price = round(avg_buy * (1 + EOD_MIN_PROFIT_PCT / 100), 2)
        progress = round((pnl_pct / INTRADAY_PROFIT_TARGET_PCT) * 100, 1)

        pnl_details[symbol] = {
            "quantity": qty,
            "avg_buy_price": round(avg_buy, 2),
            "current_price": current_price,
            "invested": round(invested, 2),
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "intraday_target": target_price,
            "eod_min_sell": eod_price,
            "progress_pct": min(100, max(0, progress)),
            "will_sell_eod": pnl_pct >= EOD_MIN_PROFIT_PCT,
            "is_large_cap": symbol in LARGE_CAP
        }

    total_pnl = total_current - total_invested
    realized_today = get_todays_realized_pnl()

    # Get cumulative realized PnL from daily_pnl table
    daily = get_daily_pnl_db()
    cumulative_realized = daily[0]["cumulative_pnl"] if daily else 0

    overall_pnl = (cash + total_current) - STARTING_CASH
    overall_pnl_pct = (overall_pnl / STARTING_CASH) * 100

    return {
        "holdings": pnl_details,
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_current, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested > 0 else 0, 2),
        "cash": round(cash, 2),
        "portfolio_value": round(cash + total_current, 2),
        "overall_pnl": round(overall_pnl, 2),
        "overall_pnl_pct": round(overall_pnl_pct, 2),
        "realized_today": realized_today,
        "cumulative_realized": round(cumulative_realized, 2),
        "intraday_target_pct": INTRADAY_PROFIT_TARGET_PCT,
        "eod_min_profit_pct": EOD_MIN_PROFIT_PCT,
        "max_holdings": MAX_HOLDINGS,
        "current_holdings": len(holdings)
    }

@app.get("/portfolio/value")
def portfolio_value():
    cash = get_cash()
    holdings = get_holdings()
    total_stock_value = 0.0
    stock_details = {}
    for symbol, data in holdings.items():
        price = get_price(symbol)
        if price is None:
            continue
        value = price * data["quantity"]
        total_stock_value += value
        stock_details[symbol] = {
            "quantity": data["quantity"],
            "current_price": price,
            "total_value": round(value, 2)
        }
    return {
        "cash": round(cash, 2),
        "stocks": stock_details,
        "total_portfolio_value": round(cash + total_stock_value, 2)
    }

@app.get("/daily-pnl")
def get_daily_pnl():
    return get_daily_pnl_db()

@app.get("/daily-trades")
def get_daily_trades():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT type,symbol,price,quantity,invested,confidence,mode,reason,pnl,time
        FROM trades ORDER BY time DESC LIMIT 500""")
    rows = c.fetchall()
    conn.close()

    grouped = defaultdict(list)
    for r in rows:
        date = r[9][:10]
        grouped[date].append({
            "type": r[0], "symbol": r[1], "price": r[2],
            "quantity": r[3], "invested": round(r[4], 2),
            "confidence": r[5], "mode": r[6],
            "reason": r[7], "pnl": round(r[8], 2), "time": r[9]
        })

    result = []
    for date in sorted(grouped.keys(), reverse=True):
        day_trades = grouped[date]
        buys = [t for t in day_trades if t["type"] == "BUY"]
        sells = [t for t in day_trades if t["type"] == "SELL"]
        realized = sum(t["pnl"] for t in sells)
        total_invested = sum(t["invested"] for t in buys)
        result.append({
            "date": date,
            "trades": day_trades,
            "total_trades": len(day_trades),
            "buys": len(buys),
            "sells": len(sells),
            "total_invested_today": round(total_invested, 2),
            "realized_pnl": round(realized, 2)
        })
    return result

@app.get("/trades")
def get_trades_route():
    return get_all_trades()

@app.get("/signals")
def get_signals():
    return signal_log

@app.get("/watchlist")
def get_watchlist_route():
    return get_watchlist_db()

@app.post("/watchlist/add/{symbol}")
def add_watchlist(symbol: str):
    symbol = symbol.upper()
    add_to_watchlist_db(symbol)
    return {"message": f"{symbol} added", "total": len(get_watchlist_db())}

@app.delete("/watchlist/remove/{symbol}")
def remove_watchlist(symbol: str):
    symbol = symbol.upper()
    remove_from_watchlist_db(symbol)
    return {"message": f"{symbol} removed"}

@app.post("/scan")
def trigger_scan():
    auto_trade()
    save_daily_snapshot()
    return {"message": "Scan complete", "signals": signal_log}

@app.get("/stats")
def get_stats():
    trades = get_all_trades()
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    profit_trades = [t for t in sell_trades if t["pnl"] > 0]
    loss_trades = [t for t in sell_trades if t["pnl"] <= 0]
    realized_pnl = sum(t["pnl"] for t in sell_trades)
    win_rate = round((len(profit_trades) / len(sell_trades) * 100) if sell_trades else 0, 1)
    daily = get_daily_pnl_db()
    return {
        "total_trades": len(trades),
        "total_buys": len([t for t in trades if t["type"] == "BUY"]),
        "total_sells": len(sell_trades),
        "profitable_sells": len(profit_trades),
        "loss_sells": len(loss_trades),
        "win_rate_pct": win_rate,
        "realized_pnl": round(realized_pnl, 2),
        "realized_today": get_todays_realized_pnl(),
        "cumulative_pnl": daily[0]["cumulative_pnl"] if daily else 0,
        "auto_trades": len([t for t in trades if t["mode"] == "AUTO"]),
        "manual_trades": len([t for t in trades if t["mode"] == "MANUAL"]),
        "watchlist_size": len(get_watchlist_db()),
        "current_holdings": len(get_holdings()),
        "max_holdings": MAX_HOLDINGS,
        "max_buys_per_scan": MAX_BUYS_PER_SCAN,
        "intraday_target_pct": INTRADAY_PROFIT_TARGET_PCT,
        "eod_min_profit_pct": EOD_MIN_PROFIT_PCT,
        "loss_strategy": "HOLD — never sell at loss"
    }

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()
