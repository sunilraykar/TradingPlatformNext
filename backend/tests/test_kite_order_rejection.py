from unittest.mock import MagicMock

import pytest
from kiteconnect.exceptions import InputException

from app.providers.kite.client import KiteOrderRejectedError, KiteProvider


def test_place_order_surfaces_definitive_kite_input_rejection():
    provider = KiteProvider.__new__(KiteProvider)
    provider._client = MagicMock()
    provider._client.access_token = "test-access-token"
    provider._client.place_order.side_effect = InputException(
        "Market orders without market protection are not allowed via API."
    )

    with pytest.raises(KiteOrderRejectedError) as error:
        provider.place_order(
            exchange="NFO",
            tradingsymbol="NIFTY",
            transaction_type="BUY",
            quantity=65,
            product="MIS",
            order_type="MARKET",
            market_protection=-1,
            validity="IOC",
            tag="AT123",
        )

    assert error.value.code == 400
    assert "market protection" in str(error.value).lower()
    provider._client.place_order.assert_called_once_with(
        variety="regular",
        exchange="NFO",
        tradingsymbol="NIFTY",
        transaction_type="BUY",
        quantity=65,
        product="MIS",
        order_type="MARKET",
        market_protection=-1,
        validity="IOC",
        tag="AT123",
    )
