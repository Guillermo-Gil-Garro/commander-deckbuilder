# Rúbrica de producción — etiquetado funcional LLM

**Versión: v4** (2026-07-18). Cambios de v4: nueva categoría `stax` (prisión /
negación de recursos; decisión de Guille 2026-07-18, para que los mazos de
prisión —Winter, Thalia+Gitrog, Grand Arbiter, Kefka— cuenten sus piezas en
lugar de que caigan en `synergy`/`none`). El resto de categorías no cambia
respecto a v3. Las etiquetas generadas con esta rúbrica llevan
`rubric_version: "v4"` en `data/tags/llm_tags.jsonl`; las entradas v2/v3
existentes siguen siendo válidas (solo les falta evaluar `stax`).

Historial: v3 (2026-07-14) añadió `protection` (recomendada por el informe
`experiments/selection/COMPARATIVA_EDHREC_B4.md`). v2 (2026-07-13) unificó el
criterio MDFC con el ground truth (aprobado por Guille), reforzó las wincons
implícitas y resolvió los casos frontera de `llm_notes.md`. v1 implícita:
`experiments/tagging/methods/llm_rubric.md`.

Vocabulario cerrado: `lands`, `ramp`, `card_draw`, `removal`, `board_wipe`,
`wincons`, `protection`, `stax`, `synergy`. Multi-etiqueta permitida; lista
vacía = none. Principio rector: la etiqueta refleja para qué mete un jugador la
carta en el mazo; no se etiquetan efectos marginales.

## lands

- Cualquier carta con cara de tierra jugable desde la mano: tierras normales y
  MDFC hechizo // tierra (Spikefield Hazard, Glasspool Mimic...). **[v2,
  criterio unificado]** Las caras de tierra de una MDFC hechizo//tierra SÍ
  cuentan como `lands`; la cara de hechizo se evalúa aparte y suma sus propias
  etiquetas (Spikefield Hazard = lands|removal).
