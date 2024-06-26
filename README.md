# Frank Energie Custom Component voor Home Assistant
Middels deze integratie wordt de huidige prijsinformatie van Frank Energie beschikbaar gemaakt binnen Home Assistant.

De waarden van de prijssensoren kunnen bijvoorbeeld gebruikt worden om apparatuur te schakelen op basis van de huidige energieprijs.

## Installatie
Plaats de map `frank_energie` uit de map `custom_components` binnen deze repo in de `custom_components` map van je Home Assistant installatie.

### HACS
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Installatie via HACS is mogelijk door deze repository toe te voegen als [custom repository](https://hacs.xyz/docs/faq/custom_repositories) met de categorie 'Integratie'.

### Configuratie

De plugin en sensoren worden per stuk geconfigureerd in `configuration.yaml`.

```
  - platform: Frank Energie
    display_options:
      - elec_market
      - elec_tax
      - elec_markup
      - elec_min
      - elec_max
      - elec_avg
      - elec_lasthour
      - elec_nexthour
#      - elec_avg24
#      - elec_avg48
#      - elec_avg72
      - elec_avg_tax
      - elec_avg_market
      - elec_hourcount
      - elec_vat
      - elec_sourcing
      - elec_tax_only
      - elec_upcoming_min
      - elec_upcoming_max
      - elec_tomorrow_min
      - elec_tomorrow_max
      - elec_tomorrow_avg
      - elec_tomorrow_avg_tax
      - elec_tomorrow_avg_market
      - elec_upcoming_avg_market
      - gas_hourcount
      - gas_market
      - gas_tax
      - gas_tax_vat
      - gas_tax_only
      - gas_sourcing
      - gas_markup
      - gas_markup_before6am
      - gas_markup_after6am
      - gas_avg
      - gas_min
      - gas_max
      - gas_tomorrow_avg
```
