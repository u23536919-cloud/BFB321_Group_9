"""One JSON error format for the whole API, so the frontend can handle every error the same way:

    {"error": {"status": 404, "code": "not_found", "message": "No region called 'fr'."}}

Errors on /api/... always come back as JSON (never as an HTML page); the website pages keep their
own HTML error pages."""
from flask import current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from db import DatabaseError, DataRuleError
from models.breakeven import ModelError


class ApiError(Exception):
    """Raise this inside an endpoint to send a clean JSON error, e.g. ApiError(404, "not_found", "...")."""

    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def error_response(status, code, message, details=None):
    body = {"status": status, "code": code, "message": message}
    if details:
        body["details"] = details
    return jsonify({"error": body}), status


def _is_api_request():
    return request.path == "/api" or request.path.startswith("/api/")


def register_blueprint_handlers(bp):
    """Handlers on the blueprint only apply to /api endpoints, so the website keeps its HTML pages.
    Called once when the api package is imported."""

    @bp.errorhandler(ApiError)
    def api_error(e):
        return error_response(e.status, e.code, e.message, e.details)

    @bp.errorhandler(ModelError)
    def model_error(e):  # the break-even model rejected an input
        return error_response(400, "invalid_input", str(e))

    @bp.errorhandler(DataRuleError)
    def data_rule_error(e):  # duplicate value or a reference to a row that does not exist
        if e.errno == DataRuleError.DUPLICATE:
            return error_response(409, "duplicate", "A record with that value already exists.")
        if e.errno == DataRuleError.MISSING_PARENT:
            return error_response(400, "bad_reference", "That refers to a record that does not exist.")
        return error_response(400, "rule_broken", "That change breaks a database rule.")

    @bp.errorhandler(DatabaseError)
    def database_error(e):
        current_app.logger.error("Database problem: %s", e)
        return error_response(503, "database_unavailable", "The database is not available right now.")

    @bp.errorhandler(Exception)
    def unexpected(e):  # anything else: log the full error, but do not leak details to the client
        current_app.logger.exception("Unexpected error on %s %s", request.method, request.path)
        return error_response(500, "internal_error", "Something went wrong. It has been logged.")



def register_app_handlers(app):
    """404 (unknown URL) and 405 (wrong method) happen before a blueprint is chosen, so this one is
    registered on the app: JSON for /api/..., Flask's normal page for everything else."""

    @app.errorhandler(HTTPException)
    def http_error(e):
        if not _is_api_request():
            return e
        code = (e.name or "error").lower().replace(" ", "_")
        return error_response(e.code, code, e.description)
