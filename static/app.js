'use strict';

// ─── Chart instances (kept so we can destroy before recreating) ──
let historyChartInst    = null;
let indicatorsChartInst = null;
let compareChartInst    = null;

// ─── Shared Chart defaults ────────────────────────────────────────
Chart.defaults.color          = '#94a3b8';
Chart.defaults.borderColor    = '#334155';
Chart.defaults.backgroundColor = 'transparent';
Chart.defaults.font.family    = "'Segoe UI', system-ui, sans-serif";

// ─── Tab switching ────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.tab).classList.add('active');
  });
});

// Allow Enter key in inputs
['price-input', 'history-input', 'indicators-input', 'compare-input'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('keydown', e => { if (e.key === 'Enter') el.nextElementSibling?.click() || el.closest('.input-row')?.querySelector('.btn-primary')?.click(); });
});

// ─── Utility helpers ──────────────────────────────────────────────
function fmt(n, decimals = 2) {
  if (n == null) return '—';
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function fmtBig(n) {
  if (n == null) return '—';
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6)  return `$${(n / 1e6).toFixed(2)}M`;
  return `$${Number(n).toLocaleString()}`;
}

function fmtVol(n) {
  if (n == null) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}

function show(id)   { document.getElementById(id)?.classList.remove('hidden'); }
function hide(id)   { document.getElementById(id)?.classList.add('hidden'); }
function text(id, v){ const el = document.getElementById(id); if (el) el.textContent = v; }
function setLoading(prefix, on) {
  if (on) { show(`${prefix}-loading`); hide(`${prefix}-result`); hide(`${prefix}-error`); }
  else     { hide(`${prefix}-loading`); }
}

async function apiFetch(url) {
  let res;
  try {
    res = await fetch(url);
  } catch {
    throw new Error('Network error — could not reach the server.');
  }

  if (!res.ok) {
    let detail;
    try {
      const errData = await res.json();
      detail = errData.detail || `Server error (${res.status})`;
    } catch {
      // Response was not JSON (e.g. plain-text "Internal Server Error")
      detail = `Server error (${res.status} ${res.statusText})`;
    }
    throw new Error(detail);
  }

  return res.json();
}

function showError(prefix, msg) {
  const el = document.getElementById(`${prefix}-error`);
  if (el) { el.textContent = `Error: ${msg}`; el.classList.remove('hidden'); }
}

function setBtn(id, disabled) {
  const el = document.getElementById(id);
  if (el) el.disabled = disabled;
}

// ─── PRICE ───────────────────────────────────────────────────────
async function handlePrice() {
  const ticker = document.getElementById('price-input').value.trim().toUpperCase();
  if (!ticker) { showError('price', 'Please enter a ticker symbol.'); return; }

  setBtn('price-btn', true);
  setLoading('price', true);

  try {
    const d = await apiFetch(`/price/${ticker}`);

    text('price-ticker-label', d.ticker);
    text('stat-price',  d.price  != null ? `$${fmt(d.price)}`  : '—');
    text('stat-open',   d.open   != null ? `$${fmt(d.open)}`   : '—');
    text('stat-high',   d.high   != null ? `$${fmt(d.high)}`   : '—');
    text('stat-low',    d.low    != null ? `$${fmt(d.low)}`    : '—');
    text('stat-volume', fmtVol(d.volume));
    text('stat-mcap',   fmtBig(d.market_cap));

    show('price-result');
  } catch (e) {
    showError('price', e.message);
  } finally {
    setLoading('price', false);
    setBtn('price-btn', false);
  }
}

