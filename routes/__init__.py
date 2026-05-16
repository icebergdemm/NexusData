from .status import router as status_router
from .kits import router as kits_router
from .players import router as players_router
from .stats import router as stats_router
from .admin import router as admin_router
from .events import router as events_router
from .moderation import api_router as moderation_api_router, panel_router as moderation_panel_router
from .sub_projects import router as sub_projects_router

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
]
