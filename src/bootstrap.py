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
