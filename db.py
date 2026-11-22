"""Database module: opens MySQL connections and runs the SQL files.
All connection details come from config.py (which reads the .env file).

Command line:
    python db.py init     build the tables (schema.sql) and load the demo data (seed.sql)
    python db.py schema   rebuild the empty tables only
    python db.py seed     load the demo data only
    python db.py check    test the connection
"""
import logging
import sys
from contextlib import contextmanager
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from config import get_config

log = logging.getLogger(__name__)
SQL_DIR = Path(__file__).resolve().parent / "database"


class DatabaseError(Exception):
    """Raised when MySQL cannot be reached or a query fails."""


class DataRuleError(DatabaseError):
    """A write broke a table rule: a duplicate UNIQUE value (MySQL error 1062), a foreign key that
    points nowhere (1452) or a CHECK constraint (3819).  The API turns this into a 409 or 400."""

    DUPLICATE, MISSING_PARENT = 1062, 1452

    def __init__(self, errno, detail):
        super().__init__("That change breaks a database rule.")
        self.errno, self.detail = errno, detail


def get_connection(config=None):
    """Open one connection using the settings of the current environment."""
    cfg = config or get_config()
    try:
        return pymysql.connect(host=cfg.DB_HOST, port=cfg.DB_PORT, user=cfg.DB_USER,
                               password=cfg.DB_PASSWORD, database=cfg.DB_NAME,
                               charset="utf8mb4", cursorclass=DictCursor, autocommit=False)
    except pymysql.MySQLError as e:
        log.error("Cannot connect to MySQL at %s:%s (%s)", cfg.DB_HOST, cfg.DB_PORT, e)
        raise DatabaseError("Cannot connect to the database. Check your .env settings "
                            "and that MySQL is running.") from e


@contextmanager
def connection(config=None):
    """Give a connection that commits on success, rolls back on an error and always closes."""
    conn = get_connection(config)
    try:
        yield conn
        conn.commit()
    except pymysql.IntegrityError as e:
        conn.rollback()
        log.warning("Write refused by a database rule, changes rolled back: %s", e)
        raise DataRuleError(e.args[0] if e.args else 0, str(e)) from e
    except pymysql.MySQLError as e:
        conn.rollback()
        log.error("Database error, changes rolled back: %s", e)
        raise DatabaseError("The database could not complete that request.") from e
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _statements(sql_text):
    """Split a SQL file into single statements (comment lines are dropped)."""
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def run_sql_file(path, config=None):
    """Run every statement in a .sql file (used for schema.sql and seed.sql)."""
    text = Path(path).read_text(encoding="utf-8")
    with connection(config) as conn:
        with conn.cursor() as cur:
            for statement in _statements(text):
                cur.execute(statement)
    log.info("Ran %s", Path(path).name)


def init_database(config=None):
    """Build the tables from nothing and fill them with the demo data."""
    run_sql_file(SQL_DIR / "schema.sql", config)
    run_sql_file(SQL_DIR / "seed.sql", config)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    try:
        if action == "init":
            init_database()
            print("Database ready: tables created and demo data loaded.")
        elif action == "schema":
            run_sql_file(SQL_DIR / "schema.sql")
            print("Tables created (empty).")
        elif action == "seed":
            run_sql_file(SQL_DIR / "seed.sql")
            print("Demo data loaded.")
        elif action == "check":
            with connection() as c, c.cursor() as cur:
                cur.execute("SELECT VERSION() AS v")
                print("Connected to MySQL", cur.fetchone()["v"])
        else:
            print(__doc__)
    except DatabaseError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
