import os
import time
from pathlib import Path

# Suppress TensorFlow info / warning logs before importing tf
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import yfinance as yf
import pandas as pd
import numpy as np
import joblib
import tensorflow as tf
from tensorflow import keras
from sklearn.preprocessing import MinMaxScaler
from typing import Optional

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = Path(BASE_DIR) / "models"
MODELS_DIR.mkdir(exist_ok=True)

MODEL_MAX_AGE  = 86400  # retrain after 24 hours
SEQ_LEN        = 60     # days the LSTM looks back (3 months of trading days)
N_FEATURES     = 7      # close, ema9, ema21, ema50, rsi, macd, volume
MODEL_VERSION  = "2"    # bump this whenever architecture or features change


# ── Model persistence ──────────────────────────────────────────────────────────

def _model_dir(symbol: str) -> Path:
    """Each symbol gets its own sub-directory inside models/."""
    return MODELS_DIR / symbol


def _load_model(symbol: str):
    """
    Load a saved LSTM bundle (model + scaler + metrics + metadata).
    Returns None if no saved model exists or if it is older than MODEL_MAX_AGE.
    """
    d         = _model_dir(symbol)
    meta_path = d / "meta.joblib"
    if not d.exists() or not meta_path.exists():
        return None

    meta = joblib.load(meta_path)
    if time.time() - meta["trained_at"] > MODEL_MAX_AGE:
        return None                         # stale — caller will retrain
    if meta.get("version") != MODEL_VERSION:
        return None                         # architecture changed — retrain

    model = keras.models.load_model(str(d / "model.keras"))
    return {
        "model":            model,
        "scaler":           meta["scaler"],
        "training_samples": meta["training_samples"],
        "metrics":          meta.get("metrics"),
    }


def _save_model(symbol: str, model, scaler: MinMaxScaler,
                training_samples: int, metrics: dict):
    """Save the Keras model, scaler, and evaluation metrics to models/<SYMBOL>/."""
    d = _model_dir(symbol)
    d.mkdir(exist_ok=True)
    model.save(str(d / "model.keras"))
    joblib.dump(
        {
            "version":          MODEL_VERSION,
            "trained_at":       time.time(),
            "training_samples": training_samples,
            "scaler":           scaler,
            "metrics":          metrics,
        },
        d / "meta.joblib",
    )


# ── Simple in-memory TTL cache (5 minutes) ────────────────────────────────────

_cache: dict = {}
CACHE_TTL    = 300


def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}


def _ticker(symbol: str) -> yf.Ticker:
    return yf.Ticker(symbol)


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
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/api", tags=["info"])
def api_info():
    return {
        "message": "Welcome to the Stock Market Data API",
        "description": "Real-time and historical stock data powered by yfinance and FastAPI.",
        "endpoints": {
            "GET /price/{ticker}":      "Current price snapshot for a stock",
            "GET /history/{ticker}":    "Historical OHLCV data (query param: period)",
            "GET /indicators/{ticker}": "Technical indicators: MA20, MA50, daily % change, BUY/SELL signal",
            "GET /compare":             "Side-by-side comparison of multiple tickers (query param: tickers)",
            "GET /predict/{symbol}":    "Next-day closing price prediction using LSTM + EMAs",
            "GET /model-info/{symbol}": "Check if a trained LSTM model is saved for this symbol",
        },
    }


