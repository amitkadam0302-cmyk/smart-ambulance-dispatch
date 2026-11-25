import os

import pytest

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql://dispatch:dispatch@localhost:5432/dispatch_test",
)
os.environ.pop("MIGRATION_DATABASE_URL", None)
os.environ["REDIS_URL"] = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/0")
os.environ["REDIS_NAMESPACE"] = f"ambulance-test-{os.getpid()}"
os.environ["APP_MODE"] = "demo"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
os.environ.pop("TOMTOM_API_KEY", None)


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")
