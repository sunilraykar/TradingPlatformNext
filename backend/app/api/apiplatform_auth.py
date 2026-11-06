from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.providers.apiplatform.auth import APIPLATFORMAuthError
from app.providers.apiplatform.registry import get_apiplatform_auth

router = APIRouter(prefix="/apiplatform/auth", tags=["APIPLATFORM Authentication"])


class LoginResult(BaseModel):
    authenticated: bool
    message: str


@router.post("/login", response_model=LoginResult)
def login(request: Request):
    """Restore or establish the APIPLATFORM session using backend environment secrets."""
    auth = getattr(request.app.state, "apiplatform_auth", None) or get_apiplatform_auth()
    try:
        return LoginResult(**auth.login())
    except APIPLATFORMAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/status")
def status(request: Request):
    auth = getattr(request.app.state, "apiplatform_auth", None) or get_apiplatform_auth()
    return auth.status()
