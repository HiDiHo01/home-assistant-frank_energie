"""Persist the pinned API client's automatic JWT renewal without extra calls."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN

_LOGGER = logging.getLogger(__name__)


def attach_token_persistence(hass: Any, entry: Any, api: Any) -> None:
    """Keep restart credentials in sync with transparent upstream renewals.

    The vendor _query() renews expired tokens internally, bypassing the
    coordinator's explicit _try_renew_token() path. Wrap the single shared
    client's renewal once; never log credentials or alter the API result.
    """
    if getattr(api, "_frank_token_persistence_attached", False):
        return
    original_renew = api.renew_token

    async def renew_and_persist(*args: Any, **kwargs: Any) -> Any:
        auth = await original_renew(*args, **kwargs)
        access = getattr(auth, "authToken", None)
        refresh = getattr(auth, "refreshToken", None)
        if not isinstance(access, str) or not access:
            return auth
        if not isinstance(refresh, str) or not refresh:
            return auth
        if (
            entry.data.get(CONF_ACCESS_TOKEN) == access
            and entry.data.get(CONF_TOKEN) == refresh
        ):
            return auth
        try:
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_ACCESS_TOKEN: access,
                    CONF_TOKEN: refresh,
                },
            )
        except Exception:
            # Persistence failure must not corrupt an already-renewed
            # in-memory API client. The next renewal can try again.
            _LOGGER.exception("Failed to persist renewed Frank Energie credentials")
        return auth

    api.renew_token = renew_and_persist
    api._frank_token_persistence_attached = True
