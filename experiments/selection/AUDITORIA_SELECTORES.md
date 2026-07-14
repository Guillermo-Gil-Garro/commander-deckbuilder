# Auditoría de decisión Fase 3 — greedy vs CP-SAT

**Fecha:** 2026-07-15 · **Script reproducible:** `experiments/selection/audit_selectors.py`

```
backend/.venv/Scripts/python.exe experiments/selection/audit_selectors.py
```

Compara los 5 pares de mazos (`decks/` vs `decks_cpsat/`) parseando los .txt que vio Guille
y cruzando con el pool (`data/processed/cards.jsonl`). Las fuentes de color se cuentan con la
misma heurística que usa el propio CP-SAT (`_produced_colors`), para medir a ambos con la misma vara.

---

## 1. Tabla de solape

| Comandante | Solape (de 99) | Solo-greedy | Solo-cpsat | Δscore excl. (c−g) | Score total (g / c) | CMC medio (g / c) | Preferencia Guille |
|---|---|---|---|---|---|---|---|
| Krenko (aggro tribal) | **96** | 2 | 3 | +0.67 | 65.58 / 66.25 | 2.68 / 2.80 | greedy |
| Meren (graveyard) | **91** | 8 | 8 | +0.66 | 71.90 / 72.56 | 2.85 / 2.80 | greedy |
| Niv-Mizzet (spellslinger) | **97** | 2 | 2 | +0.34 | 56.34 / 56.68 | 2.70 / 2.68 | cpsat |
| Omnath (lands_matter 4c) | **95** | 4 | 4 | +0.62 | 70.97 / 71.59 | 3.53 / 3.41 | cpsat |
| Sythis (enchantress) | **95** | 4 | 4 | +0.81 | 79.55 / 80.36 | 2.42 / 2.38 | indeciso |

Lecturas transversales:

- **Solape medio ~95/99.** Los dos selectores producen esencialmente el mismo mazo; la decisión
  se juega en 2–8 cartas por comandante (<1.5% del score total).
- CP-SAT gana en score total **siempre** (es el óptimo por construcción), pero por márgenes de
  +0.34 a +0.81 sobre totales de 56–80. El score medio por carta del mainboard es idéntico a dos
  decimales en los 5 pares.
- Velocidad: greedy <0.01s, CP-SAT 0.01–0.04s, siempre `OPTIMAL` y **sin relajar ninguna cuota**
  (`etapa de relajación: none` en los 5). Confirmado: la velocidad no discrimina.
- Manabase: mismo nº de no-básicas (±1). En Meren y Sythis el greedy acaba con **más fuentes de
  color** (Meren B19/G18 vs B18/G16; Sythis G20 vs G18) porque CP-SAT cambia duales por tierras
  utility incoloras — pese a que CP-SAT es el único que tiene penalty de fixing. El penalty
  actual pesa tan poco frente al score (Δobjetivo 0.14–0.65) que no muerde.

## 2. El hallazgo estructural: tierras multicategoría y semántica de cuotas

Casi toda la diferencia sistemática entre los dos selectores se reduce a **un solo mecanismo**:

**Greedy rellena las cuotas de hechizos primero y las tierras al final.** Cuando llega la fase de
tierras, una tierra etiquetada `[lands/ramp]` ya no cabe si ramp está al máximo — y ramp SIEMPRE
llega al máximo porque el relleno por score lo satura antes. Resultado: greedy deja fuera tierras
de alto score como **Serra's Sanctum (0.94)** en Sythis o **Cabal Coffers (0.42)** en Meren,
bloqueadas por hechizos de ramp de 0.42–0.62. Encima el maybeboard las justifica con
"fuera por score 0.94", que es literalmente falso (fue la cuota de ramp) — fallo de explicabilidad.

**CP-SAT intercambia globalmente:** corta el peor hechizo de ramp y mete la tierra que cuenta doble.
Por eso sus exclusivas son sistemáticamente tierras utility multicategoría: Serra's Sanctum y
Nykthos (Sythis), Cabal Coffers, Grim Backwoods y Westvale Abbey (Meren), Boseiju (Omnath).

