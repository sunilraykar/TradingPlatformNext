from types import SimpleNamespace

from app.auth import security


def test_csrf_token_round_trip(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(app_secret_key="test-secret-key-that-is-at-least-32-characters"),
    )
    token = security.create_csrf_token()
    assert security.verify_csrf_token(token)
    assert not security.verify_csrf_token(token + "tampered")
    assert not security.verify_csrf_token(None)


def test_token_hash_is_stable_and_one_way():
    first = security.hash_token("opaque-session-token")
    assert first == security.hash_token("opaque-session-token")
    assert len(first) == 64
    assert first != "opaque-session-token"
    assert first != security.hash_token("different-token")
