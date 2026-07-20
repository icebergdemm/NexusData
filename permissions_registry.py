"""
Single source of truth for the site's permission "pages" and their allowed
actions. Replaces four previously-duplicated, drifted-apart hardcoded lists
(routes/admin.py's ME_PERMISSION_PAGES, frontend lib/auth.ts's
PERMISSION_PAGES, frontend pages/users/index.tsx's PAGES, and the /debug
endpoint's own list).

Every page keeps "view"/"edit" (and "delete" where genuinely used) so the
many existing frontend `canView(admin, "page")`/`canEdit(admin, "page")`
call sites keep working unchanged. Pages with real, distinct capabilities
beyond generic view/edit also register their own named actions (e.g.
moderation's "kick"/"ban"/"message") — those are enforced at the specific
route handlers that implement them, not folded into "edit".

`check_permission(admin, page, action)` (security.py) does a plain
`{"pages": {page: {action: bool}}}` dict lookup with no action whitelist —
this registry is purely descriptive (drives the role-editor UI and the
/api/v1/me response) and adding an entry here does not by itself change any
authorization decision; the actual route handler must check it.
"""

PageAction = dict  # {"key": str, "label": str}
PageDef = dict     # {"label": str, "actions": list[PageAction]}

_VD = [{"key": "view", "label": "Просмотр"}, {"key": "delete", "label": "Удаление"}]
_VE = [{"key": "view", "label": "Просмотр"}, {"key": "edit", "label": "Изменение"}]
_VED = [{"key": "view", "label": "Просмотр"}, {"key": "edit", "label": "Изменение"}, {"key": "delete", "label": "Удаление"}]

