from fastapi import APIRouter, Depends, HTTPException
from schemas import StatusResponse
from config import get_settings
from fastapi import Request
from datetime import datetime
from security import verify_api_key_dependency

router = APIRouter(tags=["system"])

settings = get_settings()
auth_dependency = Depends(verify_api_key_dependency) if settings.require_auth else None


@router.get("/status", response_model=StatusResponse, dependencies=[auth_dependency] if auth_dependency else [])
def get_status():
    """Get API server status"""
    return {
        "status": "OK",
        "version": settings.app_version,
    }


@router.get("/health", dependencies=[auth_dependency] if auth_dependency else [])
def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@router.get("/metrics", dependencies=[auth_dependency] if auth_dependency else [])
def metrics(request: Request):
    """Basic metrics: uptime and version"""
    start = getattr(request.app.state, "start_time", None)
    uptime = None
    if start:
        uptime = (datetime.utcnow() - start).total_seconds()

    return {"status": "OK", "version": settings.app_version, "uptime_seconds": uptime}


@router.get("/debug-key", dependencies=[auth_dependency] if auth_dependency else [])
def debug_key():
    """Debug endpoint: show current key config (only in dev)."""
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        "require_auth": settings.require_auth,
        "api_key": (settings.api_key[:4] + "***") if settings.api_key else None,
        "api_key_len": len(settings.api_key or ""),
    }
