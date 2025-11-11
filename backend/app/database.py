import os
from typing import Generator, Optional

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


load_dotenv()


def _get_env(name: str, default: Optional[str] = None, *, required: bool = False) -> Optional[str]:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Environment variable '{name}' is required")
    return value


DATABASE_URL = _get_env("DATABASE_URL") or _get_env("SQLALCHEMY_URL")
if not DATABASE_URL:
    user = _get_env("POSTGRES_USER", "johka")
    password = _get_env("POSTGRES_PASSWORD", required=True)
    host = _get_env("POSTGRES_HOST", "postgres")
    port = _get_env("POSTGRES_PORT", "5432")
    db_name = _get_env("POSTGRES_DB", "johka")
    DATABASE_URL = (
        f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


__all__ = ["Base", "engine", "SessionLocal", "get_db"]