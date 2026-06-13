from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
import yfinance as yf
import pandas as pd
import numpy as np
import sqlite3
import gc
from datetime import datetime
from collections import defaultdict

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --------------------------------------------------
# CONFIG
# --------------------------------------------------
INTRADAY_PROFIT_TARGET_PCT = 2.0
EOD_MIN_PROFIT_PCT         = 0.5
MAX_HOLDINGS               = 10
MAX_BUYS_PER_SCAN          = 3
MAX_QTY_PER_TRADE          = 10
SCAN_BATCH_SIZE            = 200
STARTING_CASH              = 100000.0
STRONG_CONF                = 70
DECENT_CONF                = 60

LARGE_CAP = {
    "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","SBIN.NS","ICICIBANK.NS",
    "KOTAKBANK.NS","LT.NS","BAJFINANCE.NS","HINDUNILVR.NS","AXISBANK.NS",
    "MARUTI.NS","TITAN.NS","SUNPHARMA.NS","TATAMOTORS.NS","NESTLEIND.NS",
    "ULTRACEMCO.NS","ASIANPAINT.NS","AAPL","MSFT","GOOGL","AMZN","NVDA",
    "TSLA","META","JPM","V","JNJ","WMT","UNH","MA","HD"
}

TOP_200 = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","BHARTIARTL.NS","ICICIBANK.NS",
    "INFOSYS.NS","SBIN.NS","HINDUNILVR.NS","ITC.NS","KOTAKBANK.NS",
    "LT.NS","HCLTECH.NS","BAJFINANCE.NS","AXISBANK.NS","ASIANPAINT.NS",
    "MARUTI.NS","TITAN.NS","SUNPHARMA.NS","WIPRO.NS","NTPC.NS",
    "ONGC.NS","POWERGRID.NS","ULTRACEMCO.NS","NESTLEIND.NS","TATAMOTORS.NS",
    "ADANIENT.NS","BAJAJFINSV.NS","TECHM.NS","DRREDDY.NS","DIVISLAB.NS",
    "CIPLA.NS","EICHERMOT.NS","HEROMOTOCO.NS","BPCL.NS","COALINDIA.NS",
    "GRASIM.NS","INDUSINDBK.NS","SBILIFE.NS","HDFCLIFE.NS","BRITANNIA.NS",
    "TATACONSUM.NS","APOLLOHOSP.NS","ADANIPORTS.NS","JSWSTEEL.NS","TATASTEEL.NS",
    "HINDALCO.NS","BAJAJ-AUTO.NS","DABUR.NS","GODREJCP.NS","MARICO.NS",
    "PIDILITIND.NS","BERGEPAINT.NS","HAVELLS.NS","MUTHOOTFIN.NS","CHOLAFIN.NS",
    "PFC.NS","RECLTD.NS","IRFC.NS","TATAPOWER.NS","TORNTPOWER.NS",
    "JSPL.NS","BANKBARODA.NS","PNB.NS","CANBK.NS","FEDERALBNK.NS",
    "IDFCFIRSTB.NS","BANDHANBNK.NS","SHRIRAMFIN.NS","M&MFIN.NS","ZOMATO.NS",
    "IRCTC.NS","HAL.NS","BEL.NS","CONCOR.NS","JUBLFOOD.NS",
    "DMART.NS","TRENT.NS","LUPIN.NS","AUROPHARMA.NS","TORNTPHARM.NS",
    "ALKEM.NS","MPHASIS.NS","LTTS.NS","PERSISTENT.NS","COFORGE.NS",
    "DLF.NS","GODREJPROP.NS","OBEROIRLTY.NS","TATACHEM.NS","DEEPAKNTR.NS",
    "CHAMBLFERT.NS","COROMANDEL.NS","VEDL.NS","NMDC.NS","NATIONALUM.NS",
    "HINDCOPPER.NS","M&M.NS","VOLTAS.NS","WHIRLPOOL.NS","DIXON.NS",
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","JPM","LLY",
    "V","UNH","XOM","MA","JNJ","PG","COST","HD","MRK","WMT",
    "ABBV","CVX","KO","BAC","PEP","NFLX","ORCL","ACN","TMO","CRM",
    "AMD","CSCO","ABT","MCD","NKE","DHR","TXN","NEE","PM","INTC",
    "WFC","INTU","AMGN","MS","GS","RTX","SPGI","CAT","BLK","HON",
    "ISRG","GE","ADP","BKNG","PFE","LOW","VRTX","DE","SCHW","TJX",
    "SYK","REGN","GILD","BMY","ETN","MU","SO","DUK","CI","CME",
    "PYPL","F","GM","USB","EQIX","NOC","APD","WM","GD","TGT",
    "PLD","SHW","MCO","FDX","UBER","SHOP","PLTR","SNOW","DDOG","CRWD",
    "BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD",
    "ADA-USD","AVAX-USD","DOGE-USD","DOT-USD","MATIC-USD",
    "LINK-USD","LTC-USD","ATOM-USD","XLM-USD","ALGO-USD",
]

