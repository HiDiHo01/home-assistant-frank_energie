# Examples

This page contains practical Home Assistant automation examples for the Frank Energie integration.

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
      title: Frank Energie
      message: Tomorrow's electricity prices are now available.
```

## Lowest Price Notification

Notify when the cheapest electricity period starts.

```yaml
alias: Frank Energie - Lowest Price
triggers:
  - trigger: event
    event_type: frank_energie_event
    event_data:
      action: lowest_price
actions:
  - action: notify.mobile_app_phone
    data:
      title: Electricity Price Alert
      message: The cheapest electricity period has started.
```

## Cheapest Charging Window Notification

Notify when the cheapest charging window begins.

```yaml
alias: Frank Energie - Cheapest Charging Window
triggers:
  - trigger: event
    event_type: frank_energie_event
    event_data:
      action: lowest_4p_price
actions:
  - action: notify.mobile_app_phone
    data:
      title: EV Charging
      message: The cheapest charging window has started.
```

## Refresh Price Data

Manually refresh Frank Energie data using the refresh button entity.

```yaml
alias: Frank Energie - Refresh Prices
triggers:
  - trigger: time
    at: "11:05:00"
actions:
  - action: button.press
    target:
      entity_id: button.frank_energie_refresh_prices
```

## Battery Session Refresh

Refresh battery session information daily.

```yaml
alias: Frank Energie - Refresh Battery Sessions
triggers:
  - trigger: time
    at: "06:00:00"
actions:
  - action: button.press
    target:
      entity_id: button.frank_energie_refresh_battery_sessions
```

## Battery Mode Change Notification

Notify when the battery operating mode changes.

```yaml
alias: Frank Energie - Battery Mode Changed
triggers:
  - trigger: state
    entity_id: select.frank_energie_battery_mode
actions:
  - action: notify.mobile_app_phone
    data:
      title: Battery Mode
      message: "Battery mode changed to {{ trigger.to_state.state }}"
```

## Smart Charging Availability Alert

Notify when Smart Charging becomes unavailable.

```yaml
alias: Frank Energie - Smart Charging Unavailable
triggers:
  - trigger: state
    entity_id: binary_sensor.frank_energie_smart_charging
    to: "off"
actions:
  - action: notify.mobile_app_phone
    data:
      title: Smart Charging
      message: Smart Charging is currently unavailable.
```

## Negative Price Alert

Notify when electricity prices become negative.

```yaml
alias: Frank Energie - Negative Electricity Price
triggers:
  - trigger: numeric_state
    entity_id: sensor.frank_energie_current_electricity_price
    below: 0
actions:
  - action: notify.mobile_app_phone
    data:
      title: Negative Electricity Price
      message: Electricity prices are currently negative.
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

## Notes

- Entity IDs may differ depending on language and configuration.
- Use Home Assistant's entity picker to select the correct entities.
- Events are generally preferred over polling sensors for automation triggers.
- Smart Charging and Smart Battery examples require supported Frank Energie account features.