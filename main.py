from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime

app = FastAPI()

# --------------------------------------------------
# CORS — allows frontend from anywhere
# --------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# Portfolio — stored in memory (persists while server runs)
# --------------------------------------------------

portfolio = {
    "cash": 100000.0,
    "stocks": {},
    "trade_history": []
}

# --------------------------------------------------
# Watchlist — stocks the bot monitors automatically
# --------------------------------------------------

WATCHLIST = [
    # Indian stocks
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "SBIN.NS",
    "WIPRO.NS", "ICICIBANK.NS", "BAJFINANCE.NS", "HINDUNILVR.NS",
    # US stocks
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META"
]

# --------------------------------------------------
# Signal log — stores latest auto-scan results
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
# Get current price safely
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
# Analyze stock — returns signal, RSI, SMA
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

        if np.isnan(rsi):
            rsi = 50.0
        if np.isnan(sma20):
            sma20 = current_price
        if np.isnan(sma50):
            sma50 = current_price

        # Signal logic: RSI + moving average crossover
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
    except Exception as e:
        return None

# --------------------------------------------------
# AUTO-TRADING BOT — runs on schedule
# --------------------------------------------------

def auto_trade():
    print(f"\n[BOT] Auto-scan started at {datetime.now().strftime('%H:%M:%S')}")
    global signal_log
    new_signals = []

    for symbol in WATCHLIST:
        result = analyze_symbol(symbol)
        if result is None:
            continue

        new_signals.append(result)
        signal = result["signal"]
        price = result["current_price"]
        confidence = result["confidence"]

        # Only act on high-confidence signals
        if signal == "BUY" and confidence >= 65:
            cost = price * 1
            if portfolio["cash"] >= cost:
                portfolio["cash"] -= cost
                portfolio["stocks"][symbol] = portfolio["stocks"].get(symbol, 0) + 1
                trade = {
                    "type": "BUY",
                    "symbol": symbol,
                    "price": price,
                    "quantity": 1,
                    "confidence": confidence,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "mode": "AUTO"
                }
                portfolio["trade_history"].append(trade)
                print(f"[BOT] AUTO BUY  {symbol} @ ₹{price} (RSI {result['RSI']}, conf {confidence}%)")

        elif signal == "SELL" and confidence >= 65:
            if symbol in portfolio["stocks"] and portfolio["stocks"][symbol] > 0:
                portfolio["cash"] += price * 1
                portfolio["stocks"][symbol] -= 1
                if portfolio["stocks"][symbol] == 0:
                    del portfolio["stocks"][symbol]
                trade = {
                    "type": "SELL",
                    "symbol": symbol,
                    "price": price,
                    "quantity": 1,
                    "confidence": confidence,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "mode": "AUTO"
                }
                portfolio["trade_history"].append(trade)
                print(f"[BOT] AUTO SELL {symbol} @ ₹{price} (RSI {result['RSI']}, conf {confidence}%)")

    signal_log = new_signals
    print(f"[BOT] Scan complete. {len(new_signals)} stocks analyzed.")

# --------------------------------------------------
# SCHEDULER — runs auto_trade every 5 minutes
# --------------------------------------------------

scheduler = BackgroundScheduler()
scheduler.add_job(auto_trade, "interval", minutes=5, id="auto_trade_job")
scheduler.start()

# --------------------------------------------------
# ROUTES
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "AI Trading Bot Running",
        "watchlist": WATCHLIST,
        "auto_trading": "ACTIVE",
        "next_scan": "Every 5 minutes"
    }

# --------------------------------------------------
# GET STOCK ANALYSIS
# --------------------------------------------------

@app.get("/stock/{symbol}")
def get_stock(symbol: str):
    result = analyze_symbol(symbol)
    if result is None:
        return {"error": f"Could not fetch data for {symbol}"}
    return result

# --------------------------------------------------
# PRICE HISTORY FOR CHART
# --------------------------------------------------

@app.get("/history/{symbol}")
def stock_history(symbol: str):
    try:
        data = yf.download(symbol, period="1mo", progress=False)
        if data.empty:
            return {"dates": [], "prices": []}

        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.fillna(method="ffill")

        return {
            "dates": [str(d.date()) for d in close.index],
            "prices": [round(float(p), 2) for p in close.tolist()]
        }
    except Exception as e:
        return {"dates": [], "prices": [], "error": str(e)}

