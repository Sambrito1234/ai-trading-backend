from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
import pandas as pd
import numpy as np
import sqlite3
import os
from datetime import datetime

app = FastAPI()

# --------------------------------------------------
# CORS
# --------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# CONFIG — your settings
# --------------------------------------------------
STOP_LOSS_PCT      = 1.5    # sell if price drops 1.5% from buy price
PROFIT_TARGET_PCT  = 2.0    # sell only when profit >= 2%
MAX_POSITION_PCT   = 0.15   # max 15% of cash per trade
STARTING_CASH      = 100000.0

# Large cap stocks — require STRONG signal to buy
LARGE_CAP = {
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "SBIN.NS",
    "ICICIBANK.NS", "KOTAKBANK.NS", "LT.NS", "BAJFINANCE.NS",
    "HINDUNILVR.NS", "AXISBANK.NS", "MARUTI.NS", "TITAN.NS",
    "SUNPHARMA.NS", "TATAMOTORS.NS", "NESTLEIND.NS", "ULTRACEMCO.NS",
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META",
    "JPM", "V", "JNJ", "WMT", "UNH", "MA", "HD"
}

# Strong signal threshold (large caps)
STRONG_SIGNAL_MIN_CONFIDENCE = 70

# Decent signal threshold (smaller stocks)
DECENT_SIGNAL_MIN_CONFIDENCE = 55

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
        avg_buy_price REAL DEFAULT 0)""")

    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        symbol TEXT NOT NULL,
        price REAL NOT NULL,
        quantity INTEGER NOT NULL,
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
        pnl REAL DEFAULT 0,
        pnl_pct REAL DEFAULT 0)""")

    c.execute("SELECT COUNT(*) FROM portfolio")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO portfolio (id, cash) VALUES (1, ?)", (STARTING_CASH,))

    c.execute("SELECT COUNT(*) FROM watchlist")
    if c.fetchone()[0] == 0:
        default = [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "SBIN.NS",
            "WIPRO.NS", "ICICIBANK.NS", "BAJFINANCE.NS", "HINDUNILVR.NS",
            "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS", "MARUTI.NS", "TITAN.NS",
            "SUNPHARMA.NS", "TATAMOTORS.NS", "ADANIENT.NS", "ULTRACEMCO.NS",
            "ASIANPAINT.NS", "NESTLEIND.NS",
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META",
            "JPM", "V", "JNJ", "WMT", "UNH", "MA", "HD",
            "BTC-USD", "ETH-USD", "BNB-USD"
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
    c.execute("SELECT symbol, quantity, avg_buy_price FROM holdings WHERE quantity > 0")
    rows = c.fetchall()
    conn.close()
    return {r[0]: {"quantity": r[1], "avg_buy_price": r[2]} for r in rows}

def update_holding(symbol, quantity, avg_buy_price=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if quantity <= 0:
        c.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
    else:
        c.execute("""INSERT INTO holdings (symbol, quantity, avg_buy_price) VALUES (?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET quantity=?, avg_buy_price=?""",
            (symbol, quantity, avg_buy_price, quantity, avg_buy_price))
    conn.commit()
    conn.close()

def add_trade(type_, symbol, price, quantity, confidence, mode, reason="", pnl=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO trades (type,symbol,price,quantity,confidence,mode,reason,pnl,time)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (type_, symbol, price, quantity, confidence, mode, reason, pnl,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_all_trades():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT type,symbol,price,quantity,confidence,mode,reason,pnl,time
        FROM trades ORDER BY id DESC LIMIT 200""")
    rows = c.fetchall()
    conn.close()
    return [{"type":r[0],"symbol":r[1],"price":r[2],"quantity":r[3],
             "confidence":r[4],"mode":r[5],"reason":r[6],"pnl":round(r[7],2),"time":r[8]}
            for r in rows]

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

def save_daily_snapshot():
    cash = get_cash()
    holdings = get_holdings()
    total_value = cash
    for symbol, data in holdings.items():
        price = get_price(symbol)
        if price:
            total_value += price * data["quantity"]
    today = datetime.now().strftime("%Y-%m-%d")
    pnl = total_value - STARTING_CASH
    pnl_pct = (pnl / STARTING_CASH) * 100
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO daily_pnl (date,portfolio_value,cash,pnl,pnl_pct) VALUES (?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET portfolio_value=?,cash=?,pnl=?,pnl_pct=?""",
        (today, total_value, cash, pnl, pnl_pct,
         total_value, cash, pnl, pnl_pct))
    conn.commit()
    conn.close()
    print(f"[SNAPSHOT] {today} — ₹{total_value:.2f} ({pnl_pct:+.2f}%)")

def get_daily_pnl_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT date,portfolio_value,pnl,pnl_pct FROM daily_pnl ORDER BY date DESC LIMIT 30")
    rows = c.fetchall()
    conn.close()
    return [{"date":r[0],"portfolio_value":round(r[1],2),
             "pnl":round(r[2],2),"pnl_pct":round(r[3],2)} for r in rows]

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
# ANALYZE STOCK — returns buy score
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

        # --- BUY SCORING ---
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
        elif sentiment == -1: buy_score -= 3  # block bad news

        # Confidence out of 15
        confidence = min(95, int(50 + (buy_score / 15) * 50))

        # Signal — BUY only (we sell based on profit, not signal)
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
# PROFIT-FIRST SELL CHECKER
# Runs every scan — only sells when profit >= 2%
# --------------------------------------------------
def check_profit_and_stoploss():
    holdings = get_holdings()

    for symbol, data in holdings.items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        if avg_buy == 0 or qty == 0:
            continue

        current_price = get_price(symbol)
        if current_price is None:
            continue

        pnl_pct = ((current_price - avg_buy) / avg_buy) * 100
        pnl_amount = (current_price - avg_buy) * qty
        cash = get_cash()

        # TAKE PROFIT — sell only when >= 2% profit
        if pnl_pct >= PROFIT_TARGET_PCT:
            set_cash(cash + current_price * qty)
            update_holding(symbol, 0)
            add_trade(
                "SELL", symbol, current_price, qty, 100, "AUTO",
                f"PROFIT TARGET hit: +{pnl_pct:.2f}% (bought @ ₹{avg_buy})",
                pnl_amount
            )
            print(f"[PROFIT] {symbol} SOLD @ {current_price} | +{pnl_pct:.2f}% | P&L: +₹{pnl_amount:.2f}")

        # STOP LOSS — cut loss at 1.5%
        elif pnl_pct <= -STOP_LOSS_PCT:
            set_cash(cash + current_price * qty)
            update_holding(symbol, 0)
            add_trade(
                "SELL", symbol, current_price, qty, 100, "AUTO",
                f"STOP LOSS hit: {pnl_pct:.2f}% (bought @ ₹{avg_buy})",
                pnl_amount
            )
            print(f"[STOPLOSS] {symbol} SOLD @ {current_price} | {pnl_pct:.2f}% | Loss: ₹{pnl_amount:.2f}")

        else:
            # Still holding — log current status
            print(f"[HOLD] {symbol} @ {current_price} | P&L: {pnl_pct:+.2f}% | Waiting for {PROFIT_TARGET_PCT}% target")

# --------------------------------------------------
# AUTO-TRADING BOT — BUY LOGIC
# --------------------------------------------------
def auto_trade():
    print(f"\n[BOT] ===== Scan at {datetime.now().strftime('%H:%M:%S')} =====")
    global signal_log

    # STEP 1 — Check profit/stoploss on existing holdings first
    check_profit_and_stoploss()

    # STEP 2 — Scan watchlist for BUY opportunities
    new_signals = []
    watchlist = get_watchlist_db()
    holdings = get_holdings()
    cash = get_cash()

    for symbol in watchlist:
        result = analyze_symbol(symbol)
        if result is None:
            continue

        new_signals.append(result)

        # Skip if already holding this stock
        if symbol in holdings:
            continue

        # Skip if not enough cash
        price = result["current_price"]
        if cash < price:
            continue

        # Skip if position would exceed max %
        if price > cash * MAX_POSITION_PCT:
            continue

        # Skip if bad news sentiment
        if result["sentiment"] == -1:
            print(f"[SKIP] {symbol} — negative news sentiment")
            continue

        confidence = result["confidence"]
        is_large = result["is_large_cap"]

        # SMART BUY LOGIC:
        # Large caps → need strong signal (70%+ confidence)
        # Others → decent signal is enough (55%+ confidence)
        min_conf = STRONG_SIGNAL_MIN_CONFIDENCE if is_large else DECENT_SIGNAL_MIN_CONFIDENCE

        if confidence >= min_conf and result["signal"] == "BUY":
            set_cash(cash - price)
            update_holding(symbol, 1, price)
            add_trade(
                "BUY", symbol, price, 1, confidence, "AUTO",
                f"{'STRONG' if is_large else 'DECENT'} signal | Score:{result['buy_score']} RSI:{result['RSI']} MACD:{'▲' if result['MACD_bullish'] else '▼'} Vol:{'✓' if result['volume_confirms'] else '✗'} News:{result['sentiment']}",
                0
            )
            cash = get_cash()  # refresh after buy
            print(f"[BUY] {symbol} @ ₹{price} | conf:{confidence}% | {'LARGE CAP' if is_large else 'small cap'} | Score:{result['buy_score']}")

    signal_log = new_signals
    print(f"[BOT] Done. {len(new_signals)} scanned | {len(get_holdings())} holdings | Cash: ₹{get_cash():.2f}")

# --------------------------------------------------
# SCHEDULER
# --------------------------------------------------
scheduler = BackgroundScheduler()
scheduler.add_job(auto_trade, "interval", minutes=5, id="auto_trade")
scheduler.add_job(save_daily_snapshot, "cron", hour=10, minute=5, id="nse_snapshot")
scheduler.add_job(save_daily_snapshot, "cron", hour=21, minute=5, id="nyse_snapshot")
scheduler.start()

# --------------------------------------------------
# ROUTES
# --------------------------------------------------
@app.get("/")
def home():
    return {
        "message": "AI Trading Bot — Profit First Strategy",
        "strategy": f"Buy on signal, sell at +{PROFIT_TARGET_PCT}% profit or -{STOP_LOSS_PCT}% loss",
        "large_cap_min_confidence": f"{STRONG_SIGNAL_MIN_CONFIDENCE}%",
        "small_cap_min_confidence": f"{DECENT_SIGNAL_MIN_CONFIDENCE}%",
        "profit_target": f"+{PROFIT_TARGET_PCT}%",
        "stop_loss": f"-{STOP_LOSS_PCT}%",
        "auto_trading": "ACTIVE"
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
        return {"error": "Not enough cash"}
    set_cash(cash - total_cost)
    holdings = get_holdings()
    existing_qty = holdings.get(symbol, {}).get("quantity", 0)
    existing_avg = holdings.get(symbol, {}).get("avg_buy_price", 0)
    new_qty = existing_qty + quantity
    new_avg = ((existing_avg * existing_qty) + (price * quantity)) / new_qty
    update_holding(symbol, new_qty, new_avg)
    add_trade("BUY", symbol, price, quantity, 0, "MANUAL",
              f"Manual buy | target sell: ₹{round(price * (1 + PROFIT_TARGET_PCT/100), 2)}", 0)
    return {
        "message": f"Bought {quantity} share(s) of {symbol}",
        "price": price,
        "target_sell_price": round(price * (1 + PROFIT_TARGET_PCT / 100), 2),
        "stop_loss_price": round(price * (1 - STOP_LOSS_PCT / 100), 2),
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
    pnl = (price - avg_buy) * quantity
    cash = get_cash()
    set_cash(cash + price * quantity)
    new_qty = holdings[symbol]["quantity"] - quantity
    update_holding(symbol, new_qty, avg_buy)
    add_trade("SELL", symbol, price, quantity, 0, "MANUAL",
              f"Manual sell | P&L: {'+'if pnl>=0 else ''}₹{pnl:.2f}", pnl)
    return {
        "message": f"Sold {quantity} share(s) of {symbol}",
        "price": price,
        "pnl": round(pnl, 2),
        "pnl_pct": round(((price - avg_buy) / avg_buy) * 100, 2),
        "cash_balance": round(get_cash(), 2)
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
        if qty == 0:
            continue
        current_price = get_price(symbol)
        if current_price is None:
            continue
        invested = avg_buy * qty
        current_value = current_price * qty
        pnl = current_value - invested
        pnl_pct = ((current_price - avg_buy) / avg_buy * 100) if avg_buy > 0 else 0
        total_invested += invested
        total_current += current_value

        target_price = round(avg_buy * (1 + PROFIT_TARGET_PCT / 100), 2)
        stop_price = round(avg_buy * (1 - STOP_LOSS_PCT / 100), 2)
        distance_to_target = round(target_price - current_price, 2)
        progress_to_target = round((pnl_pct / PROFIT_TARGET_PCT) * 100, 1)

        pnl_details[symbol] = {
            "quantity": qty,
            "avg_buy_price": round(avg_buy, 2),
            "current_price": current_price,
            "invested": round(invested, 2),
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop_loss_price": stop_price,
            "target_sell_price": target_price,
            "distance_to_target": distance_to_target,
            "progress_to_target_pct": min(100, max(0, progress_to_target)),
            "is_large_cap": symbol in LARGE_CAP
        }

    total_pnl = total_current - total_invested
    overall_pnl = (cash + total_current) - STARTING_CASH
    overall_pnl_pct = (overall_pnl / STARTING_CASH) * 100

    # Realized P&L from trade history
    all_trades = get_all_trades()
    realized_pnl = sum(t["pnl"] for t in all_trades if t["type"] == "SELL")

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
        "realized_pnl": round(realized_pnl, 2),
        "profit_target_pct": PROFIT_TARGET_PCT,
        "stop_loss_pct": STOP_LOSS_PCT
    }

@app.get("/daily-pnl")
def get_daily_pnl():
    return get_daily_pnl_db()

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

    return {
        "total_trades": len(trades),
        "total_buys": len([t for t in trades if t["type"] == "BUY"]),
        "total_sells": len(sell_trades),
        "profitable_sells": len(profit_trades),
        "loss_sells": len(loss_trades),
        "win_rate_pct": win_rate,
        "realized_pnl": round(realized_pnl, 2),
        "auto_trades": len([t for t in trades if t["mode"] == "AUTO"]),
        "manual_trades": len([t for t in trades if t["mode"] == "MANUAL"]),
        "watchlist_size": len(get_watchlist_db()),
        "current_holdings": len(get_holdings()),
        "profit_target_pct": PROFIT_TARGET_PCT,
        "stop_loss_pct": STOP_LOSS_PCT
    }


@app.get("/daily-trades")
def get_daily_trades():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT type, symbol, price, quantity, confidence, mode, reason, pnl, time
        FROM trades
        ORDER BY time DESC
        LIMIT 500
    """)
    rows = c.fetchall()
    conn.close()

    # Group by date
    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        date = r[8][:10]  # extract YYYY-MM-DD
        grouped[date].append({
            "type": r[0], "symbol": r[1], "price": r[2],
            "quantity": r[3], "confidence": r[4], "mode": r[5],
            "reason": r[6], "pnl": round(r[7], 2), "time": r[8]
        })

    result = []
    for date in sorted(grouped.keys(), reverse=True):
        day_trades = grouped[date]
        buys = [t for t in day_trades if t["type"] == "BUY"]
        sells = [t for t in day_trades if t["type"] == "SELL"]
        realized = sum(t["pnl"] for t in sells)
        result.append({
            "date": date,
            "trades": day_trades,
            "total_trades": len(day_trades),
            "buys": len(buys),
            "sells": len(sells),
            "realized_pnl": round(realized, 2)
        })

    return result

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()
