# Examples

This page contains practical Home Assistant automation examples for the Frank Energie integration.

For entity-based examples, replace placeholder entity IDs with the actual entities created in your installation.

See:

- [Entities](entities.md)

for information about available entities and features.

## Calculate the costs

```yaml
template:
  - sensor:
      - name: "Elektriciteitskosten vorig kwartier"
        unique_id: elektriciteitskosten_vorig_kwartier
        device_class: monetary
        unit_of_measurement: "€"
        icon: mdi:currency-eur
        availability: >
          {{ states('sensor.frank_energie_stroomprijzen_elektriciteitsprijs_vorig_kwartier_all_in') | is_number
             and state_attr('sensor.kwartier_energieverbruik', 'last_period') | is_number }}
        state: >
          {% set price = states('sensor.frank_energie_stroomprijzen_elektriciteitsprijs_vorig_kwartier_all_in') | float %}
          {% set usage = state_attr('sensor.kwartier_energieverbruik', 'last_period') | float %}
          {{ price * usage }}
```
Do not use state_class
If you add this sensor using the user interfase(UI) add the sensor to Frank Energie > Kosten

## Calculate the costs per period, in this case per hour

```yaml
utility_meter:
  hourly_costs:
    source: sensor.frank_energie_kosten_elektriciteitskosten_vorig_kwartier
    name: Energiekosten vorig uur
    unique_id: uur_energiekosten
    cycle: hourly
    delta_values: true
    net_consumption: true
```
Change cycle to daily, weekly, monthly or yearly for more statistics

## Tomorrow Prices Available Notification

Receive a notification when tomorrow's prices become available.

```yaml
alias: Frank Energie - Tomorrow Prices Available
triggers:
  - trigger: event
    event_type: frank_energie_event
    event_data:
      action: tomorrow_prices_available
actions:
  - action: notify.mobile_app_phone
    data:
      message: Tomorrow's prices are available.
```

## Lowest Price Notification

Notify when the cheapest electricity period starts.

```yaml
alias: Frank Energie - Lowest Price Started
triggers:
  - trigger: event
    event_type: frank_energie_event
    event_data:
      action: lowest_price
actions:
  - action: notify.mobile_app_phone
    data:
      message: Lowest electricity price period has started.
```

## Only Run at Today's Lowest Electricity Price

Use the **Electricity price is lowest today** binary sensor as a condition in an
automation. Replace the placeholder with the entity selected in Home Assistant.
The condition also matches later periods tied for today's minimum price.

```yaml
conditions:
  - condition: state
    entity_id: binary_sensor.<your_lowest_electricity_price_today_entity>
    state: "on"
```

## Cheapest 4-Period Window Notification

Notify when the cheapest 4-period electricity window starts.

```yaml
alias: Frank Energie - Lowest 4 Period Price Started
triggers:
  - trigger: event
    event_type: frank_energie_event
    event_data:
      action: lowest_4p_price
actions:
  - action: notify.mobile_app_phone
    data:
      message: Cheapest 4-period window has started.
```

## Cheapest 16-Period Window Notification

Notify when the cheapest 16-period electricity window starts.

```yaml
alias: Frank Energie - Lowest 16 Period Price Started
triggers:
  - trigger: event
    event_type: frank_energie_event
    event_data:
      action: lowest_16p_price
actions:
  - action: notify.mobile_app_phone
    data:
      message: Cheapest 16-period window has started.
```

## Refresh Price Data

The integration creates a refresh button entity.

Use the entity picker in Home Assistant to select the correct button entity.

```yaml
alias: Frank Energie - Refresh Prices
triggers:
  - trigger: time
    at: "11:05:00"
actions:
  - action: button.press
    target:
      entity_id: button.<your_refresh_prices_entity>
```

## Refresh Battery Sessions

Battery session entities are only available when Smart Battery functionality is enabled.

```yaml
alias: Frank Energie - Refresh Battery Sessions
triggers:
  - trigger: time
    at: "06:00:00"
actions:
  - action: button.press
    target:
      entity_id: button.<your_refresh_battery_sessions_entity>
```

## Battery Mode Change Notification

Smart Battery entity IDs depend on the connected battery and account configuration.

```yaml
alias: Frank Energie - Battery Mode Changed
triggers:
  - trigger: state
    entity_id: select.<your_battery_mode_entity>
actions:
  - action: notify.mobile_app_phone
    data:
      title: Battery Mode
      message: "Battery mode changed to {{ trigger.to_state.state }}"
```

## Smart Charging Availability Alert

Smart Charging entities are only available for supported accounts.

```yaml
alias: Frank Energie - Smart Charging Unavailable
triggers:
  - trigger: state
    entity_id: binary_sensor.<your_smart_charging_entity>
    to: "off"
actions:
  - action: notify.mobile_app_phone
    data:
      title: Smart Charging
      message: Smart Charging is currently unavailable.
```

## Negative Price Alert

Select an electricity price sensor from your installation.

```yaml
alias: Frank Energie - Negative Electricity Price
triggers:
  - trigger: numeric_state
    entity_id: sensor.<your_electricity_price_sensor>
    below: 0
actions:
  - action: notify.mobile_app_phone
    data:
      title: Negative Electricity Price
      message: Electricity prices are currently negative.
```

