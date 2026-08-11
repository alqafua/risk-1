from datetime import datetime, timedelta
import db
import binance_client as bx
import telegram_bot as tg
import position_manager as pm

def _now():
    return datetime.utcnow()

def _iso(dt):
    return dt.isoformat()

def check_before_allow_new_position(symbol, side):
    ks_val, _ = db.get_setting("kill_switch_active", cast=bool)
    if ks_val:
        return False, "Kill Switch مفعّل يدويًا"
    limit_pct, limit_enabled = db.get_setting("daily_loss_limit_pct", cast=float)
    if limit_enabled:
        equity = pm.get_account_equity()
        stats = db.get_today_stats()
        if equity > 0 and stats["realized_pnl"] < 0:
            loss_pct = abs(stats["realized_pnl"]) / equity * 100.0
            if loss_pct >= limit_pct:
                return False, f"سقف الخسارة اليومي ({limit_pct}%) انضرب بالفعل"
    cd_enabled, _ = db.get_setting("cooldown_enabled", cast=bool)
    if cd_enabled:
        until = db.get_cooldown_until()
        if until and datetime.fromisoformat(until) > _now():
            return False, "فترة Cooldown فعّالة الآن"
    streak_enabled, _ = db.get_setting("loss_streak_enabled", cast=bool)
    if streak_enabled:
        streak = db.get_loss_streak()
        until = streak.get("locked_until")
        if until and datetime.fromisoformat(until) > _now():
            return False, "قفل سلسلة الخسائر المتتالية فعّال"
    max_trades, mt_enabled = db.get_setting("max_daily_trades", cast=int)
    if mt_enabled:
        stats = db.get_today_stats()
        if stats["trade_count"] >= max_trades:
            return False, f"وصلت للحد الأقصى لعدد الصفقات اليوم ({max_trades})"
    max_concurrent, mc_enabled = db.get_setting("max_concurrent_positions", cast=int)
    if mc_enabled:
        current = pm.get_concurrent_position_count()
        if current >= max_concurrent:
            return False, f"وصلت للحد الأقصى للصفقات المتزامنة ({max_concurrent})"
    max_exposure, me_enabled = db.get_setting("max_exposure_pct", cast=float)
    if me_enabled:
        exposure = pm.get_total_exposure_pct()
        if exposure >= max_exposure:
            return False, f"تجاوزت حد التعرض الكلي ({max_exposure}%)"
    tr_enabled, _ = db.get_setting("time_restriction_enabled", cast=bool)
    if tr_enabled:
        start_str, _ = db.get_setting("time_restriction_start")
        end_str, _ = db.get_setting("time_restriction_end")
        now_t = _now().time()
        try:
            start_t = datetime.strptime(start_str, "%H:%M").time()
            end_t = datetime.strptime(end_str, "%H:%M").time()
            in_window = (start_t <= now_t <= end_t) if start_t <= end_t else (now_t >= start_t or now_t <= end_t)
            if in_window:
                return False, f"التداول ممنوع بهذا الوقت ({start_str}-{end_str})"
        except ValueError:
            pass
    return True, ""

def check_leverage(leverage):
    max_lev, enabled = db.get_setting("max_leverage", cast=int)
    if enabled and leverage and leverage > max_lev:
        return False, f"الرافعة ({leverage}x) تتجاوز الحد المسموح ({max_lev}x)"
    return True, ""

def check_correlation(new_symbol, new_side):
    enabled, _ = db.get_setting("correlation_guard_enabled", cast=bool)
    if not enabled:
        return
    open_positions = db.get_open_positions()
    for p in open_positions:
        if p["symbol"] != new_symbol and p["side"] == new_side:
            tg.notify_correlation_warning(new_symbol, p["symbol"])
            db.log_event("correlation_warning", new_symbol, {"other_symbol": p["symbol"]})
            break

def force_close_position(symbol, side, quantity, reason):
    close_side = "SELL" if side.upper() == "LONG" else "BUY"
    try:
        bx.close_position_market(symbol, close_side, quantity)
    except Exception as e:
        db.log_event("force_close_failed", symbol, {"error": str(e)})
        return
    db.log_event("forced_close", symbol, {"reason": reason})
    tg.notify_forced_close(symbol, reason)

