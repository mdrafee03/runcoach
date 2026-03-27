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
