"""ID generators for Responses API items."""

import uuid


def _pfx(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def new_response_id() -> str:
    return _pfx("resp")


def new_reasoning_id() -> str:
    return _pfx("rs")


def new_message_id() -> str:
    return _pfx("msg")


def new_function_call_id() -> str:
    return _pfx("fc")


def new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:24]}"
