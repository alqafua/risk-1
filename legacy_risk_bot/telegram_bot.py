import os
import requests
import db

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_message(text: str, silent: bool = False):
    enabled, _ = db.get_setting("telegram_notifications_enabled", cast=bool)
    if not enabled:
        return
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"{API_BASE}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_notification": silent,
            },
            timeout=10,
        )
    except Exception:
        pass

def notify_sl_placed(symbol, sl_price, entry_price, side):
    send_message(
        f"🛡️ <b>SL تلقائي اتحط</b>\n"
        f"العملة: {symbol}\nالاتجاه: {side}\nالدخول: {entry_price}\nSL: {sl_price}"
    )

def notify_dca_recalc(symbol, new_avg, new_sl, quantity):
    send_message(
        f"🔄 <b>تم تعزيز الصفقة - SL اتحدث</b>\n"
        f"العملة: {symbol}\nمتوسط الدخول: {new_avg}\nالكمية: {quantity}\nSL: {new_sl}"
    )

def notify_daily_limit_hit(pnl_pct):
    send_message(
        f"🚨 <b>سقف الخسارة اليومي انضرب!</b>\n"
        f"الخسارة اليوم: {pnl_pct:.2f}%"
    )

def notify_cooldown_start(minutes, reason="خسارة"):
    send_message(
        f"⏸️ <b>Cooldown فعّال</b>\n"
        f"السبب: {reason}\nالمدة: {minutes} دقيقة"
    )

def notify_forced_close(symbol, reason):
    send_message(
        f"⛔ <b>صفقة اتقفلت قسريًا</b>\n"
        f"العملة: {symbol}\nالسبب: {reason}"
    )

def notify_loss_streak_lock(count, minutes):
    send_message(
        f"🔒 <b>قفل سلسلة خسائر</b>\n"
        f"عدد الخسائر: {count}\nالمدة: {minutes} دقيقة"
    )

def notify_correlation_warning(symbol, correlated_symbol):
    send_message(
        f"⚠️ <b>تنبيه ترابط</b>\n"
        f"فاتح {symbol} و{correlated_symbol} بنفس الاتجاه!"
    )

def notify_dead_man_switch():
    send_message(
        f"🔴🔴 <b>تنبيه طارئ: النظام متوقف!</b>\n"
        f"راجع Railway فورًا"
    )

def notify_volatility_guard(move_pct):
    send_message(
        f"⚡ <b>تذبذب عالي</b>\n"
        f"BTC تحرك {move_pct:.2f}%"
    )

def notify_kill_switch_activated():
    send_message("🛑 <b>Kill Switch اتفعّل</b>\nكل المراكز اتقفلت")

def notify_weekly_report(stats: dict):
    send_message(
        f"📊 <b>التقرير الأسبوعي</b>\n"
        f"الصفقات: {stats.get('trade_count', 0)}\n"
        f"Cooldown: {stats.get('cooldown_triggers', 0)}\n"
        f"القفل: {stats.get('forced_closes', 0)}\n"
        f"PnL: {stats.get('net_pnl', 0):.2f} USDT"
    )
