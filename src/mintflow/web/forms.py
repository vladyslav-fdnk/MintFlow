"""Reading small URL-encoded form bodies without trusting their size or shape."""

from collections.abc import Collection
from urllib.parse import parse_qs

from starlette.requests import Request

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"


async def read_form(
    request: Request, *, fields: Collection[str], max_bytes: int
) -> dict[str, str] | None:
    """Each named field at most once and nothing else; None for any other body.

    Absent fields are simply missing from the result; the caller decides which are required.
    """
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != FORM_CONTENT_TYPE:
        return None
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            return None
        body.extend(chunk)
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, strict_parsing=bool(body))
    except (UnicodeDecodeError, ValueError):
        return None
    if not set(parsed) <= set(fields) or any(len(values) != 1 for values in parsed.values()):
        return None
    return {name: values[0] for name, values in parsed.items()}
