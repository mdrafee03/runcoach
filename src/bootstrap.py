import logging
import re
from datetime import date, timedelta

from src.utils import plan_start_date

logger = logging.getLogger(__name__)

# Sheet column mapping: col index -> day of week (0=Mon, 6=Sun)
# Col 3=Mon, 4=Tue, 6=Wed, 7=Thu, 9=Fri, 10=Sat, 11=Sun
COL_TO_DAY = {3: 0, 4: 1, 6: 2, 7: 3, 9: 4, 10: 5, 11: 6}


def parse_distance(s: str) -> float | None:
    """Parse distance strings like '10k', '10 k', '50m', '100 m'."""
    if not s:
        return None
    s = s.strip().lower()
    # Skip swim distances (meters)
    if s.endswith("m"):
        return None
    m = re.match(r"([\d.]+)\s*k?", s)
    if m:
        return float(m.group(1))
    return None


def parse_week(type_row: list[str], dist_row: list[str] | None,
               pace_row: list[str] | None, week_num: int, phase: str,
               week_start: date) -> list[dict]:
    """Parse one week's rows into 7 plan_day dicts."""
    results = []
    for col, day_offset in COL_TO_DAY.items():
        day_date = week_start + timedelta(days=day_offset)

        workout_type = type_row[col] if col < len(type_row) else ""
        if not workout_type:
            workout_type = "Rest"

        distance = None
        if dist_row and col < len(dist_row):
            distance = parse_distance(dist_row[col])

        pace = None
        if pace_row and col < len(pace_row):
            p = pace_row[col].strip() if pace_row[col] else ""
            if p and "/km" in p:
                pace = p

        results.append({
            "date": day_date.isoformat(),
            "week_num": week_num,
            "phase": phase,
            "workout_type": workout_type,
            "target_distance_km": distance,
            "target_pace": pace,
            "actual_status": "pre-agent" if day_date < date.today() else "pending",
        })
    return results


def bootstrap(db, planner, settings: dict):
    """First-run setup: clone sheet, import plan into SQLite."""
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

    # Skip title (row 0) and header (row 1)
    data_rows = rows[2:]

    # Group rows by week: a week starts with a row where col[0] is a number
    current_week = 0
    current_phase = ""
    week_type_row = None
    week_dist_row = None
    week_pace_row = None
    days_imported = 0

    for row in data_rows:
        if not row:
            continue

        # Check if this is a week header row (starts with a number)
        first = row[0].strip() if row[0] else ""
        try:
            wk = int(first)
            # Save previous week
            if week_type_row and current_week > 0:
                week_start = start + timedelta(weeks=current_week - 1)
                for day in parse_week(week_type_row, week_dist_row, week_pace_row,
                                      current_week, current_phase, week_start):
                    db.save_plan_day(**day)
                    days_imported += 1

            current_week = wk
            phase_val = row[1].strip() if len(row) > 1 and row[1].strip() else current_phase
            if phase_val:
                current_phase = phase_val
            week_type_row = row
            week_dist_row = None
            week_pace_row = None
            continue
        except (ValueError, IndexError):
            pass

        # Non-header row — could be distances or paces
        if week_type_row and not week_dist_row:
            week_dist_row = row
        elif week_type_row and not week_pace_row:
            week_pace_row = row

    # Save last week
    if week_type_row and current_week > 0:
        week_start = start + timedelta(weeks=current_week - 1)
        for day in parse_week(week_type_row, week_dist_row, week_pace_row,
                              current_week, current_phase, week_start):
            db.save_plan_day(**day)
            days_imported += 1

    logger.info(f"Bootstrap complete: {days_imported} days imported across {current_week} weeks")
