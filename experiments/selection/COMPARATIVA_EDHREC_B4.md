# Comparativa: nuestros mazos vs average decks EDHREC Bracket 4

Fecha: 2026-07-14. Script reproducible: `experiments/selection/compare_edhrec.py`
(re-ejecutable sin red con la caché de `data/cache/edhrec_avg/` poblada).
Todos los números de este informe salen de ese script.

## Metodología y fuentes

- **Endpoint real**: `https://json.edhrec.com/pages/average-decks/<slug>/optimized.json`.
  EDHREC no expone `bracket-4.json` (403); nombra los brackets: 1=exhibition,
  2=core, 3=upgraded, **4=optimized**, 5=cedh. El header del JSON confirma
  "Average Deck for <comandante> - Optimized", así que **sí es el filtro
  Bracket 4 real**, no una aproximación. También existen
  `average-decks/<slug>.json` (sin filtro) y `commanders/<slug>/optimized.json`.
- 11 peticiones en total (7 de sondeo + 4 descargas), espaciadas >=0.7s,
  User-Agent `commander-deckbuilder/0.1`, todo cacheado.
- Los 5 average decks reconstruyen exactamente 99 cartas tras quitar el
  comandante, y todas sus cartas resuelven en nuestro pool.
- Composición del average deck calculada con **nuestro** tag store
  (`data/tags/llm_tags.jsonl`) y la misma semántica del selector
  (multicategoría cuenta en todas; carta en store con labels vacíos → bucket
  synergy). Bandas: **midrange para los 5** (el mapeo de arquetipos por
  comandante sigue pendiente, `commanders: {}` en `quotas.yaml`).

### Limitaciones honestas

1. El "average deck" es un agregado estadístico de EDHREC (~72-75 nombres
   distintos + básicas), no un mazo que alguien haya jugado.
