"""Filtered live IDaddy Swing data from APIPLATFORM."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.providers.apiplatform.client import APIPLATFORMClientError
from app.providers.apiplatform.auth import APIPLATFORMAuthError
from app.providers.apiplatform.registry import get_apiplatform_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/apiplatform/swing", tags=["APIPLATFORM Swing"])


def _star(value: Any) -> int | None:
    try:
        number = int(float(str(value).strip()))
        return number
    except (TypeError, ValueError):
        return None


def _filtered_records(records: Any, *, side: str) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        return []
    status_field = "S_OI_Status" if side == "bullish" else "R_OI_Status"
    star_field = "S_OI_BullishStarRating" if side == "bullish" else "R_OI_BearishStarRating"
    allowed_status = {"STRONG", "WTT"} if side == "bullish" else {"STRONG", "WTB"}

    # Preserve API order. For duplicate symbols, the last qualifying occurrence wins.
    selected: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        status = str(item.get(status_field) or "").strip().upper()
        star = _star(item.get(star_field))
        if not symbol or status not in allowed_status or star not in {0, 1}:
            continue
        selected[symbol] = {**item, "symbol": symbol}
    return list(selected.values())


@router.get("/live")
def idaddy_swing_live() -> dict[str, Any]:
    """Fetch and filter bullish/bearish IDaddy Swing records using shared APIPLATFORM auth."""
    try:
        response = get_apiplatform_client().get("/reports/idaddy-swing-live")
        payload = response.json()
    except (APIPLATFORMClientError, APIPLATFORMAuthError) as exc:
        logger.warning("APIPLATFORM Swing request failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="APIPLATFORM Swing endpoint returned invalid JSON.") from exc
    except Exception as exc:
        logger.exception("Unexpected APIPLATFORM Swing request failure")
        raise HTTPException(status_code=502, detail="Unable to fetch APIPLATFORM Swing data.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="APIPLATFORM Swing endpoint returned an unexpected payload.")

    bullish = _filtered_records(payload.get("bullish"), side="bullish")
    bearish = _filtered_records(payload.get("bearish"), side="bearish")
    return {
        "bullish": bullish,
        "bearish": bearish,
        "counts": {"bullish": len(bullish), "bearish": len(bearish)},
        "fetched_at": payload.get("FetchTime") or payload.get("fetchTime"),
    }
