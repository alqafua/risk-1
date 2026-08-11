import os
import sys
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db
import binance_client as bx
import telegram_bot as tg
import position_manager as pm

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
WEB_UI_TOKEN = os.environ.get("WEB_UI_TOKEN", "change-me")

SETTING_LABELS = {
    "sl_auto_enabled": "SL تلقائي على أي صفقة جديدة",
    "risk_per_trade_pct": "نسبة المخاطرة بكل صفقة (%)",
    "dca_recalc_enabled": "إعادة حساب SL عند التعزيز",
    "daily_loss_limit_enabled": "سقف الخسارة اليومي",
    "daily_loss_limit_pct": "نسبة سقف الخسارة اليومي (%)",
    "daily_loss_hard_stop": "إغلاق كل المراكز فورًا عند وصول السقف",
    "cooldown_enabled": "Cooldown بعد خسارة",
    "cooldown_minutes": "مدة الـ Cooldown (دقيقة)",
    "cooldown_force_close": "إغلاق قسري لأي صفقة تفتح خلال Cooldown",
    "max_daily_trades_enabled": "حد عدد الصفقات اليومي",
    "max_daily_trades": "أقصى عدد صفقات باليوم",
    "max_concurrent_enabled": "حد الصفقات المتزامنة",
    "max_concurrent_positions": "أقصى عدد صفقات مفتوحة بنفس الوقت",
    "max_exposure_enabled": "حد التعرض الكلي",
    "max_exposure_pct": "أقصى نسبة تعرض كلي (%)",
    "telegram_notifications_enabled": "إشعارات Telegram",
    "journal_enabled": "تسجيل السجل (Journal)",
    "dead_man_switch_enabled": "حماية انقطاع الاتصال",
    "heartbeat_timeout_minutes": "مهلة الـ heartbeat (دقيقة)",
    "volatility_guard_enabled": "حماية التذبذب العالي",
    "volatility_threshold_pct": "عتبة تذبذب BTC (%)",
    "max_leverage_enabled": "حد أقصى للرافعة",
    "max_leverage": "أقصى رافعة مسموحة",
    "loss_streak_enabled": "قفل سلسلة الخسائر المتتالية",
    "loss_streak_count": "عدد الخسائر المتتالية للقفل",
    "loss_streak_lock_minutes": "مدة قفل سلسلة الخسائر (دقيقة)",
    "time_restriction_enabled": "منع التداول بأوقات محددة",
    "time_restriction_start": "بداية وقت المنع (HH:MM)",
    "time_restriction_end": "نهاية وقت المنع (HH:MM)",
    "weekly_report_enabled": "تقرير أسبوعي تلقائي",
    "correlation_guard_enabled": "تنبيه ترابط العملات",
    "paper_mode_enabled": "وضع تجريبي (Testnet)",
    "account_equity_override": "رأس مال ثابت يدوي (0 = تلقائي)",
    "profit_withdrawal_enabled": "سحب الربح التلقائي للحساب الثاني",
    "profit_withdrawal_base_capital": "رأس المال الثابت للسحب (USDT)",
    "withdrawal_api_key": "API Key للحساب الثاني",
    "withdrawal_api_secret": "API Secret للحساب الثاني",
}

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        token = request.form.get("token", "")
        if token == WEB_UI_TOKEN:
            session["authed"] = True
            return redirect(url_for("dashboard"))
        error = "توكن غلط"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
@login_required
def dashboard():
    settings = db.get_all_settings()
    for s in settings:
        s["label"] = SETTING_LABELS.get(s["key"], s["key"])
    stats = db.get_today_stats()
    equity = 0.0
    try:
        equity = pm.get_account_equity()
    except Exception:
        pass
    kill_switch, _ = db.get_setting("kill_switch_active", cast=bool)
    return render_template("index.html", settings=settings, stats=stats, equity=equity, kill_switch=kill_switch)

@app.route("/journal")
@login_required
def journal():
    events = db.get_recent_events(limit=100)
    return render_template("journal.html", events=events)

@app.route("/api/settings/<key>", methods=["POST"])
@login_required
def update_setting_api(key):
    data = request.get_json(force=True)
    value = data.get("value")
    enabled = data.get("enabled")
    db.update_setting(key, value=value, enabled=enabled)
    return jsonify({"ok": True})

@app.route("/api/killswitch", methods=["POST"])
@login_required
def kill_switch_api():
    activate = request.get_json(force=True).get("activate", True)
    db.update_setting("kill_switch_active", value=str(activate).lower())
    if activate:
        try:
            bx.cancel_all_open_orders()
            closed = bx.close_all_positions()
            db.log_event("kill_switch_activated", None, {"closed_symbols": closed})
            tg.notify_kill_switch_activated()
        except Exception as e:
            db.log_event("kill_switch_failed", None, {"error": str(e)})
    else:
        db.log_event("kill_switch_deactivated", None, {})
    return jsonify({"ok": True})

@app.route("/api/status")
@login_required
def status_api():
    stats = db.get_today_stats()
    last_heartbeat = db.get_last_heartbeat()
    return jsonify({"stats": stats, "last_heartbeat": last_heartbeat, "open_positions": db.get_open_positions()})

def create_app():
    db.init_db()
    return app

if __name__ == "__main__":
    app_ = create_app()
    port = int(os.environ.get("PORT", 8080))
    app_.run(host="0.0.0.0", port=port)
