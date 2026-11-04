from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from app.services.notification_events import notification_status

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/status")
def get_notification_status():
    try:
        return notification_status()
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Unable to load notification queue status from MySQL.") from exc
