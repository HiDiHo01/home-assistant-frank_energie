# Troubleshooting

This page describes common issues and expected behavior of the Frank Energie integration.

## No Data Available

### Public price entities are unavailable

Possible causes:

- Frank Energie API maintenance
- Temporary API outage
- Internet connectivity issues
- Frank Energie backend issues

Check:

- Home Assistant logs
- Integration diagnostics
- Frank Energie service status

### Authenticated entities are unavailable

Possible causes:

- Authentication expired
- Invalid credentials
- Temporary Frank Energie account issues

The integration automatically attempts token renewal when possible.

If the problem persists:

1. Reload the integration.
2. Reconfigure the integration.
3. Verify your Frank Energie credentials.

## Tomorrow's Prices Are Missing

This is usually expected behavior.

Tomorrow's prices are published by upstream market operators and are not available immediately after midnight.

### Publication Window

The integration starts checking for tomorrow's prices around:

```text
11:00 UTC
```

Publication typically occurs between:

```text
11:00 UTC and 13:00 UTC
```

Publication time is controlled by upstream market systems and may vary.

### Verify Availability

Check:

- Tomorrow price sensors
- Frank Energie events
- Integration logs

When tomorrow's prices become available, the integration fires:

```text
Event: frank_energie_event
Action: tomorrow_prices_available
```

## No Updates Between 00:00 and 01:00 UTC

This is expected behavior.

Frank Energie performs a daily maintenance window during:

```text
00:00 UTC - 01:00 UTC
```

During this period:

- API calls are skipped
- Cached data is used
- Existing entities remain available when possible

Normal updates automatically resume after maintenance.

## Only 24 Electricity Prices Available

This indicates hourly market pricing:

```text
PT60M
```

Hourly pricing provides:

```text
24 prices per day
```

This may be temporary depending on market availability.

## 96 Electricity Prices Available

This indicates quarter-hourly market pricing:

```text
PT15M
```

Quarter-hourly pricing provides:

```text
96 prices per day
```

## Resolution Drift Detected

You may see messages similar to:

```text
Resolution drift detected (config=PT15M api=PT60M)
```

This is normal behavior.

It means:

- Your preferred resolution differs from the API response
- The market has not yet published the requested resolution
- Frank Energie is temporarily exposing a fallback resolution

The integration automatically switches back when the preferred resolution becomes available.

No user action is required.

## Resolution Change Has No Electricity Connection

Resolution changes target the electricity connection of the selected delivery
site. A warm price cache does not restore an electricity connection ID; the
coordinator synchronizes it from the selected site's account settings instead.

On startup, an available connection's resolution permissions are verified with
the API even when a fresh price cache avoids another price fetch. If the
connection is replaced or removed, the old permissions and resolution status
are cleared. Temporary missing account data preserves the last known
same-entry connection ID; failed permission checks do not allow a change.

If Home Assistant reports that the electricity connection is unavailable,
confirm that the selected Frank Energie site has an electricity contract and
that account settings have loaded. Retry after a temporary API failure.

## Removing Disconnected Hardware Safely

When a physical charger, vehicle, SmartBattery, Smart PV system, or Smart
Heating (HVAC) device disappears from Frank Energie's verified equipment
inventory, the integration requires **two distinct valid inventory refreshes**
that both confirm its absence before it offers a repair under
**Settings → System → Repairs** or permits manual device removal.

**No device is deleted automatically.** Confirming a Repair triggers a new
API inventory check and verifies the config entry and device identity again
before removing the device and its entities from Home Assistant.
Removal can affect existing automations and dashboards.

Missing or malformed inventories, API outages, disabled smart features and
maintenance-window skips must never be treated as proof of absence. A PV
system that was previously registered is still checked after an empty
inventory, even when new users without PV hardware use the usual API
backoff. Smart Heating devices remain registered and unavailable when
missing; the integration no longer removes them automatically during
inventory changes or after Smart Heating deactivation.
An ignored Repair remains ignored until its lifecycle changes; an
unrecognized or restored device must not be deleted.

The same double-absence rule also applies to Home Assistant's manual
device-removal action. The Delete action requests another successful, live
inventory check and refuses removal if the refresh fails or is skipped.
Wait for two successful updates before retrying when removal is rejected.
Removing the integration config entry is a separate operation and is unaffected.

**Minimum Home Assistant version for these Repairs is 2026.11.0.**
Install a compatible Core version before updating the integration.

## Smart Battery Entities Unavailable

Possible causes:

- No battery connected
- Battery onboarding incomplete
- Frank Energie battery services unavailable
- Provider synchronization issues

Check:

- Battery onboarding status
- Battery configuration entities
- Home Assistant logs

## Smart Charging Entities Unavailable

Possible causes:

- Charger not connected
- Vehicle not connected
- Enode synchronization issues
- Smart Charging not enabled on the account

Check:

- Smart Charging status entities
- Provider information entities
- Home Assistant logs

## Smart Feed-In Entities Unavailable

Possible causes:

- Feature not enabled
- Unsupported account
- Temporary backend issue

Verify:

- Smart Feed-In status entities
- Account eligibility

## Smart Trading Entities Unavailable

Possible causes:

- Feature not enabled
- Unsupported account
- Temporary backend issue

Verify:

- Smart Trading status entities
- Provider status entities

## Smart HVAC Entities Unavailable

Possible causes:

- Feature not enabled
- Heat pump not connected
- Unsupported account

Verify:

- Smart HVAC status entities
- Connected device status

## Manual Refresh Does Not Update Data

The integration provides refresh buttons for:

- Price data
- Battery sessions

Possible causes:

- API maintenance window
- Temporary API outage
- Authentication issues

Check the Home Assistant logs for coordinator update errors.

## Authentication Problems

### Reauthentication Required

If reauthentication is requested:

1. Open the integration configuration.
2. Select Reconfigure.
3. Enter updated credentials.

### Invalid Credentials

Verify:

- Email address
- Password
- Frank Energie account access

## Diagnostics

When reporting an issue:

1. Download integration diagnostics.
2. Enable debug logging.
3. Include relevant log entries.
4. Describe expected and actual behavior.

Diagnostics automatically redact sensitive information where appropriate.

## Known Behaviors

The following behaviors are expected:

- Missing tomorrow prices before publication.
- No updates during the daily maintenance window.
- Temporary resolution drift.
- Dynamic polling intervals.
- Different entities available for different accounts.
- Smart feature availability varying by account.

These situations do not necessarily indicate a problem with the integration.

## Still Need Help?

If the issue persists:

1. Enable debug logging.
2. Collect diagnostics.
3. Open an issue on GitHub.
4. Include logs, diagnostics, and reproduction steps.