def get_position_size(confidence, cash):
    if confidence >= 85: pct = 0.15
    elif confidence >= 75: pct = 0.12
    elif confidence >= 65: pct = 0.08
    else: pct = 0.05
    return round(cash * pct, 2)

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
        symbol TEXT PRIMARY KEY, quantity INTEGER NOT NULL,
        avg_buy_price REAL DEFAULT 0, invested_amount REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL, symbol TEXT NOT NULL, price REAL NOT NULL,
        quantity INTEGER NOT NULL, invested REAL DEFAULT 0,
        confidence INTEGER DEFAULT 0, mode TEXT NOT NULL,
        reason TEXT DEFAULT '', pnl REAL DEFAULT 0, time TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY)""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily_pnl (
        date TEXT PRIMARY KEY, portfolio_value REAL NOT NULL,
        cash REAL NOT NULL, realized_pnl_today REAL DEFAULT 0,
        cumulative_pnl REAL DEFAULT 0, pnl_pct REAL DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS loss_cooldown (
        symbol TEXT NOT NULL, date TEXT NOT NULL,
        PRIMARY KEY (symbol, date))""")
    c.execute("SELECT COUNT(*) FROM portfolio")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO portfolio (id, cash) VALUES (1, ?)", (STARTING_CASH,))
    c.execute("SELECT COUNT(*) FROM watchlist")
    if c.fetchone()[0] == 0:
        for sym in TOP_200:
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
    c.execute("SELECT symbol,quantity,avg_buy_price,invested_amount FROM holdings WHERE quantity>0")
    rows = c.fetchall()
    conn.close()
    return {r[0]:{"quantity":r[1],"avg_buy_price":r[2],"invested_amount":r[3]} for r in rows}

def update_holding(symbol, quantity, avg_buy_price=0, invested_amount=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if quantity <= 0:
        c.execute("DELETE FROM holdings WHERE symbol=?", (symbol,))
    else:
        c.execute("""INSERT INTO holdings (symbol,quantity,avg_buy_price,invested_amount)
            VALUES(?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
            quantity=?,avg_buy_price=?,invested_amount=?""",
            (symbol,quantity,avg_buy_price,invested_amount,
             quantity,avg_buy_price,invested_amount))
    conn.commit()
    conn.close()

def add_trade(type_,symbol,price,quantity,invested,confidence,mode,reason="",pnl=0):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO trades
        (type,symbol,price,quantity,invested,confidence,mode,reason,pnl,time)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (type_,symbol,price,quantity,invested,confidence,mode,reason,pnl,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_all_trades(limit=500):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT type,symbol,price,quantity,invested,confidence,mode,reason,pnl,time
        FROM trades ORDER BY id DESC LIMIT ?""", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{"type":r[0],"symbol":r[1],"price":r[2],"quantity":r[3],
             "invested":round(r[4],2),"confidence":r[5],"mode":r[6],
             "reason":r[7],"pnl":round(r[8],2),"time":r[9]} for r in rows]

def get_todays_realized_pnl():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE type='SELL' AND time LIKE ?",
              (f"{today}%",))
    result = c.fetchone()[0]
    conn.close()
    return round(result, 2)

