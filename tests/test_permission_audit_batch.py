from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import inspect

import models
from permission_audit import permission_summaries, permission_summary


def test_permission_summaries_matches_single_summary_without_per_admin_queries():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        admins = [
            models.AdminUser(id=41, discord_id="a41", discord_username="A41"),
            models.AdminUser(id=42, discord_id="a42", discord_username="A42"),
        ]
        db.add_all(admins)
        db.commit()

        statements = []
        from sqlalchemy import event

        event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))
        batch = permission_summaries(db, admins)
        batch_query_count = len(statements)

        expected = {admin.id: permission_summary(db, admin) for admin in admins}

        assert batch == expected
        assert batch_query_count <= 4
    finally:
        db.close()
        engine.dispose()


def test_admin_users_route_is_sync_for_sync_sqlalchemy_work():
    from routes.admin import api_admin_users_list

    assert inspect.iscoroutinefunction(api_admin_users_list) is False
