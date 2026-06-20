# Examples

This page contains practical Home Assistant automation examples for the Frank Energie integration.

For entity-based examples, replace placeholder entity IDs with the actual entities created in your installation.

See:

- [Entities](entities.md)

for information about available entities and features.

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