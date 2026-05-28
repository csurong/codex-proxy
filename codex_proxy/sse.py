"""SSE formatting utilities."""

import json


def sse_event(event: str, data: dict) -> str:
    """Format a single SSE event line."""
    payload = {"type": event, **data}
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