def trigger_daily_hard_stop():
    hard_stop, enabled = db.get_setting("daily_loss_hard_stop", cast=bool)
    equity = pm.get_account_equity()
    stats = db.get_today_stats()
    loss_pct = abs(stats["realized_pnl"]) / equity * 100.0 if equity > 0 else 0
    db.set_hard_stop_triggered()
    tg.notify_daily_limit_hit(loss_pct)
    if hard_stop and enabled:
        try:
            bx.cancel_all_open_orders()
            closed = bx.close_all_positions()
            db.log_event("daily_hard_stop", None, {"closed_symbols": closed})
        except Exception as e:
            db.log_event("daily_hard_stop_failed", None, {"error": str(e)})

def start_cooldown(reason="خسارة"):
    minutes, enabled = db.get_setting("cooldown_minutes", cast=int)
    if not enabled:
        return
    until = _now() + timedelta(minutes=minutes)
    db.set_cooldown_until(_iso(until))
    db.log_event("cooldown_start", None, {"minutes": minutes, "reason": reason})
    tg.notify_cooldown_start(minutes, reason)

def register_loss_and_maybe_lock():
    db.register_trade_result(is_loss=True)
    streak = db.get_loss_streak()
    threshold, enabled = db.get_setting("loss_streak_count", cast=int)
    if enabled and streak["consecutive_losses"] >= threshold:
        lock_minutes, _ = db.get_setting("loss_streak_lock_minutes", cast=int)
        until = _now() + timedelta(minutes=lock_minutes)
        db.set_loss_streak_lock(_iso(until))
        tg.notify_loss_streak_lock(streak["consecutive_losses"], lock_minutes)
        db.log_event("loss_streak_lock", None, {
            "count": streak["consecutive_losses"], "minutes": lock_minutes
        })

def register_win():
    db.register_trade_result(is_loss=False)

def check_daily_loss_after_pnl_update():
    limit_pct, enabled = db.get_setting("daily_loss_limit_pct", cast=float)
    limit_check_enabled, _ = db.get_setting("daily_loss_limit_enabled", cast=bool)
    if not (enabled and limit_check_enabled):
        return
    equity = pm.get_account_equity()
    stats = db.get_today_stats()
    if equity <= 0:
        return
    if stats["realized_pnl"] < 0:
        loss_pct = abs(stats["realized_pnl"]) / equity * 100.0
        if loss_pct >= limit_pct and not stats["hard_stop_triggered"]:
            trigger_daily_hard_stop()

def check_volatility_guard(btc_move_pct):
    enabled, _ = db.get_setting("volatility_guard_enabled", cast=bool)
    threshold, _ = db.get_setting("volatility_threshold_pct", cast=float)
    if enabled and abs(btc_move_pct) >= threshold:
        tg.notify_volatility_guard(btc_move_pct)
        db.log_event("volatility_guard", "BTCUSDT", {"move_pct": btc_move_pct})

def check_and_withdraw_profit():
    """يتحقق من الربح ويسحب الفائض تلقائيًا"""
    enabled, _ = db.get_setting("profit_withdrawal_enabled", cast=bool)
    if not enabled:
        return
    base_capital, _ = db.get_setting("profit_withdrawal_base_capital", cast=float)
    api_key, _ = db.get_setting("withdrawal_api_key")
    api_secret, _ = db.get_setting("withdrawal_api_secret")
    if not (api_key and api_secret and base_capital > 0):
        return
    current_equity = pm.get_account_equity()
    profit = current_equity - base_capital
    if profit > 10:
        try:
            result = bx.transfer_to_wallet("USDT", round(profit, 2), api_key, api_secret)
            db.log_event("profit_withdrawn", "USDT", {"amount": profit, "result": result})
            tg.send_message(f"💰 تم سحب الربح بنجاح: {profit:.2f} USDT")
        except Exception as e:
            db.log_event("profit_withdrawal_failed", "USDT", {"error": str(e)})
