"""Tests for the Frank Energie exceptions."""

import pytest

from custom_components.frank_energie.exceptions import (
    RequestException,
    SmartTradingNotEnabledException,
)


def test_request_exception():
    """Test the RequestException."""
    assert issubclass(RequestException, Exception)

    with pytest.raises(RequestException, match="Test error message"):
        raise RequestException("Test error message")


def test_smart_trading_not_enabled_exception():
    """Test the SmartTradingNotEnabledException."""
    assert issubclass(SmartTradingNotEnabledException, Exception)

    with pytest.raises(SmartTradingNotEnabledException, match="Not enabled"):
        raise SmartTradingNotEnabledException("Not enabled")