## Using your own sourcing markup input

Create a number input entity and get your custom price calculation.
(or use a fixed sourcing markup price like this example)

```
template:
  - sensor:
      - name: "Electricity price with my sourcing markup"
        unique_id: elec_price_with_my_markup
        device_class: monetary
        state_class: total
        unit_of_measurement: "EUR/kWh"
        state: >
          {% set markup = states('number.sourcing_markup') %} # or use a fixed number
          {% set markup = 0.02 %}
          {% set prices = state_attr('sensor.frank_energie_electricity_prices_current_electricity_market_price', 'prices') %}
          {% set nowdt = now() %}
          {% set current = prices | selectattr('from', 'le', nowdt) | selectattr('till', 'gt', nowdt) | list %}
          {{ (current[0].price + markup) | round(3) if current else 'unavailable' }}
        attributes:
          prices: >
            {% set markup = 0.02 %}
            {% set ns = namespace(result=[]) %}
            {% for p in state_attr('sensor.frank_energie_electricity_prices_current_electricity_market_price', 'prices') %}
              {% set ns.result = ns.result + [ dict(**{'from': p['from'].isoformat(), 'till': p.till.isoformat(), 'price': (p.price + markup) | round(3)}) ] %}
            {% endfor %}
            {{ ns.result }}
```

## Prices for a Date

Select **Get prices for a date** in an automation and choose the Frank Energie
configuration, the date, and the energy type. Select the configuration in the UI
to obtain its config entry ID for YAML. Dates use the Europe/Amsterdam calendar.
The example uses today's date in an installation configured for that time zone.

```yaml
actions:
  - action: frank_energie.get_prices_for_date
    data:
      config_entry: YOUR_FRANK_ENERGIE_CONFIG_ENTRY_ID
      date: "{{ now().date().isoformat() }}"
      energy_type: electricity
    response_variable: tariff
  - variables:
      cheapest_period: "{{ tariff.prices | min(attribute='all_in_price') }}"
```

The response contains `unit`, the validated UTC `coverage`, and a chronological
`prices` list. Each period gives its original `start` and `end`, `market_price`, and
`all_in_price`. The action reads the existing cache. Use a date whose prices
have been published and cached. Tomorrow's gas prices cover local midnight
through 06:00. If the requested coverage is unavailable, the action reports
an error.

## Energy Dashboard History

With Recorder enabled, the integration imports complete hourly readings from
the usual authenticated usage refresh. It creates separate statistics for
electricity consumption, feed-in, and gas consumption, plus the corresponding
cost or compensation series when the provider reports them. Each statistic's
name includes the configured entry title. History grows from the returned
usage periods, and missing hours remain gaps. Separate date and clock values
use Europe/Amsterdam. A repeated local hour needs an explicit offset to be
imported.

In the Energy Dashboard settings, select the statistics for your delivery
location. Use **Electricity consumption** for grid consumption,
**Electricity feed-in** for return to the grid, and **Gas consumption** for
gas. Choose the gas series with the unit used by your installation. Under
costs and compensation, choose the matching EUR series where applicable. Use
one consumption or cost source for each flow to avoid counting it twice.

## Dashboard Ideas

Useful entities for Energy dashboards:

- Current electricity price
- Current gas price
- Average electricity price
- Lowest electricity price
- Smart Charging status
- Smart Battery status
- Battery operating mode
- Trading strategy
- Contract resolution

Refer to [Entities](entities.md) to identify the corresponding entity IDs in your installation.

## Notes

- Entity IDs may differ depending on language, account features, and configuration.
- Use Home Assistant's entity picker to select the correct entities.
- Events are generally preferred over polling sensors for automation triggers.
- Smart Charging and Smart Battery examples require supported Frank Energie account features.
- The integration dynamically creates entities based on available account data and connected devices.

## Frank Energie Insight reporting period

The **Insight period** text control on the **Frank Energie - Usage / Inzicht** device
accepts `YYYY` (such as `2025`), `YYYY-MM` (such as `2026-10`), and
`YYYY-MM-DD` (such as `2026-11-17`). It defaults to yesterday in
Europe/Amsterdam and rejects invalid or future periods.

Changing the period retrieves the corresponding `periodUsageAndCosts` totals
for electricity, gas and feed-in. The selected period is exposed as a
`selected_period` attribute. All available provider `items` are included in
the Insight sensor attributes for day (`YYYY-MM-DD`), month (`YYYY-MM`) and
year (`YYYY`) selections alike. The nested category attribute keys are
`electricity`, `gas`, and `feed_in`, respectively, on both consumption and
cost sensors. They replace the former labels such as `Electricity costs yesterday`;
existing Home Assistant templates referring to those old attribute names must
be updated. Year-long item lists can be very large and increase Recorder
database storage considerably. The existing six Insight sensor unique IDs
remain intact, but their display names describe a selected period rather than
always claiming *yesterday*. These values are selected-period snapshots,
**not** cumulative energy meters; do not use them as Energy Dashboard meters.

The routine yesterday usage fetch and any Recorder history import remain
independent of the selector. When the selection differs from yesterday, one
additional API request is made per statistics refresh. The selected period is
persisted across Home Assistant restarts.
