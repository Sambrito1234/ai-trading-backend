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
# DATABASE SETUP
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
            quantity INTEGER NOT NULL
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
    c.execute("SELECT symbol, quantity FROM holdings")
    rows = c.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

def update_holding(symbol, quantity):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if quantity <= 0:
        c.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
    else:
        c.execute("""
            INSERT INTO holdings (symbol, quantity) VALUES (?, ?)
            ON CONFLICT(symbol) DO UPDATE SET quantity=?
        """, (symbol, quantity, quantity))
    conn.commit()
    conn.close()

def add_trade(type_, symbol, price, quantity, confidence, mode):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades (type, symbol, price, quantity, confidence, mode, time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (type_, symbol, price, quantity, confidence, mode,
          datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_all_trades():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT type, symbol, price, quantity, confidence, mode, time FROM trades ORDER BY id DESC LIMIT 100")
    rows = c.fetchall()
    conn.close()
    return [
        {"type": r[0], "symbol": r[1], "price": r[2],
         "quantity": r[3], "confidence": r[4], "mode": r[5], "time": r[6]}
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
# RSI Calculator
# --------------------------------------------------

def calculate_rsi(data, window=14):
    delta = data.diff()
    gain = delta.where(delta > 0, 0).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

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
# Analyze stock
# --------------------------------------------------

def analyze_symbol(symbol: str):
    try:
        data = yf.download(symbol, period="3mo", progress=False)
        if data.empty or len(data) < 30:
            return None

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()

        current_price = float(close.iloc[-1])
        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else sma20
        rsi_series = calculate_rsi(close)
        rsi = float(rsi_series.iloc[-1])

        if np.isnan(rsi): rsi = 50.0
        if np.isnan(sma20): sma20 = current_price
        if np.isnan(sma50): sma50 = current_price

        signal = "HOLD"
        confidence = 50

        if rsi < 30 and sma20 >= sma50:
            signal = "BUY"
            confidence = min(95, int(90 - rsi))
        elif rsi < 40 and sma20 > sma50:
            signal = "BUY"
            confidence = int(60 + (40 - rsi))
        elif rsi > 70 and sma20 <= sma50:
            signal = "SELL"
            confidence = min(95, int(rsi - 30))
        elif rsi > 60 and sma20 < sma50:
            signal = "SELL"
            confidence = int(40 + (rsi - 60))

        return {
            "symbol": symbol,
            "current_price": round(current_price, 2),
            "RSI": round(rsi, 2),
            "SMA20": round(sma20, 2),
            "SMA50": round(sma50, 2),
            "signal": signal,
            "confidence": confidence,
            "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception:
        return None

# --------------------------------------------------
# AUTO-TRADING BOT
# --------------------------------------------------

def auto_trade():
    print(f"\n[BOT] Auto-scan at {datetime.now().strftime('%H:%M:%S')}")
    global signal_log
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

        if signal == "BUY" and confidence >= 65:
            cost = price * 1
            already_owns = holdings.get(symbol, 0) > 0
            max_per_trade = cash * 0.20
            if cash >= cost and not already_owns and cost <= max_per_trade:
                set_cash(cash - cost)
                update_holding(symbol, 1)
                add_trade("BUY", symbol, price, 1, confidence, "AUTO")
                print(f"[BOT] BUY  {symbol} @ {price} (conf {confidence}%)")

        elif signal == "SELL" and confidence >= 65:
            qty = holdings.get(symbol, 0)
            if qty > 0:
                set_cash(cash + price)
                update_holding(symbol, qty - 1)
                add_trade("SELL", symbol, price, 1, confidence, "AUTO")
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
        "scan_interval": "Every 5 minutes"
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

@app.get("/candles/{symbol}")
def get_candles(symbol: str):
    try:
        data = yf.download(symbol, period="1mo", progress=False)
        if data.empty:
            return []
        candles = []
        for idx, row in data.iterrows():
            try:
                candles.append({
                    "x": str(idx.date()),
                    "o": round(float(row["Open"]), 2),
                    "h": round(float(row["High"]), 2),
                    "l": round(float(row["Low"]), 2),
                    "c": round(float(row["Close"]), 2),
                })
            except Exception:
                continue
        return candles
    except Exception:
        return []

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
    update_holding(symbol, holdings.get(symbol, 0) + quantity)
    add_trade("BUY", symbol, price, quantity, 0, "MANUAL")
    return {
        "message": f"Bought {quantity} share(s) of {symbol}",
        "price": price,
        "cash_remaining": round(get_cash(), 2)
    }

@app.post("/sell/{symbol}")
def sell_stock(symbol: str, quantity: int = 1):
    holdings = get_holdings()
    if holdings.get(symbol, 0) < quantity:
        return {"error": "Not enough shares"}
    price = get_price(symbol)
    if price is None:
        return {"error": "Invalid symbol"}
    cash = get_cash()
    set_cash(cash + price * quantity)
    update_holding(symbol, holdings[symbol] - quantity)
    add_trade("SELL", symbol, price, quantity, 0, "MANUAL")
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

    for symbol, quantity in holdings.items():
        price = get_price(symbol)
        if price is None:
            continue
        value = price * quantity
        total_stock_value += value
        stock_details[symbol] = {
            "quantity": quantity,
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
    all_trades = get_all_trades()
    cash = get_cash()

    pnl_details = {}
    total_invested = 0
    total_current = 0

    for symbol, quantity in holdings.items():
        buy_trades = [t for t in all_trades if t["symbol"] == symbol and t["type"] == "BUY"]
        if not buy_trades:
            continue

        avg_buy_price = sum(t["price"] for t in buy_trades) / len(buy_trades)
        current_price = get_price(symbol)
        if current_price is None:
            continue

        invested = avg_buy_price * quantity
        current_value = current_price * quantity
        pnl = current_value - invested
        pnl_pct = ((current_price - avg_buy_price) / avg_buy_price) * 100

        total_invested += invested
        total_current += current_value

        pnl_details[symbol] = {
            "quantity": quantity,
            "avg_buy_price": round(avg_buy_price, 2),
            "current_price": current_price,
            "invested": round(invested, 2),
            "current_value": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2)
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
