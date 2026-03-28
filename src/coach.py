import asyncio
import logging
import subprocess

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
    def __init__(self):
        self.system_prompt = SYSTEM_PROMPT

    def analyze(self, prompt: str) -> str:
        full_prompt = f"{self.system_prompt}\n\n{prompt}"
        try:
            result = subprocess.run(
                ["claude", "-p", full_prompt, "--model", "sonnet"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.error(f"Claude CLI failed: {result.stderr}")
                return f"[Coach unavailable: {result.stderr}]"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.error("Claude CLI timed out")
            return "[Coach unavailable: timeout]"
        except Exception as e:
            logger.error(f"Claude CLI call failed: {e}")
            return f"[Coach unavailable: {e}]"

    async def analyze_with_retry(self, prompt: str) -> str:
        result = self.analyze(prompt)
        if result.startswith("[Coach unavailable"):
            await asyncio.sleep(30)
            result = self.analyze(prompt)
        return result
