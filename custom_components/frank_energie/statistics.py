"""Statistical price calculations for Frank Energie integration."""

# statistics.py
# version 2026.05.31
from __future__ import annotations

from typing import TYPE_CHECKING

from python_frank_energie.models import Price

from .const import DATA_ELECTRICITY

if TYPE_CHECKING:
    from .models import FrankEnergieData


def _lowest_window(
    data: FrankEnergieData,
    window: int,
) -> tuple[float, Price, Price] | None:
    """Return the lowest average price window."""
    electricity_data = data.get(DATA_ELECTRICITY)

    if electricity_data is None or not electricity_data.today:
        return None

    prices = electricity_data.today

    if len(prices) < window:
        return None

    current_sum = sum(
        price.total for price in prices[:window]
    )

    lowest_sum = current_sum
    lowest_index = 0

    for index in range(window, len(prices)):
        current_sum += prices[index].total
        current_sum -= prices[index - window].total

        if current_sum < lowest_sum:
            lowest_sum = current_sum
            lowest_index = index - window + 1

    start_price = prices[lowest_index]
    end_price = prices[lowest_index + window - 1]

    return (
        round(lowest_sum / window, 4),
        start_price,
        end_price,
    )


def lowest_window(
    data: FrankEnergieData,
    window: int,
) -> tuple[float, Price, Price] | None:
    """
    Calculate the lowest average price window.

    Returns:
        tuple containing:
            - average price (rounded float)
            - start Price object
            - end Price object
        or None if insufficient data
    """
    electricity = data.get("electricity")

    if electricity is None or not electricity.today:
        return None

    prices: list[Price] = electricity.today

    if len(prices) < window:
        return None

    lowest_average: float | None = None
    lowest_start: Price | None = None
    lowest_end: Price | None = None

    for start_index in range(len(prices) - window + 1):
        window_prices = prices[start_index: start_index + window]

        average_price = sum(p.total for p in window_prices) / window

        if lowest_average is None or average_price < lowest_average:
            lowest_average = average_price
            lowest_start = window_prices[0]
            lowest_end = window_prices[-1]

    if lowest_average is None or lowest_start is None or lowest_end is None:
        return None

    return (
        round(lowest_average, 4),
        lowest_start,
        lowest_end,
    )


def lowest_window_optimized(
    data: FrankEnergieData,
    window: int,
) -> tuple[float, Price, Price] | None:
    """
    Optimized O(n) version of lowest window calculation.
    """
    electricity = data.get("electricity")

    if electricity is None or not electricity.today:
        return None

    prices: list[Price] = electricity.today

    if len(prices) < window:
        return None

    current_sum = sum(p.total for p in prices[:window])
    lowest_sum = current_sum
    lowest_index = 0

    for index in range(window, len(prices)):
        current_sum += prices[index].total
        current_sum -= prices[index - window].total

        if current_sum < lowest_sum:
            lowest_sum = current_sum
            lowest_index = index - window + 1

    start_price = prices[lowest_index]
    end_price = prices[lowest_index + window - 1]

    return (
        round(lowest_sum / window, 4),
        start_price,
        end_price,
    )
