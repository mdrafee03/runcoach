import logging
from datetime import date

import garth

logger = logging.getLogger(__name__)


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

        result = {
            "hrv": None, "resting_hr": None, "sleep_score": None,
            "body_battery": None, "vo2max": None, "training_load": None,
            "recovery_time": None, "training_readiness": None,
        }

        # HRV
        try:
            for h in garth.DailyHRV.list(day):
                if h.calendar_date == day:
                    result["hrv"] = h.last_night_avg
                    break
        except Exception as e:
            logger.warning(f"Failed to fetch HRV: {e}")

        # Sleep score
        try:
            for s in garth.DailySleep.list(day):
                if s.calendar_date == day:
                    result["sleep_score"] = s.value
                    break
        except Exception as e:
            logger.warning(f"Failed to fetch sleep: {e}")

        # Detailed sleep for resting HR
        try:
            sleep_data = garth.DailySleepData.get(day)
            if sleep_data and hasattr(sleep_data, "resting_heart_rate"):
                result["resting_hr"] = sleep_data.resting_heart_rate
        except Exception as e:
            logger.warning(f"Failed to fetch sleep detail: {e}")

        # Training status (training load)
        try:
            for ts in garth.DailyTrainingStatus.list(day):
                if ts.calendar_date == day:
                    result["training_load"] = ts.daily_training_load_acute
                    break
        except Exception as e:
            logger.warning(f"Failed to fetch training status: {e}")

        # Training readiness (score + recovery time)
        try:
            readiness_list = garth.TrainingReadinessData.get(day)
            if readiness_list and isinstance(readiness_list, list):
                for r in readiness_list:
                    if r.calendar_date == day:
                        result["training_readiness"] = r.score
                        result["recovery_time"] = r.recovery_time
                        break
        except Exception as e:
            logger.warning(f"Failed to fetch training readiness: {e}")

        # Body battery (latest reading)
        try:
            bb_list = garth.BodyBatteryData.get(day)
            if bb_list and isinstance(bb_list, list) and bb_list:
                last_event = bb_list[-1]
                # body_battery_values_array: [[timestamp, 'MEASURED', level, ...], ...]
                vals = last_event.body_battery_values_array or []
                if vals:
                    result["body_battery"] = vals[-1][2]  # latest level
        except Exception as e:
            logger.warning(f"Failed to fetch body battery: {e}")

        # VO2max from HRV data
        try:
            hrv_data = garth.HRVData.get(day)
            if hrv_data and hasattr(hrv_data, "hrv_summary") and hrv_data.hrv_summary:
                # Try to get VO2max from connectapi as fallback
                pass
        except Exception:
            pass

        # VO2max fallback via connectapi
        try:
            vo2 = garth.connectapi(f"/fitness-stats-service/stats/vo2Max?startDate={day}&endDate={day}")
            if vo2 and isinstance(vo2, list) and vo2:
                result["vo2max"] = vo2[0].get("generic") or vo2[0].get("running")
        except Exception as e:
            logger.warning(f"Failed to fetch VO2max: {e}")

        logger.info(f"Garmin data for {day}: {result}")
        return result
