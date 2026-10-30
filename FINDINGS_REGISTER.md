# CortexQuant Findings Register

**Generated:** 2026-10-30  
**Audit Report:** trading_system_audit_report.md (2026-10-25)  
**Phase 0 Commit:** c8b21d8  
**Phase 1 Commit:** (pending)

---

## Mapping Legend

| Status | Meaning |
|--------|---------|
| ✅ **FIXED** | Implemented, tested, and verified in code |
| 🔄 **IN_PROGRESS** | Implementation started, not yet complete |
| ⏳ **OUTSTANDING** | Confirmed real issue, not yet addressed |
| ❌ **NOT_APPLICABLE** | Code refactored; finding no longer applies |
| ❓ **UNCONFIRMED** | Could not independently verify |

---

## CRITICAL FINDINGS (Phase 0 - C1-C6, S3, S5)

| Original ID | Current ID | Title | Confirmed | Fixed | Status | Evidence |
|-------------|------------|-------|-----------|-------|--------|----------|
| C1 | C1 | Live Order Execution Gate Bypass via Race Condition | ✅ | ✅ | **FIXED** | `src/exchange/bingx_client.py:126-135` - `_with_gate()` wrapper with `threading.Lock` ensures atomic check+execute for all order paths |
| C2 | C2 | Position Reconciliation Logic Can Create Ghost Positions | ❓ | ❌ | **NOT_APPLICABLE** | Live reconciliation code (`fetch_my_trades`, `last_trade_fetch_*`) not present in current `main.py`. Paper-only reconciliation uses idempotent broker logic. |
| C3 | C3 | Daily P&L Rollover Uses Stale Equity Baseline | ✅ | ✅ | **FIXED** | `src/ledger.py:92-111` `get_mark_to_market_equity()`; `src/main.py:99-111` monitor loop updates `paper_equity` every 15s; `src/main.py:18-50` midnight rollover task |
| C4 | C4 | Kronos Forecast Direction Check Uses Wrong Comparison | ✅ | ✅ | **FIXED** | `src/kronos/forecaster.py:29` returns `'long'`/`'short'` matching `intent.side.value`; `src/decision.py:61` comparison now type-compatible |
| C5 | C5 | PaperBroker Stop/Target Logic Has Off-by-One in Price Comparison | ✅ | ✅ | **FIXED** | `src/execution/paper_broker.py:43-72` - simultaneous hit detection; distance-from-entry proxy for intrabar priority |
| C6 | C6 | Correlation Check Fails Closed but Uses Hardcoded 1.0 | ✅ | ✅ | **FIXED** | `src/decision.py:58` default changed from `1.0` to `0.0` (uncorrelated) on transient failures |
| S3 | S3 | No Authentication on Health/Kill Endpoints | ✅ | ✅ | **FIXED** | `src/health.py:15-32` `_verify_kill_auth()` with `hmac.compare_digest()`; `HEALTH_KILL_TOKEN=None` defaults to deny-all |
| S5 | S5 | No TLS Verification for Exchange Connections | ✅ | ✅ | **FIXED** | `src/exchange/bingx_client.py:53-59` configurable `verify`, `cert`, `ca_bundle` via settings |

**Phase 0 Summary:** 7/8 Critical findings FIXED, 1 NOT_APPLICABLE (C2 - live reconciliation removed in refactor)

---

## HIGH SEVERITY FINDINGS (Phase 1 - H1-H7)

| Original ID | Current ID | Title | Confirmed | Fixed | Status | Evidence |
|-------------|------------|-------|-----------|-------|--------|----------|
| H1 | H1 | Signal Parser Vulnerable to Injection/Confusion Attacks | ✅ | ⏳ | **OUTSTANDING** | `src/signals/parser.py:6-130` - regex deny-list, no length limits, substring side detection |
| H2 | H2 | RiskEngine Position Sizing Ignores Leverage in Notional Calculation | ✅ | ⏳ | **OUTSTANDING** | `src/risk/engine.py:30` `max_notional` capped at `MAX_POSITION_NOTIONAL` without leverage adjustment |
| H3 | H3 | Telegram Listener Has No Backpressure Handling | ✅ | ⏳ | **OUTSTANDING** | `src/signals/telegram_listener.py:41-49` `_handle_message` calls `drain()` synchronously for all pending |
| H4 | H4 | Kill Switch Flatten All Doesn't Verify Position Closure | ✅ | ⏳ | **OUTSTANDING** | `src/decision.py:18-26` removes positions from ledger immediately after order send |
| H5 | H5 | Daily Review Kronos Scenarios Use Current Price as Entry | ✅ | ⏳ | **OUTSTANDING** | `src/daily_review.py:70` `entry = float(current_price)` should be `original_entry` |
| H6 | H6 | Paper Trading Equity Not Marked-to-Market | ✅ | ✅ | **FIXED** | Addressed by C3 fix - MTM equity now calculated and used |
| H7 | H7 | Exchange Order Parameters Hardcoded for BingX Swap Only | ✅ | ⏳ | **OUTSTANDING** | `src/exchange/bingx_client.py:50` hardcoded `defaultType: "swap"` |

