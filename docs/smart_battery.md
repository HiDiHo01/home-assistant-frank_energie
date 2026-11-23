# Smart Battery

The Frank Energie integration provides Smart Battery support for supported battery systems connected to your Frank Energie account.

Smart Battery entities allow monitoring battery status, viewing optimization information, and controlling battery operating modes directly from Home Assistant.

## Overview

Smart Battery functionality is managed by Frank Energie and exposed through the Frank Energie API.

The integration automatically creates entities for supported battery systems discovered on your account.

Available entities depend on:

- Battery manufacturer
- Battery capabilities
- Account configuration
- Frank Energie services

## Battery Monitoring

The integration exposes battery monitoring entities such as:

- Battery status
- Battery configuration
- Battery sessions
- State of charge information
- Capacity information
- Maximum charge power
- Maximum discharge power
- Self-consumption trading status

These entities can be used in dashboards, automations, and energy management workflows.

### Current capacity

The **Current capacity** sensor (*Huidige capaciteit* in Dutch) estimates the
energy currently stored in an individual Smart Battery, in kWh:

`Battery capacity (kWh) × State of charge (%) / 100`

The calculation uses the same rounded charge percentage reported by the existing
State of charge sensor. It is an instantaneous energy-storage measurement, not a
cumulative energy meter. If the reported charge level or battery capacity is
missing or invalid, or the API marks the charge data unreliable, the sensor
reports `unknown` rather than treating missing data as zero. The sensor is
created when the battery summary is available.

The **Batteries** service also exposes a **Current capacity** sensor
(*Huidige capaciteit*) in kWh. It sums the stored-energy values from every
Smart Battery connected to this integration entry, using the same per-battery
calculation. If any reported battery is missing a reliable capacity or charge
level, the service sensor reports `unknown` instead of a misleading partial
total. A genuine combined 0 kWh remains 0 kWh.

## Battery Operating Modes

Supported batteries expose selectable operating modes.

The battery can report these modes:

- Self Consumption
- Self Consumption Mix
- Imbalance Trading
- Trading
- Congestion

The modes you can select differ per battery and are set by Frank Energie. For
example, some batteries offer Imbalance Trading and Self Consumption Mix but not
Trading. With a library release that supports per-battery settings, the mode
select offers only the modes your battery can select, and always shows the mode
the battery currently reports.

Mode changes usually take effect at the next switch moment rather than
immediately. The select shows a pending change and its effective date in the
`upcoming_change` and `upcoming_change_effective_date` attributes, and whether a
change is currently possible in `is_change_request_possible`.

The battery-mode select also exposes the same settings attributes as its
battery-mode sensor, including `battery_mode`, `imbalance_trading_strategy`,
`self_consumption_trading_allowed`,
`self_consumption_trading_threshold_price`, `created_at`, `updated_at` and
`trading_algorithm`. These attributes are scoped to the same physical battery
and retain the API's `null` and `unknown` values. A battery mode included in
the select solely to display the current state is not necessarily available
for changing: the API's selectable modes govern which changes can be sent.
`unknown` is a status fallback, never a selectable battery mode.

## Trading Strategies

The integration allows selecting battery trading strategies.

Available strategies may include:

- Standard
- Balanced
- Conservative
- Aggressive
- Imbalance Only

As with the modes, the strategies you can select differ per battery. The
strategy select is available while the battery is in Trading or Imbalance
Trading mode.

These strategies influence how the battery participates in energy optimization and trading programs.

## Self-Consumption Threshold

For batteries operating in Self Consumption or Self Consumption Mix mode, an
electricity price threshold can be configured if the battery supports it.
Frank Energie only offers the threshold on some batteries (those using the `T1`
trading algorithm); on other batteries the threshold entity is unavailable,
matching the Frank Energie app.

The range comes from the prices Frank Energie offers for your battery, and only
those prices can be set. Without per-battery settings, the defaults are:

| Setting | Value |
|----------|--------|
| Minimum | €0.20/kWh |
| Maximum | €0.40/kWh |
| Step Size | €0.05/kWh |

This threshold determines when the battery prioritizes self-consumption behavior.

## Battery Sessions

