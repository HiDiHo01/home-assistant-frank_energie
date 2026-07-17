"""Exceptions for the Frank Energie API."""


class RequestException(Exception):
    """Custom exception for request errors."""


class SmartTradingNotEnabledException(Exception):
    """Exception raised when smart trading is not enabled for the user."""


class NoSuitableSitesFoundError(Exception):
    """Exception raised when no suitable delivery sites are found for an account."""


class EncryptionError(Exception):
    """Exception raised when password encryption or decryption fails."""
