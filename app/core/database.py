"""Database engine / session."""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings

# Use SQLite for local dev/tests when DATABASE_URL is not a real Postgres,
# so the app boots without an external DB. Production keeps Postgres.
_database_url = settings.database_url
_connect_args = {}
if _database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(_database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
