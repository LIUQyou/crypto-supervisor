# Crypto Supervisor v2 🚦

**Crypto Supervisor** is a real‑time cryptocurrency vigilance service.  
It streams live data from exchanges (Binance first), keeps the most‑recent 24 h working‑set in RAM, off‑loads colder data to Redis, and fires four classes of alerts:

* Price moves — 24 h trend & 5 min momentum  
* Order‑flow dominance (buy/sell imbalance)  
* Order‑book stress (spread blow‑out)  
* Liquidity snapshots (optional extras)

Alerts are sent by e‑mail (Gmail‑friendly) with automatic retries and cool‑downs so you don’t get spammed.

---

## Table of Contents
1. [Features](#features)  
2. [Prerequisites](#prerequisites)  
3. [Installation](#installation)  
4. [Configuration](#configuration)  
5. [Usage](#usage)  
6. [Project Structure](#project-structure)  
7. [Extending the Service](#extending-the-service)  
8. [Logging & Monitoring](#logging--monitoring)

---

## Features <a id="features"></a>

| Area | Details |
|------|---------|
| **Real‑time ingestion** | Binance connector streams **ticker (1 s)**, **aggTrade**, and **depth (100 ms)** simultaneously. Reconnects with exponential back‑off + jitter. |
| **Four alert types** | 24 h Δ%, 5 min Δ%, buy/sell imbalance (3 min buckets), spread > X bp for Y s. Each has its own cool‑down. |
| **Hybrid storage** | Latest tick & 24 h deque in RAM; spill to Redis once RSS > *N* GB (configurable). |
| **Async e‑mail** | STARTTLS _or_ implicit TLS, success/failure aware, 2‑retry back‑off. Gmail App‑password ready. |
| **Plug‑in connectors & processors** | Add new exchange or analytics module with one file; everything else auto‑registers. |
| **Graceful shutdown** | SIGINT/SIGTERM closes sockets, drains tasks, exits cleanly. |
| **Python 3.10+** | Modern union‑types (`price: float | None`) and faster asyncio. |

---

## Prerequisites <a id="prerequisites"></a>

| Need | Notes |
|------|-------|
| Python **3.10+** | 3.11 recommended for speed. |
| Redis *(optional)* | Only if you enable `storage.backend: redis`. |
| SMTP | Gmail or any server that supports TLS. |
| Internet | WebSocket access to exchange endpoints. |

---

## Installation <a id="installation"></a>

```bash
git clone https://github.com/LIUQyou/crypto-supervisor.git
cd crypto-supervisor

# conda example
conda create -n crypto-supervisor python=3.11 pip -y
conda activate crypto-supervisor

pip install -r requirements.txt
```

> Need Redis? `docker run -d --name redis -p 6379:6379 redis:7`

---

## Configuration <a id="configuration"></a>

Everything lives in `config/exchanges.yaml`.

```yaml
exchanges:
  binance:
    symbols: [BTC/USDT, ETH/USDT]
    streams: [ticker, aggTrade, depth]   # toggle as needed
    reconnect_delay: 5                   # base seconds for back‑off

alerts:
  # ─ price moves ───────────────────────────
  thresholds:
    pct_24h:        0.05        # 5 % vs 24 h ago
    window_24h_ms:  86400000
    pct_short:      0.01        # 1 % in short window
    window_short_ms: 300000     # 5 min
    cooldown_ms:    600000

  # ─ spread stress ─────────────────────────
  spread:
    threshold_bps:  30
    window_ms:      10000       # 10 s
    cooldown_ms:    600000

  # ─ buy/sell flow ─────────────────────────
  flow:
    threshold:      0.6         # ≥ 60 % imbalance
    bucket_ms:      60000       # 1 min buckets
    window_buckets: 3           # 3 min window
    min_notional:   100000      # USD
    cooldown_ms:    600000

  # ─ e‑mail ────────────────────────────────
  email:
    smtp_host: smtp.gmail.com
    smtp_port: 465                # 465 = implicit TLS
    username: liuqunXX@gmail.com
    password: <Gmail‑App‑password>
    from_addr: liuqunXX@gmail.com
    to_addrs: [liuqunXX@gmail.com]

storage:
  backend: redis                  # "memory" or "redis"
  redis_url: redis://localhost:6379/0
  max_memory_gb: 8
  hot_window_hours: 24
```

Gmail note → create a **16‑digit App password** at <https://myaccount.google.com/apppasswords>.

---

## Usage <a id="usage"></a>

```bash
python -m supervisor.main --config config/exchanges.yaml --log-level INFO
```

* `DEBUG` shows every WebSocket frame and stored tick.  
* Stop with Ctrl‑C; the connector closes gracefully.

---

## Project Structure <a id="project-structure"></a>

```
crypto-supervisor/
├── config/
│   └── exchanges.yaml
├── supervisor/
│   ├── main.py                 # async runner
│   ├── config.py               # YAML loader
│   ├── connectors/
│   │   ├── base.py             # ABC
│   │   └── binance.py          # multi‑stream connector
│   ├── storage/
│   │   ├── memory.py
│   │   └── redis_store.py
│   ├── alerts/
│   │   ├── engine.py
│   │   └── email.py
│   └── processors/
│       ├── handler.py          # price + router
│       ├── flow.py             # buy/sell imbalance
│       └── spread.py           # spread monitor
└── requirements.txt
```

---

## Extending the Service <a id="extending-the-service"></a>

| Task | How |
|------|-----|
| **Another exchange** | `connectors/kraken.py` → subclass `BaseConnector`, add to YAML. |
| **New metric** | Write `processors/volatility.py`, import it in `handler.py`, wire an alert. |
| **Different storage** | Implement `supervisor/storage/postgres_store.py` with the same 3‑method API. |
| **Alternative transport** | Swap e‑mail for Slack by replacing `alerts/email.py` with a webhook sender. |

---

## Logging & Monitoring <a id="logging--monitoring"></a>

* Console format: `YYYY‑MM‑DD HH:MM:SS [LEVEL] logger: message`  
* `INFO` covers startup, reconnects, and alert subjects.  
* `DEBUG` adds raw messages & metric deltas (volume, spread, etc.).  
* Hook into systemd, Docker, or a log‑shipper of your choice.

---

*Last updated 2025‑04‑20 — Crypto Supervisor v2.  
Verify your Gmail App‑password and Redis connection before you run in production.*