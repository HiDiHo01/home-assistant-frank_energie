name: 📋 Backlog Item
description: Voeg een nieuw item toe aan de backlog
title: "[Backlog] "
labels: ["type: backlog"]
projects: ["HiDiHo01/Frank-Energie-Project"]  # Pas dit aan met jouw projectnaam
assignees: []

body:
  - type: textarea
    id: description
    attributes:
      label: Beschrijving
      description: Beschrijf wat er moet gebeuren of verbeterd moet worden.
    validations:
      required: true

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
      options:
        - 🔴 Hoog
        - 🟠 Middel
        - 🟢 Laag
    validations:
      required: false
