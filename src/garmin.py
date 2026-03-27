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
