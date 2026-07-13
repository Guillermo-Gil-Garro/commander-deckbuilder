# Rúbrica LLM — etiquetado funcional (Método 4)

Rúbrica exacta aplicada para generar `experiments/tagging/predictions/llm.json`.
Base: definiciones de `experiments/tagging/README.md`; donde el README no decide,
se aplican las reglas de frontera de abajo. Multi-etiqueta permitida; lista vacía = none.
Principio rector (del README): la etiqueta refleja para qué mete un jugador la carta
en el mazo; no se etiquetan efectos marginales.

## lands

- Cualquier carta con cara de tierra jugable desde la mano: tierras normales y
  MDFC hechizo // tierra (Spikefield Hazard, Glasspool Mimic...). La cara de
  hechizo se evalúa aparte y suma sus propias etiquetas.
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
- Sí: hechizos/permanentes que ponen tierras al campo o generan tierras extra
  (Cultivate-likes, Patron of the Moon, Spelunking, la parte de búsqueda de
  Proctor's Gaze).
- Sí: generación repetible de Treasures (Malcolm, Stark Industries Executive, Rev)
  o de tokens de maná permanentes aunque sea one-shot (Powerstone Engineer,
  Static Net): dejan una fuente de maná en mesa.
- Sí: reductores de coste genéricos y amplios cuya función principal es acelerar
  (ciclo Medallion/Monument, reductor de artefactos tipo Voyager Quickwelder).
- No: un único Treasure/Food one-shot como rider (Ant-Man's Army, Craft with Pride):
  efecto marginal.
- No: maná one-shot tipo ritual (exhaust de Loot: 3 manás una sola vez).
- No: reductores de coste ligados a un arquetipo estrecho (Hero of Iroas con Auras
  → evaluar como synergy/none).

## card_draw

- Sí: robo en ráfaga de 2+ cartas (Conch Horn, Futurist Forge, draw-3 de Loot).
- Sí: motores repetibles de robo aunque tengan condición de arquetipo razonable
  (Phyrexian Arena-likes, Leinore, Meltstrider Eulogist, Gibbering Barricade,
  Infernal Tribute, Starwinder, Case of the Ransacked Lab, The Immortal Sun).
- Sí: looting/rummaging REPETIBLE (robar+descartar como motor: Battlefield
  Scavenger, Gran-Gran, Katara, Book Devourer, wheels tipo Sensation Gorger).
  El filtrado repetido cuenta como ventaja (precedente Brainstorm del README).
- Sí: ventaja de cartas repetible vía exilio-y-juega ("impulse") propio o robado
  (Rev, Tithe Extractor).
- No: cantrip one-shot de 1 carta pegado a otro efecto ("cuando entra, roba una
  carta": Pyknite, Feather of Flight, Nylea's Presence, Spelunking) — se
  reemplaza, no genera ventaja; la carta no se juega para robar.
- No: cycling, learn (en Commander = loot one-shot), un único Clue/investigate
  (Panther Pounce, Drag the Canal), mill con selección de 1 (Picklock Prankster).

## removal (puntual)

- Sí: destruir/exiliar/-X-X/fight/daño dirigido a permanente, un objetivo o "uno
  por jugador" (edicto multijugador: Szat's Will). Incluye contrahechizos, aunque
  sean estrechos (Hisoka's Defiance) o condicionales (Disrupting Shoal, Syncopate).
- Sí: cualquier hechizo de daño directo que apunte a criatura/planeswalker,
  independientemente de la cifra (First Volley con 1 daño cuenta; regla fija para
  consistencia). En permanentes, la habilidad activada/ETB de removal real también
  cuenta (Atzocan Archer, Vial of Dragonfire, Hopeful Initiate, Morkrut Banshee).
- Sí: removal-por-exilio tipo O-Ring mientras esté en mesa (Static Net).
- Sí: bounce dirigido a permanente de un oponente (parte de Proctor's Gaze):
  interacción puntual aunque sea temporal.
- No: tap/stun/debuffs que no eliminan (Rime Chill, Mu Yanling +2, Study Break).
- No: daño que solo puede apuntar a jugadores (HYDRA Assault Robot, Heartwood
  Giant, Sparkcaster) ni pings marginales en triggers (Tibalt's Rager).
- No: descarte de mano (Deception, Bloodhusk Ritualist) — no hay categoría.
- No: robar el control o redirigir hechizos (Eriette, Return the Favor).
- No: hate estático (Soulless Jailer) ni triggers de gy-hate (Disruptor Wanderglyph).

## board_wipe

- Sí: cualquier efecto masivo "destruye/exilia/rebota todos los X" aunque X sea una
  clase acotada (Nature's Ruin: verdes; Acid Rain: Forests; Primeval Light:
  encantamientos de un jugador; Tornado Elemental: 6 daño a voladoras) — regla:
  masivo = afecta a toda una clase sin apuntar uno a uno.
- Sí: daño masivo a todas las criaturas aunque sea pequeño pero letal para dorks
  (Caldera Hellion 3 daño) y wipes asimétricos/one-sided (Plague Wind, Myojin of
  Cleansing Fire) o repetibles (Serenity).
- No: pings masivos de 1 daño repetibles como rider (Tibor and Lumia).
- Un edicto de "cada oponente sacrifica UNA criatura" es `removal`, no wipe.

## wincons

- Sí: texto explícito "you win the game" / "target player loses the game" como
  función principal (Happily Ever After, Mechanized Production, Laboratory Maniac,
  Ramses) y cartas que son EL plan de victoria explícito de su arquetipo
  (Fynn: reloj de veneno).
- No: finishers genéricos de daño/estadísticas sin texto de victoria (Dragonback
  Assault, drenajes repetibles tipo Ayara o Herald of Hadar): cierran partidas
  pero no "por sí mismas" en el sentido del README, salvo casos Craterhoof-like
  (no hay ninguno en este set).
- No: "you lose the game" como coste (Glorious End, Chance for Glory) ni cartas
  que evitan perder (Angel's Grace, Lich's Tomb, Everybody Lives!).

## synergy

- Sí: lords y anthems tribales (+X/+X a un tipo: Bladestitched Skaab, Galadhrim
  Brigade, Lord of the Accursed, Paragon of Fierce Defiance —"red" cuenta como
  tribu—, Splinter, Rundvelt Hordemaster, Joraga Warcaller, Numa) e
  "elige un tipo" (Etchings of the Chosen, Collective Inferno).
- Sí: piezas que solo funcionan dentro de un paquete inequívoco: payoffs de veneno
  (Persuasive Interrogators, Bloodroot Apothecary, Virulent Silencer), paquete de
  Auras (Hero of Iroas, Eriette), enchantress (Estrid's Invocation), tutor
  estrictamente tribal (Sarkhan's Triumph), escalado por conteo tribal
  (Gempalm Incinerator, Malcolm con Piratas, kinship de Sensation Gorger).
- No: cartas que premian un arquetipo pero funcionan como goodstuff standalone
  (Kresh, Ayara, Desecrated Tomb, Dross Scorpion, Vincent Valentine) ni anthems
  genéricos sin tribu (Veteran Armorer).
- `synergy` se combina con la etiqueta funcional si ambas son reales
  (Ramses = wincons|synergy; Gempalm = removal|synergy).

## Reglas transversales

1. Riders marginales no etiquetan (draw 1 one-shot, un Treasure suelto, ping 1).
2. Ultimates de planeswalker no etiquetan por sí solos (el -6 de Freyalise no da
   card_draw; sus +2 dorks y -2 sí dan ramp|removal).
3. Cartas modales: etiqueta por cada modo real no marginal (Trystan's Command =
   removal por su modo de destruir; los demás modos no alcanzan categoría clara).
4. Habilidad de maná con drawback fuerte sigue siendo ramp (Witch Engine).
5. Tutores no son categoría; solo etiquetan si lo tutelado define paquete
   (Sarkhan's Triumph = synergy) o si ponen tierras al campo (= ramp).
6. Fog/protección/extra turns/group hug sin categoría → none.
7. `none` = lista vacía en el JSON y va siempre sola.
