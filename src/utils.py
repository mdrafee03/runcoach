from datetime import date


def is_authorized(incoming_chat_id: int | None, allowed_chat_id: int) -> bool:
    if incoming_chat_id is None:
        return False
    return incoming_chat_id == allowed_chat_id


def plan_start_date(settings: dict) -> date:
    return date.fromisoformat(settings["plan"]["start_date"])
