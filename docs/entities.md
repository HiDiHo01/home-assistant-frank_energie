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

The **Electricity price is lowest today** binary sensor is enabled by default
under the electricity prices device, with or without authentication. It is `on`
when the current all-in electricity price equals the lowest all-in price for the
current calendar day in `Europe/Amsterdam`, and `off` during other price periods.
Every period tied for the minimum switches it on, including zero or negative
prices. It follows the available hourly or quarter-hourly intervals without
rounding prices or making additional API requests.

Today's intervals must cover the whole local day without gaps or overlaps.
Changing between hourly and quarter-hourly prices tomorrow does not affect
today's comparison. The state is `unknown` if today's coverage is incomplete or
invalid, the active interval is missing, or any of today's prices is non-finite.
A failed price coordinator makes the entity `unavailable`.

Depending on the market configuration, electricity prices may be available as:

- Hourly prices (24 prices per day)
- Quarter-hourly prices (96 prices per day)

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

The **Segments** sensor (NL/NL-BE **Segmenten**) shows the energy types for the
configured delivery site. Its state is `ELECTRICITY`, `GAS`, or
`ELECTRICITY, GAS`. It is unavailable when the API has no usable segments for
that site.

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

## Smart heating entity

The authenticated User-service binary sensor **Smart heating** (NL/NL-BE:
**Slim verwarmen**) retains the `smart_hvac` entity key. Its Boolean
`isActivated` value reflects **service activation**, not whether a
heat pump is currently heating.

| Provider result | Binary sensor | Meaning |
| --- | --- | --- |
| `isActivated = true` | `on` | Service activated |
| `isActivated = false` | `off` | Service not activated |
| Missing or invalid activation value | `unavailable` | Unknown activation |

The `RUNNING` device class is used only for localized active/inactive
presentation. The `available_on_site` attribute shows whether the
provider makes this feature available for the configured delivery site:
it is not the entity's Home Assistant availability or the activation state.

See [Smart heating activation](smart_hvac_activation.md) for details and
the dependency-release prerequisite.

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