PERMISSION_REGISTRY: dict[str, PageDef] = {
    "admin": {"label": "Администрирование", "actions": _VED},
    "players": {"label": "Игроки", "actions": _VED},
    "players_special": {"label": "Игроки (спец. данные)", "actions": [{"key": "view", "label": "Просмотр"}]},
    "moderation": {"label": "Модерирование", "actions": [
        {"key": "view", "label": "Просмотр"},
        {"key": "message", "label": "Отправка сообщения игроку"},
        {"key": "kick", "label": "Кик игрока"},
        {"key": "ban", "label": "Бан игрока"},
        {"key": "manage_anticheat", "label": "Управление античит-событиями"},
        {"key": "edit", "label": "Изменение (прочее)"},
        {"key": "delete", "label": "Удаление записей"},
    ]},
    "dispatch": {"label": "Диспетчерская", "actions": _VE},
    "replays": {"label": "Реплеи/раунды", "actions": _VD},
    "sessions": {"label": "Сессии", "actions": _VE},
    "audit": {"label": "Аудит", "actions": [{"key": "view", "label": "Просмотр"}]},
    "privileges": {"label": "Серверные привилегии", "actions": _VE},
    "kits": {"label": "Киты", "actions": _VE},
    "punishments": {"label": "Наказания", "actions": [
        {"key": "view", "label": "Просмотр"},
        {"key": "issue", "label": "Выдача наказаний"},
        {"key": "edit", "label": "Редактирование"},
        {"key": "revoke", "label": "Снятие наказаний"},
        {"key": "delete", "label": "Удаление"},
    ]},
    # Обычная зона Sub-проектов ("Тестовый полигон ASPER" — отдельная зона,
    # гейтится ЦЕЛИКОМ через департаментский модуль "test_server", не через
    # этот реестр — см. routes/sub_projects_polygon.py). "_all"-варианты
    # снимают требование владения проектом (own-project действия работают
    # для своих проектов уже при базовом ключе). deploy/undeploy/
    # manage_categories/change_owner не завязаны на владение вообще — это
    # общие серверные операции/административные действия.
    "sub_projects": {"label": "Sub-проекты", "actions": [
        {"key": "view", "label": "Просмотр каталога и карточек проектов"},
        {"key": "create", "label": "Создание проекта"},
        {"key": "edit", "label": "Редактирование своего проекта"},
        {"key": "edit_all", "label": "Редактирование любого проекта"},
        {"key": "delete", "label": "Удаление своего проекта"},
        {"key": "delete_all", "label": "Удаление любого проекта"},
        {"key": "upload_version", "label": "Загрузка версии в свой проект"},
        {"key": "upload_version_all", "label": "Загрузка версии в любой проект"},
        {"key": "delete_version", "label": "Удаление версии своего проекта"},
        {"key": "delete_version_all", "label": "Удаление версии любого проекта"},
        {"key": "restore_version", "label": "Восстановление версии своего проекта"},
        {"key": "restore_version_all", "label": "Восстановление версии любого проекта"},
        {"key": "download", "label": "Скачивание файлов своего проекта"},
        {"key": "download_all", "label": "Скачивание файлов любого проекта"},
        {"key": "deploy_server_1", "label": "Деплой на Сервер 1"},
        {"key": "deploy_server_2", "label": "Деплой на Сервер 2"},
        {"key": "undeploy_server_1", "label": "Удаление с Сервера 1"},
        {"key": "undeploy_server_2", "label": "Удаление с Сервера 2"},
        {"key": "publish_discord", "label": "Публикация своего проекта в Discord"},
        {"key": "publish_discord_all", "label": "Публикация любого проекта в Discord"},
        {"key": "manage_categories", "label": "Управление категориями"},
        {"key": "change_owner", "label": "Смена владельца проекта"},
        {"key": "manage_test_server", "label": "Управление тестовым сервером (Сервер 2 / ASPER2)"},
    ]},
    "ratings": {"label": "Рейтинг", "actions": [
        {"key": "view", "label": "Просмотр"},
        {"key": "edit", "label": "Редактирование"},
        {"key": "recalculate", "label": "Пересчёт рейтинга"},
        {"key": "manage_rewards", "label": "Управление наградами"},
    ]},
    "discord_bot": {"label": "Discord бот", "actions": [
        {"key": "view", "label": "Просмотр"},
        {"key": "manage_config", "label": "Настройка бота"},
        {"key": "broadcast", "label": "Рассылка/публикация сообщений"},
        {"key": "edit", "label": "Изменение (прочее)"},
    ]},
    "tickets": {"label": "Тикеты", "actions": [
        {"key": "view", "label": "Просмотр"},
        {"key": "reply", "label": "Ответ на обращение"},
        {"key": "close", "label": "Закрытие обращения"},
        {"key": "assign", "label": "Назначение обращения"},
        {"key": "edit", "label": "Изменение (прочее)"},
    ]},
    "analytics": {"label": "Аналитика", "actions": [{"key": "view", "label": "Просмотр"}]},
    "documents": {"label": "Документы", "actions": [
        {"key": "create_personal", "label": "Создание личных документов (всегда включено)"},
        {"key": "view", "label": "Просмотр общих/расшаренных"},
        {"key": "edit", "label": "Редактирование общих"},
        {"key": "manage_templates", "label": "Управление шаблонами"},
        {"key": "view_all", "label": "Просмотр всех документов (как у суперпользователя)"},
    ]},
    "departments": {"label": "Отделы", "actions": [
        {"key": "view", "label": "Просмотр (свои отделы, всегда включено)"},
        {"key": "view_all", "label": "Просмотр всех отделов (как у суперпользователя)"},
        {"key": "create", "label": "Создание отделов"},
    ]},
    # Single global "Задачи" page (Notion-style Kanban boards) — a board's
    # department is purely an organizational tag, never an access boundary,
    # so there's no view_all/view split here: everyone with "view" sees every
    # board. "edit"/"delete" are own-task-scoped (creator or assignee);
    # "manage_boards" covers creating/editing/archiving boards and columns.
    "tasks": {"label": "Задачи", "actions": [
        {"key": "view", "label": "Просмотр досок и задач"},
        {"key": "create", "label": "Создание задач"},
        {"key": "edit", "label": "Редактирование своих задач (созданных или назначенных)"},
        {"key": "edit_all", "label": "Редактирование любых задач"},
        {"key": "delete", "label": "Удаление своих задач"},
        {"key": "delete_all", "label": "Удаление любых задач"},
        {"key": "manage_boards", "label": "Управление досками и колонками"},
    ]},
}

