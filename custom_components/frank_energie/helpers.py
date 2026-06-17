"""Helper functions for the Frank Energie integration."""
# helpers.py
# version: 2026.6.16

from __future__ import annotations

import base64
import hashlib
import logging
from typing import TYPE_CHECKING, Final

from cryptography.fernet import Fernet, InvalidToken
from homeassistant.core import HomeAssistant

from .const import UNIT_GAS_BE, UNIT_GAS_NL

if TYPE_CHECKING:
    from python_frank_energie.models import ChargeSettings

_LOGGER = logging.getLogger(__name__)


PER_UNIT_TO_UNIT: Final[dict[str, str]] = {
    "M3": UNIT_GAS_NL,
    "KWH": UNIT_GAS_BE,
}


def resolve_gas_unit(per_unit: str | None) -> str:
    """Return the Home Assistant gas unit for an API perUnit value."""

    if per_unit is None:
        return UNIT_GAS_NL

    unit = PER_UNIT_TO_UNIT.get(per_unit.upper())

    if unit is None:
        _LOGGER.warning(
            "Unsupported gas perUnit value received from API: %s",
            per_unit,
        )
        return UNIT_GAS_NL

    return unit


def build_charge_settings_input(
    settings: ChargeSettings,
) -> dict[str, object]:
    """Build the full charge settings dict required by the mutation.

    All 13 fields must be present — the API does not support partial updates.
    """
    return {
        "id": settings.id,
        "deadline": settings.deadline.isoformat() if settings.deadline else None,
        "isSmartChargingEnabled": settings.is_smart_charging_enabled,
        "isSolarChargingEnabled": settings.is_solar_charging_enabled,
        "minChargeLimit": settings.min_charge_limit,
        "maxChargeLimit": settings.max_charge_limit,
        "hourMonday": settings.hour_monday,
        "hourTuesday": settings.hour_tuesday,
        "hourWednesday": settings.hour_wednesday,
        "hourThursday": settings.hour_thursday,
        "hourFriday": settings.hour_friday,
        "hourSaturday": settings.hour_saturday,
        "hourSunday": settings.hour_sunday,
    }


def _get_fernet_key(hass: HomeAssistant) -> bytes:
    """Derive a Fernet key from the Home Assistant instance UUID."""
    instance_uuid = hass.data.get("core.uuid", "default_key_fallback_frank_energie")
    key_material = hashlib.sha256(instance_uuid.encode()).digest()
    return base64.urlsafe_b64encode(key_material)


def encrypt_password(hass: HomeAssistant, password: str) -> str:
    """Encrypt password using Fernet."""
    if not password:
        return ""
    try:
        f = Fernet(_get_fernet_key(hass))
        return f.encrypt(password.encode()).decode()
    except Exception as ex:
        _LOGGER.error("Failed to encrypt password: %s", ex)
        return password


def decrypt_password(hass: HomeAssistant, password: str) -> str:
    """Decrypt password using Fernet with plaintext fallback."""
    if not password:
        return ""
    if password.startswith("gAAAA"):
        try:
            f = Fernet(_get_fernet_key(hass))
            return f.decrypt(password.encode()).decode()
        except (InvalidToken, Exception) as ex:
            _LOGGER.warning("Failed to decrypt stored password: %s", ex)
            return ""
    return password
