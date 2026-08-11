import db
import binance_client as bx

def get_account_equity():
    override, _ = db.get_setting("account_equity_override", cast=float)
    if override and override > 0:
        return override
    try:
        return bx.get_account_balance_usdt()
    except Exception:
        return 0.0

def calculate_sl_price(symbol, side, entry_price, quantity, leverage, equity=None):
    equity = equity if equity is not None else get_account_equity()
    risk_pct, enabled = db.get_setting("risk_per_trade_pct", cast=float)
    if not enabled or equity <= 0 or quantity <= 0:
        return None
    max_loss_usdt = equity * (risk_pct / 100.0)
    price_distance = max_loss_usdt / quantity
    if side.upper() == "LONG":
        sl_price = entry_price - price_distance
    else:
        sl_price = entry_price + price_distance
    return round(sl_price, 6)

def calculate_dca_sl(symbol, side, avg_entry_price, total_quantity, equity=None):
    return calculate_sl_price(symbol, side, avg_entry_price, total_quantity, None, equity=equity)

def get_total_exposure_pct(equity=None):
    equity = equity if equity is not None else get_account_equity()
    if equity <= 0:
        return 0.0
    try:
        positions = bx.get_position_risk()
    except Exception:
        return 0.0
    total_notional = 0.0
    for p in positions:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        mark_price = float(p.get("markPrice", 0))
        total_notional += abs(amt) * mark_price
    return (total_notional / equity) * 100.0

def get_concurrent_position_count():
    try:
        positions = bx.get_position_risk()
    except Exception:
        return 0
    return sum(1 for p in positions if float(p.get("positionAmt", 0)) != 0)

def is_position_without_sl(symbol):
    try:
        orders = bx.get_open_orders(symbol)
    except Exception:
        return False
    return not any(o.get("type") == "STOP_MARKET" for o in orders)
