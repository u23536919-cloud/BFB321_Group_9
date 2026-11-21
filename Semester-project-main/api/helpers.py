"""Small helpers shared by the endpoints: pagination, query-string filters, JSON body validation,
and turning MySQL values (Decimal, date) into plain JSON."""
from datetime import date, datetime
from decimal import Decimal
from math import ceil, isfinite

from flask import current_app, request

from .errors import ApiError


def to_json(value):
    """Decimal -> float, date -> 'YYYY-MM-DD', datetime -> ISO text, recursively."""
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and not isfinite(value):
        return None
    return value


def pagination_args():
    """?page=1&per_page=20  ->  (page, per_page, limit, offset)."""
    page = int_arg("page", 1, minimum=1)
    per_page = int_arg("per_page", 20, minimum=1)
    per_page = min(per_page, current_app.config.get("API_MAX_PER_PAGE", 100))
    return page, per_page, per_page, (page - 1) * per_page


def paginated(rows, total, page, per_page):
    return {"data": to_json(rows),
            "pagination": {"page": page, "per_page": per_page, "total": total,
                           "pages": ceil(total / per_page) if total else 0}}


def int_arg(name, default, minimum=None):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, "bad_query", f"'{name}' must be a whole number.")
    if minimum is not None and value < minimum:
        raise ApiError(400, "bad_query", f"'{name}' must be {minimum} or more.")
    return value


def choice_arg(name, allowed):
    """A query-string filter that must be one of the allowed values (None if not given)."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    if raw not in allowed:
        raise ApiError(400, "bad_query", f"'{name}' must be one of: {', '.join(map(str, allowed))}.")
    return raw


def bool_arg(name):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    if raw.lower() in ("true", "1", "yes"):
        return 1
    if raw.lower() in ("false", "0", "no"):
        return 0
    raise ApiError(400, "bad_query", f"'{name}' must be true or false.")


def month_arg(name):
    """?from=2026-06  ->  date(2026, 6, 1)."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    try:
        return datetime.strptime(raw, "%Y-%m").date()
    except ValueError:
        raise ApiError(400, "bad_query", f"'{name}' must be a month like 2026-06.")


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "Send a JSON object in the request body "
                                           "(header Content-Type: application/json).")
    return data


def validate_numbers(data, schema, partial=False):
    """Check a JSON body against {field: (minimum, maximum)}.  Returns {field: float}.
    With partial=True missing fields are allowed (used by PUT/PATCH-style updates).
    All problems are reported together under error.details."""
    unknown = sorted(set(data) - set(schema))
    problems = {name: "Unknown field." for name in unknown}
    out = {}
    for name, (low, high) in schema.items():
        if name not in data:
            if not partial:
                problems[name] = "This field is required."
            continue
        value = data[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            problems[name] = "Must be a number."
        elif low is not None and value < low:
            problems[name] = f"Must be {low} or more."
        elif high is not None and value > high:
            problems[name] = f"Must be {high} or less."
        else:
            out[name] = float(value)
    if problems:
        raise ApiError(400, "validation_failed", "Some fields are not valid.", problems)
    return out