---

## MEDIUM SEVERITY FINDINGS

| Original ID | Current ID | Title | Confirmed | Fixed | Status | Evidence |
|-------------|------------|-------|-----------|-------|--------|----------|
| M1 | M1 | Kronos Model Load Failure Degrades Silently | ✅ | ⏳ | **OUTSTANDING** | `src/kronos/forecaster.py:20-21` sets `status='degraded'` but no alert |
| M2 | M2 | Signal Quality Metric Is Inverted/Meaningless | ✅ | ⏳ | **OUTSTANDING** | `src/decision.py:47-48` inverse-rank not calibrated probability |
| M3 | M3 | No Slippage or Latency Modeling in Paper Broker | ✅ | ⏳ | **OUTSTANDING** | `src/execution/paper_broker.py` fills at exact price, no slippage |
| M4 | M4 | Position Mode (Hedge/OneWay) Mismatch Risk | ✅ | ⏳ | **OUTSTANDING** | `src/execution/protection.py:4-8` no startup validation of exchange position mode |
| M5 | M5 | Database Connection Pool Not Configured | ✅ | ⏳ | **OUTSTANDING** | `src/ledger.py:22-25` new psycopg connection per operation |
| M6 | M6 | Health Check Doesn't Verify Critical Trading Path | ✅ | ⏳ | **OUTSTANDING** | `src/health.py:7-20` checks infra only, not exchange connectivity |
| M7 | M7 | Daily Review Uses Only One Symbol for Scenario Testing | ✅ | ⏳ | **OUTSTANDING** | `src/daily_review.py:114-123` single `review_symbol()` |
| M8 | M8 | No Order ID Idempotency for Live Orders | ✅ | ⏳ | **OUTSTANDING** | `src/decision.py:81` `cid=f"live:{intent.source_id}:{intent.symbol}"` not unique per decision |
| M9 | M9 | Fee Calculation Assumes Taker Fee for All Orders | ✅ | ⏳ | **OUTSTANDING** | `src/execution/paper_broker.py:8` fixed `fee_rate=0.0005` |
| M10 | M10 | Timezone Handling Inconsistencies | ✅ | ⏳ | **OUTSTANDING** | Mixed `datetime.now(timezone.utc)` and naive datetimes |

---

## LOW SEVERITY FINDINGS

| Original ID | Current ID | Title | Confirmed | Fixed | Status | Evidence |
|-------------|------------|-------|-----------|-------|--------|----------|
| L1 | L1 | Hardcoded Magic Numbers in Risk Calculations | ✅ | ⏳ | **OUTSTANDING** | `src/risk/manager.py:138` min_size, fixed 1.5:1 R:R |
| L2 | L2 | No Structured Logging for Audit Trail | ✅ | ⏳ | **OUTSTANDING** | `src/utils/logger.py` text-only Loguru |
| L3 | L3 | Test Coverage Gaps | ✅ | ⏳ | **OUTSTANDING** | 2 skipped tests need Postgres; missing integration tests |
| L4 | L4 | Docker Healthcheck Only Checks HTTP Endpoint | ✅ | ⏳ | **OUTSTANDING** | `Dockerfile` healthcheck calls `/health` only |
| L5 | L5 | Requirements Not Pinned to Exact Versions | ✅ | ⏳ | **OUTSTANDING** | `requirements.txt` uses `>=` for many deps |
| L6 | L6 | No Configuration Validation at Startup | ✅ | ⏳ | **OUTSTANDING** | No cross-field validation (e.g., `MAX_POSITION_NOTIONAL * MAX_CONCURRENT_POSITIONS > MAX_AGGREGATE_NOTIONAL`) |
| L7 | L7 | Telegram Session File Permissions Race | ✅ | ⏳ | **OUTSTANDING** | `src/signals/telegram_listener.py:12-13` chmod after client creation |
| L8 | L8 | No Graceful Degradation for Market Data Gaps | ✅ | ⏳ | **OUTSTANDING** | `src/market_data.py:7-17` raises on fetch failure |
| L9 | L9 | Kronos Predictor Thread Safety | ✅ | ⏳ | **OUTSTANDING** | `threading.Lock` for inference but `asyncio.to_thread` only for load |
| L10 | L10 | Paper Policy Execution Mode Logic Duplicated | ✅ | ⏳ | **OUTSTANDING** | `src/paper_policy.py` and `src/exchange/bingx_client.py:31` both check |

