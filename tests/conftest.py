"""Shared test fixtures — in-memory SQLite, TestClient, auth helpers."""

import os

# Point the app at a test SQLite database *before* any app imports.
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# At this point app.main is imported, which calls
# Base.metadata.create_all(bind=engine) on test.db (fine — it's a one-shot call).
from app.core.database import Base, get_db
from app.main import app  # noqa: F401 — ensure routers are registered
from app.models.user import User
from app.models.product import Product
from app.core.security import hash_password, create_access_token

# ---------------------------------------------------------------------------
# Test database — file-based SQLite for compatibility across TestClient threads.
# ---------------------------------------------------------------------------
TEST_DB_URL = "sqlite:///./test.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})


@event.listens_for(test_engine, "connect")
def _enable_fk(dbapi_connection: Any, _connection_record: Any) -> None:
    """SQLite needs explicit PRAGMA to enforce foreign keys."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(
    bind=test_engine, autoflush=False, autocommit=False
)


# ---------------------------------------------------------------------------
# Per-test lifecycle
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def setup_db() -> Generator[None, None, None]:
    """Create all tables before each test, drop them after."""
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """Provide a fresh DB session wired to the test engine."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        # Close session and ensure the underlying connection is returned/recycled
        session.close()
        test_engine.dispose()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with the test DB session as the dependency."""

    def _inner() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = _inner
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------
def _create_user(
    db: Session,
    email: str = "consumer@test.com",
    password: str = "pass123",
    role: str = "consumer",
) -> User:
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _token_for(user: User) -> str:
    return create_access_token(user.id, user.role)


@pytest.fixture
def consumer(db: Session) -> User:
    """Create and return a consumer user."""
    return _create_user(db, email="consumer@test.com", role="consumer")


@pytest.fixture
def consumer_token(consumer: User) -> str:
    """Return a valid auth token for the consumer fixture."""
    return _token_for(consumer)


@pytest.fixture
def farmer(db: Session) -> User:
    """Create and return a farmer user."""
    return _create_user(db, email="farmer@test.com", role="farmer")


@pytest.fixture
def farmer_token(farmer: User) -> str:
    """Return a valid auth token for the farmer fixture."""
    return _token_for(farmer)


# ---------------------------------------------------------------------------
# Product fixture — created by the default farmer
# ---------------------------------------------------------------------------
@pytest.fixture
def product(client: TestClient, farmer_token: str) -> dict[str, Any]:
    """Create a product via the API and return the response JSON."""
    resp = client.post(
        "/products",
        json={
            "name": "Manzana",
            "category": "Frutas",
            "price_per_kg": 2500.0,
            "quantity_available": 100,
            "department": "Antioquia",
        },
        headers={"Authorization": f"Bearer {farmer_token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
