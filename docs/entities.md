# Entities

The Frank Energie integration creates Home Assistant entities dynamically based on the data available for your account and enabled features.

The exact entities that are created depend on:

- Whether you are logged in to your Frank Energie account
- Available Frank Energie API features
- Connected smart charging devices
- Connected smart batteries
- Active energy contracts
- Market price resolution (hourly or quarter-hourly)

## Entity Platforms

The integration may create entities from the following Home Assistant platforms.

| Platform | Description |
|-----------|-------------|
| Sensor | Electricity prices, gas prices, costs, contracts, invoices, smart charging and battery information |
| Binary Sensor | Status indicators, feature availability and configuration states |
| Select | Smart charging and smart battery configuration options |
| Number | Configurable charging and battery settings |
| Button | Actions such as refreshing data or synchronizing connected services |

## Public Entities

These entities are available without logging in.

### Electricity Prices

Electricity price entities provide information such as:

- Current electricity price
- Average electricity price
- Lowest electricity price
- Highest electricity price
- Today's prices
- Tomorrow's prices
- Cheapest consecutive price periods
- Price statistics

Depending on the market configuration, electricity prices may be available as:

- Hourly prices (24 prices per day)
- Quarter-hourly prices (96 prices per day)

### Tomorrow Price Availability

The **Tomorrow electricity prices available** binary sensor (key
`tomorrow_prices_available`) is enabled by default and works with or without a
Frank Energie login. It belongs to the existing **Prices** service device.

- `on`: at least one electricity price period for tomorrow is present.
- `off`: electricity data is present, but contains no periods for tomorrow yet.
- `unknown`: electricity data is missing.
- `unavailable`: the price coordinator's last update failed.

Tomorrow follows the Europe/Amsterdam calendar, including daylight-saving changes.
Zero and negative prices count as available. Gas prices alone do not turn this
electricity indicator on. A partial publication can turn it on; it does not
guarantee a complete day of prices.

The sensor follows the existing price coordinator and cached prices without extra
API requests or a separate publication-hour cutoff. It resets at local midnight
unless prices for the new tomorrow are already present. Use an `off` to `on` state
trigger for notifications when tomorrow's electricity prices arrive.

### Gas Prices

Gas price entities provide:

- Current gas price
- Today's gas price
- Tomorrow's gas price (when available)
- Gas price statistics

## Authenticated Entities

After signing in with a Frank Energie account, additional customer-specific entities become available.

### Contract Entities

Contract entities may include:

- Contract information
- Contract status
- Contract type
- Delivery address
- Site information

### Consumption Entities

Consumption entities may include:

- Electricity consumption
- Gas consumption
- Monthly usage
- Yearly usage
- Forecast usage

### Cost Entities

Cost entities may include:

- Current month costs
- Previous month costs
- Current year costs
- Previous year costs
- Forecast costs

### Invoice Entities

Invoice entities may include:

- Invoice totals
- Billing periods
- Historical invoices
- Annual summaries

## Smart Charging Entities

When smart charging is enabled and a supported charger is connected, additional entities become available.

## Smart Battery Entities

When a supported battery is connected, additional entities become available.

## Dynamic Entity Creation

The Frank Energie integration dynamically creates entities based on the capabilities available for your account.

## Entity Availability

Entities may temporarily become unavailable when:

- Frank Energie API maintenance is in progress
- Authentication expires
- Tomorrow's prices have not yet been published
- Smart charging providers are unavailable
- Smart battery providers are unavailable
- Connected devices are temporarily offline

## Events

The integration also fires Home Assistant events which can be used in automations.

See:

- [Events](events.md)
