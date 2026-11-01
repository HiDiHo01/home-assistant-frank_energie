# Regional support, source provenance and monthly finance correctness

This page describes provider-backed electricity/gas prices and cost data for
authenticated and anonymous users. It applies to all supported countries;
Belgian variable contracts are an important case where a public hourly market
price must **not** be represented as an invoiced customer tariff.

## What problems this contribution solves

| User/maintainer problem | General solution | Value |
| --- | --- | --- |
| Public prices may be requested with account credentials | Keep public market requests anonymous and authenticated customer requests site-scoped | Prevents credential leakage and cross-site or cross-contract price confusion |
| Login after initial anonymous setup or account changes are fragile | Explicit configuration/auth lifecycle and site selection guards | Preserves a single config entry, existing entity identities and account setup |
| Price data is shown without where it came from | Attribute customer, public/fallback, mixed, cached and unavailable values; inspect source slots separately | Automations and dashboards can decide whether a signal is suitable, rather than treating spot prices as contract prices |
| A provider query can succeed yet return incomplete data | Check raw GraphQL roots, errors, source timestamps and interval coverage | Avoids reporting success, zeros and full-day statistics when data was not published |
| Missing/null financial fields can be coerced to 0.0 by the pinned client | Capture *field presence and sign* before parsing and fail closed on unverified zeros | Protects household finance dashboards and historical metrics from fabricated zero costs |
| Actual, expected, fixed, feed-in and gas are mixed | Opt-in calendar-month finance sensors with explicit meter cutoffs, period, units and quality | Clear month-to-month provider data without claiming a complete household invoice |
| One segment may be absent despite a valid dual-fuel contract | Respect electricity/gas contract flags and explicit gas exclusion | Keeps visible available electricity/injection components without labeling the combined total complete |
| Tomorrow's gas can contain only a small portion of the day | Suppress whole-day gas averages/extrema until all local time intervals are covered, including DST days | Prevents partial six-hour samples appearing as tomorrow's daily average |
| Intermittent previous-month API failure erases useful source evidence | Retain last confirmed snapshot only for the same delivery site and month, with original retrieval timestamp | More resilient historical visibility without inventing a successful provider refresh |
| Operator diagnostics hide what the API actually published | Bounded, sanitized GraphQL observer without new network requests | Faster privacy-preserving support and reproducible source-contract debugging |

These fixes extend general integration behavior rather than introducing a
separate Belgian-only fork, site-specific configuration or another billing
engine. Existing entity unique IDs are retained.

## Price types must remain distinct

1. **Public market prices** are anonymous country-scoped market indicators.
   Electricity may be published at 15-minute resolution, depending on source.
2. **Authenticated customer price slots** belong to the selected account
   delivery site and must be tagged with source provenance. A successful
   authenticated query is not proof that a complete customer schedule exists.
3. **Invoice unit prices** require a confirmed contract billing formula.
   A Belgian variable `MODEL` contract must not be assumed to bill on each
   hourly public spot price. Provider meter codes such as `SMR1`/`SMR3`
   must not be used to infer digital meter capabilities.

The integration supports country-specific provider query behavior rather than
forcing Netherlands-shaped public price queries on Belgium or France. Country
support in a price query alone does **not** guarantee that all authenticated
features or billing fields are available in those regions.

For numeric price sensors, inspect `selected_price_origin`,
`selected_price_segment`, `customer_billing_rate_verified`, and
`cost_scope` before using a price for a financial calculation. When price
coverage is partial, include that status rather than presenting a full
local-day aggregate. Preserve the original source unit for gas.

## Opt-in monthly finance and its limits

Enable `Monthly Finance` on an authenticated account to expose period-scoped
provider `MonthInsights` data. Monthly readings may include electricity,
feed-in/injection, gas, expected fixed charges, forecast totals and actual
totals **only to the extent the raw fields are genuinely present**.

Provider source-quality indicators include:

- `finance_current_readiness` / `finance_previous_readiness`
- `not_published`, `contract_unverified`, `electricity_missing`,
  `gas_missing`, `provider_total_missing`, `provider_components_present`
- Individual metric `quality`, `period`, `retrieved_at`, reading cutoff,
  zero verification, and exclusion reasons.

`provider_components_present` does **not** mean that a real household bill
including grid, capacity, tax and invoice adjustments is reconciled. The
provider may expose expected gas amounts while withholding actual gas usage
or cost, and a month query may return a GraphQL error with null data. Missing
or provider-excluded values remain unavailable, not zero.

Forecast values for a whole requested calendar month do not require the first
meter reading of that month. Actual amounts and readings-to-date **do**
require a plausible meter cutoff within the relevant period.

Invoice payment/advance amounts must not be added to already computed
consumption charges. Do not synthesize gas unit conversion, capacity tariffs,
provider billing rates, or historical Recorder statistics.

## Supported source and reliability contracts

- Keep public and authenticated clients separate, and use only the selected
  delivery site for customer values and cached monthly evidence.
- Cache entries retain the real source date and retrieval timestamp. An API
  error does not silently refresh their age.
- Full-day completeness is determined by actual timestamp interval coverage
  in local time, not nominal slot count, across DST's 23/25-hour dates.
- Local entity state and coordinator success are distinct from raw publication
  status. A normal poll may succeed when some service entities are unavailable.
- Preserve sensor/device unique IDs and state meanings through reconfiguration,
  reload, and updates. Avoid double polling and unnecessary GraphQL requests.
- Keep all billing amounts, tokens, site references, meter IDs and provider raw
  messages out of diagnostics and public test fixtures.

## How to verify the feature

The automated suite tests country-specific public price isolation, login and
config-entry transitions, month rollover, missing/raw-zero amounts, expected
versus actual cutoffs, gas exclusion and unit mismatches, previous-month
snapshot recovery, anonymous/customer price provenance, partial tomorrow
coverage, DST boundaries, privacy-safe diagnostics and translation alignment.

Before a general release, test on real supported-country accounts with
anonymized status evidence and verify:

1. Anonymous setup, account login, site changes, restart and reload preserve
   config entries, entity IDs, and Recorder history.
2. Source-origin attributes match the provider's selected and fallback slots,
   with correct country, local-day coverage and gas units.
3. Provider finance fields reconcile to the same meter cutoff and period;
   source-missing and parser-zero cases must not become fabricated charges.
4. Publication delays, 23/25-hour DST dates, partial gas tomorrow, transient
   errors and month boundaries remain safe.
5. HACS/Hassfest, both supported CI Python targets, Ruff, link checks and the
   existing upstream release process pass.

## Privacy-safe diagnostics

See [GraphQL source observability](diagnostic-observability.md). It captures
operation type, timing, source selection, raw response shape and numeric-field
*classification* rather than customer prices or account payloads. This makes
real API behavior observable without adding test-only requests.

## Known remaining provider limitations

A current-month `MonthInsights` response can contain GraphQL errors and a null
root; gas actuals and daily gas usage may be absent for a contracted gas
segment. Those are provider/publication or pinned-client integration questions
to investigate, not reasons to invent values. Actual month-end bills and
historical reconciliation remain an additional acceptance step.

The installed Python client is pinned to `python-frank-energie==2026.9.20`.
Its older parser can coerce absent numeric fields into zero; the defensive HA
integration checks are therefore necessary until an independently versioned,
tested upstream client correction makes them redundant.