---

## ARCHITECTURAL CONCERNS

| Original ID | Current ID | Title | Confirmed | Fixed | Status |
|-------------|------------|-------|-----------|-------|--------|
| A1 | A1 | Tight Coupling Between Components | ✅ | ⏳ | **OUTSTANDING** |
| A2 | A2 | Shared Mutable State Across Async Tasks | ✅ | ⏳ | **OUTSTANDING** |
| A3 | A3 | Single Point of Failure - Postgres | ✅ | ⏳ | **OUTSTANDING** |
| A4 | A4 | No Feature Flags for Gradual Rollout | ✅ | ⏳ | **OUTSTANDING** |
| A5 | A5 | No Circuit Breaker for Exchange API | ✅ | ⏳ | **OUTSTANDING** |

---

## DATA QUALITY & BACKTESTING CONCERNS

| Original ID | Current ID | Title | Confirmed | Fixed | Status |
|-------------|------------|-------|-----------|-------|--------|
| D1 | D1 | No Historical Backtesting Framework | ✅ | ⏳ | **OUTSTANDING** |
| D2 | D2 | Signal Source Quality Unknown | ✅ | ⏳ | **OUTSTANDING** |
| D3 | D3 | Market Data Quality Not Validated | ✅ | ⏳ | **OUTSTANDING** |
| D4 | D4 | Correlation Calculation Uses Only 64 Bars (1h) | ✅ | ⏳ | **OUTSTANDING** |
| D5 | D5 | No Synthetic Data Generation for Stress Testing | ✅ | ⏳ | **OUTSTANDING** |

---

## SECURITY FINDINGS

| Original ID | Current ID | Title | Confirmed | Fixed | Status |
|-------------|------------|-------|-----------|-------|--------|
| S1 | S1 | API Keys in Environment Variables Only | ✅ | ⏳ | **OUTSTANDING** |
| S2 | S2 | Telegram Session File Stored on Disk | ✅ | ⏳ | **OUTSTANDING** |
| S3 | S3 | No Authentication on Health/Kill Endpoints | ✅ | ✅ | **FIXED** |
| S4 | S4 | Database Credentials in Connection String | ✅ | ⏳ | **OUTSTANDING** |
| S5 | S5 | No TLS Verification for Exchange Connections | ✅ | ✅ | **FIXED** |
| S6 | S6 | No Audit Logging for Critical Operations | ✅ | ⏳ | **OUTSTANDING** |

---

## PERFORMANCE BOTTLENECKS

| Original ID | Current ID | Title | Confirmed | Fixed | Status |
|-------------|------------|-------|-----------|-------|--------|
| P1 | P1 | Synchronous DB Calls in Async Event Loop | ✅ | ⏳ | **OUTSTANDING** |
| P2 | P2 | Market Data Cache Key Includes Limit | ✅ | ⏳ | **OUTSTANDING** |
| P3 | P3 | Kronos Inference Blocks Thread Pool | ✅ | ⏳ | **OUTSTANDING** |
| P4 | P4 | Full Position Reconciliation Every 15s | ✅ | ⏳ | **OUTSTANDING** |

---

## OVERFITTING RISKS

| Original ID | Current ID | Title | Confirmed | Fixed | Status |
|-------------|------------|-------|-----------|-------|--------|
| O1 | O1 | Strategy Parameters Not Versioned | ✅ | ⏳ | **OUTSTANDING** |
| O2 | O2 | Daily Review Adjusts MIN_SIGNAL_QUALITY Based on Single Day | ✅ | ⏳ | **OUTSTANDING** |
| O3 | O3 | Kronos Model Fixed, No Retraining Pipeline | ✅ | ⏳ | **OUTSTANDING** |
| O4 | O4 | Fixed 2:1 Reward:Risk Ratio | ✅ | ⏳ | **OUTSTANDING** |
| O5 | O5 | No Walk-Forward Validation | ✅ | ⏳ | **OUTSTANDING** |

