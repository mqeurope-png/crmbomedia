from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


@lru_cache
def build_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(database_url, pool_pre_ping=True)


def get_engine() -> Engine:
    return build_engine(get_settings().database_url)


def get_session() -> Generator[Session, None, None]:
    session_local = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    with session_local() as session:
        yield session
