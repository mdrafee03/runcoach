# HM Coaching Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a personal AI coaching agent that pulls Garmin/Strava data, compares against a Google Sheets training plan, and provides daily feedback via a two-way Telegram bot.

**Architecture:** Single Python async service running on macOS via launchd. Telegram bot is the main entry point and event loop. Strava polled every 15min. Garmin pulled via garth. Claude (anthropic SDK) analyzes data. SQLite stores history.

**Tech Stack:** Python 3.13, uv, python-telegram-bot, anthropic, garth, stravalib, gws CLI, SQLite3

**Spec:** `docs/superpowers/specs/2026-03-27-hm-coaching-agent-design.md`

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Project config, dependencies |
| `.env` / `.env.example` | Secrets (gitignored) |
| `config/settings.yaml` | Non-sensitive config |
| `src/__init__.py` | Package marker |
| `src/db.py` | SQLite schema + CRUD |
| `src/garmin.py` | Garmin Connect via garth |
| `src/strava.py` | Strava API + polling |
| `src/planner.py` | Google Sheets via gws CLI |
| `src/coach.py` | Claude prompt builders + API calls |
| `src/bot.py` | Telegram bot — entry point, scheduling, handlers |
| `src/utils.py` | Shared helpers |
| `src/bootstrap.py` | First-run plan import |
| `launchd/com.rafee.runcoach.plist` | macOS service |

---

### Task 1: Project Setup

**Files:** `pyproject.toml`, `.env.example`, `.gitignore`, `config/settings.yaml`, `src/__init__.py`

- [ ] Init git repo, run `uv init --name runcoach --python 3.13`

- [ ] Create `pyproject.toml`:

```toml
[project]
name = "runcoach"
version = "0.1.0"
description = "AI half-marathon coaching agent"
requires-python = ">=3.13"
dependencies = [
    "python-telegram-bot>=21.0",
    "anthropic>=0.40.0",
    "garth>=0.4.0",
    "stravalib>=2.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
]

[project.scripts]
runcoach = "src.bot:main"
```

- [ ] Create `.gitignore`: `.env`, `data/`, `__pycache__/`, `.venv/`

- [ ] Create `.env.example`:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ANTHROPIC_API_KEY=
GARMIN_EMAIL=
GARMIN_PASSWORD=
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_REFRESH_TOKEN=
```

- [ ] Create `config/settings.yaml`:

```yaml
plan:
  original_sheet_id: "1wwH1zlAWDVWLffrSa9CFum0AWYJeVOuvIJr-U1xmzOU"
  active_sheet_id: ""
  sheet_range: "KM!A1:Z80"
  start_date: "2026-01-05"

schedule:
  morning_brief_hour: 5
  morning_brief_minute: 0
  missed_check_hour: 23
  missed_check_minute: 0
  strava_poll_interval_minutes: 15
  weekly_summary_day: 6
  weekly_summary_hour: 20

race:
  date: "2026-05-31"
  goal_time: "1:35:00"
  distance_km: 21.1

db:
  path: "data/coach.db"

logging:
  file: "data/coach.log"
  level: "INFO"
```

- [ ] Create empty `src/__init__.py`
- [ ] Run `uv sync`
- [ ] Commit

---

### Task 2: Database + Data Modules

**Files:** `src/utils.py`, `src/db.py`, `src/garmin.py`, `src/strava.py`, `src/planner.py`

- [ ] Create `src/utils.py`:

```python
from datetime import date


def is_authorized(incoming_chat_id: int | None, allowed_chat_id: int) -> bool:
    if incoming_chat_id is None:
        return False
    return incoming_chat_id == allowed_chat_id


def plan_start_date(settings: dict) -> date:
    return date.fromisoformat(settings["plan"]["start_date"])
```

- [ ] Create `src/db.py`:

```python
import sqlite3


