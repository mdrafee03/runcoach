"""Manually trigger scheduled jobs for testing."""
import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from src.bot import RunCoach, load_settings, setup_logging
from src.coach import (build_morning_prompt, build_missed_prompt,
                       build_weekly_summary_prompt)
from src.planner import calculate_week_number
from datetime import date, timedelta

async def main():
    settings = load_settings()
    setup_logging(settings)
    Path("data").mkdir(exist_ok=True)

    rc = RunCoach()
    chat_id = rc.chat_id

    from telegram import Bot
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])

    job = sys.argv[1] if len(sys.argv) > 1 else "all"

    if job in ("morning", "all"):
        print("Triggering morning brief...")
        today = date.today()
        health = None
        try:
            health = rc.garmin.get_health_data(today)
            rc.db.save_health_metrics(date=today.isoformat(), **{k: v for k, v in health.items() if v is not None})
        except Exception as e:
            print(f"Garmin failed: {e}")

        plan = rc.db.get_plan_day(today.isoformat()) or {
            "workout_type": "Rest", "target_distance_km": None,
            "target_pace": None, "phase": "Unknown", "week_num": 0}
        prompt = build_morning_prompt(
            plan=plan, health=health,
            weeks_to_race=rc._weeks_to_race(),
            race_goal=rc.settings["race"]["goal_time"])
        response = await rc.coach.analyze_with_retry(prompt)
        await bot.send_message(chat_id=chat_id, text=f"🌅 MORNING BRIEF\n\n{response}")
        print("Morning brief sent!")

    if job in ("missed", "all"):
        print("Triggering missed workout check...")
        today = date.today()
        plan = rc.db.get_plan_day(today.isoformat())
        if not plan or plan.get("workout_type") == "Rest":
            msg = "No workout planned today (Rest day) — nothing to check."
            await bot.send_message(chat_id=chat_id, text=f"🌙 MISSED CHECK\n\n{msg}")
        elif rc.db.get_activities_for_date(today.isoformat()):
            msg = "Activity already logged today — you're good!"
            await bot.send_message(chat_id=chat_id, text=f"🌙 MISSED CHECK\n\n{msg}")
        else:
            prompt = build_missed_prompt(plan=plan, weeks_to_race=rc._weeks_to_race())
            response = await rc.coach.analyze_with_retry(prompt)
            await bot.send_message(chat_id=chat_id, text=f"🌙 MISSED CHECK\n\n{response}")
        print("Missed check sent!")

    if job in ("weekly", "all"):
        print("Triggering weekly summary...")
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        current_week = rc._current_week_num()
        plan_days = rc.db.get_plan_days_for_week(current_week)
        next_week_plan = rc.db.get_plan_days_for_week(current_week + 1)
        activities = rc.db.get_activities_between(monday.isoformat(), sunday.isoformat())
        weekly_km, weekly_target = rc._weekly_km()

        prompt = build_weekly_summary_prompt(
            weekly_activities=activities, plan_days=plan_days,
            weekly_km=weekly_km, weekly_target_km=weekly_target,
            weeks_to_race=rc._weeks_to_race(),
            race_goal=rc.settings["race"]["goal_time"],
            next_week_plan=next_week_plan)
        response = await rc.coach.analyze_with_retry(prompt)

        # Parse adjustments and strip JSON from message
        import re, json
        adjustments = []
        match = re.search(r'```json\s*(.*?)```', response, re.DOTALL)
        if match:
            try:
                adjustments = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        clean_response = re.sub(r'```json.*?```', '', response, flags=re.DOTALL).strip()

        # Apply adjustments
        if adjustments:
            applied = 0
            for adj in adjustments:
                adj_date = adj.get("date", "")
                field = adj.get("field", "")
                new_val = adj.get("new", "")
                if not adj_date or not field or not new_val:
                    continue
                plan_day = rc.db.get_plan_day(adj_date)
                if not plan_day:
                    continue
                if field == "workout_type":
                    rc.db.save_plan_day(date=adj_date, week_num=plan_day["week_num"], phase=plan_day["phase"], workout_type=new_val, target_distance_km=plan_day["target_distance_km"], target_pace=plan_day["target_pace"], actual_status=plan_day["actual_status"])
                elif field == "target_distance_km":
                    rc.db.save_plan_day(date=adj_date, week_num=plan_day["week_num"], phase=plan_day["phase"], workout_type=plan_day["workout_type"], target_distance_km=float(new_val), target_pace=plan_day["target_pace"], actual_status=plan_day["actual_status"])
                elif field == "target_pace":
                    rc.db.save_plan_day(date=adj_date, week_num=plan_day["week_num"], phase=plan_day["phase"], workout_type=plan_day["workout_type"], target_distance_km=plan_day["target_distance_km"], target_pace=new_val, actual_status=plan_day["actual_status"])
                rc.db.save_plan_change(date=adj_date, week_num=current_week+1, field_changed=field, old_value=str(adj.get("old","")), new_value=str(new_val), reason=adj.get("reason",""))
                applied += 1

            # Show adjusted schedule
            clean_response += "\n\n📋 NEXT WEEK ADJUSTED SCHEDULE\n"
            updated = rc.db.get_plan_days_for_week(current_week + 1)
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            adjusted_dates = {a["date"] for a in adjustments}
            for d in updated:
                dt = date.fromisoformat(d["date"])
                dn = day_names[dt.weekday()]
                workout = d["workout_type"]
                dist = f" {d['target_distance_km']}km" if d.get("target_distance_km") else ""
                pace = f" @ {d['target_pace']}" if d.get("target_pace") else ""
                marker = " ⚡ adjusted" if d["date"] in adjusted_dates else ""
                clean_response += f"  {dn} {d['date']}: {workout}{dist}{pace}{marker}\n"
        else:
            clean_response += "\n\n✅ No changes to next week's plan — stay the course!"

        await bot.send_message(chat_id=chat_id, text=f"📊 WEEKLY SUMMARY\n\n{clean_response}")
        print("Weekly summary sent!")

    print("Done!")

asyncio.run(main())
