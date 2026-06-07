"""
watchlist_builder.py
Fetches live stock lists from real sources every day.
Called automatically at market open.
"""

import requests
import pandas as pd
import sqlite3
from datetime import datetime

DB_PATH = "trading.db"

# --------------------------------------------------
# NIFTY 200 — from NSE India official CSV
# --------------------------------------------------
def get_nifty200():
    try:
        url = "https://archives.nseindia.com/content/indices/ind_nifty200list.csv"
        headers = {"User-Agent": "Mozilla/5.0"}
        df = pd.read_csv(url, storage_options={"User-Agent": "Mozilla/5.0"})
        symbols = [s.strip() + ".NS" for s in df["Symbol"].tolist()]
        print(f"[WATCHLIST] Nifty 200: {len(symbols)} stocks")
        return symbols
    except Exception as e:
        print(f"[WATCHLIST] Nifty 200 failed: {e}")
        return get_nifty200_fallback()

def get_nifty200_fallback():
    """Hardcoded Nifty 200 as fallback"""
    nifty200 = [
        "RELIANCE.NS","TCS.NS","HDFCBANK.NS","BHARTIARTL.NS","ICICIBANK.NS",
        "INFOSYS.NS","SBIN.NS","HINDUNILVR.NS","ITC.NS","KOTAKBANK.NS",
        "LT.NS","HCLTECH.NS","BAJFINANCE.NS","AXISBANK.NS","ASIANPAINT.NS",
        "MARUTI.NS","TITAN.NS","SUNPHARMA.NS","WIPRO.NS","NTPC.NS",
        "ONGC.NS","POWERGRID.NS","ULTRACEMCO.NS","NESTLEIND.NS","TATAMOTORS.NS",
        "ADANIENT.NS","BAJAJFINSV.NS","TECHM.NS","DRREDDY.NS","DIVISLAB.NS",
        "CIPLA.NS","EICHERMOT.NS","HEROMOTOCO.NS","BPCL.NS","COALINDIA.NS",
        "GRASIM.NS","INDUSINDBK.NS","SBILIFE.NS","HDFCLIFE.NS","BRITANNIA.NS",
        "TATACONSUM.NS","UPL.NS","APOLLOHOSP.NS","ADANIPORTS.NS","JSWSTEEL.NS",
        "TATASTEEL.NS","HINDALCO.NS","VEDL.NS","SAIL.NS","NMDC.NS",
        "INFY.NS","BAJAJ-AUTO.NS","DABUR.NS","GODREJCP.NS","MARICO.NS",
        "PIDILITIND.NS","BERGEPAINT.NS","HAVELLS.NS","VOLTAS.NS","WHIRLPOOL.NS",
        "MUTHOOTFIN.NS","CHOLAFIN.NS","BAJAJHLDNG.NS","LICHSGFIN.NS","PFC.NS",
        "RECLTD.NS","IRFC.NS","HUDCO.NS","SJVN.NS","NHPC.NS",
        "TATAPOWER.NS","TORNTPOWER.NS","CESC.NS","JSPL.NS","NATIONALUM.NS",
        "HINDCOPPER.NS","MOIL.NS","GMRINFRA.NS","ADANIGREEN.NS","ADANITRANS.NS",
        "ZOMATO.NS","NYKAA.NS","POLICYBZR.NS","PAYTM.NS","DELHIVERY.NS",
        "IRCTC.NS","HAL.NS","BEL.NS","BHEL.NS","CONCOR.NS",
        "MOTHERSON.NS","BALKRISIND.NS","MRF.NS","APOLLOTYRE.NS","CEAT.NS",
        "JUBLFOOD.NS","WESTLIFE.NS","DEVYANI.NS","SAPPHIRE.NS","BARBEQUE.NS",
        "DMART.NS","TRENT.NS","NYKAA.NS","SHOPERSTOP.NS","VMART.NS",
        "SUNPHARMA.NS","LUPIN.NS","AUROPHARMA.NS","TORNTPHARM.NS","ALKEM.NS",
        "IPCALAB.NS","NATCOPHARM.NS","GRANULES.NS","AJANTPHARM.NS","JBCHEPHARM.NS",
        "INDIGOPNTS.NS","KANSAINER.NS","AKZOINDIA.NS","SHALPAINTS.NS","NAVINFLUOR.NS",
        "AAPL.NS","HFCL.NS","STLTECH.NS","TEJASNET.NS","ROUTE.NS",
        "ZYDUSLIFE.NS","GLENMARK.NS","BIOCON.NS","CADILAHC.NS","PFIZER.NS",
        "ABBOTINDIA.NS","SANOFI.NS","GLAXO.NS","NOVARTIND.NS","ASTRAZEN.NS",
        "TANLA.NS","MPHASIS.NS","LTTS.NS","KPITTECH.NS","PERSISTENT.NS",
        "COFORGE.NS","MASTEK.NS","ZENSAR.NS","HEXAWARE.NS","NIIT.NS",
        "OBEROIRLTY.NS","DLF.NS","GODREJPROP.NS","PRESTIGE.NS","BRIGADE.NS",
        "PHOENIXLTD.NS","SOBHA.NS","KOLTEPATIL.NS","MAHLIFE.NS","SUNTECK.NS",
        "BANKBARODA.NS","PNB.NS","CANBK.NS","UNIONBANK.NS","INDIANB.NS",
        "BANKINDIA.NS","MAHABANK.NS","UCOBANK.NS","CENTRALBK.NS","IOB.NS",
        "FEDERALBNK.NS","IDFCFIRSTB.NS","BANDHANBNK.NS","RBLBANK.NS","YESBANK.NS",
        "SHRIRAMFIN.NS","M&MFIN.NS","MANAPPURAM.NS","AAVAS.NS","HOMEFIRST.NS",
        "M&M.NS","TATAMOTORS.NS","ASHOKLEY.NS","TVSMOTOR.NS","ESCORTS.NS",
        "FORCEMOT.NS","TIINDIA.NS","SUNDRMFAST.NS","BOSCHLTD.NS","EXIDEIND.NS",
        "ATUL.NS","DEEPAKNTR.NS","FINEORG.NS","GALAXYSURF.NS","SUDARSCHEM.NS",
        "TATACHEM.NS","GNFC.NS","GSFC.NS","CHAMBLFERT.NS","COROMANDEL.NS",
        "UPL.NS","DHANUKA.NS","RALLIS.NS","INSECTICID.NS","BAYER.NS",
        "GODREJAGRO.NS","JKCEMENT.NS","RAMCOCEM.NS","HEIDELBERG.NS","BIRLASOFT.NS",
    ]
    return list(set(nifty200))

