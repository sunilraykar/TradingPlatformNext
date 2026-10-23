from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/autotrender/auth", tags=["AutoTrender Authentication"])


class LoginResult(BaseModel):
    authenticated: bool
    message: str


def _auth(request: Request):
    auth = getattr(request.app.state, "autotrender_auth", None)
    if auth is None:
        from app.providers.autotrender.auth import AutoTrenderAuth

        auth = AutoTrenderAuth()
        request.app.state.autotrender_auth = auth
    return auth


@router.post("/login", response_model=LoginResult)
def login(request: Request):
    """Start AutoTrender login using credentials configured in backend/.env."""
    try:
        return LoginResult(**_auth(request).login())
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/status")
def status(request: Request):
    return {"authenticated": _auth(request).is_authenticated()}
