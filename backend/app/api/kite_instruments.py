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


@router.get("/option-contract")
def find_option_contract(
    underlying: str,
    expiry: str,
    strike: float,
    option_type: str,
) -> dict[str, object]:
    """Resolve and subscribe the exact option contract selected in the Option Chain."""
    name = underlying.strip().upper()
    side = option_type.strip().upper()
    if not name or len(name) > 40 or side not in {"CE", "PE"} or strike <= 0:
        raise HTTPException(status_code=400, detail="A valid underlying, strike and CE/PE option type are required")
    try:
        expiry_date = datetime.strptime(expiry.strip(), "%d-%m-%Y").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Expiry must use DD-MM-YYYY") from exc

    exchange = "BFO" if name in {"SENSEX", "BANKEX"} else "NFO"
    try:
        with get_engine().connect() as connection:
            row = connection.execute(text("""
                SELECT instrument_token, exchange, tradingsymbol, name, instrument_type,
                       expiry, strike, lot_size, tick_size
                FROM instruments
                WHERE source = 'KITE' AND is_active = TRUE
                  AND exchange = :exchange AND name = :underlying
                  AND instrument_type = :option_type
                  AND expiry = :expiry AND strike = :strike
                ORDER BY instrument_version DESC
                LIMIT 1
            """), {
                "exchange": exchange, "underlying": name, "option_type": side,
                "expiry": expiry_date, "strike": strike,
            }).mappings().first()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"No {side} contract found for {name} {strike:g} expiry {expiry_date:%d-%m-%Y}. Refresh the Kite instrument master.",
            )
        contract = dict(row)
        contract["instrument_token"] = int(contract["instrument_token"])
        contract["strike"] = float(contract["strike"])
        contract["lot_size"] = int(contract["lot_size"] or 1)
        contract["expiry"] = contract["expiry"].strftime("%d-%m-%Y") if hasattr(contract["expiry"], "strftime") else str(contract["expiry"])
        from app.services import live_market_data
        live_market_data.replace_subscription_group(
            "order_ticket_option", [contract["instrument_token"]], mode="quote"
        )
        return {"contract": contract, "market_data": live_market_data.status()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to resolve the selected Kite option contract") from exc


@router.get("/derivatives")
def find_derivative_contracts(underlying: str) -> dict[str, object]:
    """Find nearest active futures contracts for an underlying."""
    name = underlying.strip().upper()
    if not name or len(name) > 40:
        raise HTTPException(status_code=400, detail="A valid underlying symbol is required")
    try:
        with get_engine().connect() as connection:
            rows = connection.execute(text(
                "SELECT instrument_token, exchange, tradingsymbol, name, instrument_type, expiry, lot_size, tick_size "
                "FROM instruments WHERE source = 'KITE' AND is_active = TRUE AND exchange = 'NFO' "
                "AND instrument_type = 'FUT' AND UPPER(name) = :underlying AND expiry >= CURDATE() "
                "ORDER BY expiry ASC LIMIT 12"
            ), {"underlying": name}).mappings().all()
        return {"instruments": [dict(row) for row in rows]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to search the Kite futures instrument master") from exc


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
