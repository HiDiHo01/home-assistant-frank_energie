# ✅ SPEC: Gebruik van `zoneinfo.ZoneInfo` voor tijdzonebewuste datums

## 🔖 Doel

Zorg voor consistente, correcte en toekomstbestendige verwerking van tijdzones binnen het project, met ondersteuning voor:

- zomer-/wintertijd (DST),
- wereldwijde tijdzones,
- correcte ISO 8601 representatie,
- en conversies tussen lokale en UTC tijd.

## 📌 Context

Het standaard `datetime.timezone` object biedt alleen ondersteuning voor *vaste offsets* (zoals UTC+2) en **ondersteunt geen overgang van tijdzones** zoals zomertijd (DST).

Vanaf Python 3.9 introduceert de standaardbibliotheek `zoneinfo`, gebaseerd op de IANA tijdzone-database (tzdata), waarmee je volledige, dynamische tijdzones kunt gebruiken.

## 🎯 Specificatie

### ✅ Alle datetime-objecten in het project moeten:

- **timezone-aware** zijn,
- geannoteerd worden met `ZoneInfo('UTC')` voor UTC,
- of een specifieke IANA-tijdzone zoals `ZoneInfo('Europe/Amsterdam')`.

### ✅ Parseren van ISO 8601 datetime strings

Alle datums uit externe bronnen (zoals APIs of GraphQL responses) die eindigen op `Z` moeten worden geïnterpreteerd als UTC:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.rstrip('Z'))
        return dt.replace(tzinfo=ZoneInfo("UTC"))
    except ValueError as err:
        raise ValueError("Invalid datetime format: %s" % value) from err
```

### ✅ Gebruik van lokale tijdzones

Indien nodig (voor bijv. UI-weergave, logging, of planning):

```python
local_dt = utc_dt.astimezone(ZoneInfo("Europe/Amsterdam"))
```

### ✅ Gebruik van `ZoneInfo` bij aanmaken van datetime-objecten

```python
from datetime import datetime
from zoneinfo import ZoneInfo

dt = datetime(2025, 7, 24, 12, 0, tzinfo=ZoneInfo("UTC"))
```

## ❌ Verboden

- Gebruik van `datetime.timezone.utc` buiten compatibiliteitsdoeleinden.
- Gebruik van naïeve datums (zonder `tzinfo`).
- Gebruik van hardgecodeerde tijdzone-offsets zoals `timedelta(hours=2)`.

## 📎 Compatibiliteit

Voor Python-versies < 3.9 gebruik:

```bash
pip install backports.zoneinfo
```

En in de code:

```python
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
```

## 🔍 Referenties

- [PEP 615 – Support for the IANA Time Zone Database in the Standard Library](https://peps.python.org/pep-0615/)
- [zoneinfo — IANA time zone support](https://docs.python.org/3/library/zoneinfo.html)

---

## 📚 Appendix: Verschil tussen `zoneinfo` en `dateutil.tz`

| Kenmerk                        | `zoneinfo`                              | `dateutil.tz` (uit `python-dateutil`)           |
|-------------------------------|------------------------------------------|--------------------------------------------------|
| 📦 Beschikbaarheid            | Standaard in Python 3.9+                 | Externe dependency (`python-dateutil`)          |
| 🕘 Bron van tijdzonegegevens  | IANA tzdata (via OS of ingebouwd)       | Eigen interpretatie van tijdzones               |
| 🕑 DST & offset support       | ✅ Volledig DST-ondersteuning            | ✅ Ook DST-ondersteuning                         |
| 🏷️ Tijdzone-identificatie     | IANA-id's zoals "Europe/Amsterdam"      | Soms ook met alias of afwijkende formaten       |
| 📅 ISO 8601 parsing           | ❌ Alleen via `datetime.fromisoformat()` | ✅ Krachtige parsing via `dateutil.parser.parse` |
| 🐍 Onderdeel van stdlib?      | ✅ Ja, vanaf Python 3.9                  | ❌ Nee                                           |
| 🔄 Betrouwbaarheid            | ✅ Standaard en consistent               | ⚠️ Soms afwijkende offsets of verouderd gedrag   |

### ✅ Aanbeveling

Gebruik `zoneinfo.ZoneInfo` voor alle tijdzone-handling in moderne Python-projecten. Beperk `dateutil` tot parsing of legacy compatibiliteit.

Voorbeeld:

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from dateutil import tz

# ZoneInfo (aanbevolen)
dt_zoneinfo = datetime(2025, 7, 24, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))

# dateutil (alleen indien nodig)
dt_dateutil = datetime(2025, 7, 24, 12, 0, tzinfo=tz.gettz("Europe/Amsterdam"))
```

