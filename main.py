from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import yfinance as yf
import pandas as pd
import numpy as np

app = FastAPI()

# -------------------------------
# CORS
# -------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# Portfolio
# -------------------------------

portfolio = {
    "cash": 100000,
    "stocks": {}
}

# -------------------------------
# Helper Functions
# -------------------------------

def calculate_rsi(data, window=14):

    delta = data.diff()

    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()

    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()

    rs = gain / loss

    rsi = 100 - (100 / (1 + rs))

    return rsi

# -------------------------------
# Root API
# -------------------------------

@app.get("/")
def home():

    return {
        "message": "AI Trading Bot Backend Running"
    }

# -------------------------------
# Get Stock Details
# -------------------------------

@app.get("/stock/{symbol}")
def get_stock(symbol: str):

    data = yf.download(symbol, period="3mo")

    if data.empty:
        return {
            "error": "Stock not found"
        }

    close_prices = data["Close"]

    if hasattr(close_prices, "columns"):
        close_prices = close_prices.iloc[:, 0]

    close_prices = close_prices.fillna(0)

    current_price = float(close_prices.iloc[-1])

    sma20 = float(close_prices.rolling(window=20).mean().iloc[-1])

    sma50 = float(close_prices.rolling(window=50).mean().iloc[-1])

    rsi = float(calculate_rsi(close_prices).iloc[-1])

    if np.isnan(rsi):
        rsi = 0

    signal = "HOLD"

    if rsi < 30:
        signal = "BUY"

    elif rsi > 70:
        signal = "SELL"

    return {
        "symbol": symbol,
        "current_price": round(current_price, 2),
        "RSI": round(rsi, 2),
        "SMA20": round(sma20, 2),
        "SMA50": round(sma50, 2),
        "AI_signal": signal
    }

# -------------------------------
# Buy Stock
# -------------------------------

@app.post("/buy/{symbol}")
def buy_stock(symbol: str, quantity: int):

    data = yf.download(symbol, period="1d")

    if data.empty:
        return {
            "error": "Stock not found"
        }

    close_prices = data["Close"]

    if hasattr(close_prices, "columns"):
        close_prices = close_prices.iloc[:, 0]

    current_price = float(close_prices.iloc[-1])

    total_cost = current_price * quantity

    if portfolio["cash"] < total_cost:
        return {
            "message": "Not enough cash"
        }

    portfolio["cash"] -= total_cost

    if symbol in portfolio["stocks"]:
        portfolio["stocks"][symbol] += quantity
    else:
        portfolio["stocks"][symbol] = quantity

    return {
        "message": f"Bought {quantity} shares of {symbol}",
        "cash_remaining": round(portfolio["cash"], 2)
    }

# -------------------------------
# Sell Stock
# -------------------------------

@app.post("/sell/{symbol}")
def sell_stock(symbol: str, quantity: int):

    if symbol not in portfolio["stocks"]:
        return {
            "message": "Stock not owned"
        }

    if portfolio["stocks"][symbol] < quantity:
        return {
            "message": "Not enough shares"
        }

    data = yf.download(symbol, period="1d")

    close_prices = data["Close"]

    if hasattr(close_prices, "columns"):
        close_prices = close_prices.iloc[:, 0]

    current_price = float(close_prices.iloc[-1])

    total_value = current_price * quantity

    portfolio["cash"] += total_value

    portfolio["stocks"][symbol] -= quantity

    if portfolio["stocks"][symbol] == 0:
        del portfolio["stocks"][symbol]

    return {
        "message": f"Sold {quantity} shares of {symbol}",
        "cash_balance": round(portfolio["cash"], 2)
    }

# -------------------------------
# Portfolio Value
# -------------------------------

@app.get("/portfolio/value")
def portfolio_value():

    total_stock_value = 0

    stock_details = {}

    for symbol, quantity in portfolio["stocks"].items():

        try:

            data = yf.download(symbol, period="1d")

            close_prices = data["Close"]

            if hasattr(close_prices, "columns"):
                close_prices = close_prices.iloc[:, 0]

            current_price = float(close_prices.iloc[-1])

            value = current_price * quantity

            total_stock_value += value

            stock_details[symbol] = round(value, 2)

        except:
            stock_details[symbol] = 0

    total_value = portfolio["cash"] + total_stock_value

    return {
        "cash": round(portfolio["cash"], 2),
        "stocks": stock_details,
        "total_portfolio_value": round(total_value, 2)
    }

# -------------------------------
# Stock History Chart
# -------------------------------

@app.get("/history/{symbol}")
def stock_history(symbol: str):

    data = yf.download(symbol, period="1mo")

    if data.empty:
        return {
            "dates": [],
            "prices": []
        }

    close_prices = data["Close"]

    if hasattr(close_prices, "columns"):
        close_prices = close_prices.iloc[:, 0]

    close_prices = close_prices.fillna(0)

    return {
        "dates": [str(date.date()) for date in close_prices.index],
        "prices": [float(price) for price in close_prices.tolist()]
    }


@app.get("/candles/{symbol}")
def get_candles(symbol: str):

    data = yf.download(symbol, period="1mo")

    if data.empty:
        return []

    candles = []

    for index, row in data.iterrows():

        candles.append({
            "x": str(index.date()),
            "o": float(row["Open"]),
            "h": float(row["High"]),
            "l": float(row["Low"]),
            "c": float(row["Close"])
        })

    return candles