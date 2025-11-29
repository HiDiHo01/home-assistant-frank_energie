[![latest version](https://img.shields.io/github/tag/HiDiHo01/home-assistant-frank_energie?include_prereleases=&sort=semver&label=Versie)](https://github.com/HiDiHo01/home-assistant-frank_energie/releases/)
![installations](https://img.shields.io/badge/dynamic/json?label=Installaties&query=%24.frank_energie.total&url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json)

# Frank Energie Custom Component voor Home Assistant
Middels deze integratie wordt de huidige prijsinformatie van Frank Energie beschikbaar gemaakt binnen Home Assistant.

De waarden van de prijssensoren kunnen bijvoorbeeld gebruikt worden om apparatuur te schakelen op basis van de huidige energieprijs.

## Installatie
Plaats de map `frank_energie` uit de map `custom_components` binnen deze repo in de `custom_components` map van je Home Assistant installatie.

### HACS
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Installatie via HACS is mogelijk door deze repository toe te voegen als [custom repository](https://hacs.xyz/docs/faq/custom_repositories) met de categorie 'Integratie'.

### Configuratie

<a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=frank_energie" class="my badge" target="_blank">
    <img src="https://my.home-assistant.io/badges/config_flow_start.svg">
</a>

De Frank Energie integratie kan worden toegevoegd via de 'Integraties' pagina in de instellingen.
Vervolgens kunnen sensoren per stuk worden uitgeschakeld of verborgen indien gewenst.

#### Let op!

Indien je deze plugin al gebruikte en hebt ingesteld via `configuration.yaml` dien je deze instellingen te verwijderen en Frank Energie opnieuw in te stellen middels de config flow zoals hierboven beschreven.

#### Inloggen

Bij het instellen van de integratie wordt de mogelijkheid gegeven in te loggen met je Frank Energie-account. Inloggen is geen vereiste voor werking van deze integratie maar biedt de mogelijkheid om ook klantspecifieke gegevens op te halen. Op dit moment krijg je na inloggen naast de gebruikelijke tariefsensoren ook de beschikking over sensoren voor de verwachte en daadwerkelijke verbruikskosten voor de huidige maand.

### Gebruik

Een aantal sensors hebben een `prices` attribuut die alle bekende prijzen bevat. Dit kan worden gebruikt om zelf met een template nieuwe sensors te maken.

Voorbeeld om de hoogst bekende prijs na het huidige uur te bepalen:
```
{{ state_attr('sensor.current_electricity_price_all_in', 'prices') | selectattr('from', 'gt', now()) | max(attribute='price') }}
```

Laagste prijs vandaag:
```
{{ state_attr('sensor.current_electricity_price_all_in', 'prices') | selectattr('till', 'le', now().replace(hour=23)) | min(attribute='price') }}
```

Laagste prijs in de komende zes uren:
```
{{ state_attr('sensor.current_electricity_price_all_in', 'prices') | selectattr('from', 'gt', now()) | selectattr('till', 'lt', now() + timedelta(hours=6)) | min(attribute='price') }}
```

### Grafiek (voorbeelden)
Middels [apex-card](https://github.com/RomRider/apexcharts-card) is het mogelijk de toekomstige prijzen te plotten:

#### Voorbeeld 1 - Alle data

![Apex graph voorbeeld 1](/images/example_1.png "Voorbeeld 1")

```yaml 
type: custom:apexcharts-card
graph_span: 48h
span:
  start: day
now:
  show: true
  label: Nu
  color: darkblue
header:
  show: true
  title: Energieprijs (€/kWh) voor 48 uur
  show_states: false
  colorize_states: true
series:
  - entity: sensor.frank_energie_prijzen_huidige_elektriciteitsprijs_all_in
    name: Prijs
    show:
      legend_value: false
    stroke_width: 0
    float_precision: 3
    type: column
    opacity: 1
    color: "#44739e"
    color_threshold:
      - value: 0
        color: "#4473ff"
      - value: 0.19
        color: "#4473cf"
      - value: 0.2
        color: "#99ff00"
      - value: 0.25
        color: "#6fff00"
      - value: 0.26
        color: "#1aff00"
      - value: 0.27
        color: "#00ee00"
      - value: 0.28
        color: "#00bb00"
      - value: 0.29
        color: green
      - value: 0.3
        color: "#eaff00"
      - value: 0.31
        color: "#ffff00"
      - value: 0.32
        color: "#ffc40c"
      - value: 0.33
        color: darkorange
      - value: 0.35
        color: orangered
      - value: 0.375
        color: "#ff0000"
      - value: 0.4
        color: "#df0000"
      - value: 0.425
        color: "#af0000"
      - value: 0.45
        color: darkred
    data_generator: |
      return entity.attributes.prices.map((record, index) => {
        return [record.from, record.price];
      });
experimental:
  color_threshold: true
apex_config:
  chart:
    height: 300px
    animations:
      enabled: true
      easing: easeinout
      speed: 2000
      animateGradually:
        enabled: true
        delay: 500
```

#### Voorbeeld 2 - Komende 10 uur

![Apex graph voorbeeld 2](/images/example_2.png "Voorbeeld 2")

```yaml
type: custom:apexcharts-card
graph_span: 14h
span:
  start: hour
  offset: '-3h'
now:
  show: true
  label: Nu
  color: darkblue
header:
  show: true
  title: Energieprijs (€/kWh) voor 10 uur
  show_states: false
  colorize_states: true
series:
  - entity: sensor.frank_energie_prijzen_huidige_elektriciteitsprijs_all_in
    name: Prijs
    show:
      legend_value: false
    stroke_width: 0
    float_precision: 3
    type: column
    opacity: 1
    color: "#44739e"
    color_threshold:
      - value: 0
        color: "#4473ff"
      - value: 0.19
        color: "#4473cf"
      - value: 0.2
        color: "#99ff00"
      - value: 0.25
        color: "#6fff00"
      - value: 0.26
        color: "#1aff00"
      - value: 0.27
        color: "#00ee00"
      - value: 0.28
        color: "#00bb00"
      - value: 0.29
        color: green
      - value: 0.3
        color: "#eaff00"
      - value: 0.31
        color: "#ffff00"
      - value: 0.32
        color: "#ffc40c"
      - value: 0.33
        color: darkorange
      - value: 0.35
        color: orangered
      - value: 0.375
        color: "#ff0000"
      - value: 0.4
        color: "#df0000"
      - value: 0.425
        color: "#af0000"
      - value: 0.45
        color: darkred
    data_generator: |
      return entity.attributes.prices.map((record, index) => {
        return [record.from, record.price];
      });
experimental:
  color_threshold: true
apex_config:
  chart:
    height: 300px
    animations:
      enabled: true
      easing: easeinout
      speed: 2000
      animateGradually:
        enabled: true
        delay: 500
```