Este mecanismo tiene dos caras, y ahí está la clave del patrón de preferencias:

- **Cara buena (Omnath, Sythis, Niv):** la tierra multicategoría es un upgrade real. Boseiju es
  removal en una tierra; Serra's Sanctum es LA tierra de enchantress. Score y calidad coinciden.
- **Cara mala (Meren):** CP-SAT usa la multicategoría para **cumplir cuotas "sobre el papel"**.
  En Meren satisface card_draw con Grim Backwoods (0.19, robo lentísimo) y Disciple of Freyalise,
  y wincons con Westvale Abbey (0.13), lo que le permite cortar **Guardian Project, Morbid
  Opportunist y Rise of the Dark Realms** — robo real y una bomba de verdad — y gastar esos slots
  en 3 fillers de synergy (0.47) hasta clavar el techo (30/30). Numéricamente +0.66; funcionalmente
  el mazo pierde motores y pegada. La cuota se cumple; su espíritu, no.

Segundo hallazgo menor, simétrico: con **hechizos** multicategoría pasa lo contrario. En Krenko,
greedy juega Vandalblast (0.43, `[board_wipe/removal]`, one-sided en un mazo go-wide); CP-SAT lo
descarta porque le consume el max de removal (11/11) y cubre board_wipe con Blasphemous Act (0.39),
simétrico — mata a sus propios goblins. No es una carta absurda en Krenko (se castea barata), pero
es la elección que haría una hoja de cálculo, no un jugador.

Sobre la hipótesis "solo-greedy = temáticas, solo-cpsat = staples": **NO se sostiene como regla.**
En Omnath las exclusivas de CP-SAT son MÁS temáticas (Risen Reef, Loot, Boseiju, Trade Routes son
lands-matter puro), y en Krenko también (Gempalm Incinerator es tribal, Phyrexian Altar es motor de
combo con Krenko). Lo que sí se sostiene es lo anterior: CP-SAT explota la contabilidad de cuotas,
y eso ayuda o duele según el arquetipo.

## 3. Diferencias comentadas por comandante

### Krenko — solape 96/99 · Guille prefirió greedy

| Solo-greedy | Score | Comentario |
|---|---|---|
| Vandalblast | 0.43 | Wipe de artefactos one-sided: no toca tu board. |
| Hazoret's Monument | 0.31 | Ramp + filtrado; discreto. |

| Solo-cpsat | Score | Comentario |
|---|---|---|
| Gempalm Incinerator | 0.53 | Removal tribal (cycling por goblins): muy temática. |
| Phyrexian Altar | 0.49 | Motor de combo clásico de Krenko. |
| Blasphemous Act | 0.39 | Wipe simétrico: mata tu propio ejército go-wide. |

**Veredicto honesto:** las exclusivas de CP-SAT son igual o más temáticas; solo Blasphemous Act
chirría (y entró forzada por la contabilidad de removal, no por score). Con 96/99 de solape, la
preferencia por greedy aquí **no tiene una explicación estructural sólida** — candidata a ruido,
o al efecto óptico de un wipe simétrico en la lista.

### Meren — solape 91/99 (la mayor divergencia) · Guille prefirió greedy

| Solo-greedy | Score | Comentario |
|---|---|---|
| Wood Elves | 0.48 | Ramp reanimable: exactamente lo que Meren quiere sacrificar. |
| Agadeem's Awakening | 0.29 | MDFC: tierra que es reanimación masiva. |
| Golgari Rot Farm / Necroblossom Snarl | 0.27 | Duales que fijan color (por eso greedy acaba con más fuentes). |
| Guardian Project | 0.24 | Motor de robo REAL en un mazo de criaturas. |
| Morbid Opportunist | 0.23 | Robo temático (muertes). |
| Rise of the Dark Realms | 0.12 | LA bomba de graveyard: reanima todo. |

