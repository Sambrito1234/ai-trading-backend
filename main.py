from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
import pandas as pd
import numpy as np
import sqlite3
import requests
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
# CONFIG
# --------------------------------------------------

STOP_LOSS_PCT = 2.5        # Sell if price drops 2.5% from buy price
TAKE_PROFIT_PCT = 5.0      # Sell if price rises 5% from buy price
MIN_CONFIDENCE = 65        # Minimum confidence to trade
MAX_POSITION_PCT = 0.20    # Max 20% of cash per trade

# --------------------------------------------------
# DATABASE
# --------------------------------------------------

DB_PATH = "trading.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY,
            cash REAL NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS holdings (
            symbol TEXT PRIMARY KEY,
            quantity INTEGER NOT NULL,
            avg_buy_price REAL DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            confidence INTEGER DEFAULT 0,
            mode TEXT NOT NULL,
            reason TEXT DEFAULT '',
            time TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol TEXT PRIMARY KEY
        )
    """)

    c.execute("SELECT COUNT(*) FROM portfolio")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO portfolio (id, cash) VALUES (1, 100000.0)")

    c.execute("SELECT COUNT(*) FROM watchlist")
    if c.fetchone()[0] == 0:
        default = [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "SBIN.NS",
            "WIPRO.NS", "ICICIBANK.NS", "BAJFINANCE.NS", "HINDUNILVR.NS",
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META"
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
    c.execute("SELECT symbol, quantity, avg_buy_price FROM holdings")
    rows = c.fetchall()
    conn.close()
    return {row[0]: {"quantity": row[1], "avg_buy_price": row[2]} for row in rows}

def update_holding(symbol, quantity, avg_buy_price=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if quantity <= 0:
        c.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
    else:
        c.execute("""
            INSERT INTO holdings (symbol, quantity, avg_buy_price) VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET quantity=?, avg_buy_price=?
        """, (symbol, quantity, avg_buy_price, quantity, avg_buy_price))
    conn.commit()
    conn.close()

def add_trade(type_, symbol, price, quantity, confidence, mode, reason=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (type, symbol, price, quantity, confidence, mode, reason, time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (type_, symbol, price, quantity, confidence, mode, reason,
          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_all_trades():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT type, symbol, price, quantity, confidence, mode, reason, time FROM trades ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    return [
        {"type": r[0], "symbol": r[1], "price": r[2], "quantity": r[3],
         "confidence": r[4], "mode": r[5], "reason": r[6], "time": r[7]}
        for r in rows
    ]

def get_watchlist_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol FROM watchlist")
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
    histogram = macd - signal
    return macd, signal, histogram

def get_volume_signal(data):
    """Returns True if volume confirms the move"""
    try:
        volume = data["Volume"]
        if isinstance(volume, pd.DataFrame):
            volume = volume.iloc[:, 0]
        avg_volume = float(volume.rolling(20).mean().iloc[-1])
        current_volume = float(volume.iloc[-1])
        return current_volume > avg_volume * 1.2  # 20% above average
    except Exception:
        return False

def get_news_sentiment(symbol):
    """
    Returns sentiment score:
     1 = positive
     0 = neutral
    -1 = negative
    Uses Yahoo Finance news via yfinance
    """
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        if not news:
            return 0

        positive_words = ["surge", "rise", "gain", "profit", "growth", "beat",
                         "record", "strong", "bullish", "upgrade", "buy", "rally"]
        negative_words = ["fall", "drop", "loss", "decline", "miss", "weak",
                         "bearish", "downgrade", "sell", "crash", "cut", "warning"]

        score = 0
        count = 0
        for article in news[:5]:
            title = article.get("title", "").lower()
            for word in positive_words:
                if word in title:
                    score += 1
            for word in negative_words:
                if word in title:
                    score -= 1
            count += 1

        if count == 0:
            return 0
        avg = score / count
        if avg > 0:
            return 1
        elif avg < 0:
            return -1
        return 0
    except Exception:
        return 0

# --------------------------------------------------
# Get current price
# --------------------------------------------------

def get_price(symbol: str):
    try:
        data = yf.download(symbol, period="1d", progress=False)
        if data.empty:
            return None
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        price = float(close.iloc[-1])
        if np.isnan(price):
            return None
        return round(price, 2)
    except Exception:
        return None

# --------------------------------------------------
# FULL ANALYSIS — RSI + MACD + Volume + Sentiment
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

        # RSI
        rsi_series = calculate_rsi(close)
        rsi = float(rsi_series.iloc[-1])
        if np.isnan(rsi):
            rsi = 50.0

        # SMA
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        if np.isnan(sma20): sma20 = current_price
        if np.isnan(sma50): sma50 = current_price

        # MACD
        macd, macd_signal, macd_hist = calculate_macd(close)
        macd_val = float(macd.iloc[-1])
        macd_sig = float(macd_signal.iloc[-1])
        macd_hist_val = float(macd_hist.iloc[-1])
        macd_bullish = macd_val > macd_sig and macd_hist_val > 0
        macd_bearish = macd_val < macd_sig and macd_hist_val < 0

        # Volume
        volume_confirms = get_volume_signal(data)

        # News Sentiment
        sentiment = get_news_sentiment(symbol)

        # --- SCORING SYSTEM ---
        buy_score = 0
        sell_score = 0

        # RSI signals
        if rsi < 30: buy_score += 3
        elif rsi < 40: buy_score += 1
        if rsi > 70: sell_score += 3
        elif rsi > 60: sell_score += 1

        # SMA signals
        if sma20 > sma50: buy_score += 2
        else: sell_score += 2

        # MACD signals
        if macd_bullish: buy_score += 2
        if macd_bearish: sell_score += 2

        # Volume confirmation
        if volume_confirms:
            buy_score += 1
            sell_score += 1

        # News sentiment
        if sentiment == 1: buy_score += 2
        elif sentiment == -1: sell_score += 2

        # --- SIGNAL DECISION ---
        max_score = 10
        signal = "HOLD"
        confidence = 50

        if buy_score > sell_score and buy_score >= 4:
            signal = "BUY"
            confidence = min(95, int(50 + (buy_score / max_score) * 50))
        elif sell_score > buy_score and sell_score >= 4:
            signal = "SELL"
            confidence = min(95, int(50 + (sell_score / max_score) * 50))

        # Block buy if negative sentiment
        if sentiment == -1 and signal == "BUY":
            signal = "HOLD"
            confidence = 40

        return {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "RSI": round(rsi, 2),
            "SMA20": round(sma20, 2),
            "SMA50": round(sma50, 2),
            "MACD": round(macd_val, 4),
            "MACD_signal": round(macd_sig, 4),
            "MACD_bullish": macd_bullish,
            "volume_confirms": volume_confirms,
            "sentiment": sentiment,
            "signal": signal,
            "confidence": confidence,
            "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"[ERROR] analyze_symbol {symbol}: {e}")
        return None

# --------------------------------------------------
# STOP LOSS + TAKE PROFIT CHECKER
# --------------------------------------------------

def check_stop_loss_take_profit():
    holdings = get_holdings()
    cash = get_cash()

    for symbol, data in holdings.items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        if avg_buy == 0 or qty == 0:
            continue

        current_price = get_price(symbol)
        if current_price is None:
            continue

        pnl_pct = ((current_price - avg_buy) / avg_buy) * 100

        # STOP LOSS
        if pnl_pct <= -STOP_LOSS_PCT:
            set_cash(cash + current_price * qty)
            update_holding(symbol, 0)
            add_trade("SELL", symbol, current_price, qty, 100, "AUTO",
                     f"STOP LOSS triggered at {pnl_pct:.1f}%")
            print(f"[STOP LOSS] {symbol} sold at {current_price} (loss {pnl_pct:.1f}%)")

        # TAKE PROFIT
        elif pnl_pct >= TAKE_PROFIT_PCT:
            set_cash(cash + current_price * qty)
            update_holding(symbol, 0)
            add_trade("SELL", symbol, current_price, qty, 100, "AUTO",
                     f"TAKE PROFIT triggered at +{pnl_pct:.1f}%")
            print(f"[TAKE PROFIT] {symbol} sold at {current_price} (gain +{pnl_pct:.1f}%)")

# --------------------------------------------------
# AUTO-TRADING BOT
# --------------------------------------------------

def auto_trade():
    print(f"\n[BOT] Scan at {datetime.now().strftime('%H:%M:%S')}")
    global signal_log

    # First check stop loss / take profit on existing holdings
    check_stop_loss_take_profit()

    new_signals = []
    watchlist = get_watchlist_db()

    for symbol in watchlist:
        result = analyze_symbol(symbol)
        if result is None:
            continue

        new_signals.append(result)
        signal = result["signal"]
        price = result["current_price"]
        confidence = result["confidence"]
        holdings = get_holdings()
        cash = get_cash()

        if signal == "BUY" and confidence >= MIN_CONFIDENCE:
            cost = price * 1
            already_owns = symbol in holdings and holdings[symbol]["quantity"] > 0
            max_per_trade = cash * MAX_POSITION_PCT
            if cash >= cost and not already_owns and cost <= max_per_trade:
                set_cash(cash - cost)
                update_holding(symbol, 1, price)
                add_trade("BUY", symbol, price, 1, confidence, "AUTO",
                         f"RSI:{result['RSI']} MACD:{'▲' if result['MACD_bullish'] else '▼'} Vol:{'✓' if result['volume_confirms'] else '✗'} Sentiment:{result['sentiment']}")
                print(f"[BOT] BUY  {symbol} @ {price} (conf {confidence}%)")

        elif signal == "SELL" and confidence >= MIN_CONFIDENCE:
            if symbol in holdings and holdings[symbol]["quantity"] > 0:
                qty = holdings[symbol]["quantity"]
                set_cash(cash + price * qty)
                update_holding(symbol, 0)
                add_trade("SELL", symbol, price, qty, confidence, "AUTO",
                         f"RSI:{result['RSI']} MACD:{'▲' if result['MACD_bullish'] else '▼'}")
                print(f"[BOT] SELL {symbol} @ {price} (conf {confidence}%)")

    signal_log = new_signals
    print(f"[BOT] Done. {len(new_signals)} stocks scanned.")

# --------------------------------------------------
# SCHEDULER
# --------------------------------------------------

scheduler = BackgroundScheduler()
scheduler.add_job(auto_trade, "interval", minutes=5, id="auto_trade")
scheduler.start()

# --------------------------------------------------
# ROUTES
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "AI Trading Bot Running",
        "auto_trading": "ACTIVE",
        "stop_loss": f"{STOP_LOSS_PCT}%",
        "take_profit": f"{TAKE_PROFIT_PCT}%",
        "min_confidence": f"{MIN_CONFIDENCE}%"
    }

@app.get("/stock/{symbol}")
def get_stock(symbol: str):
    result = analyze_symbol(symbol)
    if result is None:
        return {"error": f"Could not fetch {symbol}"}
    return result

@app.get("/history/{symbol}")
def stock_history(symbol: str):
    try:
        data = yf.download(symbol, period="1mo", progress=False)
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
    add_trade("BUY", symbol, price, quantity, 0, "MANUAL", "Manual buy")
    return {
        "message": f"Bought {quantity} share(s) of {symbol}",
        "price": price,
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
    cash = get_cash()
    set_cash(cash + price * quantity)
    new_qty = holdings[symbol]["quantity"] - quantity
    update_holding(symbol, new_qty, holdings[symbol]["avg_buy_price"])
    add_trade("SELL", symbol, price, quantity, 0, "MANUAL", "Manual sell")
    return {
        "message": f"Sold {quantity} share(s) of {symbol}",
        "price": price,
        "cash_balance": round(get_cash(), 2)
    }

@app.get("/portfolio/value")
def portfolio_value():
    cash = get_cash()
    holdings = get_holdings()
    total_stock_value = 0.0
    stock_details = {}

    for symbol, data in holdings.items():
        if data["quantity"] == 0:
            continue
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

        # Stop loss / take profit distances
        sl_price = round(avg_buy * (1 - STOP_LOSS_PCT / 100), 2)
        tp_price = round(avg_buy * (1 + TAKE_PROFIT_PCT / 100), 2)

        pnl_details[symbol] = {
            "quantity": qty,
            "avg_buy_price": round(avg_buy, 2),
            "current_price": current_price,
            "invested": round(invested, 2),
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "stop_loss_price": sl_price,
            "take_profit_price": tp_price
        }

    total_pnl = total_current - total_invested
    return {
        "holdings": pnl_details,
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_current, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested > 0 else 0, 2),
        "cash": round(cash, 2),
        "portfolio_value": round(cash + total_current, 2)
    }

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
    return {"message": f"{symbol} added"}

@app.delete("/watchlist/remove/{symbol}")
def remove_watchlist(symbol: str):
    symbol = symbol.upper()
    remove_from_watchlist_db(symbol)
    return {"message": f"{symbol} removed"}

@app.post("/scan")
def trigger_scan():
    auto_trade()
    return {"message": "Scan complete", "signals": signal_log}

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()
