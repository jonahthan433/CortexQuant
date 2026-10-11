"""Fixed signals/parser.py — robust CKP scanner parser with symbol validation"""
"""Simple parser for @CKPscanner messages.
Looks for LONG / SHORT opportunity lists and extracts symbols.
"""

import re
from dataclasses import dataclass
from typing import List, Optional
from loguru import logger


# Tickers that are NOT tradeable coins (false positives)
NON_COIN_WORDS = {
    "TOP", "LONG", "SHORT", "USDT", "RSI", "TSI", "FUNDING", "VOLUME",
    "PRICE", "BTC", "ETH", "SOL", "AVAX", "LINK", "NEO", "ADA", "DOT",
    "DOGE", "SHIB", "XRP", "MATIC", "ATOM", "UNI", "AAVE", "SUSHI",
    "OP", "ARB", "STRK", "WIF", "PEPE", "FLOKI", "BONK", "NOT", "HERO",
    "ADMIN", "UPDATE", "BONUS", "SCAN", "CKP", "SCOUT", "LIST",
}

# Extended coin ticker pattern: 2-10 letters, optionally followed by a quote
# currency. Matches BTCUSDT, BTC/USDT, BTC:USDT and the hyphenated form the CKP
# scanner actually posts (RAMSES-USDT). The quote is optional and the separator
# may be -, /, :, # or nothing.
COIN_PATTERN = re.compile(
    r"(?:^|\s|\||\:|#)"
    r"(\d{1,2}[\.\)\-\s]+)?"          # optional rank prefix: "1. " / "1)" / "1-"
    r"#?"
    r"([A-Z][A-Z]{1,9})"              # ticker: 2-10 uppercase letters
    r"(?:[\-/_:#]?(?:USDT|BTC|ETH|BNB|USDC|EUR|GBP|JPY))?"
    r"(?=\s|$)",
    re.IGNORECASE,
)


@dataclass
class SignalCandidate:
    symbol: str
    side: str                      # "long" or "short"
    rank: Optional[int] = None
    raw_line: str = ""
    confidence: float = 0.0


def parse_ckp_message(text: str) -> List[SignalCandidate]:
    """
    Parse a message from CKPscanner.
    Returns a list of SignalCandidate objects.
    """
    if not text:
        return []

    # Only ranked list lines are tradeable. Prose (the "About these scanner
    # lists" preamble, disclaimers, funding notes) mentions LONG/SHORT and coin
    # names but carries no rank, so parsing it yields junk symbols like THE/USE.
    if not re.search(r"^\s*\d{1,2}[\.\)]\s*[A-Z0-9]", text, re.IGNORECASE | re.MULTILINE):
        return []

    candidates: List[SignalCandidate] = []
    text_upper = text.upper()

    # Detect section headers — look for isolated LONG/SHORT with context
    lines = text.splitlines()
    current_side: Optional[str] = None

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        upper = line_clean.upper()

        # Section headers: "LONG OPPORTUNITIES", "TOP LONG", "SCOUT LONG", etc.
        # Any header line resets the side first, so a SHORT section following a
        # LONG section never inherits the previous direction.
        side_match = None
        if re.search(r"\bLONG\b", upper) and (
            "OPPORTUNIT" in upper or "TOP" in upper or "SCOUT" in upper
            or "SETUP" in upper or "CONFIRM" in upper
        ):
            side_match = "long"
        elif re.search(r"\bSHORT\b", upper) and (
            "OPPORTUNIT" in upper or "TOP" in upper or "SCOUT" in upper
            or "SETUP" in upper or "CONFIRM" in upper
        ):
            side_match = "short"

        if side_match is not None:
            current_side = side_match
            continue

        # If no section detected yet, try per-line direction cues
        # "1. BTCUSDT → LONG" or "SHORT: ETHUSDT"
        dir_match = re.search(
            r"(?:LONG|SHORT|BUY|SELL|SHORT|LONG)\b",
            upper,
        )
        if dir_match and current_side is None:
            word = dir_match.group().upper()
            if word in ("LONG", "BUY"):
                current_side = "long"
            elif word in ("SHORT", "SELL"):
                current_side = "short"

        if current_side is None:
            continue

        # Extract coin tickers from the line
        for match in COIN_PATTERN.finditer(line_clean):
            rank_str = match.group(1) or ""
            ticker = match.group(2).upper()

            # Strip the quote currency so the symbol matches exchange pairs:
            # "RAMSESUSDT" -> "RAMSES", "BTC/USDT" -> "BTC".
            for _quote in ("USDT", "USDC", "BTC", "ETH", "BNB", "EUR", "GBP", "JPY"):
                if ticker.endswith(_quote) and len(ticker) > len(_quote):
                    ticker = ticker[: -len(_quote)]
                    break

            # Filter non-coin words
            if ticker in NON_COIN_WORDS:
                continue

            # Must be at least 2 chars and not look like a random acronym
            if len(ticker) < 2:
                continue

            rank: Optional[int] = None
            if rank_str:
                rm = re.search(r"\d+", rank_str)
                if rm:
                    rank = int(rm.group())

            # Confidence: rank present → higher, section header present → higher
            confidence = 0.5
            if rank is not None and rank <= 5:
                confidence = 0.8
            elif rank is not None:
                confidence = 0.6
            if "SCOUT" in upper or "TOP" in upper:
                confidence = min(confidence + 0.1, 1.0)

            candidates.append(SignalCandidate(
                symbol=ticker,
                side=current_side,
                rank=rank,
                raw_line=line_clean,
                confidence=confidence,
            ))

    # Deduplicate by symbol (keep highest confidence per symbol per side)
    seen: dict[tuple[str, str], SignalCandidate] = {}
    for c in candidates:
        key = (c.symbol, c.side)
        if key not in seen or c.confidence > seen[key].confidence:
            seen[key] = c

    result = list(seen.values())
    logger.debug(f"Parsed {len(result)} unique candidates from message")
    return result


def validate_symbol(symbol: str, exchange_symbols: set[str]) -> bool:
    """
    Check if a parsed ticker corresponds to a real exchange symbol.
    exchange_symbols: set of ccxt symbol strings like "BTC/USDT:USDT".
    """
    # Try common futures symbol formats
    variants = [
        f"{symbol}/USDT:USDT",
        f"{symbol}/USDT",
        f"{symbol}/USD:USDT",
        f"{symbol}/BTC:USDT",
    ]
    for v in variants:
        if v.upper() in exchange_symbols:
            return True
    return False
