[![latest version](https://img.shields.io/github/tag/HiDiHo01/home-assistant-frank_energie?include_prereleases=&sort=semver&label=Versie)](https://github.com/HiDiHo01/home-assistant-frank_energie/releases/)
![installations](https://img.shields.io/badge/dynamic/json?label=Installaties&query=%24.frank_energie.total&url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Support-orange)](https://buymeacoffee.com/royarx)
[![PayPal](https://img.shields.io/badge/PayPal-Donate-blue)](https://www.paypal.me/royarx)

# Frank Energie Custom Component voor Home Assistant
Middels deze integratie wordt de huidige prijsinformatie van Frank Energie beschikbaar gemaakt binnen Home Assistant.

De waarden van de prijssensoren kunnen bijvoorbeeld gebruikt worden om apparatuur te schakelen op basis van de huidige energieprijs.

## Documentation

- [Entities](docs/entities.md) - Overview of all entity platforms and features.
- [Events](docs/events.md) - Home Assistant events fired by the Frank Energie integration.
- [Smart Charging](docs/smart_charging.md) - Smart Charging features and troubleshooting.
- [Troubleshooting](docs/troubleshooting.md) - Common issues and expected behavior.

# Services

The Frank Energie integration does not currently register any Home Assistant services.

The integration exposes functionality through entities, events, buttons, selects, and sensors.

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

![Apex graph voorbeeld 1](/images/48%20uur%20per%20kwartier.png "Voorbeeld 1")

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
#### Voorbeeld 3 - Vandaag en morgen (conditional)

![Apex graph voorbeeld 3](/images/02dd4de4d8ec3260b3633d70933be667.png "Voorbeeld 3")

```
type: custom:apexcharts-card
graph_span: 24h
span:
  start: day
now:
  show: true
  label: Nu
  color: darkblue
header:
  show: true
  title: Energieprijs per kwartier (€/kWh) vandaag
series:
  - entity: sensor.frank_energie_prijzen_gemiddelde_elektriciteitsprijs_vandaag_all_in
    name: Prijs
    stroke_width: 0
    float_precision: 3
    type: column
    opacity: 1
    color: "#44739e"
    color_threshold:
      - value: 0
        color: rgb(255,255,255)
      - value: 0.05
        color: rgb(240,240,255)
      - value: 0.1
        color: rgb(225,225,255)
      - value: 0.11
        color: rgb(200,200,255)
      - value: 0.12
        color: rgb(175,175,255)
      - value: 0.13
        color: rgb(150,150,255)
      - value: 0.14
        color: rgb(125,125,255)
      - value: 0.15
        color: rgb(100,100,255)
      - value: 0.16
        color: rgb(75,75,255)
      - value: 0.17
        color: rgb(50,50,255)
      - value: 0.18
        color: rgb(25,25,225)
      - value: 0.19
        color: rgb(0,0,255)
      - value: 0.2
        color: "#aaff66"
      - value: 0.22
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
```

```
type: conditional
conditions:
  - condition: state
    entity: sensor.frank_energie_prijzen_gemiddelde_elektriciteitsprijs_morgen_all_in
    state_not: unavailable
card:
  type: custom:apexcharts-card
  graph_span: 24h
  span:
    start: day
    offset: +24h
  header:
    show: true
    title: Energieprijs per kwartier (€/kWh) morgen
  series:
    - entity: >-
        sensor.frank_energie_prijzen_gemiddelde_elektriciteitsprijs_morgen_all_in
      name: Prijs
      stroke_width: 0
      float_precision: 3
      type: column
      opacity: 1
      color: "#44739e"
      color_threshold:
        - value: 0
          color: rgb(255,255,255)
        - value: 0.05
          color: rgb(240,240,255)
        - value: 0.1
          color: rgb(225,225,255)
        - value: 0.11
          color: rgb(200,200,255)
        - value: 0.12
          color: rgb(175,175,255)
        - value: 0.13
          color: rgb(150,150,255)
        - value: 0.14
          color: rgb(125,125,255)
        - value: 0.15
          color: rgb(100,100,255)
        - value: 0.16
          color: rgb(75,75,255)
        - value: 0.17
          color: rgb(50,50,255)
        - value: 0.18
          color: rgb(25,25,225)
        - value: 0.19
          color: rgb(0,0,255)
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
        const tomorrowPrices = entity.attributes.tomorrow_prices;
        return tomorrowPrices
          ? tomorrowPrices.map((record, index) => [record.from, record.price])
        : [0];
  experimental:
    color_threshold: true
```

#### Voorbeeld 4 - Kosten per maand dit jaar

![Apex graph voorbeeld 4](/images/kosten%20per%20maand%20dit%20jaar.png "Voorbeeld 4")

```
type: custom:apexcharts-card
graph_span: 11month
span:
  start: year
header:
  show: true
  title: Kosten per maand dit jaar
now:
  show: true
  color: darkblue
  label: Nu
series:
  - entity: sensor.frank_energie_kosten_kosten_dit_jaar
    name: Kosten
    stroke_width: 0
    float_precision: 2
    show:
      header_color_threshold: false
      extremas: true
    type: column
    opacity: 0.7
    color: "#44739e"
    color_threshold:
      - value: 0
        color: rgb(67,255,71)
      - value: 10
        color: rgb(67,220,71)
      - value: 20
        color: rgb(67,190,71)
      - value: 30
        color: rgb(67,160,71)
      - value: 40
        color: rgb(67,130,71)
      - value: 50
        color: rgb(67,100,71)
      - value: 60
        color: darkorange
      - value: 80
        color: orangered
      - value: 100
        color: red
      - value: 125
        color: darkred
    data_generator: >
      const Invoices = entity.attributes.Invoices;

      if (typeof Invoices !== 'object' || Invoices === null) {
        console.error('Invoices attribute is not an object:', Invoices);
        return [0];
      }

      //console.log('Invoices:', Invoices);
      // Transform the object into an array of objects

      const invoicesArray = Object.entries(Invoices).map(([periodDescription,
      data]) => ({
        period_description: data['Period description'],
        start_date: data['Start date'],
        total_amount: data['Total amount'] || 0
      }));

      //console.log('Invoices Array:', invoicesArray);

      const data = invoicesArray.map((record) => [record.start_date, 
      record.total_amount, record.period_description]);

      //console.log('Data:', data);

      return data;
experimental:
  color_threshold: true
```

#### Voorbeeld 5 - Kosten per maand vorig jaar

![Apex graph voorbeeld 5](/images/kosten%20per%20maand%20vorig%20jaar.png "Voorbeeld 5")

```
type: custom:apexcharts-card
graph_span: 11months
span:
  start: year
  offset: "-12months"
header:
  show: true
  title: Kosten per maand vorig jaar
series:
  - entity: sensor.frank_energie_kosten_kosten_vorig_jaar
    name: Kosten
    stroke_width: 0
    float_precision: 2
    show:
      header_color_threshold: false
      extremas: true
    type: column
    opacity: 0.7
    color: "#44739e"
    color_threshold:
      - value: 0
        color: "#44739e"
      - value: 20
        color: green
      - value: 40
        color: darkgreen
      - value: 60
        color: darkorange
      - value: 80
        color: orangered
      - value: 100
        color: darkred
    data_generator: >
      const Invoices = entity.attributes.Invoices;

      if (typeof Invoices !== 'object' || Invoices === null) {
        console.error('Invoices attribute is not an object:', Invoices);
        return [0];
      }

      //console.log('Invoices:', Invoices);
      // Transform the object into an array of objects

      const invoicesArray = Object.entries(Invoices).map(([periodDescription,
      data]) => ({
        period_description: data['Period description'],
        start_date: data['Start date'],
        total_amount: data['Total amount'] || 0
      }));

      //console.log('Invoices Array:', invoicesArray);

      const data = invoicesArray.map((record) => [record.start_date, 
      record.total_amount, record.period_description]);

      //console.log('Data:', data);

      return data;
experimental:
  color_threshold: true

```

#### Voorbeeld 1 - Laagste en hoogste prijs vandaag

![markdown voorbeeld 5](/images/vandaag%20laagste%20hoogste.png "Voorbeeld 1")

```
type: markdown
content: >-
  <h1><ha-alert title="Frank Energie"></h1><h2>Vandaag is de prijs van stroom
  per kWh

  het laagst tussen
  {{as_timestamp(state_attr('sensor.frank_energie_prijzen_laagste_elektriciteitsprijs_vandaag_all_in',
  'from_time'))|timestamp_custom(' %H:%M')|replace(" 0", "")}} en
  {{(as_timestamp(state_attr('sensor.frank_energie_prijzen_laagste_elektriciteitsprijs_vandaag_all_in',
  'till_time')))|timestamp_custom(' %H:%M')|replace(" 0", "")}} (€
  {{states('sensor.frank_energie_prijzen_laagste_elektriciteitsprijs_vandaag_all_in')|round(3)}})
  en

  het hoogst tussen
  {{as_timestamp(state_attr('sensor.frank_energie_prijzen_hoogste_elektriciteitsprijs_vandaag_all_in',
  'from_time'))|timestamp_custom(' %H:%M')|replace(" 0", "")}} en
  {{(as_timestamp(state_attr('sensor.frank_energie_prijzen_hoogste_elektriciteitsprijs_vandaag_all_in',
  'till_time')))|timestamp_custom(' %H:%M')|replace(" 0", "")}} (€
  {{states('sensor.frank_energie_prijzen_hoogste_elektriciteitsprijs_vandaag_all_in')|round(3)}}).

  </h2></ha-alert>
```