# --------------------------------------------------
# CANDLESTICK DATA
# --------------------------------------------------

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
                    "o": round(float(row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"]), 2),
                    "h": round(float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]), 2),
                    "l": round(float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"]), 2),
                    "c": round(float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]), 2),
                })
            except Exception:
                continue
        return candles
    except Exception as e:
        return []

# --------------------------------------------------
# BUY STOCK (manual)
# --------------------------------------------------

@app.post("/buy/{symbol}")
def buy_stock(symbol: str, quantity: int = 1):
    price = get_price(symbol)
    if price is None:
        return {"error": "Invalid symbol or no data"}

    total_cost = price * quantity
    if portfolio["cash"] < total_cost:
        return {"error": "Not enough cash"}

    portfolio["cash"] -= total_cost
    portfolio["stocks"][symbol] = portfolio["stocks"].get(symbol, 0) + quantity

    trade = {
        "type": "BUY",
        "symbol": symbol,
        "price": price,
        "quantity": quantity,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "MANUAL"
    }
    portfolio["trade_history"].append(trade)

    return {
        "message": f"Bought {quantity} share(s) of {symbol}",
        "price": price,
        "cash_remaining": round(portfolio["cash"], 2)
    }

# --------------------------------------------------
# SELL STOCK (manual)
# --------------------------------------------------

@app.post("/sell/{symbol}")
def sell_stock(symbol: str, quantity: int = 1):
    if symbol not in portfolio["stocks"] or portfolio["stocks"][symbol] < quantity:
        return {"error": "Not enough shares to sell"}

    price = get_price(symbol)
    if price is None:
        return {"error": "Invalid symbol or no data"}

    portfolio["cash"] += price * quantity
    portfolio["stocks"][symbol] -= quantity
    if portfolio["stocks"][symbol] == 0:
        del portfolio["stocks"][symbol]

    trade = {
        "type": "SELL",
        "symbol": symbol,
        "price": price,
        "quantity": quantity,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "MANUAL"
    }
    portfolio["trade_history"].append(trade)

    return {
        "message": f"Sold {quantity} share(s) of {symbol}",
        "price": price,
        "cash_balance": round(portfolio["cash"], 2)
    }

# --------------------------------------------------
# PORTFOLIO VALUE
# --------------------------------------------------

@app.get("/portfolio/value")
def portfolio_value():
    total_stock_value = 0.0
    stock_details = {}

    for symbol, quantity in portfolio["stocks"].items():
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
        "cash": round(portfolio["cash"], 2),
        "stocks": stock_details,
        "total_portfolio_value": round(portfolio["cash"] + total_stock_value, 2)
    }

# --------------------------------------------------
# TRADE HISTORY
# --------------------------------------------------

@app.get("/trades")
def get_trades():
    return list(reversed(portfolio["trade_history"]))

# --------------------------------------------------
# SIGNAL LOG — latest auto-scan results
# --------------------------------------------------

@app.get("/signals")
def get_signals():
    return signal_log

# --------------------------------------------------
# WATCHLIST MANAGEMENT
# --------------------------------------------------

@app.get("/watchlist")
def get_watchlist():
    return WATCHLIST

@app.post("/watchlist/add/{symbol}")
def add_to_watchlist(symbol: str):
    symbol = symbol.upper()
    if symbol not in WATCHLIST:
        WATCHLIST.append(symbol)
        return {"message": f"{symbol} added to watchlist"}
    return {"message": f"{symbol} already in watchlist"}

@app.delete("/watchlist/remove/{symbol}")
def remove_from_watchlist(symbol: str):
    symbol = symbol.upper()
    if symbol in WATCHLIST:
        WATCHLIST.remove(symbol)
        return {"message": f"{symbol} removed"}
    return {"error": "Symbol not found"}

# --------------------------------------------------
# TRIGGER MANUAL SCAN
# --------------------------------------------------

@app.post("/scan")
def trigger_scan():
    auto_trade()
    return {"message": "Scan complete", "signals": signal_log}

# --------------------------------------------------
# SHUTDOWN
# --------------------------------------------------

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()
