"""Protective order placement with fail-safe position flattening."""

from loguru import logger


class PositionFlattenFailure(RuntimeError):
    """Protective order failure could not be flattened; stop trading immediately."""


def _order_id(order):
    return order.get("id") if isinstance(order, dict) else None


def protect_or_flatten(exchange, *, symbol, position_side, amount, stop_price, target_price):
    """Place both reduce-only protections; flatten immediately if either fails.

    ``position_side`` describes the opened position (``long`` or ``short``).
    Protective order sides are derived here and passed unchanged to the adapter.
    """
    if position_side not in {"long", "short"}:
        raise ValueError("position_side must be long or short")
    close_side = "sell" if position_side == "long" else "buy"
    stop_order = None
    try:
        stop_order = exchange.place_stop_loss(
            symbol=symbol, side=close_side, sl_price=stop_price, amount=amount
        )
        if not _order_id(stop_order):
            raise RuntimeError("stop order response did not include an order id")
        target_order = exchange.place_take_profit(
            symbol=symbol, side=close_side, tp_price=target_price, amount=amount
        )
        if not _order_id(target_order):
            raise RuntimeError("take-profit response did not include an order id")
        logger.info(
            "position_protected",
            extra={"symbol": symbol, "position_side": position_side,
                   "stop_order_id": _order_id(stop_order),
                   "target_order_id": _order_id(target_order)},
        )
        return True
    except Exception as protection_error:
        logger.critical(
            "protection_failed_flattening_position",
            extra={"symbol": symbol, "position_side": position_side,
                   "error_type": type(protection_error).__name__},
        )
        stop_id = _order_id(stop_order)
        if stop_id:
            try:
                exchange.cancel_order(stop_id, symbol)
            except Exception as cancel_error:
                logger.error(
                    "failed_to_cancel_partial_protection",
                    extra={"symbol": symbol, "order_id": stop_id,
                           "error_type": type(cancel_error).__name__},
                )
        try:
            flatten_order = exchange.create_market_order(
                symbol=symbol, side=close_side, amount=amount,
                params={"reduceOnly": True},
            )
            if not _order_id(flatten_order):
                raise RuntimeError("flatten order response did not include an order id")
            remaining = exchange.fetch_positions(symbol)
            if any(float(position.get("contracts") or 0) != 0 for position in remaining):
                raise RuntimeError("exchange still reports an open position after flatten")
            logger.critical(
                "unprotected_position_flattened",
                extra={"symbol": symbol, "order_id": _order_id(flatten_order)},
            )
        except Exception as flatten_error:
            logger.critical(
                "position_flatten_failed_manual_action_required",
                extra={"symbol": symbol, "position_side": position_side,
                       "error_type": type(flatten_error).__name__},
            )
            raise PositionFlattenFailure(symbol) from flatten_error
        return False
