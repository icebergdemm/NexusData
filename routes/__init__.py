from .status import router as status_router
from .kits import router as kits_router
from .players import router as players_router
from .stats import router as stats_router
from .admin import router as admin_router
from .events import router as events_router
from .moderation import api_router as moderation_api_router, panel_router as moderation_panel_router
from .sub_projects import router as sub_projects_router
from .game_api import router as game_api_router, public_router as game_public_router
from .ratings import router as ratings_router
from .discord_bot_api import router as discord_bot_api_router, panel_router as discord_bot_panel_router
from .tickets_api import bot_router as tickets_bot_router, panel_router as tickets_panel_router
from .logs import router as logs_router
from .dispatch import api_router as dispatch_api_router, panel_router as dispatch_panel_router
from .replays import api_router as replays_api_router, panel_router as replays_panel_router

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
    "game_api_router",
    "game_public_router",
    "ratings_router",
    "discord_bot_api_router",
    "discord_bot_panel_router",
    "tickets_bot_router",
    "tickets_panel_router",
    "logs_router",
    "dispatch_api_router",
    "dispatch_panel_router",
    "replays_api_router",
    "replays_panel_router",
]
