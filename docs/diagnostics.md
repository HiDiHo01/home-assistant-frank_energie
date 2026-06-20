# Diagnostics

This page explains how to collect diagnostics and debug information for the Frank Energie integration.

Diagnostics help identify issues with configuration, authentication, API communication, entities, and smart features.

## Download Diagnostics

Home Assistant can generate a diagnostics file for the integration.

To download diagnostics:

1. Open **Settings → Devices & Services**.
2. Select **Frank Energie**.
3. Select **Download Diagnostics**.
4. Save the generated file.

When reporting issues, attach the diagnostics file to the GitHub issue whenever possible.

## What Diagnostics Include

Depending on your configuration, diagnostics may include:

- Integration version
- Home Assistant version
- Configuration information
- Coordinator state
- Available entities
- Site information
- Market resolution information
- Smart feature availability
- Battery information
- Smart Charging information
- Smart Trading information
- Smart Feed-In information
- PV system information

## Sensitive Data

Diagnostics are designed to protect sensitive information.

Examples of data that may be redacted:

- Access tokens
- Refresh tokens
- Passwords
- Personal account information
- Sensitive identifiers

Always review diagnostics before sharing them publicly.

## Enable Debug Logging

Debug logging provides additional information when troubleshooting issues.

Add the following to your Home Assistant configuration:

```yaml
logger:
  logs:
    custom_components.frank_energie: debug
    python_frank_energie: debug
```

Restart Home Assistant after enabling debug logging.

## Viewing Logs

Logs can be viewed through:

1. Settings → System → Logs
2. Home Assistant log files
3. Supervisor logs (when applicable)

Look for messages containing:

```text
frank_energie
python_frank_energie
```

## Common Information Requested in Bug Reports

When opening a GitHub issue, include:

- Home Assistant version
- Frank Energie integration version
- Installation method
- Relevant logs
- Diagnostics file
- Steps to reproduce the issue
- Expected behavior
- Actual behavior

## Authentication Issues

For authentication problems, include:

- Whether authentication is enabled
- Whether reauthentication was attempted
- Relevant error messages

Do not include:

- Passwords
- Access tokens
- Refresh tokens

## Price Data Issues

When reporting pricing issues, include:

- Whether the issue affects public prices or authenticated prices
- Current resolution (`PT15M` or `PT60M`)
- Example entities affected
- Relevant logs

Examples:

- Missing tomorrow prices
- Incorrect resolution
- Resolution drift warnings
- Missing price sensors

## Smart Charging Issues

Include:

- Smart Charging entity states
- Provider information
- Diagnostics file
- Relevant log entries

## Smart Battery Issues

Include:

- Battery mode
- Trading strategy
- Battery entity states
- Session information
- Relevant logs

## API Maintenance Window

The Frank Energie API performs daily maintenance between:

```text
00:00 UTC - 01:00 UTC
```

Limited updates during this period are expected and should not normally be reported as bugs.

## Tomorrow Price Availability

Tomorrow's prices are generally checked beginning around:

```text
11:00 UTC
```

Delays in publication are usually controlled by upstream market operators.

Before reporting missing tomorrow prices, verify that publication has occurred.

## Events

The integration may fire Home Assistant events that assist with troubleshooting.

Examples include:

```text
frank_energie_event
```

See:

- [Events](events.md)

## Related Documentation

- [Configuration](configuration.md)
- [Entities](entities.md)
- [Events](events.md)
- [Troubleshooting](troubleshooting.md)
- [Update Frequency](update_frequency.md)
- [User Features](user_features.md)

## Reporting Issues

GitHub Issues:

https://github.com/HiDiHo01/home-assistant-frank_energie/issues

Including diagnostics and debug logs significantly reduces the time required to investigate and resolve issues.