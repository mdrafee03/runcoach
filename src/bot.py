import json
import logging
import os
import re
from datetime import date, time, timedelta
from pathlib import Path

import yaml
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from src.bootstrap import bootstrap
from src.coach import (Coach, build_morning_prompt, build_activity_prompt,
                       build_missed_prompt, build_chat_prompt, build_weekly_summary_prompt)
from src.db import Database
from src.garmin import GarminClient
from src.planner import PlannerClient, calculate_week_number
from src.strava import StravaClient
from src.utils import is_authorized, plan_start_date

logger = logging.getLogger(__name__)


def load_settings() -> dict:
    with open(Path(__file__).parent.parent / "config" / "settings.yaml") as f:
        return yaml.safe_load(f)


def setup_logging(settings: dict):
    log_file = settings["logging"]["file"]
    level = getattr(logging, settings["logging"]["level"].upper(), logging.INFO)
    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=7))
    logging.basicConfig(level=level, format="%(asctime)s %(name)s %(levelname)s %(message)s", handlers=handlers)


class RunCoach:
    def __init__(self):
        self.settings = load_settings()
        self.chat_id = int(os.environ["TELEGRAM_CHAT_ID"])
        self.db = Database(self.settings["db"]["path"])
        self.db.init()
        self.coach = Coach()
        self.garmin = GarminClient(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
        self.strava = StravaClient(os.environ["STRAVA_CLIENT_ID"], os.environ["STRAVA_CLIENT_SECRET"], os.environ["STRAVA_REFRESH_TOKEN"])
        self.planner = PlannerClient(self.settings["plan"]["original_sheet_id"], self.settings["plan"]["active_sheet_id"], self.settings["plan"]["sheet_range"])

    def _plan_start(self) -> date:
        return plan_start_date(self.settings)

    def _weeks_to_race(self) -> int:
        race = date.fromisoformat(self.settings["race"]["date"])
        return max(0, (race - date.today()).days // 7)

    def _current_week_num(self) -> int:
        return calculate_week_number(date.today(), self._plan_start())

    def _weekly_km(self) -> tuple[float, float]:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        activities = self.db.get_activities_between(monday.isoformat(), sunday.isoformat())
        actual = sum(a.get("distance_km", 0) for a in activities)
        plan_days = self.db.get_plan_days_for_week(self._current_week_num())
        target = sum(d.get("target_distance_km", 0) or 0 for d in plan_days)
        return actual, target

    async def morning_brief(self, context: ContextTypes.DEFAULT_TYPE):
        today = date.today()
        logger.info("Running morning brief")
        health = None
        try:
            health = self.garmin.get_health_data(today)
            self.db.save_health_metrics(date=today.isoformat(), **{k: v for k, v in health.items() if v is not None})
        except Exception as e:
            logger.warning(f"Garmin fetch failed: {e}")

        plan = self.db.get_plan_day(today.isoformat()) or {"workout_type": "Rest", "target_distance_km": None, "target_pace": None, "phase": "Unknown", "week_num": 0}
        prompt = build_morning_prompt(plan=plan, health=health, weeks_to_race=self._weeks_to_race(), race_goal=self.settings["race"]["goal_time"])
        response = await self.coach.analyze_with_retry(prompt)
        await context.bot.send_message(chat_id=self.chat_id, text=response)

    async def analyze_latest_activity(self) -> str:
        """Pull latest Strava activity + Garmin health, compare to plan, return feedback."""
        activities = self.strava.get_recent_activities(limit=1)
        if not activities:
            return "Couldn't find any recent activity on Strava. Make sure it's synced."

        act = activities[0]
        detail = self.strava.get_activity_detail(act["strava_id"])
        if detail:
            act = detail

        # Pull Garmin health data for the activity date
        health = None
        try:
            from datetime import date as date_cls
            activity_date = date_cls.fromisoformat(act["date"])
            health = self.garmin.get_health_data(activity_date)
            self.db.save_health_metrics(date=act["date"], **{k: v for k, v in health.items() if v is not None})
        except Exception as e:
            logger.warning(f"Garmin fetch failed for activity feedback: {e}")

        # Save to DB
        self.db.save_activity(**{k: v for k, v in act.items() if k in
            ("strava_id", "date", "activity_type", "distance_km", "pace_min_km", "hr_avg", "splits")})

        plan = self.db.get_plan_day(act["date"])
        if plan:
            self.db.update_plan_day_status(act["date"], "completed")
            weekly_km, weekly_target = self._weekly_km()
            prompt = build_activity_prompt(
                plan=plan, activity=act, health=health,
                weekly_km=weekly_km, weekly_target_km=weekly_target,
                weeks_to_race=self._weeks_to_race(),
                race_goal=self.settings["race"]["goal_time"])
            return await self.coach.analyze_with_retry(prompt)
        else:
            return (f"Logged: {act['activity_type']} {act['distance_km']}km "
                    f"@ {act['pace_min_km']}min/km. No plan found for {act['date']}.")

    async def missed_check(self, context: ContextTypes.DEFAULT_TYPE):
        today = date.today()
        plan = self.db.get_plan_day(today.isoformat())
        if not plan or plan.get("actual_status") == "completed" or plan.get("workout_type") == "Rest":
            return
        if self.db.get_activities_for_date(today.isoformat()):
            return
        self.db.update_plan_day_status(today.isoformat(), "missed")
        prompt = build_missed_prompt(plan=plan, weeks_to_race=self._weeks_to_race())
        response = await self.coach.analyze_with_retry(prompt)
        await context.bot.send_message(chat_id=self.chat_id, text=response)

    async def weekly_summary(self, context: ContextTypes.DEFAULT_TYPE):
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        current_week = self._current_week_num()
        plan_days = self.db.get_plan_days_for_week(current_week)
        next_week_plan = self.db.get_plan_days_for_week(current_week + 1)
        activities = self.db.get_activities_between(monday.isoformat(), sunday.isoformat())
        weekly_km, weekly_target = self._weekly_km()

        prompt = build_weekly_summary_prompt(
            weekly_activities=activities, plan_days=plan_days,
            weekly_km=weekly_km, weekly_target_km=weekly_target,
            weeks_to_race=self._weeks_to_race(),
            race_goal=self.settings["race"]["goal_time"],
            next_week_plan=next_week_plan)
        response = await self.coach.analyze_with_retry(prompt)

        # Parse and apply plan adjustments from Claude's response
        adjustments = self._parse_adjustments(response)
        if adjustments:
            applied = self._apply_adjustments(adjustments, current_week + 1)
            if applied:
                response += f"\n\n✅ {applied} plan adjustment(s) applied to next week."

        await context.bot.send_message(chat_id=self.chat_id, text=response)

    def _parse_adjustments(self, response: str) -> list[dict]:
        """Extract JSON adjustments from Claude's response."""
        try:
            match = re.search(r'```json\s*\n(.*?)\n```', response, re.DOTALL)
            if match:
                return json.loads(match.group(1))
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Failed to parse adjustments: {e}")
        return []

    def _apply_adjustments(self, adjustments: list[dict], week_num: int) -> int:
        """Apply plan adjustments to SQLite and Google Sheet."""
        applied = 0
        for adj in adjustments:
            adj_date = adj.get("date", "")
            field = adj.get("field", "")
            old_val = adj.get("old", "")
            new_val = adj.get("new", "")
            reason = adj.get("reason", "")

            if not adj_date or not field or not new_val:
                continue

            # Update SQLite
            plan_day = self.db.get_plan_day(adj_date)
            if not plan_day:
                continue

            if field == "workout_type":
                self.db.save_plan_day(
                    date=adj_date, week_num=plan_day["week_num"],
                    phase=plan_day["phase"], workout_type=new_val,
                    target_distance_km=plan_day["target_distance_km"],
                    target_pace=plan_day["target_pace"],
                    actual_status=plan_day["actual_status"])
            elif field == "target_distance_km":
                self.db.save_plan_day(
                    date=adj_date, week_num=plan_day["week_num"],
                    phase=plan_day["phase"], workout_type=plan_day["workout_type"],
                    target_distance_km=float(new_val),
                    target_pace=plan_day["target_pace"],
                    actual_status=plan_day["actual_status"])
            elif field == "target_pace":
                self.db.save_plan_day(
                    date=adj_date, week_num=plan_day["week_num"],
                    phase=plan_day["phase"], workout_type=plan_day["workout_type"],
                    target_distance_km=plan_day["target_distance_km"],
                    target_pace=new_val,
                    actual_status=plan_day["actual_status"])

            # Log the change
            self.db.save_plan_change(
                date=adj_date, week_num=week_num,
                field_changed=field, old_value=str(old_val),
                new_value=str(new_val), reason=reason)

            logger.info(f"Plan adjusted: {adj_date} {field} {old_val} -> {new_val} ({reason})")
            applied += 1

        return applied

    def _is_done_trigger(self, msg: str) -> bool:
        """Detect if the user is saying they finished a workout."""
        triggers = ["done", "finished", "completed", "just ran", "did my run",
                     "workout done", "run done", "back from run", "just finished"]
        msg_lower = msg.lower().strip()
        return any(t in msg_lower for t in triggers)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update.effective_chat.id, self.chat_id):
            return
        user_msg = update.message.text
        self.db.save_conversation("user", user_msg)

        # Check if user is reporting a completed workout
        if self._is_done_trigger(user_msg):
            await update.message.reply_text("Pulling your latest activity from Strava...")
            response = await self.analyze_latest_activity()
            self.db.save_conversation("assistant", response)
            await update.message.reply_text(response)
            return

        today = date.today()
        history = self.db.get_recent_conversations(limit=20)
        ctx = {
            "today_plan": self.db.get_plan_day(today.isoformat()),
            "health": self.db.get_health_metrics(today.isoformat()),
            "recent_activities": self.db.get_activities_between((today - timedelta(days=7)).isoformat(), today.isoformat()),
        }
        prompt = build_chat_prompt(user_message=user_msg, context=ctx, race_goal=self.settings["race"]["goal_time"], conversation_history=history)
        response = await self.coach.analyze_with_retry(prompt)
        self.db.save_conversation("assistant", response)
        await update.message.reply_text(response)

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update.effective_chat.id, self.chat_id):
            return
        await update.message.reply_text("RunCoach active! Morning brief at 5am, post-activity analysis, 11pm check-in. Message me anytime.")


def main():
    from dotenv import load_dotenv
    load_dotenv()
    settings = load_settings()
    setup_logging(settings)
    Path("data").mkdir(exist_ok=True)

    rc = RunCoach()

    # Bootstrap on first run
    if not rc.settings["plan"]["active_sheet_id"]:
        bootstrap(rc.db, rc.planner, rc.settings)

    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", rc.handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, rc.handle_message))

    sched = settings["schedule"]
    jq = app.job_queue
    jq.run_daily(rc.morning_brief, time=time(hour=sched["morning_brief_hour"], minute=sched["morning_brief_minute"]), name="morning_brief")
    jq.run_daily(rc.missed_check, time=time(hour=sched["missed_check_hour"], minute=sched["missed_check_minute"]), name="missed_check")
    jq.run_daily(rc.weekly_summary, time=time(hour=sched["weekly_summary_hour"], minute=0), days=(sched["weekly_summary_day"],), name="weekly_summary")

    logger.info("RunCoach starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
