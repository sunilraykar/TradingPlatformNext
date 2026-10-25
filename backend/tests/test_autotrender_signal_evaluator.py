from app.services.autotrender_signal_evaluator import evaluate_signal


def row(**overrides):
    value = {
        "call": 80,
        "put": 20,
        "price": 1000,
        "vwap": 999,
        "pcr": 1.2,
        "vWapSignal": "BUY",
        "optionSignal": "BUY",
    }
    value.update(overrides)
    return value


def test_bullish_matching_signals_and_pcr_are_eligible():
    result = evaluate_signal(row())
    assert result.eligible is True
    assert result.direction == "BUY"
    assert result.reason == "ELIGIBLE"


def test_bearish_matching_signals_and_pcr_are_eligible():
    result = evaluate_signal(row(call=20, put=80, pcr=0.8, vWapSignal="SELL", optionSignal="SELL"))
    assert result.eligible is True
    assert result.direction == "SELL"


def test_disagreeing_signals_fail_closed():
    result = evaluate_signal(row(optionSignal="SELL"))
    assert not result.eligible
    assert result.reason == "SIGNALS_DISAGREE"


def test_strength_threshold_is_strict():
    # 55/45 yields exactly 10 percentage points; must not pass a threshold of 10.
    result = evaluate_signal(row(call=55, put=45))
    assert not result.eligible
    assert result.reason == "STRENGTH_DIFFERENCE_NOT_ABOVE_THRESHOLD"


def test_vwap_buffer_is_strict():
    # With price 1000, VWAP 997, the absolute gap is exactly 0.3%.
    result = evaluate_signal(row(price=1000, vwap=997))
    assert not result.eligible
    assert result.reason == "VWAP_GAP_NOT_BELOW_THRESHOLD"


def test_pcr_threshold_is_strict():
    result = evaluate_signal(row(pcr=1.0))
    assert not result.eligible
    assert result.reason == "PCR_DIRECTION_CONDITION_FAILED"


def test_missing_or_invalid_inputs_fail_closed():
    for overrides in (
        {"call": None},
        {"put": -1},
        {"call": 0, "put": 0},
        {"price": 0},
        {"vwap": None},
        {"pcr": None},
        {"vWapSignal": "WAIT"},
    ):
        result = evaluate_signal(row(**overrides))
        assert not result.eligible, overrides


def test_nested_autotrend_values_and_formatted_numbers_are_supported():
    result = evaluate_signal(row(
        call={"value": "1,000"},
        put={"value": "200"},
        price={"value": "1,49,640"},
        vwap={"value": "149,500"},
        pcr={"value": "1.4"},
        vWapSignal={"type": "BUY"},
        optionSignal={"type": "BUY"},
    ))
    assert not result.eligible  # price/VWAP gap is too large, even though fields parse
    assert result.reason == "VWAP_GAP_NOT_BELOW_THRESHOLD"
