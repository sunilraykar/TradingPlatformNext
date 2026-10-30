"""Kite instrument-master refresh controls and diagnostics."""
import json
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import get_engine
from app.services.kite_instrument_master import instrument_master_status, refresh_instrument_master
from app.services.option_contract_selector import select_atm_contract

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


class SelectedContractRequest(BaseModel):
    index: str
    status: str
    signal: str
    option_type: str
    tradingsymbol: str
    instrument_token: int
    strike: float
    expiry: str
    lot_size: int
    exchange: str
    spot_price: float


@router.post("/selected-contract")
def save_selected_contract(payload: SelectedContractRequest) -> dict[str, object]:
    """Persist the exact contract currently displayed by AutoTrender for its worker."""
    index = payload.index.strip().upper()
    signal = payload.signal.strip().upper()
    expected_type = "CE" if signal == "BUY" else "PE" if signal == "SELL" else ""
    if payload.status != "SELECTED" or index not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY", "BANKEX"}:
        raise HTTPException(status_code=422, detail="A selected AutoTrender contract is required")
    if not expected_type or payload.option_type.upper() != expected_type or not payload.tradingsymbol.upper().startswith(index):
        raise HTTPException(status_code=422, detail="Selected contract does not match the underlying and signal")
    selected = payload.model_dump()
    selected["index"] = index
    selected["signal"] = signal
    selected["selected_at"] = datetime.now().astimezone().isoformat()
    key = "autotrender.selected_contract." + index
    try:
        with get_engine().begin() as connection:
            connection.execute(text(
                "INSERT INTO app_settings (setting_key, setting_value, description, is_secret) "
                "VALUES (:key, CAST(:value AS JSON), 'Current AutoTrender selected option contract', FALSE) "
                "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value), "
                "description = VALUES(description), is_secret = FALSE, version = version + 1"
            ), {"key": key, "value": json.dumps(selected, separators=(",", ":"))})
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to persist AutoTrender selected contract") from exc
    return {"saved": True, "index": index, "tradingsymbol": payload.tradingsymbol}


@router.get("/select-atm")
def select_atm(index: str, signal: str) -> dict[str, object]:
    """Select the nearest listed ATM option for a supported index and signal."""
    try:
        return select_atm_contract(index, signal)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not select an option contract from the current Kite instrument master") from exc
