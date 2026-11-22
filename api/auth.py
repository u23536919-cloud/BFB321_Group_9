"""API login with JSON Web Tokens (JWT).

1. POST /api/auth/login with {"email": ..., "password": ...}
2. The reply contains a token.  Send it on every other request in the header
       Authorization: Bearer <token>
3. The token is signed with SECRET_KEY, so it cannot be changed by the user, and it expires
   after JWT_EXPIRES_HOURS (config.py / .env).

Roles (from the roles table):
    Finance, Supply chain planner  can read everything and change inputs / run scenarios
    Senior management              can read everything (read-only)"""
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, request

from .errors import ApiError

EDITOR_ROLES = ("Finance", "Supply chain planner")
ALGORITHM = "HS256"


def create_token(user):
    """user is a dict with id, email and role."""
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user["id"]), "email": user["email"], "role": user["role"], "iat": now,
               "exp": now + timedelta(hours=current_app.config["JWT_EXPIRES_HOURS"])}
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm=ALGORITHM)


def _read_token():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Log in first: send 'Authorization: Bearer <token>'.")
    try:
        data = jwt.decode(header[7:].strip(), current_app.config["SECRET_KEY"], algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token_expired", "Your login has expired. Log in again.")
    except jwt.InvalidTokenError:
        raise ApiError(401, "invalid_token", "The login token is not valid.")
    return {"id": int(data["sub"]), "email": data["email"], "role": data["role"]}


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        g.api_user = _read_token()
        return view(*args, **kwargs)
    return wrapper


def roles_required(*roles):
    """Use under @login_required, e.g. @roles_required(*EDITOR_ROLES)."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if g.api_user["role"] not in roles:
                raise ApiError(403, "forbidden",
                               f"Your role ({g.api_user['role']}) cannot do this. Allowed: {', '.join(roles)}.")
            return view(*args, **kwargs)
        return wrapper
    return decorator
