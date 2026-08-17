# Investing Agent

Long-term Indian equity investment research agent powered by LangGraph + Zerodha Kite MCP.

> **This is a research assistant, not a trading bot.**  
> All trade actions require explicit human approval. Broker execution is disabled by default.

---

## Architecture

```
User → FastAPI → LangGraph Orchestrator
                   ├── Router Node (intent detection)
                   ├── Portfolio Node (Zerodha Kite MCP - read-only)
                   ├── Memory Node (thesis + investor profile)
                   ├── Facts Node (company fundamentals, filings)
                   ├── Events Node (corporate actions calendar)
                   ├── News/Research Node
                   ├── Earnings Estimator
                   ├── Valuation Node
                   ├── Risk Node (deterministic checks)
                   ├── Decision Node (BUY/ADD/HOLD/REDUCE/AVOID)
                   └── Approval Interrupt → [Human] → Broker Tool
                        (DISABLED by default)
```

**Storage:** PostgreSQL + pgvector  
**Source hierarchy:** Exchange filings > Brokerage research > TV/Social  

---

## Phase 1 Status

- [x] Repository structure
- [x] PostgreSQL schema (9 tables + migrations)
- [x] BrokerGateway abstraction (MockBrokerGateway + Zerodha skeleton)
- [x] LangGraph state + base graph (router → portfolio/memory/events → decision)
- [x] Pydantic schemas for all domain objects
- [x] FastAPI endpoints: `/portfolio`, `/watchlist`, `/company`, `/agent/query`
- [x] Investment thesis CRUD
- [x] Structured logging (structlog)
- [x] Unit tests + injection security tests
- [x] Docker Compose for PostgreSQL
- [ ] Live Zerodha MCP connection (Phase 2)
- [ ] Fundamentals ingestion (Phase 3)
- [ ] Vector search (Phase 3)

---

## Quick Start

### 1. Prerequisites

- Python 3.12+
- Docker

### 2. Clone and setup

```bash
cd investing-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env — minimum required: DATABASE_URL
# NEVER set BROKER_EXECUTION_ENABLED=true until Phase 8
```

### 4. Start PostgreSQL

```bash
docker-compose up postgres -d
# Wait for health check to pass (about 10 seconds)
```

### 5. Run migrations

```bash
alembic upgrade head
```

### 6. Seed development data

```bash
python scripts/seed_dev_data.py
```

### 7. Start the API server

```bash
uvicorn investing_agent.app.main:app --reload --port 8000
```

### 8. Test the API

```bash
# Health check
curl http://localhost:8000/health

# Sync portfolio (mock data in Phase 1)
curl -X POST http://localhost:8000/portfolio/sync

# Get portfolio
curl http://localhost:8000/portfolio

# Add to watchlist
curl -X POST http://localhost:8000/watchlist \
  -H "Content-Type: application/json" \
  -d '{"symbol": "COCHIN", "reason": "Defence shipbuilding play"}'

# Ask the agent
curl -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is my portfolio?"}'

# Ask about a specific stock
curl -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyze BEL", "symbols": ["BEL"]}'
```

---

## Running Tests

```bash
# Unit tests only (no database required)
pytest tests/unit -v

# Including security/injection tests
pytest tests/unit -v -m "injection"

# Integration tests (requires postgres_test container)
docker-compose up postgres_test -d
pytest tests/integration -v -m "integration"

# Coverage report
pytest tests/unit --cov=investing_agent --cov-report=term-missing
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+psycopg://...` | PostgreSQL connection URL |
| `BROKER_EXECUTION_ENABLED` | `false` | **Must remain false until Phase 8** |
| `ZERODHA_MCP_URL` | `https://mcp.kite.trade/mcp` | Kite MCP server URL |
| `ZERODHA_ACCESS_TOKEN` | — | Daily token from Kite (Phase 2) |
| `ANTHROPIC_API_KEY` | — | Claude API key (Phase 4) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `APP_ENV` | `development` | Environment name |

---

## Implementation Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Setup, schema, BrokerGateway, base graph | ✅ Complete |
| 2 | Live Zerodha MCP, portfolio snapshots | 🔜 Next |
| 3 | Company fundamentals, filings, pgvector | 🔜 |
| 4 | Investment memory, thesis, recommendation engine | 🔜 |
| 5 | Brokerage research, YouTube recommendation ingestion | 🔜 |
| 6 | Order book analysis, earnings estimation | 🔜 |
| 7 | Backtesting, source performance scoring | 🔜 |
| 8 | Risk controls, human approval, order execution | 🔜 |

---

## Safety Guarantees

- No live broker execution in Phase 1.
- `BROKER_EXECUTION_ENABLED=false` is enforced at the gateway layer, not just config.
- All trade proposals require human approval via LangGraph interrupt.
- Evidence citations retained for every recommendation (audit trail).
- Prompt injection tests verify untrusted content cannot reach privileged tools.
- Secrets are `SecretStr` in Pydantic settings — never logged as plain text.

---

## References

- [Zerodha Kite MCP](https://zerodha.com/products/mcp/)
- [Kite Connect API](https://kite.trade/docs/connect/v3/)
- [LangGraph](https://langchain-ai.github.io/langgraph/)
- [NSE Corporate Filings](https://www.nseindia.com/companies-listing/corporate-filings-actions)
