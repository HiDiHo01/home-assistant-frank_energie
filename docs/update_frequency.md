# Update Frequency

This page describes how and when the Frank Energie integration retrieves and updates data.

## Electricity Prices

Electricity prices are retrieved from the Frank Energie API and updated automatically throughout the day.

The integration supports both:

- Hourly prices (`PT60M`)
- Quarter-hourly prices (`PT15M`)

Depending on the active market resolution, electricity price sensors may contain:

| Resolution | Prices per day |
|------------|---------------|
| PT60M | 24 |
| PT15M | 96 |

## API Maintenance Window

The Frank Energie API has a daily maintenance window between **00:00 and 01:00 UTC**.

During this period:

- API calls are intentionally skipped.
- The integration uses cached data when available.
- Existing sensors continue to use cached values.
- If no cached data exists, entities may temporarily become unavailable.

The integration automatically resumes normal operation after 01:00 UTC.

## Tomorrow's Prices

The integration starts checking for tomorrow's prices at approximately **11:00 UTC**.

A dedicated publication window is used:

| Window | Time (UTC) |
|----------|-----------|
| Publication start | 11:00 UTC |
| Publication end | 13:00 UTC |

Important notes:

- Publication time is controlled by upstream market operators.
- Tomorrow's prices may appear at any point within or after the publication window.
- The integration automatically retries when prices are not yet available.
- Cached prices remain available while waiting.

When tomorrow's prices become available, the integration fires the following Home Assistant event:

```text
Event: frank_energie_event
Action: tomorrow_prices_available
```

See:

- [Events](events.md)

## Cache Behaviour

The integration maintains local caches for price and customer data.

This means:

- Existing prices remain available during temporary API interruptions.
- Cached data is used during the maintenance window.
- Historical prices are not immediately removed when the API temporarily returns no data.
- Sensor continuity is preserved whenever possible.

## Dynamic Update Intervals

The coordinator dynamically adjusts its refresh interval throughout the day.

Polling becomes more aggressive during important periods such as:

- Tomorrow price publication windows.
- Resolution transitions.
- Smart charging updates.
- Smart battery updates.

Outside these periods, the integration reduces API load by using less frequent updates.

## Authenticated Data

Customer-specific data requires authentication.

Examples include:

- Contract information
- Consumption data
- Cost calculations
- Invoice information
- Smart charging data
- Smart battery data

If authentication expires:

- Authenticated entities may become unavailable.
- Public price entities continue to operate.
- The integration automatically attempts token renewal.
- If required, the integration can re-authenticate using stored credentials.

## Resolution Changes

Frank Energie may temporarily expose different market resolutions while market data is being processed.

Examples:

- Configuration requests `PT15M`
- API temporarily returns `PT60M`

This behaviour is normal and temporary.

The integration automatically reconciles resolution differences and switches back when the preferred resolution becomes available.

Messages such as:

```text
Resolution drift detected (config=PT15M api=PT60M)
```

usually indicate a temporary upstream API state.

## Event-Driven Updates

The integration can fire Home Assistant events when important milestones occur.

Examples include:

- `tomorrow_prices_available`
- `lowest_price`
- `lowest_4p_price`
- `lowest_16p_price`

All events are emitted using:

```text
frank_energie_event
```

See:

- [Events](events.md)

## Troubleshooting

### Tomorrow's prices are unavailable

Possible causes:

- Prices have not yet been published.
- Publication is delayed by the market.
- The API is temporarily unavailable.

### Only 24 prices are available

Hourly pricing (`PT60M`) is currently active.

### 96 prices are available

Quarter-hourly pricing (`PT15M`) is currently active.

### No new data between 00:00 and 01:00 UTC

This is expected behaviour during the daily API maintenance window.

### Resolution drift detected

Temporary resolution drift is expected and normally resolves automatically without user intervention.

## Notes

- Publication times are determined by upstream market operators.
- Update frequencies may evolve as Frank Energie expands API capabilities.
- Temporary data delays do not necessarily indicate a problem with the integration.