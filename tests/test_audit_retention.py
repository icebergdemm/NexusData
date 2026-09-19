from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import models
from audit_service import prune_audit_logs


def test_prune_audit_logs_keeps_active_session_and_records_summary():
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[models.AdminSession.__table__, models.AdminActionLog.__table__])
    db = Session(engine)
    now = datetime.utcnow()
    db.add_all([
        models.AdminSession(id=7, admin_id=1, token="active", created_at=now - timedelta(days=500), expires_at=now + timedelta(days=1), revoked=0),
        models.AdminActionLog(id=1, session_id=None, action="old_event", details={}, created_at=now - timedelta(days=400)),
        models.AdminActionLog(id=2, session_id=7, action="old_active_session_event", details={}, created_at=now - timedelta(days=400)),
    ])
    db.commit()

    deleted = prune_audit_logs(db, now, retention_days=365)

    assert deleted == 1
    assert db.query(models.AdminActionLog).filter_by(id=1).first() is None
    assert db.query(models.AdminActionLog).filter_by(id=2).first() is not None
    summary = db.query(models.AdminActionLog).filter_by(action="audit_retention_prune").one()
    assert summary.actor_type == "system"
    assert summary.details["deleted_count"] == 1
    db.close()
    engine.dispose()
