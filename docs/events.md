# Events

The Frank Energie integration fires the `frank_energie_event` event on the Home Assistant event bus. These events can be used in automations, scripts, and blueprints.

## Event type

```text
frank_energie_event
```

## Common event data

All Frank Energie events contain the following fields:

| Field | Type | Description |
|---------|------|-------------|
| `entry_id` | string | Home Assistant config entry identifier. |
| `entry_title` | string | Name of the Frank Energie configuration entry. |
| `action` | string | Event action type. |

---

## Action: `tomorrow_prices_available`

Fired when tomorrow's electricity prices become available from the Frank Energie API.

### Event data

| Field | Type | Description |
|---------|------|-------------|
| `action` | string | `tomorrow_prices_available` |
| `date` | string | Date for which prices became available. |
| `resolution` | string | Price resolution (`PT15M` or `PT60M`). |

---

## Action: `lowest_price`

Fired when the current time enters the cheapest electricity price period of the day.

### Event data

| Field | Type | Description |
|---------|------|-------------|
| `action` | string | `lowest_price` |
| `resolution` | integer | Duration of the cheapest period in minutes. |
| `price` | number | Electricity price including taxes. |
| `unit` | string | Price unit (`€/kWh`). |
| `start` | string | Start timestamp of the cheapest period. |
| `end` | string | End timestamp of the cheapest period. |

---

## Action: `lowest_4p_price`

Fired when the current time enters the cheapest consecutive 4-period block of the day.

### Event data

| Field | Type | Description |
|---------|------|-------------|
| `action` | string | `lowest_4p_price` |
| `periods` | integer | Number of periods (`4`). |
| `resolution` | integer | Market resolution in minutes. |
| `average_price` | number | Average price across the period block. |
| `unit` | string | Price unit (`€/kWh`). |
| `start` | string | Start timestamp of the period block. |
| `end` | string | End timestamp of the period block. |

---

## Action: `lowest_16p_price`

Fired when the current time enters the cheapest consecutive 16-period block.

This event is only generated when the market resolution is **15 minutes (PT15M)**.

### Event data

| Field | Type | Description |
|---------|------|-------------|
| `action` | string | `lowest_16p_price` |
| `periods` | integer | Number of periods (`16`). |
| `resolution` | integer | Resolution in minutes (`15`). |
| `average_price` | number | Average price across the period block. |
| `unit` | string | Price unit (`€/kWh`). |
| `start` | string | Start timestamp of the period block. |
| `end` | string | End timestamp of the period block. |

## Example automation

```yaml
automation:
  - alias: Notify when tomorrow prices are available
    triggers:
      - trigger: event
        event_type: frank_energie_event
        event_data:
          action: tomorrow_prices_available
    actions:
      - action: notify.mobile_app_phone
        data:
          message: Tomorrow's Frank Energie prices are available.
```

## Listening for events

1. Open **Developer Tools → Events**
2. Enter `frank_energie_event`
3. Select **Start listening**
4. Wait for an event to occur

## Notes

- Each event is fired at most once per day.
- Event timestamps are ISO 8601 formatted and timezone-aware.
- The `lowest_16p_price` event is only available when quarter-hourly pricing is active.
