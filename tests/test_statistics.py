"""Tests for statistics.py's lowest_window, used by the live lowest-4h/16h price sensors."""

from types import SimpleNamespace

from custom_components.frank_energie.statistics import lowest_window


def _price(total: float) -> SimpleNamespace:
    """Minimal stand-in for a Price object — lowest_window only reads .total
    and returns the objects themselves untouched, so identity comparison in
    assertions works without needing a real Price/date_from/date_till.
    """
    return SimpleNamespace(total=total)


def _data(prices: list[SimpleNamespace]) -> dict[str, SimpleNamespace]:
    return {"electricity": SimpleNamespace(today=prices)}


def test_returns_none_when_electricity_missing() -> None:
    assert lowest_window({"electricity": None}, 4) is None
    assert lowest_window({}, 4) is None


def test_returns_none_when_today_is_empty() -> None:
    assert lowest_window(_data([]), 4) is None


def test_returns_none_when_fewer_prices_than_window() -> None:
    prices = [_price(1.0), _price(2.0), _price(3.0)]
    assert lowest_window(_data(prices), 4) is None


def test_finds_the_genuinely_lowest_window_not_first_or_last() -> None:
    # window=2: candidate averages are (5+1)/2=3, (1+4)/2=2.5, (4+9)/2=6.5,
    # (9+2)/2=5.5 — the lowest is prices[1:3], neither the first nor last.
    prices = [_price(5.0), _price(1.0), _price(4.0), _price(9.0), _price(2.0)]

    result = lowest_window(_data(prices), 2)

    assert result is not None
    average, start, end = result
    assert average == 2.5
    assert start is prices[1]
    assert end is prices[2]


def test_ties_keep_the_first_occurrence() -> None:
    """Regression-style guard for the `<` (not `<=`) comparison: on a tie,
    the first-encountered window must win, not the last."""
    prices = [_price(2.0), _price(2.0), _price(2.0), _price(2.0)]

    result = lowest_window(_data(prices), 2)

    assert result is not None
    _, start, end = result
    assert start is prices[0]
    assert end is prices[1]


def test_window_covering_the_entire_series() -> None:
    prices = [_price(1.0), _price(2.0), _price(3.0)]

    result = lowest_window(_data(prices), 3)

    assert result is not None
    average, start, end = result
    assert average == 2.0
    assert start is prices[0]
    assert end is prices[2]


def test_window_of_one_returns_the_single_lowest_price() -> None:
    prices = [_price(5.0), _price(1.0), _price(3.0)]

    result = lowest_window(_data(prices), 1)

    assert result is not None
    average, start, end = result
    assert average == 1.0
    assert start is prices[1]
    assert end is prices[1]


def test_average_is_rounded_to_four_decimal_places() -> None:
    prices = [_price(1.0), _price(2.0), _price(1.0)]

    result = lowest_window(_data(prices), 3)

    assert result is not None
    average, _, _ = result
    assert average == round((1.0 + 2.0 + 1.0) / 3, 4)
