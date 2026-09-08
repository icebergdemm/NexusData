from .status import router as status_router
from .kits import router as kits_router
from .players import router as players_router
from .stats import router as stats_router
from .admin import router as admin_router
from .events import router as events_router
from .moderation import api_router as moderation_api_router, panel_router as moderation_panel_router
from .sub_projects import router as sub_projects_router
from .sub_projects_polygon import router as sub_projects_polygon_router
from .sub_checker import router as sub_checker_router
from .game_api import router as game_api_router, public_router as game_public_router
from .ratings import router as ratings_router
from .rating_events import router as rating_events_router
from .rating_admin_v2 import router as rating_admin_v2_router
from .discord_bot_api import panel_router as discord_bot_panel_router
from .discord_viewer_api import router as discord_viewer_router
from .tickets_api import panel_router as tickets_panel_router
from .logs import router as logs_router
from .dispatch import api_router as dispatch_api_router, panel_router as dispatch_panel_router, client_router as dispatch_client_router
from .replays import api_router as replays_api_router, panel_router as replays_panel_router, ws_router as replays_ws_router
from .analytics import router as analytics_router
from .notifications import router as notifications_router
from .documents import router as documents_router
from .onlyoffice import router as onlyoffice_router
from .departments import router as departments_router
from .tasks import router as tasks_router
from .world_state import router as world_state_router, admin_router as world_state_admin_router, client_router as world_state_client_router
from .warehouse import admin_router as warehouse_admin_router, client_router as warehouse_client_router
from .cache_sync import api_router as cache_sync_api_router
from .sounds import router as sounds_router
from .respawn_dolls import game_router as dolls_game_router, admin_router as dolls_admin_router
from .achievements import game_router as achievements_game_router, admin_router as achievements_admin_router
from .error_log import router as error_log_router
from .perf_monitor import router as perf_monitor_router
from .monster_spawn import game_router as monster_spawn_game_router, admin_router as monster_spawn_admin_router
from .ai_dossier import router as ai_dossier_router
from .communications import router as communications_router
from .server_restart import router as server_restart_router

__all__ = [
    "status_router",
    "kits_router",
    "players_router",
    "stats_router",
    "admin_router",
    "events_router",
    "moderation_api_router",
    "moderation_panel_router",
    "sub_projects_router",
    "sub_projects_polygon_router",
    "sub_checker_router",
    "game_api_router",
    "game_public_router",
    "ratings_router",
    "rating_events_router",
    "rating_admin_v2_router",
    "discord_bot_panel_router",
    "discord_viewer_router",
    "tickets_panel_router",
    "logs_router",
    "dispatch_api_router",
    "dispatch_panel_router",
    "dispatch_client_router",
    "replays_api_router",
    "replays_ws_router",
    "replays_panel_router",
    "analytics_router",
    "notifications_router",
    "documents_router",
    "onlyoffice_router",
    "departments_router",
    "tasks_router",
    "world_state_router",
    "world_state_admin_router",
    "world_state_client_router",
    "warehouse_admin_router",
    "warehouse_client_router",
    "cache_sync_api_router",
    "sounds_router",
    "dolls_game_router",
    "dolls_admin_router",
    "achievements_game_router",
    "achievements_admin_router",
    "error_log_router",
    "perf_monitor_router",
    "monster_spawn_game_router",
    "monster_spawn_admin_router",
    "ai_dossier_router",
    "communications_router",
    "server_restart_router",
]