class Database:
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def init(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS activities (
                strava_id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                distance_km REAL,
                pace_min_km REAL,
                hr_avg INTEGER,
                hr_zones TEXT,
                splits TEXT,
                planned_workout TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS health_metrics (
                date TEXT PRIMARY KEY,
                hrv INTEGER, resting_hr INTEGER, sleep_score INTEGER,
                body_battery INTEGER, vo2max REAL, training_load INTEGER,
                recovery_time INTEGER,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS plan_days (
                date TEXT PRIMARY KEY,
                week_num INTEGER NOT NULL, phase TEXT NOT NULL,
                workout_type TEXT NOT NULL, target_distance_km REAL,
                target_pace TEXT, actual_status TEXT DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS plan_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL, week_num INTEGER NOT NULL,
                field_changed TEXT NOT NULL, old_value TEXT,
                new_value TEXT, reason TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT (datetime('now')),
                role TEXT NOT NULL, message TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);
            CREATE INDEX IF NOT EXISTS idx_plan_days_week ON plan_days(week_num);
        """)
        self.conn.commit()

    def save_health_metrics(self, date: str, **kwargs):
        cols = ["date"] + list(kwargs.keys())
        vals = [date] + list(kwargs.values())
        placeholders = ",".join(["?"] * len(vals))
        self.conn.execute(f"INSERT OR REPLACE INTO health_metrics ({','.join(cols)}) VALUES ({placeholders})", vals)
        self.conn.commit()

    def get_health_metrics(self, date: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM health_metrics WHERE date = ?", (date,)).fetchone()
        return dict(row) if row else None

    def save_activity(self, strava_id: int, date: str, activity_type: str,
                      distance_km: float, pace_min_km: float, hr_avg: int,
                      hr_zones: str = None, splits: str = None, planned_workout: str = None):
        self.conn.execute(
            "INSERT OR REPLACE INTO activities (strava_id, date, activity_type, distance_km, pace_min_km, hr_avg, hr_zones, splits, planned_workout) VALUES (?,?,?,?,?,?,?,?,?)",
            (strava_id, date, activity_type, distance_km, pace_min_km, hr_avg, hr_zones, splits, planned_workout))
        self.conn.commit()

    def get_activities_for_date(self, date: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM activities WHERE date = ?", (date,)).fetchall()
        return [dict(r) for r in rows]

    def get_activities_between(self, start: str, end: str) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM activities WHERE date BETWEEN ? AND ? ORDER BY date", (start, end)).fetchall()
        return [dict(r) for r in rows]

    def get_last_strava_activity_id(self) -> int | None:
        row = self.conn.execute("SELECT strava_id FROM activities ORDER BY strava_id DESC LIMIT 1").fetchone()
        return row["strava_id"] if row else None

    def save_plan_day(self, date: str, week_num: int, phase: str, workout_type: str,
                      target_distance_km: float = None, target_pace: str = None, actual_status: str = "pending"):
        self.conn.execute(
            "INSERT OR REPLACE INTO plan_days (date, week_num, phase, workout_type, target_distance_km, target_pace, actual_status) VALUES (?,?,?,?,?,?,?)",
            (date, week_num, phase, workout_type, target_distance_km, target_pace, actual_status))
        self.conn.commit()

    def get_plan_day(self, date: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM plan_days WHERE date = ?", (date,)).fetchone()
        return dict(row) if row else None

    def get_plan_days_for_week(self, week_num: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM plan_days WHERE week_num = ? ORDER BY date", (week_num,)).fetchall()
        return [dict(r) for r in rows]

    def update_plan_day_status(self, date: str, status: str):
        self.conn.execute("UPDATE plan_days SET actual_status = ? WHERE date = ?", (status, date))
        self.conn.commit()

    def save_plan_change(self, date: str, week_num: int, field_changed: str, old_value: str, new_value: str, reason: str):
        self.conn.execute("INSERT INTO plan_changes (date, week_num, field_changed, old_value, new_value, reason) VALUES (?,?,?,?,?,?)",
                          (date, week_num, field_changed, old_value, new_value, reason))
        self.conn.commit()

    def get_plan_changes_for_week(self, week_num: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM plan_changes WHERE week_num = ? ORDER BY created_at", (week_num,)).fetchall()
        return [dict(r) for r in rows]

    def save_conversation(self, role: str, message: str):
        self.conn.execute("INSERT INTO conversations (role, message) VALUES (?, ?)", (role, message))
        self.conn.commit()

    def get_recent_conversations(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM conversations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]
```

- [ ] Create `src/garmin.py`:

```python
import logging
from datetime import date

import garth

logger = logging.getLogger(__name__)


def parse_health_data(raw: dict) -> dict:
    def safe_get(data, *keys, default=None):
        current = data
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key)
            elif isinstance(current, list) and current:
                current = current[0].get(key) if isinstance(current[0], dict) else default
            else:
                return default
            if current is None:
                return default
        return current

    return {
        "hrv": safe_get(raw, "hrvSummary", "lastNightAvg"),
        "resting_hr": raw.get("restingHeartRate"),
        "sleep_score": raw.get("sleepScore"),
        "body_battery": safe_get(raw, "bodyBattery", "charged"),
        "vo2max": safe_get(raw, "vo2Max", "vo2MaxValue"),
        "training_load": safe_get(raw, "trainingLoadBalance", "totalLoad"),
        "recovery_time": raw.get("recoveryTime"),
    }


class GarminClient:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._logged_in = False

    def login(self):
        try:
            garth.resume("~/.garth")
            self._logged_in = True
        except Exception:
            garth.login(self.email, self.password)
            garth.save("~/.garth")
            self._logged_in = True
        logger.info("Garmin login successful")

    def get_health_data(self, day: date = None) -> dict:
        if not self._logged_in:
            self.login()
        day = day or date.today()
        day_str = day.isoformat()
        raw = {}

        try:
            daily = garth.connectapi(f"/wellness-service/wellness/dailySummary/{day_str}")
            if daily:
                raw["restingHeartRate"] = daily.get("restingHeartRate")
                raw["recoveryTime"] = daily.get("recoveryTime")
        except Exception as e:
            logger.warning(f"Failed to fetch daily summary: {e}")

        try:
            sleep = garth.connectapi(f"/wellness-service/wellness/dailySleepData/{day_str}")
            if sleep:
                raw["sleepScore"] = sleep.get("dailySleepDTO", {}).get("sleepScores", {}).get("overall", {}).get("value")
        except Exception as e:
            logger.warning(f"Failed to fetch sleep data: {e}")

        for key, endpoint in {
            "hrvSummary": f"/hrv-service/hrv/{day_str}",
            "bodyBattery": f"/wellness-service/wellness/bodyBattery/dates/{day_str}/{day_str}",
            "vo2Max": f"/metrics-service/metrics/maxmet/daily/{day_str}/{day_str}",
            "trainingLoadBalance": f"/metrics-service/metrics/trainingloadbalance/daily/{day_str}/{day_str}",
        }.items():
            if key not in raw:
                try:
                    raw[key] = garth.connectapi(endpoint)
                except Exception as e:
                    logger.warning(f"Failed to fetch {key}: {e}")

        return parse_health_data(raw)
```

- [ ] Create `src/strava.py`:

```python
import json
import logging
from datetime import datetime

from stravalib import Client

logger = logging.getLogger(__name__)


def parse_activity(raw: dict) -> dict:
    distance_m = raw.get("distance", 0) or 0
    distance_km = distance_m / 1000.0
    moving_time_s = raw.get("moving_time", 0) or 0
    pace_min_km = (moving_time_s / 60.0) / distance_km if distance_km > 0 else 0

    date_str = raw.get("start_date_local", "")
    if date_str:
        date_str = datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime("%Y-%m-%d")

    return {
        "strava_id": raw["id"],
        "date": date_str,
        "activity_type": raw.get("type", "Unknown"),
        "distance_km": round(distance_km, 2),
        "pace_min_km": round(pace_min_km, 2),
        "hr_avg": int(raw["average_heartrate"]) if raw.get("average_heartrate") else None,
        "splits": json.dumps(raw.get("splits_metric") or []),
    }


class StravaClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client = Client()
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    def authenticate(self):
        token_response = self.client.refresh_access_token(
            client_id=self.client_id, client_secret=self.client_secret,
            refresh_token=self.refresh_token)
        self.client.access_token = token_response["access_token"]
        self.refresh_token = token_response["refresh_token"]

    def get_recent_activities(self, limit: int = 5) -> list[dict]:
        try:
            self.authenticate()
            results = []
            for act in self.client.get_activities(limit=limit):
                raw = {
                    "id": act.id,
                    "start_date_local": act.start_date_local.isoformat() if act.start_date_local else "",
                    "type": str(act.type),
                    "distance": float(act.distance) if act.distance else 0,
                    "moving_time": int(act.moving_time.total_seconds()) if act.moving_time else 0,
                    "average_heartrate": float(act.average_heartrate) if act.average_heartrate else None,
                    "splits_metric": None,
                }
                results.append(parse_activity(raw))
            return results
        except Exception as e:
            logger.error(f"Failed to fetch Strava activities: {e}")
            return []

    def get_activity_detail(self, activity_id: int) -> dict | None:
        try:
            self.authenticate()
            act = self.client.get_activity(activity_id)
            raw = {
                "id": act.id,
                "start_date_local": act.start_date_local.isoformat() if act.start_date_local else "",
                "type": str(act.type),
                "distance": float(act.distance) if act.distance else 0,
                "moving_time": int(act.moving_time.total_seconds()) if act.moving_time else 0,
                "average_heartrate": float(act.average_heartrate) if act.average_heartrate else None,
                "splits_metric": [
                    {"distance": float(s.distance), "moving_time": int(s.moving_time.total_seconds()),
                     "average_heartrate": float(s.average_heartrate) if s.average_heartrate else None}
                    for s in (act.splits_metric or [])
                ],
            }
            return parse_activity(raw)
        except Exception as e:
            logger.error(f"Failed to fetch activity {activity_id}: {e}")
            return None
```

- [ ] Create `src/planner.py`:

```python
import json
import logging
import subprocess
from datetime import date

logger = logging.getLogger(__name__)


def calculate_week_number(target_date: date, plan_start: date) -> int:
    return (target_date - plan_start).days // 7 + 1


def get_today_plan(plan_data: list[dict], today: date = None) -> dict | None:
    today_str = (today or date.today()).isoformat()
    for day in plan_data:
        if day["date"] == today_str:
            return day
    return None


class PlannerClient:
    def __init__(self, original_sheet_id: str, active_sheet_id: str, sheet_range: str):
        self.original_sheet_id = original_sheet_id
        self.active_sheet_id = active_sheet_id
        self.sheet_range = sheet_range

    def read_sheet(self, sheet_id: str = None) -> list[list[str]]:
        sid = sheet_id or self.active_sheet_id or self.original_sheet_id
        try:
            result = subprocess.run(
                ["gws", "sheets", "+read", "--spreadsheet", sid, "--range", self.sheet_range, "--format", "json"],
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"gws sheets read failed: {result.stderr}")
                return []
            return json.loads(result.stdout)
        except Exception as e:
            logger.error(f"Failed to read sheet: {e}")
            return []

    def clone_sheet(self) -> str | None:
        try:
            result = subprocess.run(
                ["gws", "drive", "+copy", "--file-id", self.original_sheet_id, "--name", "1:35 HM Active Plan"],
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"gws drive copy failed: {result.stderr}")
                return None
            return json.loads(result.stdout).get("id")
        except Exception as e:
            logger.error(f"Failed to clone sheet: {e}")
            return None

    def update_cell(self, cell_range: str, value: str) -> bool:
        try:
            result = subprocess.run(
                ["gws", "sheets", "+update", "--spreadsheet", self.active_sheet_id,
                 "--range", cell_range, "--values", json.dumps([[value]])],
                capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Failed to update cell: {e}")
            return False
```

- [ ] Commit: `git commit -m "feat: add database and data source modules"`

---

### Task 3: Coach Module (Claude)

**Files:** `src/coach.py`

- [ ] Create `src/coach.py`:

```python
import asyncio
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a personal running coach helping an athlete achieve a 1:35 half marathon goal.
You have access to their training plan, Garmin health data, and Strava activity data.
Be concise, encouraging, and data-driven. Use specific numbers.
Keep responses under 200 words unless asked for detail.
When suggesting changes, explain why based on the data.
If health data shows fatigue (low HRV, poor sleep, low Body Battery), prioritize recovery."""


def build_morning_prompt(plan: dict, health: dict | None, weeks_to_race: int, race_goal: str) -> str:
    parts = [
        f"Generate a morning brief for today's training.",
        f"Race goal: {race_goal} half marathon in {weeks_to_race} weeks.",
        f"Training phase: {plan.get('phase', 'Unknown')} (week {plan.get('week_num', '?')})",
        f"Today's plan: {plan['workout_type']} {plan.get('target_distance_km', '')}km @ {plan.get('target_pace', 'easy')} pace",
    ]
    if health:
        parts.append(f"Health: HRV: {health.get('hrv')}, Resting HR: {health.get('resting_hr')}, "
                      f"Sleep: {health.get('sleep_score')}, Body Battery: {health.get('body_battery')}, "
                      f"VO2max: {health.get('vo2max')}, Training Load: {health.get('training_load')}, "
                      f"Recovery: {health.get('recovery_time')}h")
    else:
        parts.append("Note: Garmin data unavailable today.")
    parts.append("Provide: today's plan summary, readiness assessment, any adjustments needed.")
    return "\n".join(parts)


def build_activity_prompt(plan: dict, activity: dict, weekly_km: float,
                          weekly_target_km: float, weeks_to_race: int, race_goal: str) -> str:
    return "\n".join([
        f"Analyze this completed workout.",
        f"Race goal: {race_goal} HM in {weeks_to_race} weeks.",
        f"Plan: {plan['workout_type']} {plan.get('target_distance_km', '')}km @ {plan.get('target_pace', 'N/A')}/km",
        f"Actual: {activity['activity_type']} {activity['distance_km']}km @ {activity['pace_min_km']}min/km, HR avg: {activity.get('hr_avg', 'N/A')}",
        f"Weekly progress: {weekly_km}/{weekly_target_km}km",
        f"Provide: performance vs plan, what went well, any concerns, what's next.",
    ])


def build_missed_prompt(plan: dict, weeks_to_race: int) -> str:
    return (f"The athlete had no activity logged today. "
            f"Planned: {plan['workout_type']} {plan.get('target_distance_km', '')}km. "
            f"{weeks_to_race} weeks to race. "
            f"Acknowledge without guilt. Offer: reschedule tomorrow, skip, or adjust the week.")


def build_chat_prompt(user_message: str, context: dict, race_goal: str,
                      conversation_history: list[dict] = None) -> str:
    parts = [f"The athlete asks: \"{user_message}\"", f"Race goal: {race_goal} half marathon."]
    if conversation_history:
        parts.append("Recent conversation:")
        for msg in conversation_history[-10:]:
            parts.append(f"  {msg['role']}: {msg['message']}")
    if context.get("today_plan"):
        p = context["today_plan"]
        parts.append(f"Today's plan: {p.get('workout_type', '')} {p.get('target_distance_km', '')}km")
    if context.get("health"):
        h = context["health"]
        parts.append(f"Health: HRV {h.get('hrv')}, Body Battery {h.get('body_battery')}")
    if context.get("recent_activities"):
        for a in context["recent_activities"][-3:]:
            parts.append(f"Recent: {a['date']} — {a.get('distance_km', '')}km @ {a.get('pace_min_km', '')}min/km")
    parts.append("Respond as their coach with data-driven, actionable advice.")
    return "\n".join(parts)


def build_weekly_summary_prompt(weekly_activities: list[dict], plan_days: list[dict],
                                 weekly_km: float, weekly_target_km: float,
                                 weeks_to_race: int, race_goal: str) -> str:
    completed = [d for d in plan_days if d.get("actual_status") == "completed"]
    missed = [d for d in plan_days if d.get("actual_status") == "missed"]
    non_rest = [d for d in plan_days if d["workout_type"] != "Rest"]
    compliance = len(completed) / max(len(non_rest), 1) * 100

    parts = [
        f"Generate a weekly training summary.",
        f"Race goal: {race_goal} HM in {weeks_to_race} weeks.",
        f"Volume: {weekly_km}/{weekly_target_km}km ({weekly_km/max(weekly_target_km,1)*100:.0f}%)",
        f"Compliance: {compliance:.0f}% ({len(completed)} completed, {len(missed)} missed)",
    ]
    if missed:
        parts.append(f"Missed: {', '.join(d['workout_type'] + ' (' + d['date'] + ')' for d in missed)}")
    for act in weekly_activities:
        parts.append(f"  {act['date']}: {act['activity_type']} {act.get('distance_km','')}km @ {act.get('pace_min_km','')}min/km HR:{act.get('hr_avg','N/A')}")
    parts.append("Provide: summary, highlights, concerns, recommended adjustments to next week.")
    return "\n".join(parts)


class Coach:
    def __init__(self, api_key: str = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def analyze(self, prompt: str) -> str:
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}])
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return f"[Coach unavailable: {e}]"

    async def analyze_with_retry(self, prompt: str) -> str:
        result = self.analyze(prompt)
        if result.startswith("[Coach unavailable"):
            await asyncio.sleep(30)
            result = self.analyze(prompt)
        return result
```

- [ ] Commit: `git commit -m "feat: add coach module with Claude analysis"`

---

### Task 4: Telegram Bot + Bootstrap

**Files:** `src/bot.py`, `src/bootstrap.py`

- [ ] Create `src/bootstrap.py`:

```python
import logging
from datetime import date, timedelta

from src.utils import plan_start_date

logger = logging.getLogger(__name__)


def map_plan_to_dates(week_rows: list[list[str]], week_num: int,
                      phase: str, week_start: date) -> list[dict]:
    results = []
    for i, row in enumerate(week_rows):
        day_date = week_start + timedelta(days=i)
        workout_type = row[1] if len(row) > 1 else "Rest"
        distance_str = row[2] if len(row) > 2 else ""
        pace = row[3] if len(row) > 3 else ""
        distance = None
        if distance_str:
            try:
                distance = float(distance_str)
            except ValueError:
                pass
        results.append({
            "date": day_date.isoformat(),
            "week_num": week_num, "phase": phase,
            "workout_type": workout_type,
            "target_distance_km": distance,
            "target_pace": pace or None,
            "actual_status": "pre-agent" if day_date < date.today() else "pending",
        })
    return results


def bootstrap(db, planner, settings: dict):
    logger.info("Running bootstrap...")
    start = plan_start_date(settings)

    if not settings["plan"]["active_sheet_id"]:
        new_id = planner.clone_sheet()
        if new_id:
            settings["plan"]["active_sheet_id"] = new_id
            planner.active_sheet_id = new_id
            logger.info(f"Active sheet created: {new_id}")

    rows = planner.read_sheet()
    if not rows:
        logger.error("Could not read plan from sheet")
        return

    logger.info(f"Read {len(rows)} rows, importing plan...")
    current_week, current_phase, week_rows = 0, "", []

    for row in rows[1:]:
        if not row or not any(row):
            continue
        try:
            wk = int(row[0])
            if wk != current_week:
                if week_rows and current_week > 0:
                    week_start = start + timedelta(weeks=current_week - 1)
                    for day in map_plan_to_dates(week_rows, current_week, current_phase, week_start):
                        db.save_plan_day(**day)
                current_week = wk
                current_phase = row[1] if len(row) > 1 else current_phase
                week_rows = []
        except (ValueError, IndexError):
            pass
        week_rows.append(row)

    if week_rows and current_week > 0:
        week_start = start + timedelta(weeks=current_week - 1)
        for day in map_plan_to_dates(week_rows, current_week, current_phase, week_start):
            db.save_plan_day(**day)

    logger.info("Bootstrap complete")
```

- [ ] Create `src/bot.py`:

```python
import logging
import os
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

    async def check_strava(self, context: ContextTypes.DEFAULT_TYPE):
        logger.debug("Polling Strava")
        last_id = self.db.get_last_strava_activity_id()
        for act in self.strava.get_recent_activities(limit=5):
            if last_id and act["strava_id"] <= last_id:
                continue
            detail = self.strava.get_activity_detail(act["strava_id"])
            if detail:
                act = detail
            plan = self.db.get_plan_day(act["date"])
            self.db.save_activity(**{k: v for k, v in act.items() if k in ("strava_id", "date", "activity_type", "distance_km", "pace_min_km", "hr_avg", "splits")})
            if plan:
                self.db.update_plan_day_status(act["date"], "completed")
                weekly_km, weekly_target = self._weekly_km()
                prompt = build_activity_prompt(plan=plan, activity=act, weekly_km=weekly_km, weekly_target_km=weekly_target, weeks_to_race=self._weeks_to_race(), race_goal=self.settings["race"]["goal_time"])
                response = await self.coach.analyze_with_retry(prompt)
                await context.bot.send_message(chat_id=self.chat_id, text=response)

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
        plan_days = self.db.get_plan_days_for_week(self._current_week_num())
        activities = self.db.get_activities_between(monday.isoformat(), sunday.isoformat())
        weekly_km, weekly_target = self._weekly_km()
        prompt = build_weekly_summary_prompt(weekly_activities=activities, plan_days=plan_days, weekly_km=weekly_km, weekly_target_km=weekly_target, weeks_to_race=self._weeks_to_race(), race_goal=self.settings["race"]["goal_time"])
        response = await self.coach.analyze_with_retry(prompt)
        await context.bot.send_message(chat_id=self.chat_id, text=response)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_authorized(update.effective_chat.id, self.chat_id):
            return
        user_msg = update.message.text
        self.db.save_conversation("user", user_msg)
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
    jq.run_repeating(rc.check_strava, interval=sched["strava_poll_interval_minutes"] * 60, first=10, name="strava_poll")
    jq.run_daily(rc.weekly_summary, time=time(hour=sched["weekly_summary_hour"], minute=0), days=(sched["weekly_summary_day"],), name="weekly_summary")

    logger.info("RunCoach starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
```

- [ ] Commit: `git commit -m "feat: add Telegram bot and bootstrap"`

---

### Task 5: launchd Service + Go Live

**Files:** `launchd/com.rafee.runcoach.plist`

- [ ] Fill in `.env` with real credentials:
  1. Create Telegram bot via @BotFather → get token
  2. Send `/start` to bot, get chat_id from `https://api.telegram.org/bot<TOKEN>/getUpdates`
  3. Register Strava API app at developers.strava.com → get client_id + secret
  4. Authorize Strava OAuth → get refresh_token
  5. Fill in Garmin email/password
  6. Get Anthropic API key from console.anthropic.com

- [ ] Smoke test: `uv run runcoach` — verify bot starts and responds to /start

- [ ] Create `launchd/com.rafee.runcoach.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rafee.runcoach</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>mkdir -p /Users/rafee/Documents/fitness/running/data &amp;&amp; /Users/rafee/.local/bin/uv run --project /Users/rafee/Documents/fitness/running runcoach</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/rafee/Documents/fitness/running</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/rafee/Documents/fitness/running/data/runcoach.stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/rafee/Documents/fitness/running/data/runcoach.stderr.log</string>
</dict>
</plist>
```

- [ ] Install and start:

```bash
cp launchd/com.rafee.runcoach.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rafee.runcoach.plist
launchctl list | grep runcoach
```

- [ ] Commit: `git commit -m "feat: add launchd service — RunCoach v1 complete"`
