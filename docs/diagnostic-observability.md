# Frank Energie — safe diagnostics of actual API requests

This opt-in-free diagnostic collector observes the **real GraphQL call boundary**
in the pinned `python-frank-energie==2026.9.20` API client. It neither
replays calls nor uses additional HTTP requests. It is active from integration
setup; its records are in memory until restart, bounded to 96 entries.

## How to collect evidence in Home Assistant

1. Install a release containing the privacy-safe GraphQL request observer.
2. Restart Home Assistant or reload the Frank Energie integration. Observe
   its normal update cycle; no developer-mode setting is required.
3. Go to **Settings → Devices & services → Frank Energie → ⋮ → Download diagnostics**.
4. If a problem relates to next-day prices, export once before and once after
   the expected publication window. For month-end or gas-readings issues,
   export at different times or on following days to compare evidence.
5. Share the diagnostics privately for investigation. It contains no raw API
   bodies, but operating metadata is still shared.

## What the new `graphql_request_evidence` contains

- Whether the call used the authenticated account client or the **independent
  anonymous public market-price client**.
- Named GraphQL operation (such as `MarketPrices`, `MonthSummary`,
  `MonthInsights`), calendar date/month if applicable, UTC timestamp and
  elapsed time in milliseconds.
- Whether the API returned empty data, a GraphQL error array, or raised an
  exception. Error **class** and an HTTP **failure** code are included only
  when the client exposes them. No raw exception text is exported.
- Recognized top-level GraphQL fields and their `null`/`empty`/`present`
  status; for market-price queries, the number of raw electricity/gas slots.
- For raw `MonthInsights` finance fields: source classification as
  `missing`, `null`, `zero`, `positive` or `negative`, **never** the
  actual monetary/usage values. This is essential to diagnose the pinned
  client's known `null`→`0.0` coercion without disclosing billing amounts.
- Separately, the post-parse effective `PriceData.all` counts. This
  distinguishes API-returned data from data rejected/modified by the client
  parser and later coordinator logic.
- The latest event by source/operation as well as the 96 most recent events.

**Limitations:** No successful HTTP status code is available from the pinned
client's `_query` public return value (it returns parsed JSON, not the aiohttp
response). It is incorrect to fabricate a `200` field. GraphQL errors may
be returned with HTTP success and are reported separately.

**Contract semantics:** Belgian `MODEL` contracts are variable, not dynamic.
`SMR1` and `SMR3` are opaque Frank API codes. The actual meter hardware and its capabilities cannot be determined from those codes. The
integration must not represent spot-market prices as a variable customer's
contractual unit rate.

## Price selection outcomes and trust boundary

The source-selection diagnostic must describe **actual selected price slots**,
not the HTTP/GraphQL success flag:

- `customer`: selected slots came from authenticated customer data.
- `public`: selected slots came from the independent anonymous market feed.
- `public_fallback`: market slots filled missing customer slots; these are
  **not** verified contractual prices for a Belgian `MODEL` variable contract.
- `mixed`: some selected slots are customer values and others are fallback.
- `cached_fallback`: a previously cached value was used; do not claim it was
  fetched from today's public GraphQL response.
- `not_available`: no selected electricity or gas slots exist. An API poll
  returning successfully does not change this quality.

This classification is diagnostic metadata; it does **not** alter the existing
price entity IDs, states, Recorder history or market-price fallback behavior.
In particular, a public spot price must never be used as evidence of the
actual invoiced MODEL tariff. Actual finance requires a separate proven
provider billing contract and verified upstream missing-versus-zero handling.

## Privacy and debug logging

Do **not** set `python_frank_energie: debug` to investigate these calls.
The pinned upstream library logs the raw `MonthSummary` payload at DEBUG
and can emit GraphQL error objects, which may include customer information.
Existing integration DEBUG messages may also be verbose. This collector
is designed as a safer alternative for actual request evidence.

No GraphQL variables, headers, tokens, addresses, site references, connection
IDs, EANs, full responses, usage values, prices, invoice values or payment
details are retained in request traces. Full raw provider account payloads
are also omitted from the updated Home Assistant diagnostics export.
This does not guarantee that the independent upstream library or Home
Assistant core never logs errors elsewhere.

## Interpretation

- GraphQL `MarketPrices` has raw response slots, but coordinator's parsed
  effective slots are zero → investigate client parser or selection/fallback.
- Both public and customer `MarketPrices` contain no raw slots → source
  availability, request parameters, or provider API behavior need checking.
- API returns `monthSummary` or `periodUsageAndCosts` `null` → a
  successful coordinator poll alone is not evidence of actual billing data.
- Unknown/missing gas-reading date is **not** evidence that the physical gas
  meter is analog or absent.
- Repeated exception classes / HTTP failure codes / long durations show
  network or provider problems without exposing private exception messages.

This feature collects evidence only. It does not fix a provider API issue,
force gas data publication or fabricate tariffs. Live HA testing and consent
are required before promoting to a stable release.