def get_all_realized_pnl():
    """Always calculated from trades table — survives Render restarts"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE type='SELL'")
    result = c.fetchone()[0]
    conn.close()
    return round(result, 2)

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
    c.execute("INSERT OR IGNORE INTO loss_cooldown (symbol,date) VALUES(?,?)",(symbol,today))
    conn.commit()
    conn.close()

def is_on_cooldown(symbol):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT 1 FROM loss_cooldown WHERE symbol=? AND date=?",(symbol,today))
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
    cash = get_cash()
    holdings = get_holdings()
    total_value = cash
    for symbol, data in holdings.items():
        price = get_price(symbol)
        if price:
            total_value += price * data["quantity"]
    today = datetime.now().strftime("%Y-%m-%d")
    realized_today = get_todays_realized_pnl()
    # Cumulative always from trades table — never resets on restart
    cumulative_pnl = get_all_realized_pnl()
    pnl_pct = (cumulative_pnl / STARTING_CASH) * 100
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO daily_pnl
        (date,portfolio_value,cash,realized_pnl_today,cumulative_pnl,pnl_pct)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
        portfolio_value=?,cash=?,realized_pnl_today=?,cumulative_pnl=?,pnl_pct=?""",
        (today,total_value,cash,realized_today,cumulative_pnl,pnl_pct,
         total_value,cash,realized_today,cumulative_pnl,pnl_pct))
    conn.commit()
    conn.close()
    print(f"[SNAP] {today} | Val:₹{total_value:.0f} | Today:₹{realized_today:.0f} | Cumul:₹{cumulative_pnl:.0f}")