| Solo-cpsat | Score | Comentario |
|---|---|---|
| Necromancy / Syr Konrad / Twilight Diviner | 0.47 | Fillers de synergy temáticos y buenos (para clavar el techo 30/30). |
| Cabal Coffers | 0.42 | Tierra-ramp: buen hallazgo multicategoría. |
| Lightning Greaves | 0.42 | Única carta de protection (cumple el min [1,3] que greedy viola: 0). |
| Disciple of Freyalise / Grim Backwoods | 0.25/0.19 | "Robo" en tierra: cumple la cuota de card_draw sobre el papel. |
| Westvale Abbey | 0.13 | "Wincon" en tierra: ídem con wincons. |

**Veredicto:** aquí el patrón SÍ se explica. CP-SAT convirtió robo real y una bomba en tierras que
computan técnicamente, y de propina perdió 3 fuentes de color. Y el dato clave de la tensión que
había que explorar: CP-SAT es el único que cumple protection (Greaves), pero eso es 1 carta contra
3 motores percibidos — **el cumplimiento formal de la cuota costó calidad percibida**. La cuota de
protection, además, greedy ni siquiera la rellena activamente (no está en su `FILL_ORDER`; se cumple
de rebote o no se cumple, como aquí).

### Niv-Mizzet — solape 97/99 · Guille prefirió cpsat

Solo-greedy: Flame of Anor (0.42), Fabled Passage (0.20 — fetch mediocre en bicolor sin landfall).
Solo-cpsat: **Pongify (0.61)** — removal a 1 maná que además dispara a Niv — y **Valakut Awakening
(0.35)** — MDFC tierra/robo, cero coste de oportunidad en spellslinger. Las de CP-SAT son
simplemente mejores cartas, y en spellslinger "staple instantáneo" ES la temática. Único par con
penalty de fixing = 0. Preferencia coherente con la auditoría.

### Omnath — solape 95/99 · Guille prefirió cpsat

Solo-greedy: Beast Within (0.45), Uro (0.37), Cultivator Colossus (0.30, cmc 7), Dragonback
Assault (0.25, cmc 6). Solo-cpsat: **Boseiju (0.60, tierra-removal), Risen Reef (0.59, elemental
lands-matter puro), Loot (0.59), Trade Routes (0.21)**. Las exclusivas de CP-SAT son a la vez más
baratas (CMC 2.67 vs 4.75), de más score y MÁS temáticas: en lands_matter el sesgo de CP-SAT hacia
tierras utility multicategoría apunta directamente al tema. Preferencia coherente.

### Sythis — solape 95/99 · Guille indeciso

Solo-greedy: Abundant Growth (0.62) y Mirari's Wake (0.61) — encantamientos, disparan a Sythis —
más 2 tierras flojas (0.20). Solo-cpsat: **Serra's Sanctum (0.94)** y Nykthos vía el intercambio
global, más Grand Abolisher y Mirri's Guile. Cada lado tiene un argumento real (triggers de la
comandante vs la mejor tierra del arquetipo): la indecisión es exactamente lo que la estructura
predice.

## 4. ¿Se explica el patrón de preferencias?

**Parcialmente sí, y donde sí, con mecanismo claro:**

- **cpsat en Niv y Omnath:** en spellslinger y lands_matter, el "goodstuff" y las tierras utility
  SON el tema — el score es un proxy fiel de calidad y el optimizador global gana limpio. ✔
- **greedy en Meren:** CP-SAT jugó la contabilidad de cuotas (tierras 0.13–0.25 como robo/wincon a
  cambio de motores reales). En un mazo de motor de criaturas el score es un proxy con pérdidas, y
  optimizarlo al límite degrada lo que un jugador percibe. ✔ — este es el caso con más señal
  (91/99, 8+8 exclusivas).
- **Sythis indeciso:** ambos efectos presentes y equilibrados. ✔
- **greedy en Krenko:** ✘ no se sostiene — las exclusivas de CP-SAT son igual de temáticas y de más
  score; la única mancha es Blasphemous Act. Con 96/99 de solape, 2-3 cartas de diferencia, lo más
  honesto es tratarla como ruido u óptica de una sola carta.

Es decir: no es "greedy entiende el tema y CP-SAT no". Es "CP-SAT exprime la semántica de las
cuotas multicategoría, y eso es un upgrade cuando la tierra utility es temática (Omnath/Sythis) y
un downgrade cuando sustituye hechizos de verdad por tecnicismos (Meren)". ⚠️ revisar: con n=5 y
diferencias de 2–8 cartas, cualquier teoría sobre las preferencias va justa de datos; la de Meren
es la única con mecanismo fuerte detrás.

