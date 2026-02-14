def audit_event(event: str, details: dict | None = None) -> dict:
    return {"event": event, "details": details or {}}
