"""SSE formatting utilities."""

import json


def sse_event(event: str, data: dict, sequence_number: int | None = None) -> str:
    """Format a single SSE event line."""
    payload = {"type": event, **data}
    if sequence_number is not None:
        payload["sequence_number"] = sequence_number
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
