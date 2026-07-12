# Set de test de tagging funcional (Fase 2)

Propuesta de ~200 cartas para etiquetar a mano. Sirve de ground truth para
medir precisión/cobertura de los métodos de tagging candidatos (regex, EDHREC,
Scryfall tagger, LLM). Las etiquetas sugeridas del CSV salen de heurísticas
regex burdas que solo garantizan variedad en el muestreo: **no** son uno de los
métodos del experimento y se esperan errores — corrígelas, no las respetes.

## Cómo etiquetar

Abre `test_set_proposal.csv` y rellena `final_labels` en cada fila:

- Si la sugerencia es correcta, cópiala tal cual.
- Si no, escribe las etiquetas buenas separadas por `|` (ej. `ramp|card_draw`).
- Si la carta no cae en ninguna categoría, escribe `none`.
- En caso de duda, elige lo que harías como jugador y anota la duda en `notes`.

El CSV está ordenado por categoría sugerida y nombre, para revisar en tandas.

## Categorías

- **lands** — es una tierra (cualquiera). Las fetch son `lands`, no tutor.
  Ej.: Command Tower, Evolving Wilds, Urza's Saga (`lands` + lo que aplique).
- **ramp** — acelera maná: rocas, dorks, hechizos que buscan tierras al campo.
  Ej.: Sol Ring, Llanowar Elves, Cultivate.
- **card_draw** — ventaja de cartas repetible o en ráfaga.
  Ej.: Phyrexian Arena, Harmonize, Brainstorm.
- **removal** — interacción puntual, incluidos contrahechizos.
  Ej.: Swords to Plowshares, Beast Within, Counterspell.
- **board_wipe** — removal masivo (simétrico o no).
  Ej.: Wrath of God, Blasphemous Act, Cyclonic Rift (por su modo overloaded).
- **wincons** — carta que cierra la partida por sí misma o es el plan de
  victoria explícito. Ej.: Craterhoof Behemoth, Approach of the Second Sun,
  Torment of Hailfire.
- **synergy** — carta de paquete temático inequívoco (lord tribal, pieza que
  solo tiene sentido dentro de su arquetipo). Ej.: Goblin Warchief, Death
  Baron. Úsala solo si es claramente "de paquete"; el goodstuff genérico no es
  synergy.
- **none** — no encaja en ninguna de las anteriores.

## Regla multi-etiqueta

Una carta puede llevar varias etiquetas si hace varias cosas de verdad
(Commander's Sphere = `ramp|card_draw`; un dork con tap de maná = `ramp`).
No etiquetes efectos marginales: la etiqueta refleja para qué se mete la carta
en un mazo. `none` va siempre sola.

## Regenerar la propuesta

```
backend/.venv/Scripts/python experiments/tagging/build_test_set.py
```

Semilla fija: dos ejecuciones producen el mismo CSV. Excluye cartas baneadas
del grupo (`banlist.yaml`) y tipos no jugables (Stickers/Attractions).
Ojo: regenerar sobrescribe el CSV; no lo hagas con etiquetas a medias.
