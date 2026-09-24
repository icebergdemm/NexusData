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
    "disguise": {"label": "Маскировка игрока", "actions": [
        {"key": "use", "label": "Использование маскировки"},
        {"key": "view_details", "label": "Просмотр деталей маскировки в модерации"},
    ]},
    # "Игроки" разбита на отдельные действия по тому же принципу, что и
    # sub_projects: раньше ОДИН ключ "edit" открывал всё сразу — от заметок
    # в личном деле до выдачи внутриигровых наград, рангов и постановки
    # ловушек, хотя это совершенно разные по чувствительности вещи.
    # Базовые view/edit/delete сохранены (их проверяют общие места вроде
    # списка игроков и canEdit во фронтенде), а конкретные операции теперь
    # гейтятся своими ключами — см. PERMISSION_ACTION_SPLITS ниже, который
    # выдаёт их всем, у кого уже был "edit", чтобы никто ничего не потерял.
    "players": {"label": "Игроки", "actions": [
        {"key": "view", "label": "Просмотр списка и профилей"},
        {"key": "view_ip", "label": "Просмотр IP-адресов игроков"},
        {"key": "edit", "label": "Изменение (базовое)"},
        {"key": "delete", "label": "Удаление"},
        {"key": "sync", "label": "Принудительная синхронизация с игровым сервером"},
        {"key": "edit_rank_kit", "label": "Изменение ранга и уровня набора"},
        {"key": "edit_discord_id", "label": "Привязка/подтверждение Discord ID"},
        {"key": "manage_notes", "label": "Заметки в личном деле"},
        {"key": "manage_labels", "label": "Назначение меток игрокам"},
        {"key": "manage_secret_labels", "label": "Полное управление секретными метками"},
        {"key": "manage_label_categories", "label": "Управление категориями меток (совместимость / все операции)"},
        {"key": "create_label_category", "label": "Создание категорий меток"},
        {"key": "edit_label_category", "label": "Изменение категорий меток"},
        {"key": "delete_label_category", "label": "Удаление категорий меток"},
        {"key": "manage_traps", "label": "Ловушки (уведомление о входе игрока)"},
        {"key": "manage_achievements", "label": "Достижения"},
        {"key": "manage_rewards", "label": "Награды (наборы, предметы, статы, таланты)"},
        # Отдельно от привязки к аккаунту ND (та остаётся только суперпользователю —
        # см. /link-admin: она выдаёт сайтовые права через аккаунт) — здесь только
        # отображаемое имя игрока, без всякой привязки.
        {"key": "set_forced_name", "label": "Установка принудительного имени (без привязки к аккаунту ND)"},
    ]},
    # "view" здесь — допуск к профилям, помеченным is_secret ("Засекретить"
    # в карточке игрока), а также к профилям, привязанным к аккаунту
    # суперпользователя (см. routes/admin.py: api_player_detail / player_detail).
    "players_special": {"label": "Игроки (секретные профили)", "actions": [{"key": "view", "label": "Доступ к засекреченным профилям и профилям суперпользователей"}]},
    "moderation": {"label": "Модерирование", "actions": [
        {"key": "view", "label": "Просмотр"},
        {"key": "message", "label": "Отправка сообщения игроку"},
        {"key": "kick", "label": "Кик игрока"},
        {"key": "ban", "label": "Бан игрока"},
        {"key": "manage_anticheat", "label": "Управление античит-событиями"},
        {"key": "edit", "label": "Изменение (прочее)"},
        {"key": "delete", "label": "Удаление записей"},
        # Консоль игрового сервера (Pterodactyl) — намеренно РАЗДЕЛЬНЫЕ права
        # по серверам, а не одно общее "console": человек, которому доверяют
        # консоль ASPER1, не обязан автоматически получать её и на ASPER2.
        # Это прямой доступ к серверной консоли — самое чувствительное право
        # на этой странице, выдавать точечно.
        {"key": "console_server1", "label": "Консоль Сервера 1 (ASPER1, Pterodactyl)"},
        {"key": "console_server2", "label": "Консоль Сервера 2 (ASPER2, Pterodactyl)"},
    ]},
    "moderation_templates": {"label": "Шаблоны модерации", "actions": [
        {"key": "manage", "label": "Управление шаблонами"},
    ]},
    # Диспетчерская: раньше ОДИН ключ "edit" открывал сразу всё — от реплики
    # в эфир до торпедного удара по координатам и правки склада, хотя это
    # разные по последствиям вещи (сообщение можно опровергнуть, залп — нет).
    # Базовые view/edit сохранены: их проверяют общие места и canEdit во
    # фронтенде, а конкретные операции гейтятся своими ключами.
    "dispatch": {"label": "Диспетчерская", "actions": [
        {"key": "view", "label": "Просмотр эфира и статуса раунда"},
        {"key": "edit", "label": "Изменение (прочее)"},
        {"key": "send_message", "label": "Сообщения в эфир от диспетчера"},
        {"key": "shuttle", "label": "Отправка и отзыв снабженческого челнока"},
        {"key": "cargo", "label": "Отправка груза (списывает склад)"},
        {"key": "torpedo", "label": "Торпедный удар"},
        {"key": "manage_warehouse", "label": "Управление складом"},
        {"key": "manage_webhooks", "label": "Настройка вебхуков внешней системы"},
        {"key": "manage_ai", "label": "Включение и отключение ИИ-диспетчера"},
    ]},
    # Куклы респавна: заранее подготовленные персонажи для ивентов.
    # Создание отделено от отмены: подготовить куклу и снять чужую со спавна —
    # разные по последствиям действия.
    "respawn_dolls": {"label": "Куклы респавна", "actions": [
        {"key": "view", "label": "Просмотр списка кукол"},
        {"key": "create", "label": "Создание и правка кукол"},
        {"key": "cancel", "label": "Отмена кукол"},
    ]},
    # Ачивки: конструктор (событие → условия → награда) + прогресс игроков.
    # "grant" отдельно от "edit": ручная выдача/сброс ачивки конкретному
    # игроку — не то же самое, что редактирование самого конструктора.
    "achievements": {"label": "Ачивки", "actions": [
        {"key": "view", "label": "Просмотр списка ачивок и прогресса игроков"},
        {"key": "edit", "label": "Создание и редактирование ачивок"},
        {"key": "grant", "label": "Ручная выдача/сброс ачивки игроку"},
    ]},
    # Звуки для ивентов: файлы .wav/.mp3 на игровых серверах (по SFTP).
    # Действия разделены, потому что «послушать/скачать» и «удалить с боевого
    # сервера» — принципиально разный уровень доверия. Настройки SFTP правит
    # только суперпользователь (проверяется явно в routes/sounds.py), поэтому
    # отдельного ключа под них здесь нет.
    "sounds": {"label": "Звуки для ивентов", "actions": [
        {"key": "view", "label": "Просмотр и прослушивание"},
        {"key": "upload", "label": "Загрузка новых звуков"},
        {"key": "rename", "label": "Переименование"},
        {"key": "delete", "label": "Удаление с сервера"},
        {"key": "restore", "label": "Восстановление из локальной копии"},
    ]},
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
    # Проверщик кораблей по ГОСТу. Запуск проверки и правка самого норматива —
    # принципиально разные вещи: проверить корабль нужно каждому судостроителю,
    # а менять пороги, формулы и списки запрещённых предметов — только тем, кто
    # отвечает за ГОСТ, поэтому настройки разведены на два отдельных ключа.
    "sub_checker": {"label": "Чекер кораблей (ГОСТ)", "actions": [
        {"key": "view", "label": "Просмотр отчётов и истории проверок"},
        {"key": "check", "label": "Запуск проверки и загрузка файлов"},
        {"key": "manage_rules", "label": "Настройка проверок, формул и запрещённых предметов"},
        {"key": "manage_classes", "label": "Настройка классов кораблей и наполнения"},
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
        {"key": "delete", "label": "Удаление закрытого обращения"},
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
        {"key": "developer", "label": "Режим разработки и смена статусов"},
        {"key": "create", "label": "Создание задач"},
        {"key": "edit", "label": "Редактирование своих задач (созданных или назначенных)"},
        {"key": "edit_all", "label": "Редактирование любых задач"},
        {"key": "delete", "label": "Удаление своих задач"},
        {"key": "delete_all", "label": "Удаление любых задач"},
        {"key": "manage_boards", "label": "Управление досками и колонками"},
    ]},
    # Read-only live-round viewer fed by the client-side WorldStateStreamer
    # plugin (see WORLD_STATE_API_GUIDE.md / routes/world_state.py) — no
    # write actions exist on the admin side, just "can see it".
    "world_state": {"label": "Обзор мира", "actions": [{"key": "view", "label": "Просмотр"}]},
    "monster_spawn": {"label": "Управление монстрами", "actions": [
        {"key": "view", "label": "Просмотр"},
        {"key": "edit", "label": "Редактирование настроек и планов"},
        {"key": "manage_memory", "label": "Редактирование долговременной памяти"},
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
    # Диспетчерская: до разбиения все перечисленные операции гейтились ровно
    # `check_permission(admin, "dispatch", "edit")` (сообщения, челнок, груз,
    # торпеда — routes/dispatch.py; склад — routes/warehouse.py), поэтому
    # выдаём их всем, у кого "edit" уже есть.
    # "manage_webhooks" НАМЕРЕННО не бэкфиллится: настройка вебхуков была
    # доступна только суперпользователю (жёсткая проверка в обработчике), и
    # выдача её нынешним держателям "edit" была бы расширением прав, а не
    # сохранением текущих возможностей.
    "dispatch": {"edit": ["send_message", "shuttle", "cargo", "torpedo", "manage_warehouse"]},
    # "Игроки": до разбиения ОДИН ключ "edit" открывал все перечисленные
    # операции без исключений (см. routes/admin.py — все они гейтились
    # ровно `check_permission(admin, "players", "edit")`), поэтому все новые
    # ключи выдаются каждому, у кого уже был "edit" — это сохранение
    # текущих возможностей, а не расширение. "delete" не добавляет ничего
    # нового: там, где он упоминался, это был лишь альтернативный вариант
    # рядом с "edit", своих эксклюзивных операций у него не было.
    "players": {
        "edit": ["sync", "edit_rank_kit", "edit_discord_id", "manage_notes",
                 "manage_labels", "manage_label_categories", "manage_traps",
                 "manage_achievements", "manage_rewards",
                 "create_label_category", "edit_label_category", "delete_label_category"],
        "manage_label_categories": ["create_label_category", "edit_label_category", "delete_label_category"],
    },
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
    # Проверщик кораблей — новая страница, но кнопка «Проверить корабль» стоит
    # в карточке каждого Sub-проекта и автопроверка запускается при загрузке
    # версии, поэтому смотреть и запускать её должен уметь всякий, кто и так
    # работает с проектами. Правка самого норматива (manage_rules/
    # manage_classes) НАМЕРЕННО не бэкфиллится: это отдельное доверие.
    ("sub_projects", "view", "sub_checker", "view"),
    ("sub_projects", "view", "sub_checker", "check"),
]


def registry_pages() -> list[str]:
    return list(PERMISSION_REGISTRY.keys())