// ─── HISTORY ─────────────────────────────────────────────────────
async function handleHistory() {
  const ticker = document.getElementById('history-input').value.trim().toUpperCase();
  const period = document.getElementById('history-period').value;
  if (!ticker) { showError('history', 'Please enter a ticker symbol.'); return; }

  setBtn('history-btn', true);
  setLoading('history', true);

  try {
    const d = await apiFetch(`/history/${ticker}?period=${period}`);

    const labels = d.data.map(r => r.date);
    const closes = d.data.map(r => r.close);
    const opens  = d.data.map(r => r.open);

    const latest = d.data[d.data.length - 1];
    const first  = d.data[0];
    const trend  = closes[closes.length - 1] >= closes[0];
    const lineColor = trend ? '#10b981' : '#ef4444';

    if (historyChartInst) historyChartInst.destroy();
    historyChartInst = new Chart(document.getElementById('historyChart'), {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: `${d.ticker} Close`,
          data: closes,
          borderColor: lineColor,
          backgroundColor: trend
            ? 'rgba(16,185,129,.08)'
            : 'rgba(239,68,68,.08)',
          fill: true,
          tension: 0.3,
          pointRadius: labels.length > 60 ? 0 : 3,
          pointHoverRadius: 5,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` Close: $${fmt(ctx.parsed.y)}`,
            },
          },
        },
        scales: {
          x: { grid: { color: '#1e293b' }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
          y: {
            grid: { color: '#1e293b' },
            ticks: { callback: v => `$${fmt(v)}` },
          },
        },
      },
    });

    text('history-ticker-label', d.ticker);
    const periodMap = { '1d':'1 Day','5d':'5 Days','1mo':'1 Month','3mo':'3 Months','6mo':'6 Months','1y':'1 Year' };
    text('history-period-label', periodMap[d.period] || d.period);

    const summary = document.getElementById('history-ohlcv-summary');
    summary.innerHTML = `
      <span>Open <b>$${fmt(latest.open)}</b></span>
      <span>High <b style="color:#10b981">$${fmt(latest.high)}</b></span>
      <span>Low <b style="color:#ef4444">$${fmt(latest.low)}</b></span>
      <span>Close <b>$${fmt(latest.close)}</b></span>
      <span>Volume <b>${fmtVol(latest.volume)}</b></span>
      <span>Period Change <b style="color:${trend?'#10b981':'#ef4444'}">${trend?'+':''}${fmt(((closes[closes.length-1]-closes[0])/closes[0])*100)}%</b></span>
    `;

    show('history-result');
  } catch (e) {
    showError('history', e.message);
  } finally {
    setLoading('history', false);
    setBtn('history-btn', false);
  }
}

