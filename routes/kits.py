from fastapi import APIRouter, Depends, Query
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


@router.get("/{kit_id}", response_model=KitOut)
def get_kit(kit_id: int, db: Session = Depends(get_game_db)):
    """Get kit by ID"""
    service = KitService(db)
    return service.get_kit(kit_id)
