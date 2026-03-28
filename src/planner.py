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
        """Read training plan from Google Sheets via gws CLI.
        Returns the 'values' array from the Sheets API response."""
        sid = sheet_id or self.active_sheet_id or self.original_sheet_id
        params = json.dumps({"spreadsheetId": sid, "range": self.sheet_range})
        try:
            result = subprocess.run(
                ["gws", "sheets", "spreadsheets", "values", "get", "--params", params],
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"gws sheets read failed: {result.stderr}")
                return []
            data = json.loads(result.stdout)
            return data.get("values", [])
        except Exception as e:
            logger.error(f"Failed to read sheet: {e}")
            return []

    def clone_sheet(self) -> str | None:
        """Clone the original sheet via gws drive files copy."""
        params = json.dumps({"fileId": self.original_sheet_id})
        body = json.dumps({"name": "1:35 HM Active Plan"})
        try:
            result = subprocess.run(
                ["gws", "drive", "files", "copy", "--params", params, "--json", body],
                capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"gws drive copy failed: {result.stderr}")
                return None
            return json.loads(result.stdout).get("id")
        except Exception as e:
            logger.error(f"Failed to clone sheet: {e}")
            return None

    def update_cell(self, cell_range: str, value: str) -> bool:
        """Update a cell in the active sheet."""
        params = json.dumps({
            "spreadsheetId": self.active_sheet_id,
            "range": cell_range,
            "valueInputOption": "RAW",
        })
        body = json.dumps({"values": [[value]]})
        try:
            result = subprocess.run(
                ["gws", "sheets", "spreadsheets", "values", "update",
                 "--params", params, "--json", body],
                capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Failed to update cell: {e}")
            return False
