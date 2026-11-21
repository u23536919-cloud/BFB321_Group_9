"""Settings for the three environments (development, testing, production).
Secrets and database details are read from the environment / a .env file,
never typed into the code. Copy .env.example to .env and fill it in."""
import os

from dotenv import load_dotenv

load_dotenv()  # reads .env if it exists (values already in the environment win)


def _flag(name, default):
    """Read a true/false setting from the environment."""
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Config:
    """Settings shared by every environment."""
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    USE_DATABASE = _flag("USE_DATABASE", True)
    DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER", "")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "fintr_breakeven")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    DEBUG = False
    TESTING = False


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    """Unit tests run without MySQL; the database tests use their own database."""
    TESTING = True
    USE_DATABASE = False
    DB_NAME = os.environ.get("DB_TEST_NAME", "fintr_breakeven_test")


class ProductionConfig(Config):
    """In production a real SECRET_KEY must be set in the environment."""
    SECRET_KEY = os.environ.get("SECRET_KEY")


CONFIGS = {"development": DevelopmentConfig, "testing": TestingConfig, "production": ProductionConfig}


def get_config(name=None):
    """Pick a config by name, or by the APP_ENV variable (default: development)."""
    name = (name or os.environ.get("APP_ENV", "development")).lower()
    if name not in CONFIGS:
        raise ValueError(f"Unknown APP_ENV '{name}'. Use one of: {', '.join(CONFIGS)}")
    return CONFIGS[name]
