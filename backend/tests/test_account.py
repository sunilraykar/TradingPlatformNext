from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_live_order_requires_explicit_confirmation():
    with patch("app.api.account.get_kite_provider") as get_provider:
        response = client.post("/api/v1/kite/orders", json={
            "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
            "quantity": 1, "product": "CNC", "order_type": "MARKET",
        })
    assert response.status_code == 400
    get_provider.assert_not_called()


def test_live_order_validates_limit_price_before_broker_call():
    with patch("app.api.account.get_kite_provider") as get_provider:
        response = client.post("/api/v1/kite/orders", json={
            "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
            "quantity": 1, "product": "CNC", "order_type": "LIMIT",
            "confirm_live_action": True, "confirmation_text": "CONFIRM LIVE ORDER",
        })
    assert response.status_code == 422
    get_provider.assert_not_called()


def test_live_order_submits_only_after_confirmation():
    provider = MagicMock()
    provider.place_order.return_value = "order-123"
    with patch("app.api.account.get_kite_provider", return_value=provider):
        response = client.post("/api/v1/kite/orders", json={
            "exchange": "NSE", "tradingsymbol": "INFY", "transaction_type": "BUY",
            "quantity": 1, "product": "CNC", "order_type": "MARKET",
            "confirm_live_action": True, "confirmation_text": "CONFIRM LIVE ORDER",
        })
    assert response.status_code == 200
    assert response.json()["order_id"] == "order-123"
    provider.place_order.assert_called_once_with(
        exchange="NSE", tradingsymbol="INFY", transaction_type="BUY",
        quantity=1, product="CNC", order_type="MARKET", validity="DAY",
        disclosed_quantity=0,
    )


def test_cancel_order_requires_confirmation_before_broker_call():
    provider = MagicMock()
    with patch("app.api.account.get_kite_provider", return_value=provider):
        response = client.request(
            "DELETE", "/api/v1/kite/orders/regular/order-123",
            json={"confirm_live_action": True, "confirmation_text": "no"},
        )
    assert response.status_code == 400
    provider.cancel_order.assert_not_called()


def test_trading_account_ui_is_served():
    response = client.get("/api/v1/trading-account")
    assert response.status_code == 200
    assert "Place live Kite order" in response.text



def test_modify_order_requires_confirmation_before_broker_call():
    provider = MagicMock()
    with patch("app.api.account.get_kite_provider", return_value=provider):
        response = client.put(
            "/api/v1/kite/orders/regular/order-123",
            json={"quantity": 2, "confirm_live_action": True, "confirmation_text": "no"},
        )
    assert response.status_code == 400
    provider.modify_order.assert_not_called()


def test_modify_order_calls_kite_after_confirmation():
    provider = MagicMock()
    provider.modify_order.return_value = "order-123"
    with patch("app.api.account.get_kite_provider", return_value=provider):
        response = client.put(
            "/api/v1/kite/orders/regular/order-123",
            json={
                "quantity": 2,
                "confirm_live_action": True,
                "confirmation_text": "CONFIRM LIVE ORDER",
            },
        )
    assert response.status_code == 200
    assert response.json()["status"] == "modified"
    provider.modify_order.assert_called_once_with(
        order_id="order-123", variety="regular", quantity=2
    )

def test_cancel_amo_order_calls_kite_after_confirmation():
    provider = MagicMock()
    provider.cancel_order.return_value = "order-123"
    with patch("app.api.account.get_kite_provider", return_value=provider):
        response = client.request(
            "DELETE", "/api/v1/kite/orders/amo/order-123",
            json={"confirm_live_action": True, "confirmation_text": "CONFIRM LIVE ORDER"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "cancel_requested"
    provider.cancel_order.assert_called_once_with(order_id="order-123", variety="amo")