@app.get("/price/{ticker}")
def get_price(ticker: str):
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
    if period not in VALID_PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid period '{period}'. Must be one of: {', '.join(sorted(VALID_PERIODS))}",
        )

    symbol    = ticker.upper()
    cache_key = f"history:{symbol}:{period}"
    cached    = _cache_get(cache_key)
    if cached:
        return cached

    try:
        df = _ticker(symbol).history(period=period)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch history for '{symbol}': {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No historical data found for ticker '{symbol}'.")

    df.index = df.index.strftime("%Y-%m-%d")
    records  = [
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
    ma20  = close.rolling(window=20).mean().dropna()
    ma50  = close.rolling(window=50).mean().dropna() if len(close) >= 50 else None

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
        "ticker":           symbol,
        "latest_close":     latest_close,
        "MA20":             ma20_val,
        "MA50":             ma50_val,
        "daily_change_pct": daily_change_pct,
        "signal":           signal,
    }
    _cache_set(f"indicators:{symbol}", result)
    return result


@app.get("/compare")
def compare_tickers(
    tickers: str = Query(..., description="Comma-separated list of stock tickers, e.g. AAPL,MSFT,GOOGL"),
):
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    if not ticker_list:
        raise HTTPException(status_code=400, detail="No valid tickers provided.")
    if len(ticker_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum of 10 tickers allowed per request.")

    results = {}
    for sym in ticker_list:
        cache_key = f"compare:{sym}"
        cached    = _cache_get(cache_key)
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


# ── Prediction endpoints ───────────────────────────────────────────────────────

@app.get("/model-info/{symbol}", tags=["prediction"])
def model_info(symbol: str):
    """
    Check whether a saved LSTM model exists for this symbol.
    Instant — no market data is fetched.
    """
    sym       = symbol.upper()
    d         = _model_dir(sym)
    meta_path = d / "meta.joblib"

    if not d.exists() or not meta_path.exists():
        return {"symbol": sym, "model_saved": False}

    meta     = joblib.load(meta_path)
    age_secs = time.time() - meta["trained_at"]
    fresh    = age_secs < MODEL_MAX_AGE
    return {
        "symbol":           sym,
        "model_saved":      True,
        "fresh":            fresh,
        "age_hours":        round(age_secs / 3600, 2),
        "training_samples": meta["training_samples"],
        "metrics":          meta.get("metrics"),
    }


@app.get("/predict/{symbol}", tags=["prediction"])
def predict_price(symbol: str):
    """
    Next-day closing price prediction using an LSTM neural network.

    **Data:** 6 months of daily closing prices + EMA-9, EMA-21, EMA-50.

    **Model:** A single-layer LSTM (64 units) that looks back SEQ_LEN=30 days
    to predict the next day's closing price.  Features are normalised with
    MinMaxScaler before training and inverse-transformed on output.

    A trained model is saved to `models/<SYMBOL>/` and reused for 24 hours
    before retraining.

    - **symbol**: Stock symbol, e.g. `AAPL`, `TSLA`
    """
    sym    = symbol.upper()
    cached = _cache_get(f"predict:{sym}")
    if cached:
        return cached

    # Always fetch fresh market data (needed for current EMAs / prediction features)
    try:
        df = _ticker(sym).history(period="1y")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch data for '{sym}': {e}")

    if df.empty:
        raise HTTPException(status_code=404, detail=f"No data found for ticker '{sym}'.")

    close  = df["Close"].copy()
    volume = df["Volume"].copy().astype(float)

    # Price-based indicators
    ema9  = close.ewm(span=9,  adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    # RSI-14
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, 1e-10)))

    # MACD (12-period EMA minus 26-period EMA)
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()

    # Feature matrix: [close, ema9, ema21, ema50, rsi, macd, volume]  (N_FEATURES = 7)
    feat_df  = pd.DataFrame({
        "close": close, "ema9": ema9, "ema21": ema21, "ema50": ema50,
        "rsi": rsi, "macd": macd, "volume": volume,
    }).dropna()
    features = feat_df.values

    min_rows = SEQ_LEN + 5
    if len(features) < min_rows:
        raise HTTPException(
            status_code=422,
            detail=f"Not enough data for '{sym}' (need ≥ {min_rows} trading days, got {len(features)}).",
        )

    # ── Try to load a saved model ─────────────────────────────────
    bundle = _load_model(sym)

    # helper: inverse-transform the close column only (col 0)
    def _inv_close(arr: np.ndarray) -> np.ndarray:
        dummy = np.zeros((len(arr), N_FEATURES))
        dummy[:, 0] = arr
        return _active_scaler.inverse_transform(dummy)[:, 0]

    if bundle:
        model            = bundle["model"]
        scaler           = bundle["scaler"]          # use the SAVED scaler (same fit as training)
        training_samples = bundle["training_samples"]
        metrics          = bundle["metrics"]
        model_status     = "loaded"
        _active_scaler   = scaler
    else:
        # ── Train a new LSTM ──────────────────────────────────────
        scaler          = MinMaxScaler()
        _active_scaler  = scaler
        features_scaled = scaler.fit_transform(features)

        # Build (X, y) sequences
        X_all = np.array([features_scaled[i - SEQ_LEN:i] for i in range(SEQ_LEN, len(features_scaled))])
        y_all = features_scaled[SEQ_LEN:, 0]   # target = scaled close price

        # 80 / 20 train-test split
        split   = max(1, int(len(X_all) * 0.8))
        X_train = X_all[:split];  y_train = y_all[:split]
        X_test  = X_all[split:];  y_test  = y_all[split:]

        model = keras.Sequential([
            keras.layers.Input(shape=(SEQ_LEN, N_FEATURES)),
            keras.layers.LSTM(128, return_sequences=True),
            keras.layers.Dropout(0.2),
            keras.layers.LSTM(64),
            keras.layers.Dropout(0.2),
            keras.layers.Dense(32, activation="relu"),
            keras.layers.Dense(1),
        ])
        model.compile(optimizer="adam", loss="mse")

        early_stop = keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=5, restore_best_weights=True
        )
        model.fit(
            X_train, y_train,
            epochs=50, batch_size=16,
            validation_split=0.1,
            callbacks=[early_stop],
            verbose=0,
        )

        # ── Evaluate on test set ──────────────────────────────────
        y_pred_scaled = model.predict(X_test, verbose=0).flatten()
        y_pred_actual = _inv_close(y_pred_scaled)
        y_test_actual = _inv_close(y_test)

        mae  = float(np.mean(np.abs(y_pred_actual - y_test_actual)))
        rmse = float(np.sqrt(np.mean((y_pred_actual - y_test_actual) ** 2)))
        mape = float(np.mean(np.abs((y_pred_actual - y_test_actual) / y_test_actual)) * 100)

        # Directional accuracy: did we predict up/down vs the previous actual close?
        prev_actual = _inv_close(X_test[:, -1, 0])   # last close in each input sequence
        dir_acc     = float(np.mean((y_test_actual > prev_actual) == (y_pred_actual > prev_actual)) * 100)

        metrics = {
            "test_mae":             round(mae,     4),
            "test_rmse":            round(rmse,    4),
            "test_mape":            round(mape,    4),
            "directional_accuracy": round(dir_acc, 2),
            "test_samples":         len(X_test),
        }

        training_samples = len(X_train)
        _save_model(sym, model, scaler, training_samples, metrics)
        model_status = "trained"

    # ── Predict next day using the most recent SEQ_LEN rows ──────
    features_scaled = _active_scaler.transform(features)
    last_seq        = features_scaled[-SEQ_LEN:].reshape(1, SEQ_LEN, N_FEATURES)
    pred_scaled     = float(model.predict(last_seq, verbose=0)[0, 0])

    # Inverse-transform: put prediction in close column (col 0) of a dummy row
    dummy       = np.zeros((1, N_FEATURES))
    dummy[0, 0] = pred_scaled
    predicted_close = float(_active_scaler.inverse_transform(dummy)[0, 0])

    # Build chart series (last 90 rows ≈ 3 months) from already-fetched data
    chart_df  = feat_df.tail(90)
    chart_dates  = df.index[-len(chart_df):]
    if hasattr(chart_dates, "strftime"):
        chart_labels = chart_dates.strftime("%Y-%m-%d").tolist()
    else:
        chart_labels = [str(d) for d in chart_dates]

    last = feat_df.iloc[-1]
    result = {
        "symbol":               sym,
        "last_close":           round(float(last["close"]), 4),
        "ema9":                 round(float(last["ema9"]),  4),
        "ema21":                round(float(last["ema21"]), 4),
        "ema50":                round(float(last["ema50"]), 4),
        "predicted_next_close": round(predicted_close, 4),
        "model":                "LSTM",
        "model_status":         model_status,
        "training_samples":     training_samples,
        "metrics":              metrics,
        "chart": {
            "labels": chart_labels,
            "close":  [round(float(v), 4) for v in chart_df["close"]],
            "ema9":   [round(float(v), 4) for v in chart_df["ema9"]],
            "ema21":  [round(float(v), 4) for v in chart_df["ema21"]],
            "ema50":  [round(float(v), 4) for v in chart_df["ema50"]],
        },
        "note":                 "Educational model only — not financial advice.",
    }
    _cache_set(f"predict:{sym}", result)
    return result


# Mount static files last so API routes take priority
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
