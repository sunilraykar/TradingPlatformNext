from types import SimpleNamespace

import pytest

from app.core import outbound_proxy


def _settings(**overrides):
    values = {
        "outbound_proxy_enabled": True,
        "outbound_proxy_scheme": "http",
        "outbound_proxy_host": "dc-mum-001.staticip.in",
        "outbound_proxy_port": 443,
        "outbound_proxy_username": "sip_user",
        "outbound_proxy_password": "p@ss:word",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_proxy_url_encodes_credentials(monkeypatch):
    monkeypatch.setattr(outbound_proxy, "get_settings", lambda: _settings())

    assert outbound_proxy.get_proxy_url() == (
        "http://sip_user:p%40ss%3Aword@dc-mum-001.staticip.in:443"
    )


def test_proxy_url_brackets_ipv6_literal(monkeypatch):
    monkeypatch.setattr(
        outbound_proxy,
        "get_settings",
        lambda: _settings(outbound_proxy_host="2001:db8::10"),
    )

    assert outbound_proxy.get_proxy_url() == (
        "http://sip_user:p%40ss%3Aword@[2001:db8::10]:443"
    )


def test_proxy_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        outbound_proxy,
        "get_settings",
        lambda: _settings(outbound_proxy_enabled=False),
    )

    assert outbound_proxy.get_proxy_map() == {}


def test_enabled_proxy_requires_credentials(monkeypatch):
    monkeypatch.setattr(
        outbound_proxy,
        "get_settings",
        lambda: _settings(outbound_proxy_password=""),
    )

    with pytest.raises(RuntimeError, match="USERNAME and OUTBOUND_PROXY_PASSWORD"):
        outbound_proxy.get_proxy_url()


def test_proxy_status_is_kite_only(monkeypatch):
    monkeypatch.setattr(outbound_proxy, "get_settings", lambda: _settings())

    status = outbound_proxy.proxy_status()

    assert status["kite_rest"] is True
    assert status["kite_websocket"] is False
    assert status["other_providers"] is False
    assert status["firebase"] is False
