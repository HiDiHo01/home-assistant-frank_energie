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

## Battery Operating Modes

Supported batteries expose selectable operating modes.

Available modes may include:

- Self Consumption
- Self Consumption Mix
- Imbalance Trading
- Trading

The available modes are determined by the Frank Energie API and battery capabilities.

## Trading Strategies

The integration allows selecting battery trading strategies.

Available strategies may include:

- Balanced
- Conservative
- Aggressive
- Imbalance Only

These strategies influence how the battery participates in energy optimization and trading programs.

## Self Consumption Mix Threshold

For batteries operating in Self Consumption Mix mode, a configurable electricity price threshold is available.

Supported configuration:

| Setting | Value |
|----------|--------|
| Minimum | €0.20/kWh |
| Maximum | €0.40/kWh |
| Step Size | €0.05/kWh |

This threshold determines when the battery prioritizes self-consumption behavior.

## Battery Sessions

The integration exposes battery session information when available.

Examples include:

- Charging sessions
- Discharging sessions
- Session summaries
- Optimization information

Session availability depends on battery support and API capabilities.

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