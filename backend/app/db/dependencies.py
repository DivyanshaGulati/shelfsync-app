"""One SQLAlchemy session per request, closed even on errors."""
from collections.abc import Generator
from sqlalchemy.orm import Session
from app.db.session import get_session_factory


def get_db() -> Generator[Session, None, None]:
    with get_session_factory()() as session:
        yield session
