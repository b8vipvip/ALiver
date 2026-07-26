import os
from pathlib import Path

os.environ.setdefault("ALIVER_DATABASE_URL", "sqlite:///./data/test.db")
os.environ.setdefault("ALIVER_SECRET_KEY", "test-secret-key")

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def pytest_sessionfinish(session, exitstatus):
    Path("data/test.db").unlink(missing_ok=True)