The period sensors use Frank Energie's reported amounts in EUR. Their unique IDs
stay stable for existing dashboards and statistics. The ID suffixes predate the
current API field names.

| Sensor | Existing ID suffix | Source or calculation |
| --- | --- | --- |
| Period Trading Result | `period_imbalance_result` | `periodBreakdownTradingResult` |
| Period Frank Slim Bonus | `period_frank_slim_bonus` | `periodBreakdownFrankSlim` |
| Period Total Result (before EPEX) | `period_trading_result` | `periodResult` |
| Period EPEX Correction | `period_epex_result` | `periodBreakdownEpexCorrection`, with its reported sign |
| Period Total to Settle | `period_total_result` | `periodResult + periodBreakdownEpexCorrection` |

For example, a result of €0.0899 and a correction of −€0.6557 settle at
−€0.5658. A positive correction increases the total. If either operand is
missing, the settlement total is unknown. A reported zero remains zero.

### Finalized results for yesterday

Each battery has five EUR sensors that share one single-day request. The
existing **Yesterday's Result** keeps its entity identity (`daily_trading_result`).

| Sensor | Amount for the previous Amsterdam calendar day |
| --- | --- |
| Yesterday's Trading Result | Trading component |
| Yesterday's Frank Slim | Frank Slim component |
| Yesterday's Result | Provider's result before EPEX |
| Yesterday's EPEX Correction | Signed EPEX correction |
| Yesterday's Total | Result plus the signed EPEX correction |

`result_date` identifies the requested day. The amounts become available when
all sessions for that day have `session_status: complete_final`. A status such
as `completed` is provisional. An empty response shows `missing`, and a failed
request shows `unavailable`. Missing amounts remain unknown. A finalized zero
displays as zero. The four period fields require a newer SDK than 2026.11.21.

At Amsterdam midnight the sensors switch to the new day and clear the previous
values. Unfinished days are checked on battery inventory refreshes, starting
after 15 minutes. The wait doubles up to four hours and respects maintenance
and provider throttling. Once a battery's day is final, ordinary refreshes reuse
it for the rest of the day. Reloading the integration fetches the applicable day
again. The action below can retrieve dates missed while Home Assistant was offline.

### Results for a date range

On the **Batteries** device, set **Period start date** and **Period end date**
to update the period sensors for every battery at that site. **Period length**
counts Amsterdam calendar days, including both endpoints. For example, 31 December
through 2 January is three days. Equal dates select a single day.

**Battery reporting period** offers **Month to date** (the default), **Yesterday**,
**Previous calendar month** and **Custom dates**. The first three follow the
Amsterdam calendar. Choosing a preset changes both dates together. Selecting
**Custom dates** freezes the displayed pair. Editing either date also switches
to **Custom dates**, and the selection survives a restart. To move the range
entirely into the past, set the start first. To move it forward, set the end first.
An edit that would reverse the range raises an error and keeps the previous dates.
Each battery shares one request across its period sensors. The previous period's
amounts disappear while the new report is being fetched. Selecting yesterday
reuses the verified one-day report. The five yesterday sensors always refer
to the previous day, regardless of the period selected. User-selected reporting
periods can overlap, so the period amounts and trade index do not feed long-term
statistics. Their current values and normal Recorder history remain available.

Use **Get Smart Battery results** (`frank_energie.get_battery_results`) with a
signed-in configuration. Omit both date fields to read the selected period, or
pass both `start_date` and `end_date` to request a different range for that call.
Both are inclusive Amsterdam calendar dates. Use the same date for yesterday,
or request a whole billing period in one call. The SDK forwards both dates
directly. Any maximum range depends on the provider.

The response contains the requested dates, their inclusive `period_days` count
and a `batteries` object keyed by battery ID. Each battery has the five amounts
for the **whole range** and the provider's daily sessions. To get all five
amounts for one day, request that day.

```json
{
  "start_date": "2026-11-14",
  "end_date": "2026-11-14",
  "period_days": 1,
  "batteries": {
    "example-battery": {
      "trading_result": 0.1613,
      "frank_slim": 0.0202,
      "result": 0.1814,
      "epex_correction": -0.0034,
      "total": 0.1780,
      "sessions": [
        {"date": "2026-11-14", "result": 0.1814, "status": "complete_final"}
      ]
    }
  }
}
```