## 5. Opciones

### A. Greedy
- **Pros:** razones por carta narrativas ("cuota", "relleno", "tierra recomendada"); nunca juega
  con la semántica de cuotas; manabase con algo más de fixing; trivial de mantener.
- **Contras:** el bloqueo por orden de fases es **arquitectónico** (deja fuera Serra's Sanctum con
  0.94 — indefendible ante el usuario); no rellena `protection` (viola el min en Meren); razones
  de maybeboard a veces falsas ("fuera por score" cuando fue una cuota); cada mejora acaba
  reinventando un solver a parches.

### B. CP-SAT tal cual
- **Pros:** óptimo garantizado, cumple TODAS las cuotas sin relajar (protection incluida), informa
  de relajaciones y déficits de fixing, encuentra las tierras multicategoría buenas.
- **Contras:** cumple cuotas sobre el papel con tierras marginales (Meren); el penalty de fixing no
  muerde (acaba con MENOS fuentes que greedy); razón por carta pobre ("cp-sat, score X").

### C. Híbrido por arquetipo (greedy en tribal/graveyard, cpsat en el resto)
- **Pros:** replica las preferencias observadas tal cual.
- **Contras:** dos motores que mantener, testear y explicar para siempre; la frontera es arbitraria
  (¿aristocrats es graveyard o no?); se apoya en n=5 donde al menos un caso (Krenko) parece ruido.
  Sobreingeniería para ~4 cartas de diferencia.

### D. CP-SAT con ajustes dirigidos (por esta auditoría)
- **Pros:** un solo motor; corrige exactamente los modos de fallo observados con restricciones, no
  con heurística nueva:
  1. **Los mínimos de cuotas de hechizos (card_draw, wincons, board_wipe, removal) deben cubrirse
     con no-tierras**; las tierras multicategoría pueden seguir contando hacia el max (mantiene
     Boseiju/Sanctum, mata el truco de Grim Backwoods/Westvale). Esto es una restricción lineal
     trivial en el modelo.
  2. **Recalibrar el peso del penalty de fixing** (dato objetivo: hoy CP-SAT acaba peor fijado que
     greedy pese al penalty).
  3. **Razones post-hoc estilo greedy** al formatear ("cubre cuota X", "relleno por score", "tierra
     multicategoría: cuenta en X"): la explicabilidad es del formateador, no del algoritmo.
- **Contras:** 2-3 knobs más que calibrar; requiere un segundo eyeball de Guille (Meren y Krenko)
  para validar que el ajuste 1 cierra la brecha.
- Nota: el "bonus de sinergia en arquetipos temáticos" que estaba sobre la mesa **no ataca el
  problema real** — en Meren CP-SAT ya clava el techo de synergy (30/30) y sus fillers son
  temáticos; lo que duele es la calidad de los slots de cuota, no la cantidad de sinergia.

## 6. Recomendación

**Opción D: CP-SAT como motor único, con los tres ajustes dirigidos.** La razón de fondo es una
asimetría: los defectos observados en CP-SAT (cuotas cumplidas con tierras marginales, fixing
blando) se corrigen con una restricción y un peso — son parametrizables; el defecto de greedy
(el orden de fases que deja fuera a Serra's Sanctum, y una cuota de protection que ni intenta
rellenar) es arquitectónico y solo se arregla convirtiéndolo en un solver. Con un solape del 95%
la calidad base es la misma, la velocidad es idéntica en la práctica (≤0.04s, `OPTIMAL` sin
relajar en los 5), y la explicabilidad se iguala generando las razones en el formateador — que es
donde greedy la tenía ganada, no en el algoritmo. Validación propuesta antes de cerrar en
`DECISIONS.md`: aplicar el ajuste 1, regenerar Meren y Krenko, y que Guille re-compare esos dos
pares (los únicos donde prefirió greedy); si la brecha percibida se cierra, decisión tomada.
