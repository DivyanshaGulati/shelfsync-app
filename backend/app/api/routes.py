"""Connectivity and database readiness endpoints."""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_engine

router = APIRouter(prefix="/api", tags=["System"])


@router.get("/health")
def health():
    return {"status": "ok", "service": "shelfsync-backend"}


@router.get("/health/db")
def database_health():
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, ImportError, OSError) as exc:
        # Never return DSNs or connection error details (which could contain credentials).
        raise HTTPException(status_code=503, detail="PostgreSQL connection unavailable") from exc
    return {"status": "ok", "database": "postgresql"}