// ─── INDICATORS ──────────────────────────────────────────────────
async function handleIndicators() {
  const ticker = document.getElementById('indicators-input').value.trim().toUpperCase();
  if (!ticker) { showError('indicators', 'Please enter a ticker symbol.'); return; }

  setBtn('indicators-btn', true);
  setLoading('indicators', true);

  try {
    const d = await apiFetch(`/indicators/${ticker}`);

    text('ind-ticker-label', d.ticker);
    text('ind-close',  `$${fmt(d.latest_close)}`);

    const changeEl = document.getElementById('ind-change');
    const chg = d.daily_change_pct;
    changeEl.textContent = chg != null ? `${chg >= 0 ? '+' : ''}${fmt(chg)}%` : '—';
    changeEl.className = `stat-value ${chg >= 0 ? 'green' : 'red'}`;

    text('ind-ma20', d.MA20 != null ? `$${fmt(d.MA20)}` : '—');
    text('ind-ma50', d.MA50 != null ? `$${fmt(d.MA50)}` : '—');

    const sigEl = document.getElementById('ind-signal');
    if (d.signal === 'BUY') {
      sigEl.textContent = '▲ BUY';
      sigEl.className = 'signal-badge signal-buy';
    } else if (d.signal === 'SELL') {
      sigEl.textContent = '▼ SELL';
      sigEl.className = 'signal-badge signal-sell';
    } else {
      sigEl.textContent = 'N/A';
      sigEl.className = 'signal-badge signal-na';
    }

    // Mini chart: close vs MA20 vs MA50
    const histData = await apiFetch(`/history/${ticker}?period=3mo`);
    const labels = histData.data.map(r => r.date);
    const closes = histData.data.map(r => r.close);

    // Compute MAs client-side for chart overlay
    function rollingMean(arr, w) {
      return arr.map((_, i) => i < w - 1 ? null : arr.slice(i - w + 1, i + 1).reduce((a, b) => a + b, 0) / w);
    }
    const ma20arr = rollingMean(closes, 20);
    const ma50arr = rollingMean(closes, 50);

    if (indicatorsChartInst) indicatorsChartInst.destroy();
    indicatorsChartInst = new Chart(document.getElementById('indicatorsChart'), {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Close',
            data: closes,
            borderColor: '#60a5fa',
            backgroundColor: 'rgba(96,165,250,.07)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
          },
          {
            label: 'MA20',
            data: ma20arr,
            borderColor: '#f59e0b',
            borderDash: [4, 3],
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 1.5,
            fill: false,
          },
          {
            label: 'MA50',
            data: ma50arr,
            borderColor: '#a78bfa',
            borderDash: [6, 4],
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 1.5,
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: true, position: 'top', labels: { boxWidth: 12, padding: 14 } },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? '$' + fmt(ctx.parsed.y) : '—'}`,
            },
          },
        },
        scales: {
          x: { grid: { color: '#1e293b' }, ticks: { maxTicksLimit: 7, maxRotation: 0 } },
          y: { grid: { color: '#1e293b' }, ticks: { callback: v => `$${fmt(v)}` } },
        },
      },
    });

    show('indicators-result');
  } catch (e) {
    showError('indicators', e.message);
  } finally {
    setLoading('indicators', false);
    setBtn('indicators-btn', false);
  }
}

// ─── COMPARE ─────────────────────────────────────────────────────
async function handleCompare() {
  const raw = document.getElementById('compare-input').value.trim();
  if (!raw) { showError('compare', 'Please enter at least one ticker symbol.'); return; }

  setBtn('compare-btn', true);
  setLoading('compare', true);

  try {
    const d = await apiFetch(`/compare?tickers=${encodeURIComponent(raw)}`);
    const entries = Object.entries(d.comparison);

    const tbody = document.getElementById('compare-tbody');
    tbody.innerHTML = '';

    const chartLabels  = [];
    const chartChanges = [];
    const chartColors  = [];

    for (const [sym, info] of entries) {
      chartLabels.push(sym);
      const chg = info.change_1mo_pct;
      chartChanges.push(chg ?? 0);
      chartColors.push(chg == null ? '#64748b' : chg >= 0 ? '#10b981' : '#ef4444');

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><span class="ticker-badge">${sym}</span></td>
        <td>${info.latest_close != null ? '$' + fmt(info.latest_close) : '<span style="color:var(--muted)">N/A</span>'}</td>
        <td class="${chg == null ? 'change-neutral' : chg >= 0 ? 'change-positive' : 'change-negative'}">
          ${chg != null ? (chg >= 0 ? '+' : '') + fmt(chg) + '%' : '—'}
          ${info.error ? `<br><small style="color:var(--muted)">${info.error}</small>` : ''}
        </td>
        <td class="${chg == null ? '' : chg >= 0 ? 'trend-up' : 'trend-down'}">${chg == null ? '—' : chg >= 0 ? '▲' : '▼'}</td>
      `;
      tbody.appendChild(tr);
    }

    if (compareChartInst) compareChartInst.destroy();
    compareChartInst = new Chart(document.getElementById('compareChart'), {
      type: 'bar',
      data: {
        labels: chartLabels,
        datasets: [{
          label: '1-Month Change (%)',
          data: chartChanges,
          backgroundColor: chartColors.map(c => c + '33'),
          borderColor: chartColors,
          borderWidth: 2,
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.parsed.y >= 0 ? '+' : ''}${fmt(ctx.parsed.y)}%`,
            },
          },
        },
        scales: {
          x: { grid: { color: '#1e293b' } },
          y: {
            grid: { color: '#1e293b' },
            ticks: { callback: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` },
          },
        },
      },
    });

    show('compare-result');
  } catch (e) {
    showError('compare', e.message);
  } finally {
    setLoading('compare', false);
    setBtn('compare-btn', false);
  }
}
