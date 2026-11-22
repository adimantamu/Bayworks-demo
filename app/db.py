from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    schema = Path(__file__).resolve().parent.parent / "schema.sql"
    statements = [s.strip() for s in schema.read_text().split(";") if s.strip()]
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(842001)"))
        for stmt in statements:
            conn.execute(text(stmt))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
