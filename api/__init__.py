"""Step 4: the REST API (a Flask Blueprint mounted at /api).

    errors.py   JSON error format and the error handlers
    auth.py     JWT login tokens and the @login_required / @roles_required decorators
    helpers.py  pagination, filters, validation and turning MySQL rows into JSON
    routes.py   the endpoints themselves

Every endpoint is listed in README.md and described in docs/openapi.json."""
from flask import Blueprint

from .errors import register_app_handlers, register_blueprint_handlers

bp = Blueprint("api", __name__, url_prefix="/api")
register_blueprint_handlers(bp)

from . import routes  # noqa: E402,F401  (adds the endpoints to bp)


def init_api(app):
    """Attach the API to the Flask app (called from create_app in app.py)."""
    app.register_blueprint(bp)
    register_app_handlers(app)
