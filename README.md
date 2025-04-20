# Crypto Supervisor v2 🚦

**Crypto Supervisor** is a real‑time cryptocurrency monitoring service written in Python.  
It connects to one or more exchanges via WebSockets, keeps the *hot* 24 h price window in RAM (with optional Redis off‑loading), runs rule‑based alerts, and notifies you by e‑mail.

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
| **Real‑time ingestion** | *Binance* connector streams **ticker**, **aggTrade**, and **depth** simultaneously (configurable). <br>Automatic reconnect with exponential back‑off + jitter. |
| **Modular connectors** | Implement `BaseConnector` once and drop it into `supervisor/connectors/`—the runner picks it up dynamically. |
| **Hybrid storage** | *MemoryStore* (default) or *RedisStore* (hot data in RAM, cold data evicted to Redis once the Python process exceeds an 8 GiB threshold—both sizes configurable). |
| **Alert engine** | Thresholds for 24 h change **and** short‑window change with **cool‑down** to avoid spamming. |
| **E‑mail notifications** | STARTTLS *or* implicit‑TLS, async send with retry on failure. |
| **Graceful shutdown** | Handles `SIGINT/SIGTERM`, closes WebSockets, awaits tasks. |
| **Python 3.10+ typing & perf** | Uses the modern `|` union syntax and runs fastest on 3.11. |

---

## Prerequisites <a id="prerequisites"></a>

* **OS**  Linux/macOS (tested on Ubuntu 22.04)  
* **Python**  3.10 or newer  
* **Redis**  *(optional)* running locally or remotely if you enable the Redis backend  
* **SMTP**  server reachable by the host (for alert e‑mails)  
* **Outbound Internet**  to exchange WebSocket endpoints  

---

## Installation <a id="installation"></a>

```bash
# clone
git clone https://github.com/LIUQyou/crypto-supervisor.git
cd crypto-supervisor

# create environment (conda example)
conda create -n crypto-supervisor python=3.11 pip -y
conda activate crypto-supervisor

# install deps
pip install -r requirements.txt
```

> If you plan to use Redis persistence you’ll also need a running Redis server  
> (`docker run -d -p 6379:6379 redis:7` is fine).

---

## Configuration <a id="configuration"></a>

Everything lives in **`config/exchanges.yaml`**.

```yaml
exchanges:
  binance:
    symbols: [BTC/USDT, ETH/USDT]
    streams: [ticker, aggTrade, depth]   # any subset; omit for ticker‑only
    reconnect_delay: 5                   # base seconds for back‑off

alerts:
  thresholds:
    pct_24h: 0.05            # 5 % over 24 h
    pct_short: 0.02          # 2 % inside short window
    window_short_ms: 3600000 # 1 h
    cooldown_ms: 3600000     # suppress repeat alerts for 1 h
  email:
    smtp_host: smtp.example.com
    smtp_port: 465           # 465 = implicit TLS, 587 = STARTTLS
    username: user@example.com
    password: hunter2
    from_addr: alerts@example.com
    to_addrs: [user@example.com]

storage:
  backend: redis                       # "memory" (default) or "redis"
  redis_url: redis://localhost:6379/0
  max_memory_gb: 8                     # spill to Redis after this
  hot_window_hours: 24                 # keep last 24 h in RAM
```

---

## Usage <a id="usage"></a>

```bash
python -m supervisor.main --config config/exchanges.yaml --log-level INFO
```

* `--log-level DEBUG` shows raw WebSocket traffic and every stored tick.  
* Ctrl‑C stops gracefully.

---

## Project Structure <a id="project-structure"></a>

```
crypto-supervisor/
├── config/
│   └── exchanges.yaml        # runtime config
├── supervisor/
│   ├── main.py               # async entry point
│   ├── config.py             # YAML loader + validation
│   ├── connectors/
│   │   ├── base.py           # BaseConnector ABC
│   │   └── binance.py        # multi‑stream Binance connector
│   ├── storage/
│   │   ├── memory.py         # in‑RAM latest‑tick store
│   │   └── redis_store.py    # hot‑cold hybrid store
│   ├── alerts/
│   │   ├── engine.py         # thresholds, cooldown, dedupe
│   │   └── email.py          # async SMTP sender
│   └── processors/
│       └── handler.py        # TickHandler: store + alert pipeline
└── requirements.txt
```

---

## Extending the Service <a id="extending-the-service"></a>

| Task | How |
|------|-----|
| **Add exchange** | Create `FooConnector(BaseConnector)` → import path `supervisor.connectors.foo.py` → add to YAML under `exchanges:`. |
| **Custom storage** | Implement `update/get_latest/get_all` in `supervisor/storage/my_store.py`, declare `backend: my_store` in YAML. |
| **New alert logic** | Add methods in `alerts/engine.py` or drop a new processor under `processors/` and call it from `TickHandler`. |

---

## Logging & Monitoring <a id="logging--monitoring"></a>

* Console format: `YYYY-MM-DD HH:MM:SS [LEVEL] logger.name: message`  
* Levels: `DEBUG` (traffic & state), `INFO` (key milestones), `ERROR` (failures).  
* Pipe stdout to a file or use systemd‑journald / Docker logging driver in production.

---

*Last updated 2025‑04‑20 for Crypto Supervisor v2. Please verify SMTP & Redis credentials before running in production.*