"""Shared test fixtures — SQLite temp file with seeded users + products."""

import os
import tempfile

# Force SQLite in-memory BEFORE any app module is imported.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("CORS_ORIGINS", "*")

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from unittest.mock import patch

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.product import Product
from app.models.user import User
from app.services.mono import MonoClient
from app.services.velafi import VelaFiClient


# Monkey-patch webhook signature verification for tests — always accept.
_patcher_velafi = patch.object(VelaFiClient, "verify_webhook", return_value=True)
_patcher_velafi.start()

_patcher_mono = patch.object(MonoClient, "verify_webhook", return_value=True)
_patcher_mono.start()


@pytest.fixture(scope="function")
def db_path():
    """Temp file path for an isolated per-test SQLite database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        yield f.name
    os.unlink(f.name)


@pytest.fixture(scope="function")
def engine(db_path):
    """Function-scoped SQLite engine backed by a temp file."""
    e = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    # Enable foreign keys
    @event.listens_for(e, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=e)
    yield e
    e.dispose()


@pytest.fixture()
def session(engine) -> Generator[Session, Any, Any]:
    """Transaction-per-test session with rollback.

    Uses a SAVEPOINT so that ``session.commit()`` inside the router
    only commits the savepoint, not the outer transaction.  At teardown
    the outer transaction is rolled back, discarding all test data.
    """
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    session = SessionLocal()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(session) -> Generator[TestClient, Any, Any]:
    """FastAPI TestClient with overridden get_db."""

    def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def farmer_token(session) -> str:
    """Create + return a farmer user's JWT (idempotent by email)."""
    user = session.query(User).filter(User.email == "farmer@test.com").first()
    if not user:
        user = User(email="farmer@test.com", role="farmer", password_hash="x")
        session.add(user)
        session.flush()
    return create_access_token(subject=user.id, role="farmer")


@pytest.fixture()
def consumer_token(session) -> str:
    """Create + return a consumer user's JWT (idempotent by email)."""
    user = session.query(User).filter(User.email == "consumer@test.com").first()
    if not user:
        user = User(email="consumer@test.com", role="consumer", password_hash="x")
        session.add(user)
        session.flush()
    return create_access_token(subject=user.id, role="consumer")


@pytest.fixture()
def headers_farmer(farmer_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {farmer_token}"}


@pytest.fixture()
def consumer(session) -> User:
    """Create and return a consumer user (idempotent by email)."""
    usr = session.query(User).filter(User.email == "consumer@test.com").first()
    if not usr:
        usr = User(email="consumer@test.com", role="consumer", password_hash="x")
        session.add(usr)
        session.flush()
    return usr


@pytest.fixture()
def headers_consumer(consumer_token) -> dict[str, str]:
    return {"Authorization": f"Bearer {consumer_token}"}


@pytest.fixture()
def sample_product(session) -> Product:
    """A product with 100 kg available, 0 reserved.

    Creates a farmer user (id=1) first so the FK constraint is satisfied.
    """
    farmer = session.query(User).filter(User.email == "farmer@test.com").first()
    if not farmer:
        farmer = User(email="farmer@test.com", role="farmer", password_hash="x")
        session.add(farmer)
        session.flush()
    p = Product(
        farmer_id=farmer.id,
        name="Test Tomato",
        category="vegetables",
        price_per_kg=5.0,
        quantity_available=100,
        quantity_reserved=0,
        department="TestDept",
    )
    session.add(p)
    session.flush()
    return p
