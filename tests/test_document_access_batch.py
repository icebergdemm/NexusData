from types import SimpleNamespace
import sys
import types
import inspect

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

if sys.platform == "win32" and "fcntl" not in sys.modules:
    fcntl = types.ModuleType("fcntl")
    fcntl.LOCK_EX = 2
    fcntl.LOCK_UN = 8
    fcntl.flock = lambda *_args, **_kwargs: None
    sys.modules["fcntl"] = fcntl

from routes.documents import DocumentAccessContext, get_document_access


def test_document_access_uses_prefetched_context_without_database_queries():
    class NoQueryDatabase:
        def query(self, *_args, **_kwargs):
            raise AssertionError("document access performed an unexpected query")

    admin = SimpleNamespace(id=5, is_superuser=False, privilege_group_id=None)
    level = SimpleNamespace(
        key="base",
        permissions={"pages": {"documentation": {"view": True}}},
    )
    department = SimpleNamespace(id=9, access_levels=[level])
    member = SimpleNamespace(
        department_id=9,
        admin_id=5,
        held_level_keys=["base"],
        is_head=False,
    )
    document = SimpleNamespace(
        owner_admin_id=99,
        department_id=9,
        department_folder_id=None,
        is_news=False,
        is_order=False,
        permissions=[],
    )
    context = DocumentAccessContext(
        departments={9: department},
        memberships={9: member},
        folders={},
        folder_permissions={},
    )

    assert get_document_access(NoQueryDatabase(), document, admin, context) == "view"


def test_document_list_route_is_sync_for_sync_sqlalchemy_work():
    from routes.documents import api_list_documents

    assert inspect.iscoroutinefunction(api_list_documents) is False


def test_document_access_context_uses_fixed_queries_for_many_documents():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import models

    models.Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        admin = models.AdminUser(id=5, discord_id="a5", discord_username="A5")
        department = models.Department(id=9, name="Dept", slug="dept")
        member = models.DepartmentMember(
            admin_id=5,
            department_id=9,
            held_level_keys=[],
        )
        db.add_all([admin, department, member])
        db.commit()
        db.refresh(admin)
        documents = [
            SimpleNamespace(department_id=9, department_folder_id=None)
            for _ in range(50)
        ]
        statements = []
        event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))

        from routes.documents import build_document_access_context

        build_document_access_context(db, admin, documents)

        assert len(statements) <= 2
    finally:
        db.close()
        engine.dispose()
