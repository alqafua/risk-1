import asyncio
import json
import time
import logging
import websockets
import db
import binance_client as bx
import risk_engine as re
import position_manager as pm
import telegram_bot as tg

logger = logging.getLogger("ws_listener")
WS_BASE = "wss://stream.binancefuture.com" if bx.TESTNET else "wss://fstream.binance.com"
_btc_price_history = []

async def run_user_data_stream():
    while True:
        try:
            listen_key = bx.create_listen_key()
            url = f"{WS_BASE}/ws/{listen_key}"
            logger.info("Connecting to user data stream...")
            async with websockets.connect(url, ping_interval=180) as ws:
                keepalive_task = asyncio.create_task(_keepalive_listen_key(listen_key))
                try:
                    async for raw_msg in ws:
                        db.update_heartbeat()
                        await _handle_message(json.loads(raw_msg))
                finally:
                    keepalive_task.cancel()
        except Exception as e:
            logger.error(f"WS user data stream error: {e}")
            db.log_event("ws_disconnect", None, {"error": str(e)})
            await asyncio.sleep(5)

async def _keepalive_listen_key(listen_key):
    while True:
        await asyncio.sleep(30 * 60)
        try:
            bx.keepalive_listen_key(listen_key)
        except Exception as e:
            logger.error(f"listenKey keepalive failed: {e}")

async def _handle_message(msg: dict):
    event_type = msg.get("e")
    if event_type == "ACCOUNT_UPDATE":
        await _handle_account_update(msg)
    elif event_type == "ORDER_TRADE_UPDATE":
        await _handle_order_update(msg)

async def _handle_account_update(msg: dict):
    positions = msg.get("a", {}).get("P", [])
    for p in positions:
        symbol = p.get("s")
        amt = float(p.get("pa", 0))
        entry_price = float(p.get("ep", 0))
        existing = next((x for x in db.get_open_positions() if x["symbol"] == symbol), None)
        if amt == 0:
            if existing:
                realized = float(p.get("cr", 0))
                db.close_position(symbol, realized)
                db.add_realized_pnl(realized)
                re.check_daily_loss_after_pnl_update()
                re.check_and_withdraw_profit()
                is_loss = realized < 0
                if is_loss:
                    re.register_loss_and_maybe_lock()
                    re.start_cooldown(reason=f"خسارة على {symbol}")
                else:
                    re.register_win()
                db.log_event("position_closed", symbol, {"realized_pnl": realized})
            continue
        side = "LONG" if amt > 0 else "SHORT"
        quantity = abs(amt)
        if not existing:
            allowed, reason = re.check_before_allow_new_position(symbol, side)
            if not allowed:
                re.force_close_position(symbol, side, quantity, reason)
                continue
            db.increment_trade_count()
            db.upsert_position(symbol, side, entry_price, entry_price, quantity, None)
            re.check_correlation(symbol, side)
            sl_enabled, _ = db.get_setting("sl_auto_enabled", cast=bool)
            if sl_enabled:
                sl_price = pm.calculate_sl_price(symbol, side, entry_price, quantity, None)
                if sl_price:
                    _place_sl(symbol, side, sl_price, quantity, entry_price)
            current_count = pm.get_concurrent_position_count()
            db.update_concurrent_max(current_count)
        else:
            if abs(quantity - existing["quantity"]) > 1e-9 or abs(entry_price - existing["avg_entry_price"]) > 1e-9:
                db.upsert_position(symbol, side, entry_price, entry_price, quantity, None, is_dca=True)
                dca_enabled, _ = db.get_setting("dca_recalc_enabled", cast=bool)
                if dca_enabled:
                    new_sl = pm.calculate_dca_sl(symbol, side, entry_price, quantity)
                    if new_sl:
                        _cancel_existing_sl(symbol, existing.get("sl_order_id"))
                        _place_sl(symbol, side, new_sl, quantity, entry_price, is_dca=True)

async def _handle_order_update(msg: dict):
    order = msg.get("o", {})
    if order.get("x") == "TRADE" and order.get("X") == "FILLED" and order.get("ot") == "STOP_MARKET":
        symbol = order.get("s")
        db.log_event("sl_triggered", symbol, {"price": order.get("ap")})

def _place_sl(symbol, side, sl_price, quantity, entry_price, is_dca=False):
    close_side = "SELL" if side.upper() == "LONG" else "BUY"
    try:
        result = bx.place_stop_market_order(symbol, close_side, sl_price, quantity)
        order_id = result.get("orderId")
        db.upsert_position(symbol, side, entry_price, entry_price, quantity, None,
                            sl_price=sl_price, sl_order_id=order_id, is_dca=is_dca)
        if is_dca:
            tg.notify_dca_recalc(symbol, entry_price, sl_price, quantity)
            db.log_event("dca_recalc", symbol, {"new_sl": sl_price, "quantity": quantity})
        else:
            tg.notify_sl_placed(symbol, sl_price, entry_price, side)
            db.log_event("sl_placed", symbol, {"sl_price": sl_price})
    except Exception as e:
        db.log_event("sl_placement_failed", symbol, {"error": str(e)})

def _cancel_existing_sl(symbol, order_id):
    if not order_id:
        return
    try:
        bx.cancel_order(symbol, order_id)
    except Exception:
        pass

async def run_volatility_monitor():
    while True:
        try:
            price = bx.get_mark_price("BTCUSDT")
            now = time.time()
            _btc_price_history.append((now, price))
            cutoff = now - 5 * 60
            while _btc_price_history and _btc_price_history[0][0] < cutoff:
                _btc_price_history.pop(0)
            if len(_btc_price_history) >= 2:
                old_price = _btc_price_history[0][1]
                move_pct = (price - old_price) / old_price * 100.0
                re.check_volatility_guard(move_pct)
        except Exception as e:
            logger.error(f"Volatility monitor error: {e}")
        await asyncio.sleep(60)