2. Categorizar el avg con nuestra rúbrica arrastra sus sesgos: los cantrips
   (Ponder, Opt, Preordain…) y las cartas de protección (Lightning Greaves,
   Teferi's Protection, Heroic Intervention) tienen labels vacíos en el store,
   así que caen en synergy. Las lecturas de `card_draw` y `wincons` del avg
   dicen tanto de la rúbrica como del mazo (solo 64 cartas `wincons` en todo
   el store).
3. Cobertura del store: 0 cartas sin entrada en 4 comandantes, pero **22 sin
   tag en Atraxa** (paquete infect/proliferate reciente: Tekuthal, Ixhel,
   Venerated Rotpriest…) que quedan fuera del conteo por categoría.
4. Conteo de tierras por type_line de la cara frontal; los MDFC con cara de
   tierra (p. ej. Strength of the Harvest en Sythis) cuentan como `lands` para
   el store pero no para el conteo físico — de ahí ±1 en Sythis.
5. El suelo Karsten citado es el de NUESTRO mazo; el avg B4 tiene curva más
   baja, así que su propio suelo sería menor. No es comparable 1:1.

## Tabla resumen

| Comandante | Solape greedy | Solape cpsat | Tierras EDHREC / greedy / cpsat (suelo K) | CMC EDHREC / greedy / cpsat | Categorías del avg fuera de nuestras bandas |
|---|---|---|---|---|---|
| Krenko, Mob Boss | 70.7% | 72.7% | 33 / 36 / 36 (33) | 2.64 / 2.73 / 2.78 | lands↓33, card_draw↓3, removal↑13, board_wipe↓2, wincons↓0, synergy↑**49** |
| Atraxa, Praetors' Voice | 68.7% | 68.7% | 36 / 36 / 39 (36) | 2.97 / 3.21 / 3.18 | card_draw↓4, wincons↓0 |
| Meren of Clan Nel Toth | 62.6% | 63.6% | 32 / 36 / 36 (35) | 2.58 / 3.09 / 3.14 | lands↓32, ramp↑13, card_draw↓5, removal↑**16**, wincons↓0, synergy↑31 |
| Niv-Mizzet, Parun | 66.7% | 67.7% | 33 / 36 / 36 (33) | 2.45 / 2.74 / 2.85 | lands↓33, card_draw↑13, removal↑**19**, wincons↓1 |
| Sythis, Harvest's Hand | 67.7% | 68.7% | 32 / 36 / 36 (34) | 2.42 / 2.72 / 2.75 | lands↓33*, ramp↑**17**, card_draw↓8, board_wipe↓1, synergy↑38 |
| **Media** | **67.3%** | **68.3%** | **+2.8 / +3.4 vs EDHREC** | **+0.29 / +0.33 vs EDHREC** | |

↓/↑ = por debajo/encima de la banda midrange. *Sythis: 32 por type_line, 33 con el MDFC.
Solape = intersección multiconjunto sobre 99 (ambos mazos son 99, el % es simétrico).

## Discordancias concretas (solo-EDHREC de alta inclusión)

Agregadas de los 10 pares mazo↔avg; inclusión de la página del comandante en EDHREC.

**Funcionamiento correcto (banlist del grupo) — no tocar:**

| Carta | Dónde falta | Incl. | Motivo |
|---|---|---|---|
| Rhystic Study | Atraxa, Niv | 29-39% | BANNED |
| Smothering Tithe | Atraxa, Sythis | 22-31% | BANNED |
| Mystic Remora | Niv | 30% | BANNED |
| Demonic / Vampiric / Mystical / Enlightened / Worldly Tutor, Gamble | varios | 15-41% | BANNED (regla tutores genéricos) |

**Discordancias reales (señal para el sistema):**

| Carta | Dónde falta | Incl. | Cat. | Hipótesis |
|---|---|---|---|---|
| Sol Ring | Sythis | **60%** | ramp | synergy EDHREC -0.15 → score bajo; cuota ramp llena con cartas más "temáticas". Está en maybeboard. |
| Assassin's Trophy | Meren | 52% | removal | synergy +0.05; cuota removal llena con removal tribal de más score. Maybeboard. |
| Chaos Warp | Krenko, Niv | 39-52% | removal | synergy -0.09; el removal genérico eficiente pierde contra removal on-theme. |
| Lightning Greaves | Krenko, Atraxa, Meren | 25-49% | (sin cat.: protección) | labels vacíos → bucket synergy, y el techo synergy se llena antes. Maybeboard. |
| Path to Exile | Sythis | 47% | removal | synergy -0.07; mismo patrón anti-staple. Maybeboard. |
| Abrade / Lightning Bolt | Krenko | 29-38% | removal | synergy negativa; fuera incluso del maybeboard. |
| Heroic Intervention / Teferi's Protection | Sythis, Atraxa | 22-37% | (protección) | sin categoría en la rúbrica → synergy → fuera. |
| Beast Within | Meren | 35% | removal | synergy +0.01; cuota llena. |
| Arcane Signet | Sythis | 35% | ramp | synergy -0.22 (¡la más castigada!); ramp lleno de ramp-encantamiento. |
| Grave Pact / Dictate of Erebos | Meren | 25-34% | removal | en maybeboard; cuota removal (max 11) llena y el avg B4 juega 16. |
| Birds of Paradise / Elvish Mystic | Atraxa, Meren, Sythis | 21-33% | ramp | dorks con synergy ≈0 pierden contra ramp de tema; avg B4 los juega casi siempre. |
| Blood Moon | Krenko | 33% | (sin cat.) | labels vacíos → synergy llena. Explícitamente legal en el grupo. |
| Faithless Looting | Krenko, Niv | 36% | (sin cat.) | labels vacíos; la rúbrica no lo ve como card_draw. |
| Tamiyo, Field Researcher + paquete infect | Atraxa | 25-29% | sin tag | no están en el store → bucket synergy sin score de categoría; casi todos en maybeboard. |

## Patrones sistemáticos (5 comandantes)

1. **Sesgo anti-staple del score** (el patrón más gordo): score = synergy +
   inclusion y la synergy de EDHREC es negativa para staples por construcción.
   De las ~15 discordancias reales, 9 tienen synergy ≤ +0.05 con inclusión
   21-60% (Sol Ring, Path, Trophy, Chaos Warp, Abrade, Bolt, Beast Within,
   BoP, Signet). El avg B4 las juega; nosotros las dejamos en maybeboard o fuera.
2. **Curva sistemáticamente más alta**: +0.29 (greedy) / +0.33 (cpsat) de CMC
   medio en los 5/5 mazos; peor en Meren (+0.51/+0.55). Causas visibles: las
   staples baratas de arriba no entran, y el min de wincons mete bombas caras
   que el avg no juega (Rise of the Dark Realms, Liliana Dreadhorde General,
   Elspeth Sun's Champion aparecen en solo-nuestras).
3. **Tierras: nosotros por encima, no por debajo**: +2.8/+3.4 de media. El avg
   B4 juega 32-33 en 4/5 comandantes — por debajo de nuestro min de banda (36)
   y en Meren incluso por debajo de nuestro suelo Karsten (35, con la salvedad
   de curva de la limitación 5). El binding constraint es la banda midrange,
   no Karsten. Además hay problema de CALIDAD: nuestros mazos incluyen
   taplands flojas que el avg B4 nunca juega (Golgari Guildgate, Jungle
   Hollow, Swiftwater Cliffs, Evolving Wilds…) porque el score de tierras casi
   no discrimina.
4. **El techo synergy=28 se queda corto para mazos de tema**: el avg B4 lo
   supera en Krenko (49, ¡+21!), Sythis (38) y Meren (31). En B4 los mazos
   tribales/tema son MÁS temáticos que nuestro midrange, no menos. En cambio
   Atraxa (16) y Niv (22) caben de sobra: es cosa de arquetipo, no del techo
   global.
5. **removal del avg por encima de nuestro max en 3/5** (Krenko 13, Meren 16,
   Niv 19 — la rúbrica cuenta counterspells como removal): la banda [8-11]
   midrange se queda corta para mesas B4 interactivas.
6. **wincons=0-2 en todos los avg** y card_draw "bajo" en 4/5: en gran parte
   artefacto de rúbrica (cantrips y protección sin label, wincons estrechísimo).
   Krenko: ni greedy ni CP-SAT encuentran 1 sola carta `wincons` en rojo-goblin
   (ambos below con 0) — problema de cobertura de tagging, no de selector.
7. Greedy vs CP-SAT casi empatados frente a EDHREC (67.3% vs 68.3% de solape);
   la diferencia entre selectores es mucho menor que la distancia común al avg.

## Recomendaciones accionables (dentro del sistema de cuotas)

1. **Mapear arquetipos por comandante en `quotas.yaml` ya**: con aggro, Krenko
   pasaría a lands [33-36], synergy 35, board_wipe [1-3] — 3 de sus 6
   categorías fuera de banda se arreglan solas (evidencia: tabla resumen).
2. **Corregir el sesgo anti-staple del score**: p. ej. `score = w_s*max(synergy,0)
   + w_i*inclusion` o un suelo por inclusión alta — Sol Ring con 60% de
   inclusión no puede quedarse en el maybeboard por un -0.15 de synergy.
3. **Subir el peso de inclusion en removal/ramp** (o score por-categoría): las 9
   staples perdidas son casi todas de esas dos categorías con synergy ≤ +0.05.
4. **Añadir label de protección a la rúbrica (o subcategoría de synergy)**:
   Lightning Greaves/Heroic Intervention/Teferi's Protection (incl. 22-49%)
   mueren en el bucket synergy sin poder competir.
5. **Techo synergy por arquetipo tribal/tema ~35-40**: el avg B4 de Krenko
   lleva 49 y el de Sythis 38 de synergy; nuestro 28 midrange los capa.
6. **No bajar el min de tierras** (36 vs 33 del avg es decisión defendible y
   Karsten manda), pero **filtrar calidad de tierra** en el relleno: nada de
   gates/taplands sin texto si hay básicas o mejores duales disponibles.
7. **Ampliar cobertura `wincons` del tagging** (64 cartas en 5.101): Krenko
   termina con 0/2 sin que exista candidato posible — es un hueco del store,
   no del selector.
8. **Vigilar la curva en el filler de synergy** (desempate por CMC menor o
   penalización leve): +0.3 CMC sistemático sobre el avg B4 con el mismo
   comandante es señal de goodstuff caro entrando de relleno.

Nada de esto implica copiar el average deck: los solo-nuestras incluyen
decisiones correctas que EDHREC no puede tomar (Bayou/duals originales porque
jugamos proxies, cartas baneadas fuera, Karsten como suelo duro).
