"""Isolated tests for AutoTrender Kite order submission; no broker calls are made."""
from unittest.mock import Mock, patch

import pytest

from app.services import autotrender_kite_execution as execution
from app.providers.kite.client import KiteOrderRejectedError


class _BeginContext:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def begin(self):
        return _BeginContext()


class _ClaimResult:
    rowcount = 1


class _Engine:
    def begin(self):
        return _BeginContext()


@pytest.fixture
 def prepared_intent():
    return {
        "idempotency_key": "AUTOTRENDER:TEST:NIFTY:2026-11-25T10:00:00+05:30",
        "order_tag": "ATMOCKTESTTAG123456",
        "role": "ENTRY",
        "underlying": "NIFTY",
        "trade_id": None,
        "broker_order_id": None,
        "status": "PREPARED",
        "exchange": "NFO",
        "tradingsymbol": "NIFTY26NOV25000CE",
        "transaction_type": "BUY",
        "quantity": 75,
        "filled_quantity": 0,
        "average_price": None,
        "payload": {},
        "last_error": None,
    }


def test_submit_intent_calls_provider_with_expected_order_parameters(prepared_intent):
    provider = Mock()
    provider.place_order.return_value = "MOCK-BROKER-ORDER-ID"
    engine = _Engine()
    get_intent_calls = [dict(prepared_intent), dict(prepared_intent, status="OPEN", broker_order_id="MOCK-BROKER-ORDER-ID")]

    with patch.object(execution, "_get_intent", side_effect=get_intent_calls), \
         patch.object(execution, "get_engine", return_value=engine), \
         patch.object(execution, "get_kite_provider", return_value=provider), \
         patch.object(execution, "_save_status") as save_status, \
         patch.object(execution.text, "__call__", side_effect=lambda value: value, create=True):
        # SQL execution is mocked at the connection level to avoid any database access.
        mock_connection = Mock()
        mock_connection.execute.return_value = _ClaimResult()
        begin_context = Mock()
        begin_context.__enter__ = Mock(return_value=mock_connection)
        begin_context.__exit__ = Mock(return_value=False)
        engine.begin = Mock(return_value=begin_context)
        result = execution._submit_intent(prepared_intent["idempotency_key"])

    provider.place_order.assert_called_once_with(
        exchange="NFO",
        tradingsymbol="NIFTY26NOV25000CE",
        transaction_type="BUY",
        quantity=75,
        product="MIS",
        order_type="MARKET",
        market_protection=-1,
        validity="IOC",
        tag="ATMOCKTESTTAG123456",
    )
    save_status.assert_called_once_with(prepared_intent["idempotency_key"], "OPEN", order_id="MOCK-BROKER-ORDER-ID")
    assert result["status"] == "OPEN"
    assert result["broker_order_id"] == "MOCK-BROKER-ORDER-ID"


def test_definite_kite_rejection_is_recorded_without_retry(prepared_intent):
    provider = Mock()
    provider.place_order.side_effect = KiteOrderRejectedError("Mock broker rejection")
    engine = _Engine()
    mock_connection = Mock()
    mock_connection.execute.return_value = _ClaimResult()
    begin_context = Mock()
    begin_context.__enter__ = Mock(return_value=mock_connection)
    begin_context.__exit__ = Mock(return_value=False)
    engine.begin = Mock(return_value=begin_context)

    with patch.object(execution, "_get_intent", return_value=dict(prepared_intent)), \
         patch.object(execution, "get_engine", return_value=engine), \
         patch.object(execution, "get_kite_provider", return_value=provider), \
         patch.object(execution, "_save_status") as save_status:
        with pytest.raises(KiteOrderRejectedError, match="Mock broker rejection"):
            execution._submit_intent(prepared_intent["idempotency_key"])

    provider.place_order.assert_called_once()
    save_status.assert_called_once_with(prepared_intent["idempotency_key"], "REJECTED", error="Mock broker rejection")
