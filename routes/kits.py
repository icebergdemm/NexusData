from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_game_db
from services import KitService
from schemas import KitOut, PaginatedResponse
from security import verify_api_key_dependency
from config import get_settings
import math

settings = get_settings()

router = APIRouter(
    prefix="/kits",
    tags=["kits"],
    dependencies=[Depends(verify_api_key_dependency)] if settings.require_auth else []
)


@router.get("", response_model=PaginatedResponse[KitOut])
def get_all_kits(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_game_db)
):
    """Get all available kits with pagination"""
    service = KitService(db)
    kits, total = service.get_all_kits(page, page_size)
    
    total_pages = math.ceil(total / page_size)
    has_next = page < total_pages
    has_prev = page > 1
    
    return {
        "items": kits,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "has_next": has_next,
        "has_prev": has_prev,
    }


@router.get("/resolve")
def resolve_kit(
    steamid: str = Query(..., description="SteamID игрока"),
    job_id: str = Query(..., description="Профессия без суффикса _k, напр. \"captain\""),
    variation: int = Query(1, ge=1, le=3, description="Выбранная вариация (1..3)"),
    db: Session = Depends(get_game_db),
):
    """Разрешает состав кита для КОНКРЕТНОГО игрока на сервере, а не отдаёт
    весь каталог (как делает GET /kits — тот эндпоинт остаётся без изменений,
    это дополнительный, необязательный путь).

    Раньше клиент был вынужден скачивать весь каталог китов (`GET /kits?
    page_size=500`) и сам повторять алгоритм подбора (уровень квал-кита →
    поиск строки job_id+level → спец-кит/вариация → фолбэк на items) —
    ту же логику, что уже реализована в конструкторе китов на сайте.
    Теперь сервер может один раз дёрнуть этот эндпоинт с готовым ответом.

    ⚠️ Матчинг спец-китов/вариаций здесь воспроизведён по документации в
    models.Kit (см. докстринг колонок is_special/special_variation) — сверьте
    с реальной клиентской логикой (Achievements.cs/аналог) перед тем, как
    переключать боевой трафик на этот путь; если матчинг разойдётся —
    старый GET /kits как был, так и работает, ничего не сломано."""
    from models import PlayerData, Kit

    player = db.query(PlayerData).filter(PlayerData.player_steamid == steamid).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    ach = player.achievements or {}
    if isinstance(ach, list):
        ach = {}
    kval_kits = ach.get("kval_kits") or {}
    level = int(kval_kits.get(job_id, 0) or 0)

    kit_job_id = f"{job_id}_k"
    base = (
        db.query(Kit)
        .filter(Kit.job_id == kit_job_id, Kit.kit_level == level, Kit.is_special.is_(False))
        .first()
    )
    special = (
        db.query(Kit)
        .filter(Kit.job_id == kit_job_id, Kit.kit_level == level, Kit.is_special.is_(True), Kit.special_variation == variation)
        .first()
    )

    source = special or base
    if not source:
        return {
            "job_id": job_id, "level": level, "variation": variation,
            "is_special": False, "items": [], "found": False,
        }

    if special:
        items = special.items
    elif variation == 2 and source.variation_2_items:
        items = source.variation_2_items
    elif variation == 3 and source.variation_3_items:
        items = source.variation_3_items
    else:
        items = source.items

    return {
        "job_id": job_id, "level": level, "variation": variation,
        "is_special": bool(special), "kit_id": source.id, "items": items or [], "found": True,
    }


@router.get("/{kit_id}", response_model=KitOut)
def get_kit(kit_id: int, db: Session = Depends(get_game_db)):
    """Get kit by ID"""
    service = KitService(db)
    return service.get_kit(kit_id)

