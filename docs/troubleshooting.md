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
