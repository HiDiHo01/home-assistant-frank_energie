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
