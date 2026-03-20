from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from contextlib import contextmanager
from src.config import Config

engine = create_engine(Config.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def verify_database_connection() -> None:
    """
    Fail fast with a clear message if PostgreSQL is unreachable.
    Avoids logging the same connection error for every tender in the loop.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(
            "Cannot connect to PostgreSQL. "
            "Start the database (see README or docker-compose.yml) and set "
            "DATABASE_URL in .env (e.g. postgresql://postgres:postgres@localhost:5432/tender_db). "
            f"Original error: {exc}"
        ) from exc

@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
