from app.api import apiplatform_option_chain as option_chain_api


def test_latest_snapshot_cache_preserves_complete_provider_payload():
    option_chain_api.clear_latest_snapshot()
    payload = {
        "FetchTime": "2026-11-06 12:49:19",
        "Price_S": "24695.45",
        "symbolMarketDirection": {"marketDirection": "NIFTY IS IN BULL RUN"},
        "RS_Status": [{"R_Status": "VOLUME"}],
        "OptionChain": [{"strike": "24500", "callOI": "7.0"}],
    }

    snapshot = option_chain_api.store_latest_snapshot("NIFTY", "09-11-2026", 65, payload)
    cached = option_chain_api.read_latest_snapshot()

    assert snapshot["cached"] is True
    assert snapshot["symbol"] == "NIFTY"
    assert snapshot["expiry"] == "09-11-2026"
    assert snapshot["lot_size"] == 65
    assert snapshot["fetch_time"] == "2026-11-06 12:49:19"
    assert snapshot["option_chain_count"] == 1
    assert cached["payload"] == payload
    assert cached["payload"]["symbolMarketDirection"] == payload["symbolMarketDirection"]


def test_latest_snapshot_cache_reports_empty_after_clear():
    option_chain_api.clear_latest_snapshot()

    assert option_chain_api.read_latest_snapshot() == {
        "cached": False,
        "payload": None,
    }