# --------------------------------------------------
# S&P 500 — from Wikipedia
# --------------------------------------------------
def get_sp500():
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)
        df = tables[0]
        symbols = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        print(f"[WATCHLIST] S&P 500: {len(symbols)} stocks")
        return symbols
    except Exception as e:
        print(f"[WATCHLIST] S&P 500 failed: {e}")
        return get_sp500_fallback()

def get_sp500_fallback():
    """Top 100 S&P 500 by market cap as fallback"""
    return [
        "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","AVGO","JPM",
        "LLY","V","UNH","XOM","MA","JNJ","PG","COST","HD","MRK",
        "WMT","ABBV","CVX","KO","BAC","PEP","NFLX","ORCL","ACN","TMO",
        "CRM","AMD","LIN","CSCO","ABT","MCD","NKE","DHR","TXN","NEE",
        "PM","INTC","WFC","INTU","AMGN","MS","GS","RTX","T","SPGI",
        "CAT","BLK","HON","ISRG","GE","ADP","BKNG","PFE","LOW","MDLZ",
        "VRTX","CB","DE","SCHW","TJX","SYK","REGN","MMC","GILD","BMY",
        "ETN","BX","MDT","ADI","C","TMUS","ZTS","MU","SO","DUK",
        "CI","CME","PYPL","F","GM","USB","ICE","AON","EQIX","ITW",
        "NOC","APD","WM","GD","TGT","PLD","ECL","SHW","MCO","FDX",
        "UBER","SNAP","ROKU","SHOP","COIN","PLTR","RBLX","HOOD","SOFI","RIVN",
        "LCID","NIO","XPEV","LI","NKLA","WKHS","GOEV","FSR","FFIE","MULN",
    ]

# --------------------------------------------------
# CRYPTO — top coins by market cap
# --------------------------------------------------
def get_crypto():
    return [
        "BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD",
        "ADA-USD","AVAX-USD","DOGE-USD","DOT-USD","MATIC-USD",
        "LINK-USD","UNI-USD","LTC-USD","ATOM-USD","XLM-USD",
        "ALGO-USD","VET-USD","FIL-USD","THETA-USD","EOS-USD",
    ]

# --------------------------------------------------
# BUILD FULL WATCHLIST
# --------------------------------------------------
def build_full_watchlist():
    print(f"\n[WATCHLIST] Building watchlist at {datetime.now().strftime('%H:%M:%S')}")

    indian = get_nifty200()
    us = get_sp500()
    crypto = get_crypto()

    all_symbols = list(set(indian + us + crypto))

    # Save to DB
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Clear old watchlist
    c.execute("DELETE FROM watchlist")

    # Insert all new symbols
    for sym in all_symbols:
        c.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (sym.strip(),))

    conn.commit()
    conn.close()

    print(f"[WATCHLIST] Total: {len(all_symbols)} stocks ({len(indian)} Indian + {len(us)} US + {len(crypto)} Crypto)")
    return all_symbols

if __name__ == "__main__":
    symbols = build_full_watchlist()
    print(f"Done: {len(symbols)} stocks loaded")
