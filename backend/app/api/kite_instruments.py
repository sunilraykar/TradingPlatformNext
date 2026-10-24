"""Kite instrument-master refresh controls and diagnostics."""
from fastapi import APIRouter, HTTPException

from app.services.kite_instrument_master import instrument_master_status, refresh_instrument_master

router = APIRouter(prefix="/kite/instruments", tags=["Kite Instrument Master"])


@router.get("/refresh-status")
def refresh_status() -> dict[str, object]:
    """Return scheduler timing and the latest instrument-master refresh result."""
    return instrument_master_status()


@router.post("/refresh")
def refresh_now() -> dict[str, object]:
    """Manually download and atomically replace the KITE instrument master."""
    try:
        return refresh_instrument_master()
    except RuntimeError as exc:
        raise HTTPException(status_code=409 if "already running" in str(exc) else 503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Kite instrument-master refresh failed; previous master retained") from exc
