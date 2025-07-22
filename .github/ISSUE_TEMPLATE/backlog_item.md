name: 📋 Backlog Item
description: Voeg een nieuw item toe aan de backlog
title: "[Backlog] "
labels: ["type: backlog"]
projects: ["HiDiHo01/2"]
assignees: []

body:
  - type: textarea
    id: description
    attributes:
      label: Beschrijving
      description: Geef een heldere beschrijving van wat er moet gebeuren.
    validations:
      required: true

  - type: textarea
    id: context
    attributes:
      label: Context of motivatie
      description: Waarom is dit item nodig? Wat is het probleem of de aanleiding?
    validations:
      required: false

  - type: textarea
    id: acceptance
    attributes:
      label: Acceptatiecriteria
      description: Wat moet er minimaal werken of aanwezig zijn?
    validations:
      required: false

  - type: checkboxes
    id: criteria
    attributes:
      label: Acceptatiecriteria
      options:
        - label: Duidelijke doelstelling geformuleerd
        - label: Eventuele afhankelijkheden benoemd
        - label: Technische haalbaarheid beoordeeld
        - label: Testbaar gedefinieerd
    validations:
      required: false

  - type: dropdown
    id: priority
    attributes:
      label: Prioriteit
      description: Selecteer de prioriteit
      options:
        - 🔴 Hoog
        - 🟠 Middel
        - 🟢 Laag
    validations:
      required: false
