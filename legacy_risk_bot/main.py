import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

import db
import ws_listener
import telegram_bot as tg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("main")

def run_web_ui():
    from web_ui.app import create_app
    app = create_app()
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

def run_dead_man_switch():
    alerted = False
    while True:
        try:
            enabled, _ = db.get_setting("dead_man_switch_enabled", cast=bool)
            timeout_min, _ = db.get_setting("heartbeat_timeout_minutes", cast=int)
            if enabled:
                last_beat = db.get_last_heartbeat()
                if last_beat:
                    last_dt = datetime.fromisoformat(last_beat)
                    if datetime.utcnow() - last_dt > timedelta(minutes=timeout_min):
                        if not alerted:
                            tg.notify_dead_man_switch()
                            alerted = True
                    else:
                        alerted = False
        except Exception as e:
            logger.error(f"Dead man switch check failed: {e}")
        time.sleep(60)

def run_weekly_report():
    last_sent_week = None
    while True:
        try:
            enabled, _ = db.get_setting("weekly_report_enabled", cast=bool)
            now = datetime.utcnow()
            current_week = now.isocalendar()[1]
            if enabled and now.weekday() == 0 and now.hour == 9 and current_week != last_sent_week:
                events = db.get_recent_events(limit=1000)
                cooldown_triggers = sum(1 for e in events if e["event_type"] == "cooldown_start")
                forced_closes = sum(1 for e in events if e["event_type"] == "forced_close")
                daily_limit_hits = sum(1 for e in events if e["event_type"] == "daily_hard_stop")
                stats = db.get_today_stats()
                tg.notify_weekly_report({
                    "trade_count": stats["trade_count"],
                    "cooldown_triggers": cooldown_triggers,
                    "forced_closes": forced_closes,
                    "daily_limit_hits": daily_limit_hits,
                    "net_pnl": stats["realized_pnl"],
                })
                last_sent_week = current_week
        except Exception as e:
            logger.error(f"Weekly report failed: {e}")
        time.sleep(60 * 30)

async def run_ws_loop_forever():
    while True:
        try:
            await asyncio.gather(
                ws_listener.run_user_data_stream(),
                ws_listener.run_volatility_monitor(),
            )
        except Exception as e:
            logger.error(f"WS loop crashed, restarting in 5s: {e}")
            db.log_event("ws_loop_crash", None, {"error": str(e)})
            await asyncio.sleep(5)

def main():
    db.init_db()
    logger.info("Risk Guard starting...")
    threading.Thread(target=run_web_ui, daemon=True).start()
    threading.Thread(target=run_dead_man_switch, daemon=True).start()
    threading.Thread(target=run_weekly_report, daemon=True).start()
    tg.send_message("✅ Risk Guard اشتغل وبدأ المراقبة 24/7")
    while True:
        try:
            asyncio.run(run_ws_loop_forever())
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break
        except Exception as e:
            logger.error(f"Fatal error in main loop, restarting in 10s: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
