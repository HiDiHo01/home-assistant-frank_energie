# Frequently Asked Questions (FAQ)

This page answers common questions about the Frank Energie integration.

## Why are tomorrow's prices missing?

This is usually expected behavior.

Tomorrow's prices are published by upstream market operators and are not available immediately after midnight.

The integration begins checking for tomorrow's prices around:

```text
11:00 UTC
```

When prices become available, the integration fires:

```text
Event: frank_energie_event
Action: tomorrow_prices_available
```

See:

- [Update Frequency](update_frequency.md)
- [Events](events.md)

---

## Why do I only have 24 electricity prices?

You are likely using hourly pricing:

```text
PT60M
```

Hourly pricing contains:

```text
24 prices per day
```

This may be your configured resolution or a temporary fallback provided by the API.

---

## Why do I have 96 electricity prices?

You are using quarter-hourly pricing:

```text
PT15M
```

Quarter-hourly pricing contains:

```text
96 prices per day
```

---

## What is resolution drift?

Resolution drift occurs when the configured resolution differs from the resolution currently returned by the API.

Example:

```text
config=PT15M
api=PT60M
```

This is normal and usually temporary.

The integration automatically switches back when the preferred resolution becomes available.

No user action is required.

---

## Why are Smart Battery entities missing?

Possible reasons:

- Smart Battery is not enabled on your account.
- No supported battery is connected.
- Battery onboarding is incomplete.
- The API is not reporting battery information.

See:

- [Smart Battery](smart_battery.md)

---

## Why are Smart Charging entities missing?

Possible reasons:

- Smart Charging is not enabled on your account.
- No supported charger is connected.
- Provider connectivity issues.
- The API is not reporting Smart Charging information.

See:

- [Smart Charging](smart_charging.md)

---

## Why are Smart Trading entities missing?

Possible reasons:

- Smart Trading is not enabled.
- Your account does not support Smart Trading.
- The API is not currently reporting Smart Trading information.

Entity availability depends on account capabilities.

---

## Why are Smart Feed-In entities missing?

Possible reasons:

- Smart Feed-In is not enabled.
- Your account is not eligible.
- The API is not currently reporting Smart Feed-In information.

---

## Why are Smart HVAC entities missing?

Possible reasons:

- Smart HVAC is not enabled.
- No supported device is connected.
- The API is not reporting Smart HVAC information.

---

## Why are PV System entities missing?

Possible reasons:

- No PV systems are connected.
- PV onboarding is incomplete.
- The API is not reporting PV system information.

See:

- [User Features](user_features.md)

---

## Why did entities appear or disappear?

The integration dynamically creates entities based on:

- Account features
- Connected devices
- Smart services
- Available API data

As account features change, entity availability may also change.

This is expected behavior.

---

## Why is no data retrieved between 00:00 and 01:00 UTC?

The Frank Energie API performs a daily maintenance window during:

```text
00:00 UTC - 01:00 UTC
```

During this period:

- API requests are skipped.
- Cached data is used when available.
- Updates resume automatically afterwards.

This is expected behavior.

---

## Why are public prices available but authenticated prices missing?

Possible reasons:

- Authentication expired.
- Temporary account API issue.
- User-specific endpoints are unavailable.

Public market data and authenticated account data use different API endpoints.

---

## Do I need a Frank Energie account?

No.

The integration supports:

- Public market data without authentication.
- Account-specific features with authentication.

See:

- [Configuration](configuration.md)

---

## Can I use multiple delivery sites?

Yes.

For accounts with multiple eligible delivery sites:

- Sites are discovered automatically.
- A site can be selected during setup.
- The selected site can be changed through reconfiguration.

---

## Why does the integration create different entities than another user's installation?

Entity creation depends on:

- Account features
- Smart services
- Connected hardware
- Battery availability
- Charging availability
- API capabilities

Not all users receive the same entities.

---

## How do I manually refresh data?

The integration provides refresh button entities.

Available buttons depend on enabled features.

Examples include:

- Price refresh
- Battery session refresh

See:

- [Examples](examples.md)

---

## How do I report a bug?

Before opening an issue:

1. Download diagnostics.
2. Enable debug logging.
3. Collect relevant logs.
4. Document steps to reproduce the issue.

See:

- [Diagnostics](diagnostics.md)
- [Troubleshooting](troubleshooting.md)

GitHub Issues:

https://github.com/HiDiHo01/home-assistant-frank_energie/issues

---

## Where can I find my entity IDs?

Entity IDs vary between installations.

Use:

- Home Assistant entity picker
- Home Assistant Developer Tools
- Devices & Services

See:

- [Entities](entities.md)

---

## Where can I find automation examples?

See:

- [Examples](examples.md)

Examples include:

- Tomorrow price notifications
- Cheapest price notifications
- Smart Battery automations
- Smart Charging automations
- Dashboard ideas