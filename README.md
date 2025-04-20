# Crypto Supervisor

**Crypto Supervisor** is a Python-based real-time cryptocurrency price monitoring service. It connects to multiple exchanges via WebSocket, ingests live ticker data, maintains an in-memory store of latest prices, computes movement metrics, and triggers email alerts when predefined thresholds (e.g., percent change over 24 h or a short window) are crossed.

---

## Table of Contents

1. [Features](#features)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Configuration](#configuration)
5. [Usage](#usage)
6. [Project Structure](#project-structure)
7. [Extending the Service](#extending-the-service)
8. [Logging & Monitoring](#logging--monitoring)

---

## Features

- **Real-time data ingestion** via WebSocket connectors for Binance and Coinbase Pro.
- **Modular connector interface** — add new exchanges by implementing the `BaseConnector`.
- **Thread-safe in-memory storage** of the latest tick data.
- **Alert engine** for:
  - 24 h percentage change (default 5 %).
  - Short-term window percentage change (default 2 % over 1 h).
- **Email notifications** through configurable SMTP settings.
- **Graceful shutdown** on SIGINT/SIGTERM.

---

## Prerequisites

- **Operating System:** Ubuntu 22.04 (or any Linux/macOS with Python 3.9+)
- **Python:** 3.9 or later
- **Network access** to exchange WebSocket endpoints and your SMTP server

---

## Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url> crypto-supervisor
   cd crypto-supervisor
   ```

2. **Create & activate a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

> The `requirements.txt` should include at least:
> ```text
> websockets
> pyyaml
> ```

---

## Configuration

All runtime settings live in the YAML file at `config/exchanges.yaml`:

```yaml
exchanges:
  binance:
    symbols:
      - BTC/USDT
      - ETH/USDT
    reconnect_delay: 5         # seconds
  coinbase:
    symbols:
      - BTC-USDT
      - ETH-USDT
    reconnect_delay: 5

alerts:
  thresholds:
    pct_24h: 0.05               # 5% move over 24h
    window_24h_ms: 86400000
    pct_short: 0.02             # 2% move in short window
    window_short_ms: 3600000
  email:
    smtp_host: smtp.example.com
    smtp_port: 587
    username: user@example.com
    password: yourpassword
    from_addr: alerts@example.com
    to_addrs:
      - user@example.com
```

- **exchanges**: mapping of exchange names to their symbol lists and reconnect delays.  
- **alerts.thresholds**: percent-change thresholds and window sizes (in milliseconds).  
- **alerts.email**: SMTP credentials and recipient list.

---

## Usage

Run the supervisor with:

```bash
python -m supervisor.main --config config/exchanges.yaml
```

Or, from the root:

```bash
python supervisor/main.py --config config/exchanges.yaml
```

The service will start connectors for each configured exchange, log status to console, and send email alerts upon threshold breaches.

---

## Project Structure

```
crypto-supervisor/
├── README.md               # This file
├── requirements.txt        # Python dependencies
├── config/                 # Static configuration files
│   └── exchanges.yaml      # YAML config for exchanges & alerts
└── supervisor/             # Python application package
    ├── __init__.py
    ├── main.py             # Entry point & orchestrator
    ├── config.py           # Loader & validator for exchanges.yaml
    ├── connectors/         # Exchange-specific WebSocket clients
    │   ├── base.py         # Abstract connector interface
    │   ├── binance.py      # BinanceConnector implementation
    │   └── coinbase.py     # CoinbaseConnector implementation
    ├── processors/         # Core data processing logic
    │   └── handler.py      # TickHandler: storage, metrics, alert dispatch
    ├── storage/            # Data storage backends
    │   └── memory.py       # Thread-safe in-memory store
    └── alerts/             # Alerting mechanisms
        ├── engine.py       # AlertEngine: threshold checks, dedupe
        └── email.py        # EmailSender: SMTP integration
```

---

## Extending the Service

1. **Add a new exchange**:
   - Implement a subclass of `BaseConnector` in `supervisor/connectors/`.
   - Update `config/exchanges.yaml` with the new exchange name and symbols.

2. **Custom storage**:
   - Create a new module in `supervisor/storage/` (e.g., `redis_store.py`).
   - Implement the same interface as `MemoryStore` (`update`, `get_latest`, `get_all`).
   - Swap out in `TickHandler` initialization.

3. **Additional alerts or analytics**:
   - Extend `AlertEngine.check()` or add new processors in `supervisor/processors/`.

---

## Logging & Monitoring

- Logs are printed to stdout with format:<br>`YYYY-MM-DD HH:MM:SS [LEVEL] logger.name: message`  
- Connector errors and reconnect attempts are logged at `ERROR` and `INFO` levels, respectively.  
- Alert triggers are logged at `INFO` level.

For production, consider redirecting logs to a file or log aggregator.

---

*Prepared on Ubuntu 22.04 with Python 3.9+. Please verify your SMTP settings before running.*

