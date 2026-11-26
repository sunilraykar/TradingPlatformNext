import logging
import traceback
from datetime import datetime
from fastapi import APIRouter, Request, Query
from sqlalchemy import text
from app.db.session import get_engine

router = APIRouter(prefix="/logs", tags=["system logs"])
logger = logging.getLogger(__name__)

def ensure_logs_schema():
    with get_engine().begin() as conn:
        conn.execute(text("""CREATE TABLE IF NOT EXISTS application_error_logs (
          id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
          created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
          category VARCHAR(40) NOT NULL, severity VARCHAR(16) NOT NULL,
          symbol VARCHAR(80) NULL, operation VARCHAR(120) NULL,
          message TEXT NOT NULL, details LONGTEXT NULL,
          reviewed BOOLEAN NOT NULL DEFAULT FALSE,
          INDEX ix_app_logs_created (created_at), INDEX ix_app_logs_filter (category,severity)
        ) ENGINE=InnoDB"""))

def record_event(category, severity, message, details=None, symbol=None, operation=None):
    ensure_logs_schema()
    with get_engine().begin() as conn:
        conn.execute(text("""INSERT INTO application_error_logs
          (category,severity,message,details,symbol,operation)
          VALUES (:category,:severity,:message,:details,:symbol,:operation)"""),
          {"category":category,"severity":severity,"message":str(message)[:6000],
           "details":str(details)[:20000] if details else None,"symbol":symbol,"operation":operation})

@router.get("")
def list_logs(category: str|None=None, severity: str|None=None, symbol: str|None=None,
              reviewed: bool|None=None, limit: int=Query(200,ge=1,le=500), offset: int=Query(0,ge=0)):
    ensure_logs_schema()
    where=[]; params={"limit":limit,"offset":offset}
    if category: where.append("category=:category"); params["category"]=category
    if severity: where.append("severity=:severity"); params["severity"]=severity
    if symbol: where.append("symbol LIKE :symbol"); params["symbol"]="%"+symbol+"%"
    if reviewed is not None: where.append("reviewed=:reviewed"); params["reviewed"]=reviewed
    clause=" WHERE "+" AND ".join(where) if where else ""
    with get_engine().connect() as conn:
        rows=conn.execute(text("SELECT * FROM application_error_logs"+clause+" ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),params).mappings().all()
        count=conn.execute(text("SELECT COUNT(*) FROM application_error_logs"+clause),params).scalar()
    return {"items":[dict(x) for x in rows],"total":count}

@router.post("/test")
def create_test_log():
    record_event("SYSTEM", "WARNING", "System log test event: persistence and viewer are connected", details="Generated from the System Logs viewer test action.", operation="log_test")
    return {"created": True}


@router.patch("/{log_id}/review")
def mark_reviewed(log_id: int):
    ensure_logs_schema()
    with get_engine().begin() as conn:
        result=conn.execute(text("UPDATE application_error_logs SET reviewed=TRUE WHERE id=:id"),{"id":log_id})
    return {"updated":result.rowcount>0,"id":log_id,"reviewed":True}
