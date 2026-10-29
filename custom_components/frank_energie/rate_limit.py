"""Keep server-requested cooldowns scoped to an entry and API resource."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from python_frank_energie import FrankEnergie
from python_frank_energie.exceptions import RateLimitException

from .const import DOMAIN

_RATE_LIMIT_STATES = "rate_limit_states"


@dataclass(frozen=True, slots=True)
class _Cooldown:
    """A server-requested deadline with its original operation name."""

    deadline: float
    operation_name: str


class RateLimitState:
    """Guard future calls without sleeping, replaying requests, or parsing headers."""

    def __init__(self) -> None:
        """Initialize resource cooldowns for one config entry or login flow."""
        self._cooldowns: dict[str, _Cooldown] = {}

    def _check(self, resource: str) -> None:
        """Reject a call while the provider's deadline is still in the future."""
        if (cooldown := self._cooldowns.get(resource)) is None:
            return
        remaining = cooldown.deadline - monotonic()
        if remaining > 0:
            raise RateLimitException(cooldown.operation_name, retry_after=remaining)
        del self._cooldowns[resource]

    async def async_call[T](
        self,
        resource: str,
        request: Callable[[], Awaitable[T]],
        *,
        authenticated: bool = True,
    ) -> T:
        """Call a resource unless it or shared authentication is cooling down.

        Resource names are public client method names, not inferred GraphQL
        paths. A renewal error may escape from any authenticated client call.
        No delay is invented when the provider supplies no retry hint.
        """
        self._check(resource)
        if authenticated:
            self._check("renew_token")
            self._check("login")
        try:
            return await request()
        except RateLimitException as err:
            key = (
                "renew_token"
                if err.operation_name == FrankEnergie.RENEW_TOKEN_OPERATIONNAME
                else resource
            )
            if err.retry_after is not None:
                deadline = monotonic() + err.retry_after
                previous = self._cooldowns.get(key)
                # Concurrent responses must not shorten an existing cooldown.
                if previous is None or deadline > previous.deadline:
                    self._cooldowns[key] = _Cooldown(deadline, err.operation_name)
            raise


@dataclass(kw_only=True)
class FrankEnergieSetupData:
    """Retain cooldowns across temporary setup failures and entry reloads."""

    rate_limits: RateLimitState = field(default_factory=RateLimitState)


def get_rate_limit_state(
    hass: HomeAssistant,
    entry: ConfigEntry[FrankEnergieSetupData],
) -> RateLimitState:
    """Return the same cooldown state to every coordinator for this entry."""
    # Core deletes runtime_data on unload. Keep only cooldowns here so a reload
    # cannot bypass a provider deadline; coordinators still live in runtime_data.
    states: dict[str, RateLimitState] = hass.data.setdefault(DOMAIN, {}).setdefault(
        _RATE_LIMIT_STATES, {}
    )
    if not isinstance(getattr(entry, "runtime_data", None), FrankEnergieSetupData):
        state = states.get(entry.entry_id)
        if not isinstance(state, RateLimitState):
            state = RateLimitState()
        entry.runtime_data = FrankEnergieSetupData(rate_limits=state)
    states[entry.entry_id] = entry.runtime_data.rate_limits
    return entry.runtime_data.rate_limits


def remove_rate_limit_state(hass: HomeAssistant, entry_id: str) -> None:
    """Forget cooldowns when their config entry is permanently removed."""
    states = hass.data.get(DOMAIN, {}).get(_RATE_LIMIT_STATES, {})
    states.pop(entry_id, None)


def rate_limit_update_failed(
    err: RateLimitException, update_interval: timedelta | None
) -> UpdateFailed:
    """Schedule recovery no earlier than the server delay or normal polling.

    An absent hint leaves Home Assistant's normal schedule intact. In
    particular, Retry-After: 0 must not create a tight automatic retry loop.
    """
    retry_after = err.retry_after
    if retry_after is not None and update_interval is not None:
        retry_after = max(retry_after, update_interval.total_seconds())
    return UpdateFailed(str(err), retry_after=retry_after)
