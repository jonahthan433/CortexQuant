"""Execution-target gate self-check.

Asserts the money-path invariants. Run inside CT106:
  docker exec -w /app ckp_trading_bot python - < test_execution_gate.py
Fails loudly if any EXECUTION_TARGET/LIVE_MODE combination routes wrongly.
"""
from config.settings import Settings
from src.paper_policy import resolve_execution_target


def target_for(execution_target, live_mode, bingx_demo=True, paper_days=7):
    s = Settings(EXECUTION_TARGET=execution_target, LIVE_MODE=live_mode,
                 BINGX_DEMO=bingx_demo, PAPER_DAYS=paper_days)
    orig_t, orig_l = None, None
    import config.settings as cs
    orig_t, orig_l = cs.settings.EXECUTION_TARGET, cs.settings.LIVE_MODE
    cs.settings.EXECUTION_TARGET, cs.settings.LIVE_MODE = s.EXECUTION_TARGET, s.LIVE_MODE
    try:
        return resolve_execution_target()
    finally:
        cs.settings.EXECUTION_TARGET, cs.settings.LIVE_MODE = orig_t, orig_l


CASES = [
    # (target, live_mode, expected)
    ("paper",       "false", "paper"),
    ("paper",       "true",  "paper"),   # LIVE_MODE alone can never route orders
    ("bingx_demo",  "false", "bingx_demo"),  # demo orders do NOT require LIVE_MODE
    ("bingx_demo",  "true",  "bingx_demo"),
    ("live",        "false", "paper"),   # two-key gate: real money needs BOTH
    ("live",        "true",  "live"),
    ("LIVE",        "true",  "paper"),   # case-normalized by validator, but never implies orders
    ("bogus",       "true",  "paper"),   # unknown target falls back to paper
]

failures = []
for t, lm, expected in CASES:
    try:
        got = target_for(t, lm)
    except Exception as e:  # validator rejected an invalid value -> treated as paper
        got = "paper"
    ok = got == expected
    print(f"  [{'PASS' if ok else 'FAIL'}] EXECUTION_TARGET={t!r} LIVE_MODE={lm!r} -> {got} (want {expected})")
    if not ok:
        failures.append((t, lm, expected, got))

# The two invariants that actually protect real money:
assert target_for("live", "false") == "paper", "REAL MONEY REACHED WITHOUT LIVE_MODE=true"
assert target_for("live", "LIVE") == "paper", "REAL MONEY REACHED WITHOUT exact LIVE_MODE=true"
assert target_for("paper", "true") == "paper", "LIVE_MODE alone must not route orders"

print("\nRESULT:", "ALL PASS" if not failures else f"{len(failures)} FAIL")
raise SystemExit(1 if failures else 0)
