import os
import time
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import yfinance as yf
import pandas as pd
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Browser-like session so Yahoo Finance doesn't rate-limit cloud server IPs ──
_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
})

# ── Simple in-memory TTL cache (5 minutes) ─────────────────────────────────────
_cache: dict = {}
CACHE_TTL = 300  # seconds

def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None

def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}

def _ticker(symbol: str) -> yf.Ticker:
    """Return a Ticker using the shared session."""
    return yf.Ticker(symbol, session=_session)


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Stock Market Data API",
    description="A FastAPI-based data engineering API for real-time and historical stock market data.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def root():
    """Serve the frontend dashboard."""
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/api", tags=["info"])
def api_info():
    """
    API overview endpoint.

    Returns a brief description of the Stock Market Data API and its available routes.
    """
    return {
        "message": "Welcome to the Stock Market Data API",
        "description": "Real-time and historical stock data powered by yfinance and FastAPI.",
        "endpoints": {
            "GET /price/{ticker}":      "Current price snapshot for a stock",
            "GET /history/{ticker}":    "Historical OHLCV data (query param: period)",
            "GET /indicators/{ticker}": "Technical indicators: MA20, MA50, daily % change, BUY/SELL signal",
            "GET /compare":             "Side-by-side comparison of multiple tickers (query param: tickers)",
        },
    }


@app.get("/price/{ticker}")
def get_price(ticker: str):
    """
    Current price snapshot for a stock ticker.

    Uses `fast_info` (fewer API calls than `.info`) to return the latest price,
    open, high, low, volume, and market cap. Results are cached for 5 minutes.

    - **ticker**: Stock symbol, e.g. `AAPL`, `TSLA`, `MSFT`
    """
    symbol = ticker.upper()
    cached = _cache_get(f"price:{symbol}")
    if cached:
        return cached

    try:
        t  = _ticker(symbol)
        fi = t.fast_info
        price = fi.last_price
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch data for '{symbol}': {e}")

    if price is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{symbol}' not found or has no market data.")

    def _r(v):
        return round(float(v), 4) if v is not None else None

    result = {
        "ticker":     symbol,
        "price":      _r(price),
        "open":       _r(getattr(fi, "open",       None)),
        "high":       _r(getattr(fi, "day_high",   None)),
        "low":        _r(getattr(fi, "day_low",    None)),
        "volume":     getattr(fi, "last_volume",   None),
        "market_cap": getattr(fi, "market_cap",    None),
    }
    _cache_set(f"price:{symbol}", result)
    return result


VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y"}


@app.get("/history/{ticker}")
def get_history(
    ticker: str,
    period: Optional[str] = Query(default="1mo", description="Time period: 1d, 5d, 1mo, 3mo, 6mo, 1y"),
):
    """
    Historical OHLCV data for a stock ticker.

    Returns a list of daily records containing date, open, high, low, close,
    and volume. Results are cached per ticker+period for 5 minutes.

    - **ticker**: Stock symbol, e.g. `AAPL`
    - **period**: One of `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y` (default: `1mo`)
    """
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'. Must be one of: {', '.join(sorted(VALID_PERIODS))}",
        )

    symbol = ticker.upper()
    cache_key = f"history:{symbol}:{period}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    try:
        df = _ticker(symbol).history(period=period)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch history for '{symbol}': {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No historical data found for ticker '{symbol}'.")

    df.index = df.index.strftime("%Y-%m-%d")
    records = [
        {
            "date":   date,
            "open":   round(row["Open"],  4),
            "high":   round(row["High"],  4),
            "low":    round(row["Low"],   4),
            "close":  round(row["Close"], 4),
            "volume": int(row["Volume"]),
        }
        for date, row in df.iterrows()
    ]

    result = {"ticker": symbol, "period": period, "data": records}
    _cache_set(cache_key, result)
    return result


@app.get("/indicators/{ticker}")
def get_indicators(ticker: str):
    """
    Technical indicators for a stock ticker.

    Computes and returns:
    - **MA20**: 20-day simple moving average of closing prices
    - **MA50**: 50-day simple moving average of closing prices
    - **daily_change_pct**: Latest day's percentage price change
    - **signal**: `BUY` if latest close > MA50, otherwise `SELL`

    Results are cached for 5 minutes.

    - **ticker**: Stock symbol, e.g. `AAPL`
    """
    symbol = ticker.upper()
    cached = _cache_get(f"indicators:{symbol}")
    if cached:
        return cached

    try:
        df = _ticker(symbol).history(period="3mo")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch data for '{symbol}': {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for ticker '{symbol}'.")

    if len(df) < 20:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough data to compute indicators for '{symbol}' (need at least 20 trading days).",
        )

    close = df["Close"]
    ma20 = close.rolling(window=20).mean().dropna()
    ma50 = close.rolling(window=50).mean().dropna() if len(close) >= 50 else None

    latest_close     = round(float(close.iloc[-1]), 4)
    prev_close       = round(float(close.iloc[-2]), 4)
    daily_change_pct = round(((latest_close - prev_close) / prev_close) * 100, 4)
    ma20_val = round(float(ma20.iloc[-1]), 4) if not ma20.empty else None
    ma50_val = round(float(ma50.iloc[-1]), 4) if ma50 is not None and not ma50.empty else None

    signal = (
        "BUY"  if ma50_val is not None and latest_close > ma50_val else
        "SELL" if ma50_val is not None else
        "INSUFFICIENT_DATA"
    )

    result = {
        "ticker":          symbol,
        "latest_close":    latest_close,
        "MA20":            ma20_val,
        "MA50":            ma50_val,
        "daily_change_pct": daily_change_pct,
        "signal":          signal,
    }
    _cache_set(f"indicators:{symbol}", result)
    return result


@app.get("/compare")
def compare_tickers(
    tickers: str = Query(..., description="Comma-separated list of stock tickers, e.g. AAPL,MSFT,GOOGL"),
):
    """
    Side-by-side comparison of multiple stock tickers.

    Returns latest closing price and 1-month % change for each ticker.
    Tickers with no data return `null` values instead of failing the whole request.
    Results are cached per ticker for 5 minutes.

    - **tickers**: Comma-separated stock symbols, e.g. `?tickers=AAPL,MSFT,GOOGL`
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers provided.")
    if len(ticker_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum of 10 tickers allowed per request.")

    results = {}
    for sym in ticker_list:
        cache_key = f"compare:{sym}"
        cached = _cache_get(cache_key)
        if cached:
            results[sym] = cached
            continue

        try:
            df = _ticker(sym).history(period="1mo")
            if df.empty or len(df) < 2:
                results[sym] = {"latest_close": None, "change_1mo_pct": None, "error": "No data available"}
                continue

            latest     = round(float(df["Close"].iloc[-1]), 4)
            earliest   = round(float(df["Close"].iloc[0]),  4)
            change_pct = round(((latest - earliest) / earliest) * 100, 4)

            entry = {"latest_close": latest, "change_1mo_pct": change_pct}
            _cache_set(cache_key, entry)
            results[sym] = entry
        except Exception as e:
            results[sym] = {"latest_close": None, "change_1mo_pct": None, "error": str(e)}

    return {"comparison": results}


# Mount static files last so API routes take priority
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