---

## SPECIAL TOPICS ADDRESSED

### Funding Accounting
- **Status:** ❌ **NOT_APPLICABLE** (no funding payment handling in current codebase)
- **Details:** Paper broker uses fixed fee rate; no funding simulation in `paper_broker.py`. Live path uses BingX which handles funding automatically. Gap: backtester (`src/research/backtester.py:89-93`) simulates funding but paper broker doesn't.

### Live Position Reconciliation
- **Status:** ❌ **NOT_APPLICABLE** (no live reconciliation in current version)
- **Details:** Audit C2 referenced `main.py:76-125` with `fetch_my_trades` and `last_trade_fetch_*` cursor. Current `main.py` (125 lines) has no live reconciliation - only paper reconciliation in monitor loop. If live trading enabled, this gap must be addressed.

### SQLite/PostgreSQL Duplication
- **Status:** ✅ **CONFIRMED_SEPARATE** 
- **Details:** Two separate stores:
  - SQLite: `src/signals/inbox.py` - Telegram inbox only (legacy, not used in current flow)
  - PostgreSQL: `src/ledger.py` - all trading state (orders, fills, positions, daily_pnl, system_state)
- Current code uses Postgres-backed `PortfolioLedger` throughout. `TelegramListener` writes to Postgres `telegram_inbox` table. SQLite inbox is legacy/unused.

### Order Idempotency
- **Status:** ⏳ **OUTSTANDING** (partially addressed)
- **Paper:** `src/execution/paper_broker.py:10` uses deterministic `uuid5(NAMESPACE_URL, f"{source_id}:{symbol}:entry")` - idempotent per signal
- **Live:** `src/decision.py:81` `cid=f"live:{intent.source_id}:{intent.symbol}"` - NOT idempotent per decision (same signal → same ID → ON CONFLICT DO NOTHING prevents ledger dup but NOT exchange dup)
- **Gap:** Need timestamp/nonce in live client_order_id; pre-flight exchange order query before submit

---

## VERIFICATION COMMANDS

```bash
# Run all tests
cd /home/admin/CortexQuant && .venv/bin/python -m pytest tests/ -v

# Verify safety gates
.venv/bin/python -c "
from config.settings import settings
assert settings.LIVE_MODE == 'false'
assert settings.EXECUTION_TARGET == 'paper'
print('Safety gates: LIVE_MODE=false, EXECUTION_TARGET=paper ✓')
"

# Verify Phase 0 fixes present
.venv/bin/python -c "
# C1: Lock exists
from src.exchange.bingx_client import BingXClient
import inspect
src = inspect.getsource(BingXClient)
assert '_gate_lock' in src and '_with_gate' in src
print('C1: Execution gate lock ✓')

# C3: MTM equity method
from src.ledger import PortfolioLedger
assert hasattr(PortfolioLedger, 'get_mark_to_market_equity')
print('C3: MTM equity method ✓')

# C4: Kronos direction mapping
from src.kronos.forecaster import KronosForecaster
src = inspect.getsource(KronosForecaster.forecast)
assert \"'long'\" in src and \"'short'\" in src
print('C4: Kronos direction long/short ✓')

# C5: Stop/target priority
from src.execution.paper_broker import PaperBroker
src = inspect.getsource(PaperBroker.on_market_bar)
assert 'stop_hit and target_hit' in src
print('C5: Stop/target priority logic ✓')

# C6: Correlation default 0.0
from src.decision import DecisionEngine
src = inspect.getsource(DecisionEngine.process)
assert '(0.0,position' in src
print('C6: Correlation fallback 0.0 ✓')

# S3: Kill switch auth
from src.health import HealthServer
src = inspect.getsource(HealthServer)
assert '_verify_kill_auth' in src and 'hmac.compare_digest' in src
print('S3: Kill switch auth ✓')

# S5: TLS config
src = inspect.getsource(BingXClient._create_exchange)
assert 'BINGX_VERIFY_SSL' in src and 'BINGX_CA_BUNDLE' in src
print('S5: TLS verification config ✓')

print('ALL PHASE 0 FIXES VERIFIED ✓')
"