- NO cuenta una cara trasera de tierra a la que solo se llega por transformación
  (Ojer Pakpatiq // Temple of Cyclical Time no es `lands`: no puedes jugarla como tierra).
- Fetches y tierras utility son `lands`; si además rampan de verdad
  (Blighted Woodland: sacrifica y sube 2 básicas = +1 tierra neta) añaden `ramp`.
  Reemplazarse a sí misma sin ganancia neta (Flagstones of Trokair) no es `ramp`.

## ramp

- Sí: cualquier permanente con habilidad de maná repetible — rocas (aunque sean
  filtros ineficientes: Mana Cylix, Celestial Prism), dorks y criaturas con tap de
  maná (incl. condicionados: Endrider Catalyzer, Nardole), habilidades de maná con
  restricción de gasto (Fabrication Foundry, Ronin), y triggers recurrentes de maná
  (Hulking Raptor). También motores de maná activados no-tap (Skirge Familiar).
  Un drawback fuerte no quita el ramp (Witch Engine).
- Sí: hechizos/permanentes que ponen tierras al campo o generan tierras extra
  (Cultivate-likes, Patron of the Moon, Spelunking, la parte de búsqueda de
  Proctor's Gaze).
- Sí: generación repetible de Treasures (Malcolm, Rev) o de tokens de maná
  permanentes aunque sea one-shot (Powerstone Engineer, Static Net): dejan una
  fuente de maná en mesa.
- Sí: reductores de coste genéricos y amplios cuya función principal es acelerar
  (ciclo Medallion/Monument, reductor de artefactos tipo Voyager Quickwelder).
- No: un único Treasure/Food one-shot como rider (Ant-Man's Army): efecto marginal.
- No: maná one-shot tipo ritual, incluidos los exhaust de un solo uso
  (el exhaust de Loot: 3 manás una sola vez).
- No: reductores de coste ligados a un arquetipo estrecho (Hero of Iroas con
  Auras → evaluar como synergy/none). La frontera con los Medallion es la
  amplitud de la clase reducida.
- No: producción de maná que en la práctica es autoprotección u otra función
  (Hydro-Man: solo produce en turnos ajenos mientras está animado → none).

## card_draw

- Sí: robo en ráfaga de 2+ cartas (Conch Horn, draw-3 de Loot).
- Sí: motores repetibles de robo aunque tengan condición de arquetipo razonable
  (Phyrexian Arena-likes, The Immortal Sun, Champions from Beyond: robo
  condicionado a atacar con 4+ es motor real en un mazo go-wide).
- Sí: looting/rummaging REPETIBLE (robar+descartar como motor: Book Devourer,
  wheels tipo Sensation Gorger). El filtrado repetido cuenta como ventaja.
- Sí: ventaja de cartas repetible vía exilio-y-juega ("impulse") propio o robado
  (Rev, Tithe Extractor).
- No: cantrip one-shot de 1 carta pegado a otro efecto ("cuando entra, roba una
  carta": Nylea's Presence, Spelunking) — se reemplaza, no genera ventaja.
- No: cycling, learn (en Commander = loot one-shot), un único Clue/investigate,
  ni selección de 1 entre varias molidas (Picklock Prankster ≈ cantrip).
- Matiz de paquete: si el robo es real pero condicional a un paquete tribal,
  decide su magnitud — wheel masivo = card_draw|synergy (Sensation Gorger);
  impulse ocasional al morir la tribu = solo synergy (Rundvelt Hordemaster).

## removal (puntual)

- Sí: destruir/exiliar/-X-X/fight/daño dirigido a permanente, un objetivo o "uno
  por jugador" (edicto multijugador: Szat's Will = removal, NO board_wipe).
  Incluye contrahechizos, aunque sean estrechos o condicionales (Syncopate).
- Sí: cualquier hechizo de daño directo que apunte a criatura/planeswalker,
  independientemente de la cifra (First Volley con 1 daño cuenta; regla fija para
  consistencia). En permanentes, la habilidad activada/ETB de removal real también
  cuenta (Vial of Dragonfire, Morkrut Banshee), aunque sea cara
  (Exploding Barrel: sac por daño = removal real, no rider).
- Sí: removal-por-exilio tipo O-Ring mientras esté en mesa (Static Net).
- Sí: bounce dirigido a permanente de un oponente (Proctor's Gaze):
  interacción puntual aunque sea temporal.
- No: tap/stun/debuffs que no eliminan (Rime Chill, Study Break).
- No: daño que solo puede apuntar a jugadores ni pings marginales en triggers.
- No: descarte de mano (Bloodhusk Ritualist) — no hay categoría.
- No: robar el control, copiar o redirigir hechizos (Eriette, Return the Favor):
  interacción que no elimina nada por sí misma.
- No: hate estático (Soulless Jailer) ni triggers de gy-hate.
- No: negación de un turno vía "you lose the game" diferido (Glorious End):
  pieza de combo/desesperación, no removal.

## board_wipe

- Sí: cualquier efecto masivo "destruye/exilia/rebota todos los X" aunque X sea
  una clase acotada (Nature's Ruin: verdes; Acid Rain: Forests; Primeval Light:
  encantamientos de un jugador; Tornado Elemental: 6 daño a voladoras) — regla:
  masivo = afecta a toda una clase sin apuntar uno a uno, aunque el humano
  pueda leerlo como hoser.
- Sí: daño masivo a todas las criaturas aunque sea pequeño pero letal para dorks
  (Caldera Hellion 3 daño) y wipes asimétricos/one-sided (Plague Wind) o
  repetibles (Serenity).
- No: pings masivos de 1 daño repetibles como rider (Tibor and Lumia = none:
  cifra marginal, ni wipe ni removal).
- Un edicto de "cada oponente sacrifica UNA criatura" es `removal`, no wipe.

## wincons

- Sí: texto explícito "you win the game" / "target player loses the game" como
  función principal (Happily Ever After, Mechanized Production, Laboratory
  Maniac, Ramses).
- Sí **[v2, refuerzo de wincons implícitas]**: cartas sin texto de victoria que
  son EL plan de victoria de su arquetipo. Criterio operativo: la carta
  establece un reloj alternativo que gana por sí misma dentro de su plan de
  mazo. Cuenta el veneno/infect como plan (Fynn, the Fangbearer =
  wincons|synergy; un infectador eficiente en un mazo de veneno dedicado
  también); cuentan los Craterhoof-like (pump masivo + evasión que cierra en
  el turno). NO cuenta el drenaje/daño genérico repetible por grande que sea
  (Ayara, Herald of Hadar = no wincons): cierra partidas pero no "por sí
  mismo"; es goodstuff de arquetipo.
- No: "loses the game" como ventana condicional de un turno con una amenaza
  bloqueable (Summon: Primal Odin = removal por su capítulo I, no wincons).
- No: "you lose the game" como coste (Glorious End, Chance for Glory) ni cartas
  que evitan perder (Angel's Grace, Lich's Tomb, Everybody Lives!).

## protection **[v3, nueva categoría]**

Para qué la mete un jugador: mantener con vida sus amenazas (típicamente el
comandante) o a sí mismo frente a removal/wipes ajenos.

- Sí: conceder hexproof, shroud, indestructible, protección de color/todo,
  ward o phasing a TUS permanentes o a ti como jugador, sea puntual
  (Heroic Intervention, Tyvar's Stand), repetible (Mother of Runes,
  Giver of Runes) o estático (Asceticism, Shalai, Voice of Plenty,
  Sylvan Safekeeper).
- Sí: free spells de protección (Flawless Maneuver, Deflecting Swat,
  Teferi's Protection) — son el patrón de referencia de la categoría.
- Sí: redirecciones de objetivo que salvan tus cartas (Deflecting Swat,
  Bolt Bend, Ricochet Trap).
- Sí: equipo cuya función es proteger al portador: ciclo Greaves/Boots
  (Lightning Greaves, Swiftfoot Boots) y equivalentes (Champion's Helm,
  Mithril Coat).
- Sí: efectos "phase out" defensivos sobre lo tuyo (Teferi's Time Twist,
  Slip Out the Back).
- No: counterspells — siguen siendo `removal`, aunque protejan de facto.
- No: fogs ni prevención de daño genérica (regla transversal 6 intacta).
- No: la indestructibilidad/hexproof INNATA de la propia carta (Darksteel
  Colossus no es protection: no protege a nada más).
- No: conceder esas habilidades a criaturas AJENAS o simétricamente sin
  control (donaciones tipo Vow no son protection).
- Multi-etiqueta con lo existente como siempre: si además el paquete es
  inequívoco, añade `synergy` (p. ej. protección estrictamente tribal);
  Lightning Greaves en un mazo cualquiera es solo protection.

## stax **[v4, nueva categoría]**

Para qué la mete un jugador: **restringir o negar de forma persistente los
recursos o las acciones de los oponentes** (prisión), rompiendo la paridad a su
favor. El eje es la NEGACIÓN CONTINUA, no la interacción puntual: un efecto de
un solo uso no es stax (es `removal`, `board_wipe`, o nada).

Criterio operativo: el efecto está en mesa (permanente estático/activado) o es
un castigo recurrente, y limita lo que los rivales pueden hacer con maná,
hechizos, destapar, atacar, robar, buscar, ETBs o habilidades.

- Sí: **impuestos** — hechizos o acciones ajenas cuestan más maná o vida
  (Thalia, Guardian of Thraben; Sphere of Resistance; Thorn of Amethyst;
  Trinisphere; Kambal, Consul of Allocation; Esper Sentinel). Si además te da
  recurso, multi-etiqueta (Esper Sentinel = stax|card_draw).
- Sí: **negación de destapar / bloqueo de recursos** persistente (Winter Orb,
  Static Orb, Stasis, Rising Waters, Root Maze, Kismet, Blind Obedience,
  Authority of the Consuls: "entran tappeadas").
- Sí: **límite de acciones por turno** (Archon of Emeria, Drannith Magistrate,
  Ethersworn Canonist, Eidolon of Rhetoric, Rule of Law, Deafening Silence,
  Teferi, Time Raveler: solo a velocidad de conjuro).
- Sí: **negación de robar / buscar** ajenas (Aven Mindcensor, Leonin Arbiter,
  Opposition Agent, Stranglehold, Ashiok Dream Render, Narset Parter of Veils,
  Hullbreacher, Notion Thief). Si roban ellos también → stax|card_draw.
- Sí: **negación de ETBs / triggers** (Torpor Orb, Hushbringer, Tocatli Honor
  Guard) y de **habilidades activadas** (Cursed Totem, Linvala Keeper of
  Silence, Damping Sphere, Pithing Needle, Phyrexian Revoker).
- Sí: **negación de maná** amplia o simétrica-que-rompes: Blood Moon, Magus of
  the Moon, Back to Basics, Contamination, Overburden; destrucción MASIVA de
  tierras como plan de prisión (Armageddon, Ravages of War, Catastrophe,
  Cataclysm → stax|board_wipe). Un Strip Mine / destrucción de UNA tierra es
  `removal`, no stax.
- Sí: **pillow fort** — "no pueden atacar/bloquear salvo que paguen / a menos
  que…" persistente (Ghostly Prison, Propaganda, Windborn Muse, Norn's Annex,
  Archangel of Tithes, Sphere of Safety, Silent Arbiter, Dueling Grounds).
  Un Fog puntual NO es stax.
- Sí: **motores de sacrificio forzado** recurrentes (Smokestack, Tangle Wire,
  Braids Cabal Minion). Un edicto de un solo uso sigue siendo `removal`; un
  sacrificio masivo de una vez es `board_wipe`.
- No: **contrahechizos** — siguen siendo `removal` (regla transversal), aunque
  nieguen de facto.
- No: efectos que te dan recurso SIN restringir al rival (Rhystic Study,
  Smothering Tithe): el rival paga opcional y tú ganas → `card_draw`/`ramp`,
  no stax.
- No: descarte de mano (sin categoría), fog / prevención de daño (`none`),
  hexproof/protección a lo tuyo (`protection`).
- No por defecto: odio de cementerio estrecho de un solo propósito
  (Grafdigger's Cage, Rest in Peace, Soulless Jailer): es hate de nicho, no
  prisión de recursos amplia. Etiqueta stax solo si además restringe recursos
  generales.
- Multi-etiqueta como siempre: Cataclysm = stax|board_wipe; Esper Sentinel =
  stax|card_draw; una pieza de stax estrictamente tribal añade `synergy`.

## synergy

- Sí: lords y anthems tribales (+X/+X a un tipo: Lord of the Accursed,
  Joraga Warcaller; "red creatures" cuenta como tribu) e
  "elige un tipo" (Etchings of the Chosen, Collective Inferno).
- Sí: piezas que solo funcionan dentro de un paquete inequívoco: payoffs de
  veneno (Persuasive Interrogators, Bloodroot Apothecary, Virulent Silencer),
  paquete de Auras (Hero of Iroas, Eriette), enchantress (Estrid's Invocation:
  sin paquete es carta muerta), tutor estrictamente tribal (Sarkhan's Triumph),
  escalado por conteo tribal (Gempalm Incinerator, Malcolm con Piratas,
  kinship de Sensation Gorger).
- No: cartas que premian un arquetipo pero funcionan como goodstuff standalone
  (Kresh, Ayara: drenaje que funciona con cualquier fodder; Desecrated Tomb)
  ni anthems genéricos sin tribu (Veteran Armorer).
- `synergy` se combina con la etiqueta funcional si ambas son reales
  (Ramses = wincons|synergy; Gempalm = removal|synergy; Fynn = wincons|synergy;
  Malcolm = ramp|synergy).

## Reglas transversales

1. Riders marginales no etiquetan (draw 1 one-shot, un Treasure suelto, ping 1).
2. Ultimates de planeswalker no etiquetan por sí solos (el -6 de Freyalise no da
   card_draw; sus +2 dorks y -2 sí dan ramp|removal).
3. Cartas modales/multicara: etiqueta por cada modo o cara real no marginal;
   una MDFC suma las etiquetas de ambas caras (regla lands de arriba).
4. Habilidad de maná con drawback fuerte sigue siendo ramp (Witch Engine).
5. Tutores no son categoría; solo etiquetan si lo tutelado define paquete
   (Sarkhan's Triumph = synergy) o si ponen tierras al campo (= ramp).
6. Fog/extra turns/group hug sin categoría → none. **[v3]** La protección a
   lo propio ya NO es none: ver `protection`; fogs y prevención de daño
   genérica siguen sin categoría.
7. `none` = lista de labels vacía y va siempre sola.

## Formato de salida de una etiquetadora de lote

Para cada carta del lote (`batches/batch_NNN.jsonl`), una línea JSONL:

```json
{"oracle_id": "...", "name": "...", "labels": ["removal", "synergy"]}
```

- `labels` solo con valores del vocabulario; `[]` para none.
- No inventes campos; `source` y `rubric_version` los pone el merge
  (`tags.store.merge_batch`) con sus defaults (`llm`, `v3`).
- No re-etiquetes cartas fuera de tu lote.
