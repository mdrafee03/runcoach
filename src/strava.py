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
