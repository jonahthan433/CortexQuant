"""
Simplified Alpha Extraction & Signal Combination
"""

import numpy as np
from typing import List, Dict
from loguru import logger
from src.signals.parser import SignalCandidate


class AlphaCombiner:
    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def score_candidates(
        self,
        candidates: List[SignalCandidate],
        returns_dict: Dict[str, np.ndarray]
    ) -> List[tuple]:
        if not candidates:
            return []

        scored = []
        valid = []

        for c in candidates:
            rets = returns_dict.get(c.symbol)
            if rets is not None and len(rets) >= 5:
                valid.append((c, rets[-self.lookback:]))

        if not valid:
            logger.warning("No valid return series for alpha scoring – falling back")
            return [(c, 0.0) for c in candidates]

        normalized = []
        volatilities = []

        for c, rets in valid:
            rets = np.asarray(rets, dtype=float)
            x = rets - np.mean(rets)
            var = np.mean(x ** 2) + 1e-12
            sigma = np.sqrt(var)
            y = x / sigma
            normalized.append(y)
            volatilities.append(sigma)

        normalized = np.array(normalized)
        volatilities = np.array(volatilities)

        cross_mean = np.mean(normalized, axis=0)
        Lambda = normalized - cross_mean

        recent_window = min(5, Lambda.shape[1])
        E = np.mean(normalized[:, -recent_window:], axis=1)
        E_norm = E / volatilities

        avg_lambda = np.mean(Lambda, axis=1)
        residual = E_norm - avg_lambda

        raw_weights = residual / (volatilities + 1e-12)
        abs_sum = np.sum(np.abs(raw_weights)) + 1e-12
        weights = raw_weights / abs_sum

        for i, (c, _) in enumerate(valid):
            score = float(weights[i])
            scored.append((c, score))

        scored.sort(key=lambda x: abs(x[1]), reverse=True)

        logger.info("Alpha scores:")
        for c, score in scored:
            logger.info(f"  {c.side.upper():5} {c.symbol:8} → score={score:+.4f}")

        return scored
