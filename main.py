from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import yfinance as yf
import pandas as pd
from typing import Optional

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


def fetch_ticker(ticker: str) -> yf.Ticker:
    t = yf.Ticker(ticker)
    info = t.info
    if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        raise HTTPException(status_code=404, detail=f"Ticker '{ticker}' not found or has no market data.")
    return t


@app.get("/", include_in_schema=False)
def root():
    """Serve the frontend dashboard."""
    return FileResponse("static/index.html")


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
            "GET /price/{ticker}": "Current price snapshot for a stock",
            "GET /history/{ticker}": "Historical OHLCV data (query param: period)",
            "GET /indicators/{ticker}": "Technical indicators: MA20, MA50, daily % change, BUY/SELL signal",
            "GET /compare": "Side-by-side comparison of multiple tickers (query param: tickers)",
        },
    }


@app.get("/price/{ticker}")
def get_price(ticker: str):
    """
    Current price snapshot for a stock ticker.

    Returns the latest price, open, high, low, volume, and market cap
    fetched via yfinance `.info`. Raises 404 if the ticker is invalid.

    - **ticker**: Stock symbol, e.g. `AAPL`, `TSLA`, `MSFT`
    """
    t = fetch_ticker(ticker.upper())
    info = t.info

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    return {
        "ticker": ticker.upper(),
        "price": price,
        "open": info.get("open") or info.get("regularMarketOpen"),
        "high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
        "low": info.get("dayLow") or info.get("regularMarketDayLow"),
        "volume": info.get("volume") or info.get("regularMarketVolume"),
        "market_cap": info.get("marketCap"),
    }


VALID_PERIODS = {"1d", "5d", "1mo", "3mo", "6mo", "1y"}


@app.get("/history/{ticker}")
def get_history(
    ticker: str,
    period: Optional[str] = Query(default="1mo", description="Time period: 1d, 5d, 1mo, 3mo, 6mo, 1y"),
):
    """
    Historical OHLCV data for a stock ticker.

    Returns a list of daily records containing date, open, high, low, close,
    and volume. Use the `period` query parameter to control the time range.

    - **ticker**: Stock symbol, e.g. `AAPL`
    - **period**: One of `1d`, `5d`, `1mo`, `3mo`, `6mo`, `1y` (default: `1mo`)
    """
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'. Must be one of: {', '.join(sorted(VALID_PERIODS))}",
        )

    t = yf.Ticker(ticker.upper())
    df = t.history(period=period)

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No historical data found for ticker '{ticker.upper()}'.")

    df.index = df.index.strftime("%Y-%m-%d")
    records = []
    for date, row in df.iterrows():
        records.append({
            "date": date,
            "open": round(row["Open"], 4),
            "high": round(row["High"], 4),
            "low": round(row["Low"], 4),
            "close": round(row["Close"], 4),
            "volume": int(row["Volume"]),
        })

    return {"ticker": ticker.upper(), "period": period, "data": records}


@app.get("/indicators/{ticker}")
def get_indicators(ticker: str):
    """
    Technical indicators for a stock ticker.

    Computes and returns:
    - **MA20**: 20-day simple moving average of closing prices
    - **MA50**: 50-day simple moving average of closing prices
    - **daily_change_pct**: Latest day's percentage price change
    - **signal**: `BUY` if latest close > MA50, otherwise `SELL`

    Requires at least 50 days of trading history. Raises 404 if the ticker
    is invalid or has insufficient data.

    - **ticker**: Stock symbol, e.g. `AAPL`
    """
    t = yf.Ticker(ticker.upper())
    df = t.history(period="3mo")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for ticker '{ticker.upper()}'.")

    if len(df) < 20:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough data to compute indicators for '{ticker.upper()}' (need at least 20 trading days).",
        )

    close = df["Close"]

    ma20 = close.rolling(window=20).mean().dropna()
    ma50 = close.rolling(window=50).mean().dropna() if len(close) >= 50 else None

    latest_close = round(float(close.iloc[-1]), 4)
    prev_close = round(float(close.iloc[-2]), 4)
    daily_change_pct = round(((latest_close - prev_close) / prev_close) * 100, 4)

    ma20_val = round(float(ma20.iloc[-1]), 4) if not ma20.empty else None
    ma50_val = round(float(ma50.iloc[-1]), 4) if ma50 is not None and not ma50.empty else None

    if ma50_val is not None:
        signal = "BUY" if latest_close > ma50_val else "SELL"
    else:
        signal = "INSUFFICIENT_DATA"

    return {
        "ticker": ticker.upper(),
        "latest_close": latest_close,
        "MA20": ma20_val,
        "MA50": ma50_val,
        "daily_change_pct": daily_change_pct,
        "signal": signal,
    }


@app.get("/compare")
def compare_tickers(
    tickers: str = Query(..., description="Comma-separated list of stock tickers, e.g. AAPL,MSFT,GOOGL"),
):
    """
    Side-by-side comparison of multiple stock tickers.

    Accepts a comma-separated `tickers` query parameter and returns for each:
    - **latest_close**: Most recent closing price
    - **change_1mo_pct**: Percentage price change over the past 1 month

    Tickers with no available data are reported with `null` values rather than
    causing the entire request to fail.

    - **tickers**: Comma-separated stock symbols, e.g. `?tickers=AAPL,MSFT,GOOGL`
    """
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers provided.")

    if len(ticker_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum of 10 tickers allowed per request.")

    results = {}
    for sym in ticker_list:
        try:
            df = yf.Ticker(sym).history(period="1mo")
            if df.empty or len(df) < 2:
                results[sym] = {"latest_close": None, "change_1mo_pct": None, "error": "No data available"}
                continue

            latest = round(float(df["Close"].iloc[-1]), 4)
            earliest = round(float(df["Close"].iloc[0]), 4)
            change_pct = round(((latest - earliest) / earliest) * 100, 4)

            results[sym] = {"latest_close": latest, "change_1mo_pct": change_pct}
        except Exception as e:
            results[sym] = {"latest_close": None, "change_1mo_pct": None, "error": str(e)}

    return {"comparison": results}


# Mount static files last so API routes take priority
app.mount("/static", StaticFiles(directory="static"), name="static")
