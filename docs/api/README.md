# NexusData Admin API

API принимает активный ключ входа, выданный через `/slogin`, в заголовке:

```http
X-ND-Login-Key: nd_<секрет>
```

Ключ действует ровно как аккаунт, которому он выдан. Он не превращает
модератора в суперпользователя. Ключ нельзя передавать в query string.

Обычный лимит API-ключа — 400 запросов за 60 секунд на конкретный login key.
Для превышения сервер возвращает `429` и `Retry-After`. Внутренние лимиты
дорогих операций сохраняются.

Основные endpoints описаны в `openapi.yaml`. Полный список существующих
операций можно получать тем же способом: каждый endpoint сохраняет свои
проверки `check_permission`, object-level ACL и superuser-only ограничения.

## Аудит прав

`GET /api/v1/admin/users` возвращает список администраторов и краткий summary.

`GET /api/v1/admin/users/{admin_id}/authorization` возвращает effective matrix,
источники права, группу, individual permissions, департаменты и уровни,
linked player, login keys, а также отдельные документные и task-board ACL.

В page/action matrix используются три состояния:

- отсутствующий ключ — `unset`;
- `true` — `allow`;
- `false` — `deny`.

В режиме `deny_v2` любой явный `deny` из индивидуальных или групповых прав побеждает `allow`. Права департаментов — grant-only: они могут добавлять доступ, но их `deny` не отнимает доступ и не участвует в конфликте. Суперпользователь
остаётся bypass. Игровые права Barotrauma в этот API-аудит не входят.

## Переключение policy без shell

1. `GET /api/v1/admin/permission-policy/impact` — получить digest и список изменений.
2. Проверить `affected_admins` и `changed_permission_count`.
3. Передать digest в `POST /api/v1/admin/permission-policy/apply` с подтверждением `DENY_POLICY_V2`.
4. Для отката получить impact с `target=legacy_additive` и вызвать rollback с подтверждением `RESTORE_LEGACY_PERMISSION_POLICY`.

Операции apply/rollback доступны только superuser, проверяют digest и пишут
аудит. GET impact ничего не изменяет.
