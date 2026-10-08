# CKP Trading System

Paper-first Telegram signal execution service. Python 3.11, Postgres ledger, CCXT market data, optional Kronos inference, local PaperBroker by default.

## Safety and mode policy

- `LIVE_MODE` defaults to the exact string `false`. Only exact `LIVE_MODE=true` is accepted as opt-in; `BINGX_DEMO=false` alone never enables live execution.
- First startup stores `PAPER_START_TS` in Postgres. It is durable across restarts. `PAPER_DAYS` defaults to 7. Every startup remains local paper until the full period has elapsed.
- After the paper period, the service still remains paper unless `LIVE_MODE=true`. With that opt-in, `BINGX_DEMO=true` selects BingX demo; `BINGX_DEMO=false` selects production BingX.
- The completion message is sent to the Telegram account's Saved Messages once and logged. The timer never turns live execution on automatically.
- Keep `.env` outside the image. The Docker build context excludes `.env`, logs, data, Telegram sessions, and VCS state. Use `docker compose config` carefully because it can display environment values.

## Setup on CT106

1. Install Docker/Compose and PostgreSQL in CT106. The app uses host networking to connect to Postgres at `127.0.0.1:5432`; this avoids exposing Postgres on the container bridge.
2. Create a least-privilege Postgres login and database, then set `DATABASE_URL` in `.env` (URL-encode reserved characters in the password).
3. Copy `.env.example` to `.env`, set Telegram API credentials/channel and BingX demo credentials. Keep `LIVE_MODE=false`, `BINGX_DEMO=true`, `PAPER_DAYS=7`.
4. Build/run: `docker compose build --pull && docker compose up -d`.
5. Check `curl http://127.0.0.1:8080/health`; readiness requires Telegram connected, Postgres reachable, and Kronos loaded or explicitly degraded. The JSON reports `paper.days_remaining`.

## Configuration

- `DATABASE_URL`: Postgres DSN; required for durable state.
- `LIVE_MODE`: exact `true` required after the full paper period; every other value is paper mode.
- `BINGX_DEMO`: true selects demo after live opt-in; false selects production only when the paper timer and `LIVE_MODE=true` also pass.
- `PAPER_DAYS`, `PAPER_START_TS`: minimum paper duration and optional first-start seed. Postgres `system_state.paper_start_ts` is authoritative after first boot.
- `RISK_PER_TRADE`, `MAX_DAILY_LOSS`, `MAX_POSITION_NOTIONAL`, `MAX_AGGREGATE_NOTIONAL`, `MAX_CONCURRENT_POSITIONS`: risk limits.
- `KRONOS_MODEL`, `KRONOS_TOKENIZER`, `KRONOS_DEVICE`: defaults use NeoQuasar/Kronos-small and Kronos-Tokenizer-base; device auto-detects CUDA or CPU. Model load failures set health status to degraded and predictions remain optional. When available, forecast volatility affects fallback stop sizing; `KRONOS_REQUIRE_ALIGNMENT=true` can additionally require forecast direction to match the signal.
- `HEALTH_HOST` defaults to loopback; `HEALTH_PORT` defaults to 8080. Local kill switch endpoints: `POST /kill-switch`, `POST /kill-switch/disable`.

## Durability and operations

Postgres stores Telegram deduplication/inbox state, system state, orders, fills, positions, daily P&L, and events. The Telegram session lives in the persistent `./data/telegram_session` bind mount (directory 0700, session 0600). Compose uses `restart: unless-stopped`, a Docker healthcheck, and 10 MB × 5 Docker JSON log rotation; application logs rotate at 10 MB with 30-day retention.

Kronos is pinned to upstream commit `67b630e67f6a18c9e9be918d9b4337c960db1e9a`. The small model weights are fetched by Hugging Face at runtime into the persistent data cache. The inference adapter loads the tokenizer/model once at process startup.

## Tests

Run `python -m unittest discover -s tests -v`. The suite covers protective sides/failure flatten, daily loss, inbox idempotency, paper countdown/live opt-in, health readiness, and a signal-to-ledger paper fill path.