A missing amount is `null`. A battery whose request failed, or whose response
belongs to another battery or range, has a `null` entry. If maintenance begins
or the SDK signals throttling, results already fetched are retained and the
remaining batteries have `null` entries. A successful empty inventory returns
`"batteries": {}`. An unavailable inventory or a configuration without a login
raises an action error. Requests leave the sensors' polling data intact.

To catch up after downtime, create a date-only `input_datetime` helper named
`input_datetime.battery_last_settlement`. Set it to the day before the first
settlement you want to retrieve. The example checks one outstanding day at
startup and at 08:00, 10:00 and 12:00, using Home Assistant's Europe/Amsterdam
timezone. It advances the helper only after posting a finalized total, so a
failed or pending day will be retried. Select your Frank Energie configuration
in the action editor and replace the example battery ID.

```yaml
alias: Report outstanding finalized Smart Battery results
mode: single
triggers:
  - trigger: homeassistant
    event: start
  - trigger: time
    at:
      - "08:00:00"
      - "10:00:00"
      - "12:00:00"
variables:
  battery_id: example-battery
  last_date: "{{ states('input_datetime.battery_last_settlement') }}"
actions:
  - condition: template
    value_template: "{{ as_datetime(last_date, none) is not none }}"
  - variables:
      day: "{{ (as_datetime(last_date).date() + timedelta(days=1)).isoformat() }}"
  - condition: template
    value_template: "{{ as_datetime(day).date() < now().date() }}"
  - action: frank_energie.get_battery_results
    data:
      config_entry: YOUR_FRANK_ENERGIE_CONFIG_ENTRY_ID
      start_date: "{{ day }}"
      end_date: "{{ day }}"
    response_variable: report
  - variables:
      battery: "{{ report.batteries.get(battery_id) }}"
  - condition: template
    value_template: >-
      {{ battery is mapping and battery.total is number
         and battery.sessions | count > 0
         and battery.sessions | rejectattr('status', 'equalto', 'complete_final')
                              | list | count == 0 }}
  - action: persistent_notification.create
    data:
      notification_id: "battery_result_{{ battery_id }}_{{ day }}"
      title: "Battery settlement for {{ day }}"
      message: "Total including EPEX correction: €{{ battery.total }}"
  - action: input_datetime.set_datetime
    target:
      entity_id: input_datetime.battery_last_settlement
    data:
      date: "{{ day }}"
```

## Battery Optimization

Frank Energie may optimize battery operation based on:

- Electricity prices
- Market conditions
- Trading programs
- Self-consumption configuration

Optimization behavior is controlled by Frank Energie services and account settings.

## Manual Refresh

The integration provides a dedicated button for refreshing battery session information.

Use this button when:

- Troubleshooting battery data
- Verifying newly available sessions
- Confirming synchronization changes

## Home Assistant Automations

Battery entities can be used in automations.

Example use cases:

- Notify when battery mode changes.
- Alert when trading mode becomes active.
- Track optimization state.
- Display battery performance dashboards.
- Monitor charging and discharging sessions.

## Troubleshooting

### Battery Entities Are Missing

Possible causes:

- No supported battery is connected.
- Battery onboarding is incomplete.
- Frank Energie battery services are unavailable.
- The account does not have Smart Battery functionality enabled.

### Battery Sessions Are Missing

Possible causes:

- Session data is not yet available.
- API synchronization delay.
- Battery provider issues.

Use the Battery Session Refresh button and check Home Assistant logs.

### Battery Mode Cannot Be Changed

Possible causes:

- Temporary API issue.
- Unsupported battery configuration.
- Frank Energie service limitations.

Verify that the desired mode is supported by your battery system.

## Diagnostics

When reporting Smart Battery issues:

1. Download integration diagnostics.
2. Enable debug logging.
3. Include relevant battery entities.
4. Include any coordinator update errors.

Sensitive information is automatically redacted where appropriate.

## Notes

- Smart Battery functionality depends on Frank Energie account features.
- Available entities may vary between battery manufacturers.
- Available operating modes are determined by the Frank Energie API.
- New battery features may automatically appear as Frank Energie expands Smart Battery support.