def get_daily_pnl_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT date,portfolio_value,realized_pnl_today,cumulative_pnl,pnl_pct
        FROM daily_pnl ORDER BY date ASC""")
    rows = c.fetchall()
    conn.close()
    return [{"date":r[0],"portfolio_value":round(r[1],2),
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
    gain = delta.where(delta>0,0).rolling(window=window).mean()
    loss = (-delta.where(delta<0,0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1+rs))

def calculate_macd(close):
    ema12 = close.ewm(span=12,adjust=False).mean()
    ema26 = close.ewm(span=26,adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9,adjust=False).mean()
    return macd, signal, macd-signal

def get_volume_signal(data):
    try:
        vol = data["Volume"]
        if isinstance(vol,pd.DataFrame): vol = vol.iloc[:,0]
        avg = float(vol.rolling(20).mean().iloc[-1])
        cur = float(vol.iloc[-1])
        return cur > avg * 1.2
    except: return False

def get_news_sentiment(symbol):
    try:
        news = yf.Ticker(symbol).news
        if not news: return 0
        pos = ["surge","rise","gain","profit","growth","beat","record","strong","bullish","upgrade","rally"]
        neg = ["fall","drop","loss","decline","miss","weak","bearish","downgrade","crash","warning","risk"]
        score = 0
        for a in news[:5]:
            title = a.get("title","").lower()
            for w in pos:
                if w in title: score += 1
            for w in neg:
                if w in title: score -= 1
        return 1 if score > 0 else (-1 if score < 0 else 0)
    except: return 0

def get_price(symbol:str):
    try:
        data = yf.download(symbol,period="1d",progress=False)
        if data.empty: return None
        close = data["Close"]
        if isinstance(close,pd.DataFrame): close = close.iloc[:,0]
        price = float(close.iloc[-1])
        del data
        gc.collect()
        return None if np.isnan(price) else round(price,2)
    except: return None

def analyze_symbol(symbol:str):
    data = None
    try:
        data = yf.download(symbol,period="3mo",progress=False)
        if data.empty or len(data) < 20: return None
        close = data["Close"]
        if isinstance(close,pd.DataFrame): close = close.iloc[:,0]
        close = close.dropna()
        if len(close) < 20: return None

        current_price = float(close.iloc[-1])
        rsi = float(calculate_rsi(close).iloc[-1])
        if np.isnan(rsi): rsi = 50.0

        sma20 = float(close.rolling(20).mean().iloc[-1])
        sma50 = float(close.rolling(min(50,len(close))).mean().iloc[-1])
        if np.isnan(sma20): sma20 = current_price
        if np.isnan(sma50): sma50 = current_price

        macd,macd_sig,macd_hist = calculate_macd(close)
        macd_bullish = float(macd.iloc[-1]) > float(macd_sig.iloc[-1]) and float(macd_hist.iloc[-1]) > 0

        sma20s = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_u = float((sma20s+2*std20).iloc[-1])
        bb_l = float((sma20s-2*std20).iloc[-1])
        bb_pos = (current_price-bb_l)/(bb_u-bb_l) if bb_u!=bb_l else 0.5

        vol_ok = get_volume_signal(data)
        sentiment = get_news_sentiment(symbol)

        score = 0
        if rsi < 25: score += 4
        elif rsi < 35: score += 3
        elif rsi < 45: score += 1
        if sma20 > sma50: score += 2
        if macd_bullish: score += 3
        if bb_pos < 0.2: score += 2
        elif bb_pos < 0.35: score += 1
        if vol_ok: score += 1
        if sentiment == 1: score += 2
        elif sentiment == -1: score -= 3

        confidence = min(95, int(50 + (score/15)*50))

        # Strict: MACD bullish OR very oversold
        strong_enough = (
            (macd_bullish and score >= 5) or
            (rsi < 25 and score >= 4) or
            (rsi < 30 and macd_bullish)
        )
        signal = "BUY" if strong_enough else "WATCH"

        return {
            "symbol":symbol, "current_price":round(current_price,2),
            "RSI":round(rsi,2), "SMA20":round(sma20,2), "SMA50":round(sma50,2),
            "MACD_bullish":macd_bullish, "BB_position":round(bb_pos*100,1),
            "volume_confirms":vol_ok, "sentiment":sentiment,
            "buy_score":score, "signal":signal, "confidence":confidence,
            "is_large_cap":symbol in LARGE_CAP,
            "scanned_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"[ERR] {symbol}: {e}")
        return None
    finally:
        del data
        gc.collect()

# --------------------------------------------------
# INTRADAY PROFIT CHECK — NEVER sells at loss
# --------------------------------------------------
def check_intraday_profit():
    for symbol, data in get_holdings().items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        invested = data["invested_amount"]
        if avg_buy==0 or qty==0: continue
        current_price = get_price(symbol)
        if current_price is None: continue
        pnl_pct = ((current_price-avg_buy)/avg_buy)*100
        pnl_amount = (current_price-avg_buy)*qty
        if pnl_pct >= INTRADAY_PROFIT_TARGET_PCT:
            sell_val = current_price*qty
            set_cash(get_cash()+sell_val)
            update_holding(symbol,0)
            add_trade("SELL",symbol,current_price,qty,invested,100,"AUTO",
                f"INTRADAY +{pnl_pct:.2f}% invested:Rs{invested:.0f} sold:Rs{sell_val:.0f}",pnl_amount)
            print(f"[PROFIT] {symbol} +{pnl_pct:.2f}% Rs{pnl_amount:.2f}")
        elif pnl_pct < 0:
            print(f"[HOLD] {symbol} {pnl_pct:.2f}% — NO sell")

# --------------------------------------------------
# EOD PROFIT BOOKING
# --------------------------------------------------
def end_of_day_profit_booking():
    print(f"\n[EOD] {datetime.now().strftime('%H:%M:%S')}")
    sold = held = profit = 0
    for symbol, data in get_holdings().items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        invested = data["invested_amount"]
        if avg_buy==0 or qty==0: continue
        current_price = get_price(symbol)
        if current_price is None: continue
        pnl_pct = ((current_price-avg_buy)/avg_buy)*100
        pnl_amount = (current_price-avg_buy)*qty
        if pnl_pct >= EOD_MIN_PROFIT_PCT:
            sell_val = current_price*qty
            set_cash(get_cash()+sell_val)
            update_holding(symbol,0)
            add_trade("SELL",symbol,current_price,qty,invested,100,"AUTO",
                f"EOD +{pnl_pct:.2f}% Rs{invested:.0f}->Rs{sell_val:.0f}",pnl_amount)
            profit += pnl_amount
            sold += 1
            print(f"[EOD SELL] {symbol} +{pnl_pct:.2f}% Rs{pnl_amount:.2f}")
        else:
            add_loss_cooldown(symbol)
            held += 1
            print(f"[EOD HOLD] {symbol} {pnl_pct:.2f}%")
    save_daily_snapshot()
    print(f"[EOD] Sold:{sold} Held:{held} Profit:Rs{profit:.2f}")

# --------------------------------------------------
# AUTO-TRADING BOT
# --------------------------------------------------
def auto_trade():
    print(f"\n[BOT] Scan {datetime.now().strftime('%H:%M:%S')}")
    global signal_log
    clear_old_cooldowns()
    check_intraday_profit()

    holdings = get_holdings()
    if len(holdings) >= MAX_HOLDINGS:
        print(f"[BOT] Full {MAX_HOLDINGS}/{MAX_HOLDINGS}")
        signal_log = []
        return

    slots = MAX_HOLDINGS - len(holdings)
    buys_allowed = min(MAX_BUYS_PER_SCAN, slots)
    watchlist = get_watchlist_db()

    # Rotate batches so different stocks get scanned each time
    now = datetime.now()
    batch_index = (now.hour*4 + now.minute//15) % max(1, len(watchlist)//SCAN_BATCH_SIZE)
    start = batch_index * SCAN_BATCH_SIZE
    batch = watchlist[start:start+SCAN_BATCH_SIZE] or watchlist[:SCAN_BATCH_SIZE]

    print(f"[BOT] Batch {batch_index+1} | {len(batch)} stocks | {buys_allowed} slots")

    all_signals = []
    for symbol in batch:
        result = analyze_symbol(symbol)
        if result:
            all_signals.append(result)

    signal_log = all_signals

    candidates = [
        s for s in all_signals
        if s["signal"]=="BUY"
        and s["symbol"] not in holdings
        and not is_on_cooldown(s["symbol"])
        and s["sentiment"] != -1
        and ((s["is_large_cap"] and s["confidence"]>=STRONG_CONF)
             or (not s["is_large_cap"] and s["confidence"]>=DECENT_CONF))
    ]

    candidates.sort(key=lambda x:(x["confidence"],x["buy_score"]),reverse=True)
    top_picks = candidates[:buys_allowed]
    print(f"[BOT] {len(candidates)} candidates -> buying {len(top_picks)}")

    bought = 0
    for pick in top_picks:
        symbol = pick["symbol"]
        price = pick["current_price"]
        confidence = pick["confidence"]
        cash = get_cash()
        if price <= 0: continue

        position_size = get_position_size(confidence, cash)
        if position_size < price:
            print(f"[SKIP] {symbol} price Rs{price} > position Rs{position_size:.0f}")
            continue

        qty = min(int(position_size/price), MAX_QTY_PER_TRADE)
        if qty < 1: qty = 1
        total_cost = price * qty
        if cash < total_cost: continue

        set_cash(cash-total_cost)
        update_holding(symbol,qty,price,total_cost)
        target = round(price*(1+INTRADAY_PROFIT_TARGET_PCT/100),2)
        eod_t = round(price*(1+EOD_MIN_PROFIT_PCT/100),2)
        add_trade("BUY",symbol,price,qty,total_cost,confidence,"AUTO",
            f"conf:{confidence}% score:{pick['buy_score']} RSI:{pick['RSI']} MACD:{'UP' if pick['MACD_bullish'] else 'DN'} target:Rs{target} eod:Rs{eod_t}",0)
        bought += 1
        print(f"[BUY] {symbol} qty:{qty} Rs{total_cost:.0f} conf:{confidence}%")

    del all_signals, candidates
    gc.collect()
    print(f"[BOT] Done | Bought:{bought} | Holdings:{len(get_holdings())}/{MAX_HOLDINGS} | Cash:Rs{get_cash():.0f}")

# --------------------------------------------------
# SCHEDULER
# --------------------------------------------------
scheduler = BackgroundScheduler()
scheduler.add_job(auto_trade,               "interval", minutes=15,  id="auto_trade")
scheduler.add_job(end_of_day_profit_booking,"cron", hour=9,  minute=45, id="eod_nse",  timezone="UTC")
scheduler.add_job(end_of_day_profit_booking,"cron", hour=16, minute=25, id="eod_nyse", timezone="UTC")
scheduler.add_job(save_daily_snapshot,      "cron", hour=10, minute=5,  id="snap_nse")
scheduler.add_job(save_daily_snapshot,      "cron", hour=21, minute=5,  id="snap_nyse")
scheduler.start()

# --------------------------------------------------
# ROUTES
# --------------------------------------------------
@app.get("/")
def home():
    return {
        "status":"AI Trading Bot v8",
        "scan_interval":"15 minutes",
        "stocks_per_scan":SCAN_BATCH_SIZE,
        "watchlist_total":len(get_watchlist_db()),
        "current_holdings":len(get_holdings()),
        "max_holdings":MAX_HOLDINGS,
        "cash":round(get_cash(),2),
        "cumulative_pnl":get_all_realized_pnl(),
        "strategy":f"Buy top 3 | Sell +{INTRADAY_PROFIT_TARGET_PCT}% | EOD +{EOD_MIN_PROFIT_PCT}% | Hold losses"
    }

@app.get("/stock/{symbol}")
def get_stock(symbol:str):
    result = analyze_symbol(symbol)
    return result if result else {"error":f"Could not fetch {symbol}"}

@app.get("/history/{symbol}")
def stock_history(symbol:str):
    try:
        data = yf.download(symbol,period="3mo",progress=False)
        if data.empty: return {"dates":[],"prices":[]}
        close = data["Close"]
        if isinstance(close,pd.DataFrame): close = close.iloc[:,0]
        close = close.ffill()
        result = {"dates":[str(d.date()) for d in close.index],
                  "prices":[round(float(p),2) for p in close.tolist()]}
        del data; gc.collect()
        return result
    except: return {"dates":[],"prices":[]}

@app.post("/buy/{symbol}")
def buy_stock(symbol:str, quantity:int=1):
    price = get_price(symbol)
    if price is None: return {"error":"Invalid symbol"}
    cash = get_cash()
    total_cost = price*quantity
    if cash < total_cost: return {"error":f"Need Rs{total_cost:.0f} have Rs{cash:.0f}"}
    holdings = get_holdings()
    eq = holdings.get(symbol,{}).get("quantity",0)
    ea = holdings.get(symbol,{}).get("avg_buy_price",0)
    nq = eq+quantity
    na = ((ea*eq)+(price*quantity))/nq
    set_cash(cash-total_cost)
    update_holding(symbol,nq,na,total_cost)
    target = round(price*(1+INTRADAY_PROFIT_TARGET_PCT/100),2)
    add_trade("BUY",symbol,price,quantity,total_cost,0,"MANUAL",f"Manual target:Rs{target}",0)
    return {"message":f"Bought {quantity} of {symbol}","price":price,
            "invested":total_cost,"target":target,"cash_remaining":round(get_cash(),2)}

@app.post("/sell/{symbol}")
def sell_stock(symbol:str, quantity:int=1):
    holdings = get_holdings()
    if symbol not in holdings or holdings[symbol]["quantity"]<quantity:
        return {"error":"Not enough shares"}
    price = get_price(symbol)
    if price is None: return {"error":"Invalid symbol"}
    avg_buy = holdings[symbol]["avg_buy_price"]
    invested = holdings[symbol]["invested_amount"]
    pnl = (price-avg_buy)*quantity
    pnl_pct = ((price-avg_buy)/avg_buy)*100
    set_cash(get_cash()+price*quantity)
    update_holding(symbol,holdings[symbol]["quantity"]-quantity,avg_buy,invested)
    add_trade("SELL",symbol,price,quantity,invested,0,"MANUAL",
              f"Manual PnL:Rs{pnl:.2f} ({pnl_pct:+.2f}%)",pnl)
    return {"message":f"Sold {quantity} of {symbol}","price":price,
            "pnl":round(pnl,2),"pnl_pct":round(pnl_pct,2),"cash":round(get_cash(),2)}

@app.post("/eod-sell")
def manual_eod_sell():
    end_of_day_profit_booking()
    return {"message":"EOD complete",
            "realized_today":get_todays_realized_pnl(),
            "cumulative_pnl":get_all_realized_pnl()}

@app.get("/portfolio/pnl")
def portfolio_pnl():
    holdings = get_holdings()
    cash = get_cash()
    pnl_details = {}
    total_invested = total_current = 0
    for symbol, data in holdings.items():
        qty = data["quantity"]
        avg_buy = data["avg_buy_price"]
        invested = data["invested_amount"]
        if qty==0: continue
        current_price = get_price(symbol)
        if current_price is None: continue
        current_value = current_price*qty
        pnl = current_value-invested
        pnl_pct = ((current_price-avg_buy)/avg_buy*100) if avg_buy>0 else 0
        total_invested += invested
        total_current += current_value
        pnl_details[symbol] = {
            "quantity":qty,"avg_buy_price":round(avg_buy,2),
            "current_price":current_price,"invested":round(invested,2),
            "current_value":round(current_value,2),"pnl":round(pnl,2),
            "pnl_pct":round(pnl_pct,2),
            "intraday_target":round(avg_buy*(1+INTRADAY_PROFIT_TARGET_PCT/100),2),
            "eod_min_sell":round(avg_buy*(1+EOD_MIN_PROFIT_PCT/100),2),
            "progress_pct":min(100,max(0,round((pnl_pct/INTRADAY_PROFIT_TARGET_PCT)*100,1))),
            "will_sell_eod":pnl_pct>=EOD_MIN_PROFIT_PCT,
            "is_large_cap":symbol in LARGE_CAP
        }
    total_pnl = total_current-total_invested
    realized_today = get_todays_realized_pnl()
    cumulative_pnl = get_all_realized_pnl()
    overall_pnl = (cash+total_current)-STARTING_CASH
    return {
        "holdings":pnl_details,
        "total_invested":round(total_invested,2),
        "total_current_value":round(total_current,2),
        "total_pnl":round(total_pnl,2),
        "total_pnl_pct":round((total_pnl/total_invested*100) if total_invested>0 else 0,2),
        "cash":round(cash,2),
        "portfolio_value":round(cash+total_current,2),
        "overall_pnl":round(overall_pnl,2),
        "overall_pnl_pct":round((overall_pnl/STARTING_CASH)*100,2),
        "realized_today":realized_today,
        "cumulative_pnl":cumulative_pnl,
        "cumulative_pnl_pct":round((cumulative_pnl/STARTING_CASH)*100,2),
        "intraday_target_pct":INTRADAY_PROFIT_TARGET_PCT,
        "eod_min_profit_pct":EOD_MIN_PROFIT_PCT,
        "max_holdings":MAX_HOLDINGS,
        "current_holdings":len(holdings)
    }

@app.get("/portfolio/value")
def portfolio_value():
    cash = get_cash()
    holdings = get_holdings()
    total = 0
    stocks = {}
    for sym,data in holdings.items():
        price = get_price(sym)
        if price is None: continue
        val = price*data["quantity"]
        total += val
        stocks[sym] = {"quantity":data["quantity"],"current_price":price,"total_value":round(val,2)}
    return {"cash":round(cash,2),"stocks":stocks,"total_portfolio_value":round(cash+total,2)}

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
        grouped[date].append({"type":r[0],"symbol":r[1],"price":r[2],
            "quantity":r[3],"invested":round(r[4],2),"confidence":r[5],
            "mode":r[6],"reason":r[7],"pnl":round(r[8],2),"time":r[9]})
    result = []
    for date in sorted(grouped.keys(),reverse=True):
        day = grouped[date]
        buys = [t for t in day if t["type"]=="BUY"]
        sells = [t for t in day if t["type"]=="SELL"]
        result.append({
            "date":date,"trades":day,"total_trades":len(day),
            "buys":len(buys),"sells":len(sells),
            "total_invested_today":round(sum(t["invested"] for t in buys),2),
            "realized_pnl":round(sum(t["pnl"] for t in sells),2)
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
def add_watchlist(symbol:str):
    symbol = symbol.upper()
    add_to_watchlist_db(symbol)
    return {"message":f"{symbol} added","total":len(get_watchlist_db())}

@app.delete("/watchlist/remove/{symbol}")
def remove_watchlist(symbol:str):
    symbol = symbol.upper()
    remove_from_watchlist_db(symbol)
    return {"message":f"{symbol} removed"}

@app.post("/scan")
def trigger_scan():
    auto_trade()
    save_daily_snapshot()
    return {"message":"Scan complete","signals_count":len(signal_log)}

@app.post("/reset")
def reset_portfolio():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM holdings")
    c.execute("DELETE FROM trades")
    c.execute("DELETE FROM daily_pnl")
    c.execute("DELETE FROM loss_cooldown")
    c.execute("UPDATE portfolio SET cash=?", (STARTING_CASH,))
    conn.commit()
    conn.close()
    return {"message":"Portfolio reset","cash":STARTING_CASH}

@app.get("/stats")
def get_stats():
    trades = get_all_trades()
    sells = [t for t in trades if t["type"]=="SELL"]
    wins = [t for t in sells if t["pnl"]>0]
    realized = get_all_realized_pnl()
    win_rate = round((len(wins)/len(sells)*100) if sells else 0,1)
    return {
        "total_trades":len(trades),
        "total_buys":len([t for t in trades if t["type"]=="BUY"]),
        "total_sells":len(sells),
        "profitable_sells":len(wins),
        "loss_sells":len(sells)-len(wins),
        "win_rate_pct":win_rate,
        "realized_pnl":realized,
        "realized_today":get_todays_realized_pnl(),
        "cumulative_pnl":realized,
        "auto_trades":len([t for t in trades if t["mode"]=="AUTO"]),
        "manual_trades":len([t for t in trades if t["mode"]=="MANUAL"]),
        "watchlist_size":len(get_watchlist_db()),
        "current_holdings":len(get_holdings()),
        "max_holdings":MAX_HOLDINGS,
        "scan_interval":"15 minutes",
        "stocks_per_scan":SCAN_BATCH_SIZE
    }

@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()
