import re
from datetime import datetime, timezone
from src.models import TradeIntent
from src.signals.parser import parse_ckp_message

NUM = r"([0-9]+(?:\.[0-9]+)?)"
ENTRY = re.compile(r"\b(?:ENTRY|BUY\s*ZONE|SELL\s*ZONE)\b\s*[:=@-]?\s*"+NUM, re.I)
STOP = re.compile(r"\b(?:SL|STOP(?:\s*LOSS)?)\b\s*[:=@-]?\s*"+NUM, re.I)
TARGET = re.compile(r"\b(?:TP\s*\d*|TARGET\s*\d*)\b\s*[:=@-]?\s*"+NUM, re.I)

def translate(text: str, source_id: str, timestamp: datetime | None = None) -> list[TradeIntent]:
    candidates=parse_ckp_message(text)
    lines=text.splitlines()
    candidate_starts=[]
    for c in candidates:
        try: index=lines.index(c.raw_line)
        except ValueError: index=0
        candidate_starts.append((index,c))
    candidate_starts.sort(key=lambda item:item[0])
    stamp=timestamp or datetime.now(timezone.utc)
    intents=[]
    for position,(start,candidate) in enumerate(candidate_starts):
        end=candidate_starts[position+1][0] if position+1<len(candidate_starts) else len(lines)
        # Prices belong only to this ticker's block. Never reuse another coin's levels.
        block="\n".join(lines[start:end])
        entry_m=ENTRY.search(block); stop_m=STOP.search(block)
        targets=[float(m.group(1)) for m in TARGET.finditer(block)]
        intents.append(TradeIntent(symbol=candidate.symbol,side=candidate.side,
            entry=float(entry_m.group(1)) if entry_m else None,
            stop=float(stop_m.group(1)) if stop_m else None,targets=targets,
            source_id=source_id,timestamp=stamp,raw_text=text,rank=candidate.rank))
    return intents
