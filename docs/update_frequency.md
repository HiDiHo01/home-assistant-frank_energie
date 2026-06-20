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

## Gas Prices

Gas prices are updated when new gas price information becomes available from the API.

## Tomorrow's Prices

Tomorrow's electricity prices are normally published by the market during the afternoon.

Important notes:

- Publication time is not guaranteed.
- Prices may become available later than expected.
- Availability depends on upstream market data.
- The integration automatically detects when tomorrow's prices become available.

When tomorrow's prices are detected, the integration fires the `tomorrow_prices_available` event.

See:

- [Events](events.md)

## Cache Behaviour

The integration maintains cached price data.

This means:

- Existing prices remain available even if the API temporarily stops providing data.
- Temporary API interruptions do not immediately remove historical price data.
- Cached prices are used to maintain sensor continuity.

## Daily API Gap

A short period exists each day where the Frank Energie API may not return price data.

This is expected behaviour.

During this period:

- The API may temporarily return no price data.
- Cached prices remain available.
- Sensors may continue to show previously retrieved values.

The integration automatically recovers when new data becomes available.

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

## Resolution Changes

Frank Energie may temporarily expose different market resolutions while new market data is being processed.

Examples:

- Configuration requests `PT15M`
- API temporarily returns `PT60M`

This behaviour is normal and temporary.

The integration automatically adapts when the preferred resolution becomes available again.

## Coordinator Updates

The integration periodically refreshes data in the background.

Refresh intervals may vary depending on:

- Data type
- API availability
- Authentication status
- Smart charging providers
- Smart battery providers

The integration automatically balances data freshness with API load.

## Troubleshooting

### Tomorrow's prices are unavailable

Possible causes:

- Prices have not yet been published.
- Market data is delayed.
- Upstream systems are temporarily unavailable.

### Only 24 prices are available

This indicates hourly pricing (`PT60M`) is currently active.

### 96 prices are available

This indicates quarter-hourly pricing (`PT15M`) is currently active.

### Resolution drift detected

Messages such as:

```text
Resolution drift detected (config=PT15M api=PT60M)
```

are expected during temporary API transitions and usually resolve automatically.

## Notes

- Update frequencies may change as Frank Energie expands API capabilities.
- Publication times are controlled by upstream market operators.
- Temporary data delays do not necessarily indicate a problem with the integration.