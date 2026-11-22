"""Creates the Flask app (the application factory).

We used AI to help build our frontend model.  The code is split so each file has one job:
    app.py         creates the app: settings, logging, error pages, and registers the blueprints
    pages.py       the website pages (Blueprint "pages")
    api/           the REST API under /api (Blueprint "api", JWT login)            - Step 4
    services.py    shared helpers used by the pages and the API
    models/        the break-even model                                              - Step 2
    db.py, repository.py, database/   MySQL connection, queries and SQL files        - Step 3
Step 5 wired the pages to the API (static/js/api.js) and made the website login check real passwords."""
import logging

from flask import Flask, render_template

from api import init_api  # Step 4: REST API blueprint (api/)
from config import get_config  # settings per environment (development / testing / production)
from db import DatabaseError  # raised when MySQL is down or a query fails
from pages import bp as pages_bp
from services import (MARGIN, REGIONS, build_results, refresh_snapshot,  # noqa: F401  (tests import these from app)
                      region_inputs, region_name, region_volume, use_db)
from services import REGIONAL_SHIPMENT_FIELDS, setting_fields  # noqa: F401  (the tests import these from app)


# create_app() builds the Flask app and plugs in the website pages (pages.py) and the REST API (api/).
def create_app(config_name=None, **overrides):
    app = Flask(__name__)
    app.config.from_object(get_config(config_name))  # development / testing / production (config.py)
    app.config.update(overrides)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY is not set. Add it to your .env file.")
    # log lines have a time, a level and the message; LOG_FILE in .env also keeps them in a file
    handlers = [logging.StreamHandler()]
    if app.config.get("LOG_FILE"):
        handlers.append(logging.FileHandler(app.config["LOG_FILE"], encoding="utf-8"))
    logging.basicConfig(level=app.config["LOG_LEVEL"], format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)

    # if MySQL is down we show a friendly page instead of a raw error
    @app.errorhandler(DatabaseError)
    def database_problem(e):
        app.logger.error("Database problem: %s", e)
        return render_template("error.html", title="Database unavailable", msg=str(e)), 503

    
    # Step 4: the REST API under /api (see the api/ package).  The API reuses the same helpers as
    # the pages, so both always show the same numbers.
    app.extensions["fintr"] = {"inputs": region_inputs, "volume": region_volume, "name": region_name,
                               "use_db": use_db, "refresh": refresh_snapshot, "regions": REGIONS,
                               "margin": MARGIN}
    app.extensions["fintr"]["analysis"] = build_results  # the API sends the same table + chart data
    init_api(app)
    app.register_blueprint(pages_bp)  # the website pages (pages.py)
    return app


# this starts the web app when app.py runs directly
if __name__ == "__main__":
    application = create_app()
    # debug (with its error pages) only in development: production never shows stack traces
    application.run(debug=application.config["DEBUG"])