# Registry keys that were split off an existing generic action when this
# rework shipped — used by database.py's one-time backfill so admins who
# already had e.g. moderation:edit=true don't lose kick/ban/message access.
# Shape: {page: {old_action: [new_action, ...]}} — backfill sets
# page[new_action]=True on the SAME page wherever page[old_action] was True.
PERMISSION_ACTION_SPLITS: dict[str, dict[str, list[str]]] = {
    "moderation": {"edit": ["message", "kick", "ban", "manage_anticheat"]},
    "punishments": {"edit": ["issue", "revoke"]},
    "ratings": {"edit": ["recalculate", "manage_rewards"]},
    "discord_bot": {"edit": ["manage_config", "broadcast"]},
    "tickets": {"edit": ["reply", "close", "assign"]},
    # NOTE: "view_all" is deliberately NOT backfilled here — before this
    # rework, seeing every document (not just shared/owned ones) was
    # superuser-only (hardcoded), never reachable via "documents":"edit".
    # Granting it retroactively to existing "edit" holders would be a
    # privilege escalation, not a preservation of prior behavior.
    "documents": {"edit": ["manage_templates"]},
    # Inverted case from the others above: "departments":"view" ITSELF just
    # became baseline-True for everyone (own memberships only — see
    # api_list_departments). Before this change, "view"=True meant "see
    # every department" (oversight), so that capability must be preserved
    # as the new "view_all" action for anyone who already had "view" —
    # otherwise existing oversight-granted admins would silently regress to
    # seeing only their own departments.
    "departments": {"view": ["view_all"]},
    # sub_projects went from 3 actions (view/edit/delete) to 22. Mapping below
    # is verified line-by-line against the PRE-rework route behavior so no
    # existing privilege group's real capabilities regress:
    #   - bare "view" already reached create/upload_version/download via the
    #     ownership fallback on those endpoints (not just edit!).
    #   - bare "edit" (WITHOUT delete) already reached upload/download/deploy/
    #     undeploy/publish on ANY project, not just the owner's — the old
    #     gate on those five endpoints was "edit OR delete", no ownership
    #     check at all.
    #   - "delete" already reached everything unrestricted (any project, any
    #     version, categories, change-owner) since the old gate was always
    #     "is_superuser OR delete" / "delete" alone with no ownership check.
    #   - publish_discord_all is deliberately NOT backfilled from bare
    #     "delete" (without edit) — the old route checked "edit" exclusively
    #     for discord publish, so a delete-only group could never publish;
    #     backfilling it here would be a privilege escalation, not
    #     preservation.
    #   - delete_version/restore_version (OWN-project scoped) are new
    #     capabilities with no old equivalent (no ownership fallback existed
    #     on those two endpoints before) — intentionally not backfilled.
    "sub_projects": {
        # "edit" included here too — the pre-rework update endpoint's gate was
        # literally `(edit OR view) AND owner`, i.e. bare view already let you
        # edit your own project's metadata, not just create/upload/download.
        "view": ["create", "edit", "upload_version", "download"],
        "edit": ["create", "upload_version", "download",
                 "upload_version_all", "download_all", "publish_discord_all",
                 "deploy_server_1", "deploy_server_2", "undeploy_server_1", "undeploy_server_2"],
        "delete": ["delete_all", "edit_all",
                   "upload_version_all", "download_all",
                   "deploy_server_1", "deploy_server_2", "undeploy_server_1", "undeploy_server_2",
                   "delete_version_all", "restore_version_all",
                   "manage_categories", "change_owner"],
    },
}

# Analytics used to piggyback on the "admin" page's "view" action
# (routes/analytics.py used to check check_permission(admin, "admin", "view"))
# and now has its own "analytics" page — cross-page backfill so nobody loses
# analytics access they already had.
PERMISSION_CROSS_PAGE_SPLITS: list[tuple[str, str, str, str]] = [
    # (from_page, from_action, to_page, to_action)
    ("admin", "view", "analytics", "view"),
]


def registry_pages() -> list[str]:
    return list(PERMISSION_REGISTRY.keys())
