"""Portable tests for the read-only verifier; PostgreSQL run happens on user's Mac."""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.seed import seed_database
from app.verification.phase1 import verify_database, verify_auth
from test_schema import db


def test_read_only_phase1_verification_with_real_seeded_schema(db, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@localhost.test")
    monkeypatch.setattr(settings, "jwt_secret", "test-only-key-longer-than-thirty-two-bytes-for-tests")
    with Session(db) as session:
        with session.begin():
            seed_database(session, owner_email="owner@localhost.test",
                          owner_password="test-owner-password-123!",
                          employee_password="test-employee-password-123!",
                          as_of=datetime(2026, 11, 15, tzinfo=timezone.utc))
    with db.connect() as connection:
        before = connection.exec_driver_sql("SELECT count(*) FROM transactions").scalar()
    results = verify_database(db)
    assert len(results) >= 20
    auth = verify_auth(db, "test-owner-password-123!", "test-employee-password-123!")
    assert len(auth) == 7
    with db.connect() as connection:
        after = connection.exec_driver_sql("SELECT count(*) FROM transactions").scalar()
    assert before == after
