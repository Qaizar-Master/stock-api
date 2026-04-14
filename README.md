# Stock Market Dashboard

A full-stack stock market data app with a real-time web dashboard and a REST API backend, built with **FastAPI** and **yfinance**. Developed as part of a college MLOps mini-project (Project 12 — Continuous Delivery of Flask/FastAPI Data Engineering API).

---

## Features

- **Interactive web dashboard** served at `/` — no separate frontend server needed
- Live price snapshots (price, open, high, low, volume, market cap)
- Historical OHLCV charts with Chart.js (1d → 1y range)
- Technical indicators: MA20, MA50, daily % change, BUY/SELL signal with overlay chart
- Multi-ticker comparison bar chart and table with 1-month performance
- Auto-generated Swagger UI at `/docs`

---

## Project Structure

```
stock-api/
├── main.py             # FastAPI app — API routes + static file serving
├── requirements.txt    # Python dependencies
├── render.yaml         # Render deployment config
├── README.md
└── static/
    ├── index.html      # Single-page dashboard
    ├── style.css       # Dark-theme styles
    └── app.js          # Fetch calls + Chart.js rendering
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Web dashboard (HTML) |
| GET | `/api` | JSON overview of all routes |
| GET | `/price/{ticker}` | Current price snapshot |
| GET | `/history/{ticker}` | Historical OHLCV data |
| GET | `/indicators/{ticker}` | Technical indicators and BUY/SELL signal |
| GET | `/compare` | Side-by-side comparison of multiple tickers |

### Example API URLs

```
GET /api
GET /price/AAPL
GET /price/TSLA
GET /history/MSFT
GET /history/GOOGL?period=3mo
GET /indicators/AAPL
GET /indicators/NVDA
GET /compare?tickers=AAPL,MSFT,GOOGL
GET /compare?tickers=TSLA,AMZN,META,NVDA
```

### Valid `period` values for `/history/{ticker}`

| Value | Description |
|-------|-------------|
| `1d`  | 1 day |
| `5d`  | 5 days |
| `1mo` | 1 month (default) |
| `3mo` | 3 months |
| `6mo` | 6 months |
| `1y`  | 1 year |

---

## Running Locally

### 1. Clone the repository

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd stock-api
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start the development server

```bash
uvicorn main:app --reload
```

| URL | What you get |
|-----|-------------|
| `http://127.0.0.1:8000` | Interactive dashboard |
| `http://127.0.0.1:8000/docs` | Swagger API docs |
| `http://127.0.0.1:8000/api` | JSON API overview |

---

## Deployment on Render

This project is configured for **zero-config deployment on [Render](https://render.com)** via `render.yaml`.

### Steps

1. Push this repository to GitHub.
2. Go to [render.com](https://render.com) and create a new **Web Service**.
3. Connect your GitHub repository — Render detects `render.yaml` automatically.
4. Click **Deploy**. Render installs dependencies and starts the server.

Every subsequent push to the connected branch triggers an **automatic redeploy** (Continuous Delivery).

### Render configuration (`render.yaml`)

| Setting | Value |
|---------|-------|
| Runtime | Python 3.11.9 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn main:app --host 0.0.0.0 --port 10000` |

---

## Tech Stack

- [FastAPI](https://fastapi.tiangolo.com/) — API framework + static file serving
- [yfinance](https://github.com/ranaroussi/yfinance) — Yahoo Finance market data
- [pandas](https://pandas.pydata.org/) — moving average calculations
- [Chart.js](https://www.chartjs.org/) — frontend charting (loaded from CDN)
- [uvicorn](https://www.uvicorn.org/) — ASGI server
- [Render](https://render.com) — cloud deployment with CD
