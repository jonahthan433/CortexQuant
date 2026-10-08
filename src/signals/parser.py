import re
from dataclasses import dataclass
from typing import List, Optional
from loguru import logger

COIN_PATTERN = re.compile(
    r"(?:^|[\s:\|])+([1-9]\d*)?\s*\.?\s*([A-Za-z]{2,40}(?:-?USD[TB]?)\b)"
)

# Emoji ranges (common trading app emojis)
EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F5FF]"
    r"|[\U0001F600-\U0001F64F]"
    r"|[\U0001F680-\U0001F6FF]"
    r"|[\U0001F700-\U0001F77F]"
    r"|[\U0001F780-\U0001F7FF]"
    r"|[\U0001F800-\U0001F8FF]"
    r"|[\U0001F900-\U0001F9FF]"
    r"|[\U0001FA00-\U0001FA6F]"
    r"|[\U0001FA70-\U0001FAFF]"
    r"|[\U00002700-\U000027BF]"
    r"|[⏰🔴🟢🟣🟠🟦🟨▶▷⬆⬇↑↓]",
)

QUOTE_SUFFIXES = ("USDT", "BTC", "ETH", "BNB", "USDC", "EUR", "GBP", "JPY", "USD")

# Non-coin words that should never be treated as tradeable tickers.
# NOTE: Do NOT include actual coin tickers here — they ARE tradeable.
NON_COIN_WORDS = {
    "TOP", "LONG", "SHORT", "USDT", "RSI", "TSI", "FUNDING", "VOLUME",
    "PRICE", "OPPORTUNIT", "CONFIRM", "SETUP", "SUGGEST", "PICK",
    "SCAN", "CKP", "SCOUT", "LIST", "ADMIN", "UPDATE", "BONUS",
}


def _strip_quote(ticker: str) -> str:
    """Strip known quote suffix. 'BTCUSDT' -> 'BTC'."""
    upper = ticker.upper().replace("-", "")
    for q in sorted(QUOTE_SUFFIXES, key=len, reverse=True):
        if upper.endswith(q) and len(upper) > len(q):
            return upper[: -len(q)]
    return upper


@dataclass
class SignalCandidate:
    symbol: str
    side: str
    rank: Optional[int] = None
    raw_line: str = ""
    confidence: float = 0.0


def _detect_side(line: str, prev_side: Optional[str]) -> Optional[str]:
    """Returns NEW side if section header found, else None (keeps prev_side)."""
    upper = line.upper()

    # 1. Explicit section headers: "LONG OPPORTUNITIES", "SHORT TOP", etc.
    if re.search(r"\bLONG\b", upper) and (
        "OPPORTUNIT" in upper or "TOP" in upper or "SCOUT" in upper
        or "SETUP" in upper or "CONFIRM" in upper
    ):
        return "long"
    if re.search(r"\bSHORT\b", upper) and (
        "OPPORTUNIT" in upper or "TOP" in upper or "SCOUT" in upper
        or "SETUP" in upper or "CONFIRM" in upper
    ):
        return "short"

    # 2. Emoji/prefix markers + LONG/SHORT word (🔴 LONG, 🟢 SHORT, etc.)
    if EMOJI_RE.search(upper[:10]) and ("LONG" in upper or "SHORT" in upper):
        if "SHORT" in upper:
            return "short"
        return "long"

    # 3. Inline markers: "LONG:", "→ LONG", etc.
    if re.search(r"(?:LONG|SHORT)\s*[:→]|[→←]\s*(?:LONG|SHORT)\b", upper):
        m = re.search(r"(LONG|SHORT)", upper)
        if m:
            return "long" if m.group() == "LONG" else "short"

    return None


def parse_ckp_message(text):
    # SQL_ASCII DB encoding makes psycopg return raw_text as bytes; decode here
    # so every caller (translate, translator tests) is safe at the shared entry point.
    if isinstance(text, bytes): text = text.decode("utf-8", "replace")
    if not text:
        return []
    candidates: List[SignalCandidate] = []
    lines = text.splitlines()
    current_side: Optional[str] = None
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        detected = _detect_side(line_clean, current_side)
        if detected is not None:
            current_side = detected
            continue
        if current_side is None:
            continue
        for match in COIN_PATTERN.finditer(line_clean):
            rank_str = match.group(1) or ""
            raw_ticker = match.group(2).upper()
            ticker = _strip_quote(raw_ticker)
            if ticker in NON_COIN_WORDS or len(ticker) < 2:
                continue
            rank = None
            if rank_str:
                rm = re.search(r"\d+", rank_str)
                if rm:
                    rank = int(rm.group())
            confidence = 0.5
            if rank is not None and rank <= 5:
                confidence = 0.8
            elif rank is not None:
                confidence = 0.6
            if any(kw in line_clean.upper() for kw in ["SCOUT", "TOP", "OPPORTUNIT"]):
                confidence = min(confidence + 0.1, 1.0)
            candidates.append(SignalCandidate(
                symbol=ticker, side=current_side, rank=rank,
                raw_line=line_clean, confidence=confidence,
            ))
    seen: dict[tuple[str, str], SignalCandidate] = {}
    for c in candidates:
        key = (c.symbol, c.side)
        if key not in seen or c.confidence > seen[key].confidence:
            seen[key] = c
    result = list(seen.values())
    logger.debug(f"Parsed {len(result)} unique candidates from message")
    return result


def validate_symbol(symbol: str, exchange_symbols: set[str]) -> bool:
    """Check if a parsed ticker exists on the exchange."""
    variants = [
        f"{symbol}/USDT:USDT", f"{symbol}/USDT",
        f"{symbol}/USD:USDT", f"{symbol}/BTC:USDT",
    ]
    for v in variants:
        if v.upper() in exchange_symbols:
            return True
    return False
