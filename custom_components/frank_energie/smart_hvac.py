"""Poll Smart Heating data and interpret the selected site's feature flags."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from time import monotonic
from typing import Final, TypedDict

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import (
    AuthRequiredException,
    FrankEnergieException,
    RateLimitException,
    RefreshTokenInvalidException,
)
from python_frank_energie.models import SmartHvacDevice, SmartHvacSummary

from .const import DATA_USER
from .coordinator import FrankEnergieData
from .helpers import api_maintenance_active

_LOGGER = logging.getLogger(__name__)
_UPDATE_INTERVAL: Final = timedelta(minutes=15)
_REQUEST_TIMEOUT: Final = 10


class SmartHvacRequestStatus(TypedDict):
    """Connection request details safe to show as local entity attributes."""

    brand: str | None
    status: str | None
    has_reported_error: bool


@dataclass(frozen=True, slots=True)
class SmartHvacData:
    """Store independent Smart Heating results for one config entry.

    ``None`` represents a null or unknown inventory/request collection.
    The fetched flags distinguish a successful null response from a skipped
    or failed read. Empty collections confirm zero records. Successful
    summary reads (including null values) and skipped reads with prior
    values are stored in ``summaries``. ``stale_summaries`` identifies
    retained values that must not be exposed as fresh.
    """

    devices: dict[str, SmartHvacDevice] | None = None
    summaries: dict[str, SmartHvacSummary | None] = field(default_factory=dict)
    requests: tuple[SmartHvacRequestStatus, ...] | None = None
    devices_fetched: bool = False
    requests_fetched: bool = False
    stale_summaries: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _ReadResult[T]:
    """Distinguish successful, skipped, and failed reads, including null results."""

    value: T | None = None
    fetched: bool = False
    skipped: bool = False


class FrankEnergieSmartHvacCoordinator(DataUpdateCoordinator[SmartHvacData]):
    """Poll read-only HVAC endpoints for a single authenticated config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: FrankEnergie,
        site_reference: str,
    ) -> None:
        """Bind HVAC polling to the config entry and its selected delivery site."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Frank Energie Smart Heating",
            update_interval=_UPDATE_INTERVAL,
            always_update=False,
        )
        self.data = SmartHvacData()
        self.site_reference = site_reference
        self._api = api
        self._retry_at = 0.0

    def _requests_paused(self) -> bool:
        """Return whether maintenance or provider backoff blocks requests."""
        return api_maintenance_active(datetime.now(UTC)) or monotonic() < self._retry_at

    async def _async_read[T](
        self, operation: str, method: Callable[..., Awaitable[T]], *args: str
    ) -> _ReadResult[T]:
        """Read one HVAC resource within the request timeout.

        Distinguish skipped requests from attempted failures and defer
        further requests after rate limiting. Raise ``ConfigEntryAuthFailed``
        when Home Assistant needs to start reauthentication.
        """
        if self._requests_paused():
            return _ReadResult(skipped=True)
        try:
            async with asyncio.timeout(_REQUEST_TIMEOUT):
                return _ReadResult(await method(*args), fetched=True)
        except (AuthRequiredException, RefreshTokenInvalidException):
            raise ConfigEntryAuthFailed(
                "Frank Energie authentication is required"
            ) from None
        except RateLimitException as err:
            self._retry_at = monotonic() + max(
                _UPDATE_INTERVAL.total_seconds(), err.retry_after or 0
            )
            _LOGGER.debug("Smart Heating rate limited while fetching %s", operation)
        except (FrankEnergieException, ClientError, TimeoutError, JSONDecodeError):
            _LOGGER.debug("Smart Heating data unavailable for %s", operation)
        return _ReadResult()

    async def _async_update_data(self) -> SmartHvacData:
        """Refresh inventory and account requests, then read summaries in sequence.

        Keep the previous snapshot when maintenance or backoff pauses the
        refresh before any reads start.
        """
        if not self._api.is_authenticated:
            return SmartHvacData()
        if self._requests_paused():
            return self.data

        inventory = await self._async_read(
            "inventory", self._api.smart_hvacs, self.site_reference
        )
        requests = await self._async_read(
            "connection requests", self._api.smart_hvac_connect_requests
        )
        devices = None
        if inventory.value is not None:
            devices = {
                device.id: replace(device, connection_ean=None, external_reference=None)
                for device in inventory.value
                if device.deleted_at is None
            }
        summaries: dict[str, SmartHvacSummary | None] = {}
        stale_summaries: set[str] = set()
        for device_id in devices or {}:
            summary = await self._async_read(
                "device summary", self._api.smart_hvac_summary, device_id
            )
            if summary.fetched:
                summaries[device_id] = summary.value
            elif summary.skipped and device_id in self.data.summaries:
                summaries[device_id] = self.data.summaries[device_id]
                stale_summaries.add(device_id)
        safe_requests: tuple[SmartHvacRequestStatus, ...] | None = None
        if requests.value is not None:
            safe_requests = tuple(
                SmartHvacRequestStatus(
                    brand=request.brand,
                    status=request.status,
                    has_reported_error=bool(request.error),
                )
                for request in {item.id: item for item in requests.value}.values()
            )
        return SmartHvacData(
            devices=devices,
            summaries=summaries,
            requests=safe_requests,
            devices_fetched=inventory.fetched,
            requests_fetched=requests.fetched,
            stale_summaries=frozenset(stale_summaries),
        )


def smart_hvac_activation(data: FrankEnergieData) -> bool | None:
    """Return the Boolean service activation flag, or ``None`` if unknown."""
    feature = getattr(data.get(DATA_USER), "smartHvac", None)
    activated = (
        feature.get("isActivated")
        if isinstance(feature, dict)
        else getattr(feature, "isActivated", None)
    )
    return activated if isinstance(activated, bool) else None


def smart_hvac_site_attributes(data: FrankEnergieData) -> dict[str, object]:
    """Return the available Boolean site flags for entity attributes."""
    feature = getattr(data.get(DATA_USER), "smartHvac", None)
    if isinstance(feature, dict):
        flags = {
            "available_in_country": feature.get("isAvailableInCountry"),
            "available_on_site": feature.get("isAvailableOnSite"),
            "has_accepted_terms": feature.get("hasAcceptedTerms"),
        }
    else:
        flags = {
            "available_in_country": getattr(feature, "isAvailableInCountry", None),
            "available_on_site": getattr(feature, "is_available_on_site", None),
            "has_accepted_terms": getattr(feature, "has_accepted_terms", None),
        }
    return {key: value for key, value in flags.items() if isinstance(value, bool)}
