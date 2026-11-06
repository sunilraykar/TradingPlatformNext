from app.providers.apiplatform.auth import APIPLATFORMAuth
from app.providers.apiplatform.client import APIPLATFORMClient

_auth: APIPLATFORMAuth | None = None
_client: APIPLATFORMClient | None = None


def configure_apiplatform(auth: APIPLATFORMAuth | None = None) -> APIPLATFORMClient:
    """Configure one process-wide auth/session pair for all APIPLATFORM services."""
    global _auth, _client
    if _client is not None:
        _client.auth.shutdown()
    _auth = auth or APIPLATFORMAuth()
    _client = APIPLATFORMClient(_auth)
    return _client


def get_apiplatform_auth() -> APIPLATFORMAuth:
    if _auth is None:
        configure_apiplatform()
    assert _auth is not None
    return _auth


def get_apiplatform_client() -> APIPLATFORMClient:
    if _client is None:
        configure_apiplatform()
    assert _client is not None
    return _client


def shutdown_apiplatform() -> None:
    global _auth, _client
    if _auth is not None:
        _auth.shutdown()
    _auth = None
    _client = None
