# DECISIONS

Decisiones tomadas y su porqué. Incluirá los resultados de los experimentos (Fases 2 y 3).

## 2026-07-12 — Alcance inicial: solo pipeline Scryfall
Guille decide arrancar Fase 0 con el pipeline de datos de Scryfall en lugar de la fase completa. Motivo: trabajo acotado y revisable antes de fijar scaffold de FastAPI/React.

## 2026-07-12 — Formato de datos procesados: JSONL, sin pandas
El pipeline escribe las cartas procesadas como JSONL en `data/processed/` (gitignorado). Motivo: evitar dependencia de pandas/parquet en el backend; el volumen (~30k cartas) no lo justifica y JSONL se lee en streaming.

## 2026-07-12 — Banlist v1.0-rc y regla de resolución de nombres
Banlist custom del grupo en `banlist.yaml` (fuente única de verdad; los porqués van dentro del propio fichero). Pendiente de revisión del grupo (`status: pending_group_review`). Regla de resolución para multicara descubierta al validar contra el pool real: igualdad exacta en dos pasos — nombre completo Scryfall primero, nombre de cara solo como fallback — porque "Demonic Tutor" es también cara trasera de "Emeritus of Woe // Demonic Tutor" y "Tergrid, God of Fright" solo existe como cara de su MDFC.

## 2026-07-12 — Karsten portado del repo TFM, no reimplementado
Guille autorizó reutilizar su repo antiguo (https://github.com/Guillermo-Gil-Garro/commander-deckbuilder-tfm) como referencia para Karsten, CP-SAT y frontend. El cálculo de tierras (regresión `31.42 + 3.13·avgMV − 0.28·(ramp+draw)`) y la demanda de fuentes de color (hipergeométrica pura, fiabilidad 0.90, factor de calibración empírico 0.80, ancla Karsten {22,29,34}) se portan tal cual a `backend/quotas/` conservando los números validados en el TFM. Detalle metodológico: la demanda de color usa solo pips puros (híbridos/phyrexianos excluidos — no comprometen a un color), a diferencia de `Card.pips` que los cuenta para estadística de pool.

Implicación pendiente para Fases 2-3: el TFM ya contiene un CP-SAT maduro y scoring ML; el experimento de Fase 3 probablemente sea "port simplificado del CP-SAT del TFM vs greedy" en lugar de construir desde cero. 🔶 A decidir al llegar.

## 2026-07-13 — Informe comparativo de tagging funcional (Fase 2) 🔶 pendiente decisión

Cuatro métodos evaluados sobre 200 cartas etiquetadas a mano por Guille
(`experiments/tagging/test_set_filled.csv`; métricas por `evaluate.py`, multi-etiqueta):

| Método | micro-F1 | exact-match | Fuerte en | Débil en |
|---|---|---|---|---|
| **LLM cacheado** | **0.89** | **173/200** | card_draw 0.95, removal 0.95, synergy 0.76 | wincons R=0.62 |
| Regex | 0.85 | 162/200 | precisión perfecta en wipes/wincons | synergy R=0.57 |
| Scryfall otags | 0.75 | 133/200 | lands 1.00, wipes 0.94 | card_draw P=0.50, synergy 0.00 |
| EDHREC headers | 0.08 | 75/200 | — | ciego a 4/7 categorías (hallazgo estructural) |

Lecturas clave:
- EDHREC queda **descartado como tagger** (sus páginas agrupan por tipo, no por función);
  sus scores de sinergia/inclusión siguen siendo la señal de *puntuación* del selector.
- La synergy solo la capturan decentemente LLM (0.76) y regex tribal (0.68 con recall bajo).
- wincons es la categoría difícil para todos (recall 0.62 en los dos mejores): las
  wincons implícitas (veneno, drenajes) no tienen marcador textual fiable.
- Los "fallos" de lands de LLM/regex (P=0.38) son en realidad una discrepancia de
  criterio con el etiquetado de Guille sobre MDFCs hechizo//tierra — a unificar en la
  rúbrica, no un error de método.

**Decisión de Guille (2026-07-13)**: ✅ aprobada la recomendación — LLM cacheado como
motor primario (batch offline + incremental por set nuevo, nunca en caliente), regex
como contraste de auditoría (discrepancia → cola de revisión), otags como tercera
señal gratuita, EDHREC solo para scores. Criterio MDFC unificado: las caras de tierra
de una MDFC hechizo//tierra SÍ cuentan como `lands` (los ~5 desacuerdos del ground
truth en lands quedan resueltos a favor del criterio de la rúbrica).

## 2026-07-15 — Selector: CP-SAT (Fase 3 decidida)
Guille decide CP-SAT como motor único tras la auditoría comparativa
(`experiments/selection/AUDITORIA_SELECTORES.md`). Evidencia: 91-97/99 de solape
entre selectores; la diferencia real es un único mecanismo (tierras multicategoría)
que en greedy es un vicio arquitectónico (bloqueo por orden de fases; incumplió el
min de protection en Meren) y en CP-SAT se corrige con una restricción y un peso.
Arreglos aplicados al elegirlo: (1) los mins de cuotas de hechizos se cubren solo
con no-tierras — las tierras multicategoría siguen entrando pero no "cuentan" para
el mínimo; (2) recalibrado el peso del fixing de color, que no mordía; (3) razones
por carta post-hoc en la salida. El greedy queda en el repo como baseline
experimental, sin mantenimiento activo.

## 2026-07-15 — API stateless y swap sin re-resolver (Fase 4)

**El mazo vive en el cliente** y viaja en cada request (`deck: [{name, count}]`). Sin
sesiones de servidor: el Space se duerme y reinicia, y una sesión perdida a mitad de
un mazo es peor experiencia que un payload de 2 KB. Patrón del repo TFM.

**El cliente manda `dials`, nunca `bands`.** Las bandas son derivadas
(`resolve_bands(config, commander, dials)`) y el servidor las recalcula en cada
request. Si el cliente pudiera mandarlas, relajaría cualquier cuota y validaría
cualquier swap: el anti-tampering del stateless. Verificado — inyectar `bands` da 422.

**El swap no re-resuelve el CP-SAT** (que tarda 0,05-10 s): `selector/swap.py` valida
con aritmética entera sobre las 99. Medido: **0,079 ms** la función pura, **4,2 ms de
mediana end-to-end** por HTTP (requisito <100 ms). Sostenido por `counts_after_swap`,
un conteo incremental O(categorías) — recontar el mazo por candidato costaba 21 ms
solo de conteo para 500 candidatos, por encima del presupuesto.

**Cómo se evita que el checker diverja del solver**: las reglas duras se extraen a
`selector/constraints.py` como definición única (`hard_violations`), y un test de
contrato verifica que todo `CpSatResult` en etapa `none` no las viola, más una
aserción `if __debug__` post-solve. **Límite conocido y aceptado**: si alguien añade
una restricción dura al modelo y no al checker, el contrato no lo detecta (el checker
quedaría más laxo, no más estricto). Está documentado en ambos módulos.

**Regla de no-empeoramiento**: el CP-SAT relaja por etapas, así que un mazo entregado
en etapa relajada ya viola un suelo. Un checker ingenuo bloquearía todo swap sobre él,
incluido el que lo arregla. Cada restricción se acepta si `cumple(después) OR
no_peor(después, antes)`; sobre un mazo de etapa `none` es idéntica a la regla dura.

**Semáforo RED/AMBER** (implementa `rules.yaml:69-72`, que ya lo anunciaba): solo la
banlist bloquea. `never` significa "nunca la auto-recomiendo", no "ilegal" — no se
ofrece como candidata, pero si el jugador la busca a mano entra con aviso. Quitar un
`always` es AMBER. Decisión de Guille: el jugador tiene la última palabra sobre su mazo.

**Una carta baneada no aparece en el buscador de comandantes**, aunque `banned` hable
literalmente de las 99: el baneo del grupo se lee como "fuera del formato". Decisión
de Guille.

**Política de fallo del arranque**: *artefacto de datos gitignorado → degrada; config
versionada → falla duro*. Sin pool la app arranca y `/health` dice `degraded` (el
Space debe poder explicarse, no morir en bucle). Pero sin tags **falla duro**, aunque
parezca "un artefacto": el tagger vacío manda todo a `synergy` y el CP-SAT construye
99 cartas que incumplen todas las cuotas *sin que nadie lo note*. Un mazo plausible y
mal es peor que no arrancar.

**Banlist unificada** (deuda saldada): `banlist_names` proyecta los oracle_ids del
resolver formal a los nombres que esperan los selectores. Se descartó cambiar la firma
de los selectores a oracle_ids: tocaría el greedy congelado y rompería los fixtures de
277 tests por cero valor de usuario. Los 5 mazos de referencia **no cambian**.

**INFEASIBLE no es error HTTP** (patrón TFM): un mazo en etapa relajada devuelve 200 +
`solver.stage` + warning AMBER. Solo el input estructuralmente imposible da 422.

## 2026-07-16 — La superficie de la API se alinea con la del TFM

Guille abrió la API de Fase 4 y su reacción fue *"no se parece en nada al formato que
tenía la del TFM"*. Tenía razón: se adoptaron los patrones **internos** del TFM
(stateless, lifespan+AppState, swap sin re-resolver, handlers finos) pero se rediseñó
la **superficie** — rutas, nombres y forma de las respuestas.

**Fallo de proceso, anotado para no repetirlo**: los endpoints iban en el plan
aprobado, en una tabla dentro de un documento denso, y las ausencias como "fuera de
alcance". Aprobar un plan no es haber visto el diseño. Cuando existe una referencia
que el usuario dice explícitamente que le gusta, la comparativa lado a lado va
**antes** de escribir código, no después.

Alineado: sin prefijo `/api` · `GET /commanders` → `{count, commanders}` con
`archetype` · `GET /commanders/search` · `GET /structure` · `POST /build` ·
`POST /sequential/candidates` (`current` en vez de `out`) · `POST /sequential/validate`
· `POST /maybeboard` (agrupado por categoría, derivado del mazo actual: se actualiza
según swapeas) · `POST /export`. Bandas `{lo, hi}` **solo en la capa HTTP**;
`quotas.config.QuotaBand` sigue con `min`/`max` (lo usan los selectores y los tests).

Consecuencia asumida de quitar el prefijo: una ruta inexistente devuelve el
`index.html` del SPA con 200 en vez de un 404. Es el comportamiento del TFM.

**No copiado del TFM, y por qué**: `/sequential/candidates` no devuelve `power`
(el TFM separa `synergy`/`power` porque tiene dos scorers; nosotros uno — un `power`
vacío sería fingir paridad). `/structure` no publica `karsten_floor`: el suelo Karsten
es función de la curva del mazo, y antes de que exista un mazo no hay suelo — el TFM
puede porque guarda una curva por comandante en su config y nosotros no. Publicarlo
habría sido un número falso.

**Pendiente de decisión de Guille tras usar la API**: `/sequential/start` con la lista
de `decisions` (cartas dudosas por codo de score) — que es el "switcheo semiinteractivo"
del charter y hoy no existe —, `/why-not`, `/audit` y `/cards/search`.

## 2026-07-16 — Lo que Guille quería replicar era el FRONTEND, no la API

Tras dos intentos fallidos de "parecerse al TFM" (primero se alinearon los patrones
internos, luego los nombres de las rutas), Guille lo dijo explícito: *"Lo que me
interesa es que repliques el frontend eh. A partir de ahí ya trabajamos en el
backend"*. La API solo importa en la medida en que da de comer al frontend.

**Lección de proceso, y ya van dos**: cuando el usuario señala una referencia que le
gusta, hay que ir a **verla** antes de deducir qué le gusta de ella. El Space estaba
público y vivo todo el tiempo; interrogar su API en directo (`/openapi.json`, `/build`)
dio en diez minutos lo que dos rondas de suposiciones no dieron.

**Fase 0 reabierta**: el pool no guardaba imágenes y el frontend las pinta en todas
partes. `Card` gana `image_uri_normal` e `image_uri_art_crop` (31.552/31.552, +46% de
fichero). Regla verificada contra el bulk real: **raíz primero, cara frontal como
fallback** — `split`/`adventure`/`flip` (322 cartas) llevan la imagen en la raíz y sus
`card_faces` no tienen ninguna, mientras `transform`/`modal_dfc` (486) solo la tienen
por cara. La regla ingenua "usa `card_faces[0]`" habría roto 322 cartas en silencio.

**Fase 1 completada**: los 55 destacados tenían pendiente su arquetipo desde julio
(*"no individualizaremos todos, sólo los que me parezcan más interesantes"*). Asignados
los 55 — **no es cosmético: cambia las cuotas con las que se construyen esos mazos**
(Zur pasa de 3 a 6 cartas de protección; Titania cambia 12 cartas y sube a 42 tierras).
55/55 OPTIMAL, relajaciones 11 → 8. **26 quedan marcados dudosos**, pendientes del
criterio de Guille. Cada destacado gana además una `description` de una frase, porque
sus amigos no conocen los comandantes.

**El picker NO usa art_crop**, a diferencia del TFM: usa la **carta entera**
(`image_uri_normal`). Petición explícita de Guille — *"muchos de mis amigos no saben
qué hacen los distintos comandantes... de esta forma pueden leer qué hace la carta"*.
El art_crop se queda solo para el fondo global desenfocado.

**El filtro de estilo de juego solo aplica a los 55 curados.** La API devuelve
`archetype: "midrange"` para los otros 3.233, pero eso es el bloque por defecto del
resolver, **no un juicio**: la UI no se lo atribuye a nadie y filtrar por estilo
muestra solo destacados. Un filtro que mintiera sería peor que no tenerlo.

**gzip**: `/commanders` manda los 3.288 de golpe (el picker filtra y pagina en cliente,
como el TFM). Con imagen y descripción son 1,34 MB sin comprimir — lo más pesado que
servimos, sobre un Space que se duerme. Medido: **1,34 MB → 0,25 MB (81%)**.

## 2026-07-16 — La aserción `__debug__` del CP-SAT daba falsos positivos

El riesgo #1 anotado al introducir `constraints.py` era real y se materializó: la
aserción recalculaba el suelo de tierras sobre el mazo terminado, pero el solver llega
al suyo por fixpoint y ese suelo puede quedar **por encima** del Karsten del mazo que
acaba produciendo. Resultado: un mazo legal se leía como fuera de techo y el selector
moría **acusando a `constraints.py` de haber divergido** — culpando al sitio equivocado.

Reproducido con Giada, Font of Hope en bandas de aggro: 37 tierras, suelo del fixpoint
37 (techo `max(36,37)=37`, legal), suelo recalculado 36 (techo 36) → falsa violación
`lands_ceiling`. `hard_violations` acepta ahora un `lands_min` opcional: quien impuso
un suelo lo pasa; el checker de swap sigue recalculando, que es lo correcto ahí porque
no hay fixpoint que honrar.

**Coste real del bug**: tres comandantes (Giada, Gishath, Kaalia) fueron movidos de
arquetipo para esquivarlo, creyendo que era infactibilidad. Esa decisión se tomó sobre
una premisa falsa y hay que revisarla con criterio de juego, no de "qué no peta".

## 2026-07-16 — Modo secuencial retirado; auditoría de mazo pendiente de diseño

Guille, tras probarlo: *"Olvídate del modo secuencial. Prefiero que el mazo salga
tal cual."* Se retira la vista Sequential (las decisiones guiadas carta a carta). El
mazo se entrega completo desde `/build`. **El swap manual dentro de Result se
mantiene** (click en carta → candidatos → validación en vivo): es el "switchear
cartas semiinteractivamente" del charter y Guille no pidió quitarlo — decisión mía,
marcada para que la corrija si no era eso.

**Idea de auditoría, a diseñar bien más adelante** (apunte de Guille, es el sustituto
correcto del modo secuencial): en vez de forzar decisiones, **señalar** las cartas
que conviene revisar —"qué se desvía más, qué cartas convendría revisar si se quieren
dentro o fuera"— y marcarlas **tanto en el mainboard como en el maybeboard**. O sea:
el mazo sale entero, y la UI resalta las dudosas (las de debajo del codo de score de
su categoría, que es justo lo que ya calcula `/sequential/start`) sin obligar a
tocarlas. El algoritmo del codo ya existe; lo que falta es el diseño de UX de
"señalar sin forzar". Endpoints `/sequential/candidates` y `/sequential/validate` se
conservan (los usa el swap de Result). `/sequential/start` queda sin consumidor en el
front, pero se mantiene en la API para cuando se retome la auditoría.

## 2026-07-16 — Sesgo de precio en el score de EDHREC (para revisar) 🔶

Hallazgo de Guille mirando el maybeboard de The Ur-Dragon: las **duales ABUR**
(Badlands, Bayou, Tropical Island, Volcanic Island, Plateau, Savannah) y staples caros
quedan en maybeboard, no en el mainboard, con scores bajos (0.28–0.36). Motivo: el
score de EDHREC refleja **lo que la gente juega de verdad**, y la gente no juega esas
tierras porque son **caras** — aunque para un grupo con proxies son autoincludes
estrictos. Es el mismo sesgo de arranque en frío que ya conocíamos, pero por precio:
*"hay cartas que son autoincludes que no entran porque la gente es pobre de dinero"*.

Consecuencia: en un mazo multicolor, el fixing óptimo (duales) puntúa por debajo de
tierras peores pero más jugadas. **Sin resolver, solo anotado.** Opciones futuras a
valorar: (a) un boost `prefer` para las duales por pareja de colores en `rules.yaml`
(como ya hacemos con Signets/Talismanes), (b) una señal de "fixing ideal" independiente
del score de popularidad, (c) tratarlas como los Signets en el sistema de reglas.
Encaja con la categoría de "cold start" que ya documentamos: el score de EDHREC mide
popularidad, no calidad, y el precio distorsiona la popularidad hacia abajo.

## 2026-07-17 — 4 arquetipos nuevos, excepción de banlist por arquetipo, PDF, ataque al sesgo de precio

**4 arquetipos nuevos** (de 8 a 12), para comandantes que no encajaban en los 8:
`aristocrats` (sac + drenaje; wincon = drenaje), `mill` (moler; board_wipe bajo porque
mono-U no tiene barridos), `big_mana` (rampa dura a bombas vía vida/criaturas/devoción,
no tierras), `stax` (prison — LIMITACIÓN: sin etiqueta de "pieza de stax" en el tagger
se comporta como control de wincons bajas; a afinar cuando se añada esa categoría).
Reasignados 12 comandantes. **9/12 construyen OPTIMAL limpio; 3 relajan en `wincons`**
(Wilhelt, Zhulodok, Ulalek) porque el drenaje de aristócratas y el "castear el bicho
gordo" de big-mana no están etiquetados `wincons` — hueco de tagging, no infactibilidad.
Bandas sin tocar, pendiente de decisión de jugador de Guille.

**Excepción de banlist por arquetipo**: Rhystic Study, Mystic Remora y Smothering Tithe
(baneadas como trío de taxes pasivos) son legales SOLO en `enchantress` (encantamientos
on-theme). Mecanismo `legal_in_archetypes` en banlist.yaml; el build/maybeboard/
candidates/validate/why-not usan `effective_banned_names(archetype)`. La identidad de
color filtra sola (Tithe blanca → solo enchantress blancas como Sythis). El panel de
banlist las marca "salvo en enchantress". Verificado: Sythis mete Smothering Tithe.

**Zur → enchantress** (era voltron), **Giada → aggro** (era midrange, movida por el bug
de la aserción ya arreglado), **lands_matter synergy 18→24** (para que no expulse sus
propios payoffs). **Black Market Connections → watchlist**.

**PDF de proxies**: `POST /export/pdf`, 3×3 A4, cartas 63×88 mm, DFC a dos caras.
Básicas incluidas por cantidad con el **full-art de Theros Beyond Death** (decisión de
Guille). fpdf2 (dep nueva, pura Python).

**Corte (revisión 2026-07-17):** las cartas ya iban pegadas borde con borde, pero las
guías de corte se pintaban como rejilla gris *encima* de los bordes compartidos →
tentaban a cortar a ambos lados = doble borde. Sustituidas por **marcas de corte (ticks)
solo en el margen**, alineadas con cada línea de la rejilla; nada dibujado sobre las
cartas, un corte limpio por borde compartido.

**Tokens en el PDF (2026-07-17):** con `include_tokens` (opt-in; el front lo manda en
`true`) los tokens que el mazo puede generar **rellenan las celdas vacías** de la última
página y desbordan a páginas nuevas (no se pierde ninguno). Fuente: `all_parts` de
Scryfall (`component: token`), añadido al modelo del pipeline como `tokens` (pool
regenerado); arte vía `api.scryfall.com/cards/<id>?format=image` (302 al CDN, el fetcher
ahora sigue redirects). Dedup por (nombre, type_line). **Copias — regla "inteligente"
elegida por Guille:** 1 para cualquier token que no sea criatura (Treasure/Clue/Food) y
para criaturas puntuales (Beast Within); 2 (una derecha + una para tappear) para tokens
de criatura que hacen ≥2 cartas o cuyo único generador implica varios/recurrente (señales
de texto: "number of", "for each", "at the beginning"…). Fetch de token best-effort: si
falla la imagen se omite ese token con warning, el PDF sigue.

**Ataque al sesgo de precio de EDHREC** (el score mide popularidad y el precio la
deprime). Tres capas complementarias, NO solapadas:
1. **Fixing como prefer**: 10 duales ABUR + 10 fetchlands en `rules.yaml` `preferred`
   (boost 0.4, solo si la identidad contiene sus DOS colores). **Hallazgo de fondo**: el
   sistema `preferred` NO inyectaba candidatos — solo boosteaba lo ya recomendado, así
   que un staple price-suprimido nunca aparecía. Arreglado: los `preferred` ahora se
   inyectan al pool de candidatos cuando la identidad casa (como `always`, sin forzar).
   Esto cambia la composición de TODOS los mazos (más fixing/staples premium).
2. **Sección de maybeboard "Caras y buenas"**: diff EDHREC `expensive − optimized`
   (idea de Guille, validada con datos). Surge lo que los mazos con dinero juegan y la
   lista optimizada infrapondera por precio (Cradle, Mox, duales, Wheel...), limpiando
   ruido barato (< $5) y baneadas, conservando Reserved List (usd nulo). **Señala, no
   fuerza** — no se exporta al decklist. Es la capa para lo que EDHREC NO recomienda.
3. **Método C** (`C_WEIGHT=0.15` en cp_sat): boost de score = `price_factor(precio) ·
   inclusion_factor(inclusión)`, solo sobre candidatos que EDHREC SÍ recomienda, para
   que una cara-y-jugada no pierda su slot frente a una barata peor. Suelo $10, satura
   a $100, nulo/inclusión-0 → 0. Calibrado sobre 8 comandantes (mueve 0-4 cartas, sin
   cambiar relajación). Es solo score: no toca constraints ni Karsten. `C_WEIGHT=0` lo
   apaga.

⚠️ **Las tres capas cambian la composición del mainboard y Guille aún no las ha jugado.**
Todas son fáciles de dial back (quitar prefer, `C_WEIGHT=0`). Pendiente su validación en
partida. **Inconsistencia cosmética conocida**: C se aplica en cp_sat pero no en el
re-scoring de `service.py` (panel de swap/maybeboard), así que el score mostrado de una
carta cara puede diferir ≤0.15 entre contextos. No afecta a la legalidad (constraints es
conteo puro). Se arregla extrayendo C a un helper compartido si molesta.

## 2026-07-17 (noche) — Feedback de Ur-Dragon: manabase forzada, capa 2 retirada, diseño de auditoría

Guille revisó Ur-Dragon en la web y dio feedback carta a carta. Verificado contra el
build real (39 tierras, tope de banda [34,39], **0 básicas**):

**Hallazgos:**
- Las duales ABUR entran por score+boost, no garantizado: **7 de 10** en el mainboard, 3
  (Scrubland/Tundra/Underground Sea) perdieron su hueco contra el tope de 39 frente a sus
  shocks del mismo color. El boost 0.4 de `preferred` no basta. Autoinclude = forzar, no
  boostear: era un error de implementación (lo hice como boost).
- **Cero básicas** con 10 fetches: funciona de milagro (los fetches buscan duales/shocks
  con tipo básico) pero frágil y raro. La popularidad EDHREC no reserva slots de básica.
- **"Caras y buenas" saca staples genéricos, no tech del comandante** (Wheel, Gaea's
  Cradle, Mox Opal, Force of Negation): el diff `expensive − optimized` escora a poder
  universal, no a sinergia. Su único acierto (las duales) desaparece al forzarlas.
- **Fierce Guardianship en mainboard**: counter gratis solo con el comandante en juego,
  inútil con un comandante de CMC 9. EDHREC lo ranquea top y ni el score ni el tagger ven
  ese matiz.

**Decisiones:**
1. **Manabase — forzar duales/fetches + reservar básicas** (aprobado). Los `preferred`
   que son TIERRA pasan de boost a forzados (`x==1`, duro en toda etapa, como banlist);
   los no-tierra siguen con boost. Hereda el gate de identidad ya existente (⊇ sus DOS
   colores, gate conservador y correcto de "en los mazos de sus colores"). Suelo de
   básicas: ≥1 por color de la identidad (tunable). **Cruza la frontera de diseño
   "rules.yaml es no-tierras / la manabase posee las tierras" — aprobado explícitamente.**
   Cambia la composición de TODOS los mazos multicolor.
2. **Retirar "Caras y buenas" (capa 2)**. El sesgo de precio no es de precio: es usar
   *popularidad* EDHREC como señal de calidad. La señal de calidad de verdad es la
   auditoría (abajo), que señala lo bueno por ser bueno, no por caro. La sección era un
   parche débil; su único valor (duales) lo cubre el forzado. Se deja de calcular en el
   backend y se oculta en el front (reversible; la fontanería queda).
3. **Fierce Guardianship y similares → a la auditoría**, no a borrado manual.

### Diseño de la auditoría (sustituto del modo secuencial: señalar sin forzar)

Sobre un mazo YA construido, dos salidas simétricas: **dudosas dentro** y **buenas que
faltan**. Señal = calidad/sinergia, NO popularidad ni precio. Como el mazo está fijo en
99, la auditoría **es el swap-workspace pero iniciado por el sistema**: reusa
`swap-candidates`, solo añade "qué señalar y por qué".

**Capas de detección de dudosas (de barata a cara):**
- **Capa 1 — lista curada de condicionales (MVP, se implementa ahora).** Cartas cuyo
  valor depende de una propiedad del mazo, con predicado de debilidad, reusando la
  maquinaria `when` de rules.yaml. Caso principal: el ciclo "gratis si controlas tu
  comandante" (Fierce Guardianship, Deflecting Swat, Deadly Rollick, Flawless Maneuver…,
  ~8-10 cartas) con predicado *comandante CMC alto* (≈≥5). Si el mazo la lleva y el
  predicado casa → flag ámbar con motivo. Precisa pero curada (solo caza lo enumerado).
- **Capa 2 — filler de baja sinergia (PENDIENTE, no ahora).** Cartas con sinergia EDHREC
  ≤0 fuera de una allowlist de staples universales. Ruidosa: el trabajo real es mantener
  la allowlist. Alternativa conservadora: solo cartas que caen en `synergy` puro (sin
  categoría real). Ver ROADMAP.
- **Capa 3 — auditoría LLM (PENDIENTE, proyecto aparte).** Pase LLM cacheado sobre
  comandante+mazo: la lectura de calidad genérica y con matiz, la buena de verdad. Ver
  ROADMAP.

**Abanico de reemplazos** (al marcar una carta, menú de hasta 4, solape permitido, todos
swaps FACTIBLES por la carta que se corta — si uno deja el mazo ilegal, ese slot va vacío):
- **2× mismo rol**: top-2 de su categoría fuera del mazo, por sinergia.
- **1× mejor en general**: mayor score del solver (sinergia+inclusión) fuera del mazo,
  del rol que sea (eje "upgrade", ignora necesidades del mazo).
- **1× refuerzo**: top de la categoría con mayor déficit vs su banda (eje "balance",
  respeta el hueco). Ej. Farewell si vas justo de board wipe.
- Cada slot etiquetado con su porqué; dedupe suave (no repetir la misma carta).

## 2026-07-18 — Dudosos revisados, arquetipo artifacts, Kona

Revisados los 8 comandantes dudosos contra builds reales: **7/8 construían OPTIMAL
limpio** (arquetipo mecánicamente correcto; el cambio sería de sabor). Decisiones de
Guille:

- **Arquetipo `artifacts` nuevo**: piezas baratas + payoffs; los mana rocks son la rampa
  (ramp [10,16] alto, tierras [33,38] algo más bajas), robo alto, synergy 34 holgado para
  el paquete de artefactos. **Reorganización**: Emry (de graveyard) y Urza (de control) →
  artifacts. Ambos OPTIMAL limpio.
- **Narset**: sube protección con override `[3,5]` (de spellslinger [2,4]) — el mazo
  depende de que sobreviva y conecte.
- **Obeka**: baja el suelo de wincons con override `[0,4]` — no lleva wincons dedicados
  (plan = caja de "terminar el turno"); antes relajaba a `soft_category_floors`, ahora
  OPTIMAL limpio.
- **Kona, Rescue Beastie** añadida (featured + quotas) → **big_mana**. Mono-verde que
  trampea un permanente/turno de la mano si está tappeada; suelo Karsten 40, que solo
  big_mana acomoda. (No es artifacts pese a "trampear permanentes": es rampa-verde-a-bombas.)
- Baral/control, Ketramose/midrange, Locust God/spellslinger, Kefka/control (stax es
  provisional), Arcades/midrange: **se dejan** (borderline defendibles).
- **+5 comandantes de artifacts** (petición de Guille, "Jhoira, Osgir, Breya…"):
  **Osgir** (RW, recursión/doblado de artefactos), **Breya** (WUBR, thopters +
  sac-aristócratas), **Jhoira, Weatherlight Captain** (UR, motor de robo por históricos),
  **Sai, Master Thopterist** (U, go-wide de thopters), **Sydri** (WUB, anima artefactos
  como removal). Todos → arquetipo `artifacts`, todos OPTIMAL stage=none, tierras en banda.
  Descartados: Jhoira of the Ghitu (storm, no artefactos) y Arcum (combo puro). Featured
  pasa de 56 a **61**.

**Coste de meter `stax` en el tagger** (preguntó Guille): es el mismo playbook que
`protection` (ya hecho una vez). No es un tweak de código: (1) añadir `stax` a `CATEGORIES`
(`quotas/config.py` + `tags/store.py`) y decidir su semántica de banda; (2) definir `stax`
en `RUBRIC.md`; (3) **re-etiquetar el pool con el LLM** para la nueva etiqueta (el coste
real: llamadas LLM cacheadas sobre ~5.283 cartas + revisar la cola de auditoría; protection
rindió 168 etiquetas, stax sería de escala parecida); (4) bandas de stax por arquetipo +
regenerar; (5) tests. Moderado, no gratis: es una campaña de tagging, no una línea.

**EJECUTADO 2026-07-18** — campaña `stax` completa (rúbrica v4). Definición: negación
PERSISTENTE de recursos/acciones ajenas (prisión), no interacción puntual. Contrahechizos
siguen siendo `removal`; Rhystic/Smothering Tithe siguen ramp/draw (te dan recurso, no
restringen). Scanner `find_stax_candidates.py` (recall-oriented; enters-tapped filtra
taplands propias por is_land_card): 410 candidatos del store. Etiquetado en paralelo (6+1
subagentes con la rúbrica embebida) → **118 etiquetas stax** aplicadas al store vía
`add_label` (v4). Bandas por arquetipo: min 0 en todos salvo el arquetipo `stax` (suelo
que fuerza la identidad de prisión). **Calibración con datos**: suelo del arquetipo stax
puesto en **3** (no 4) porque el pool EDHREC B4 de Winter solo reúne 3 piezas; Thalia+
Gitrog llega a 5. Ambos OPTIMAL stage=none tras el ajuste. Grand Arbiter/Oloro cuentan 5,
Baral 3 (control tolera stax, no lo exige). Las 8 relajaciones que quedan en el barrido de
61 son por wincons/board_wipe/card_draw (limitaciones preexistentes de pool), NO por stax
(`add_label` solo añade etiquetas, nunca reduce otras cuentas; stax min 0 en esos
arquetipos). RUBRIC_VERSION v3→v4; CATEGORIES ahora 9 (stax entre protection y synergy).

**Recalibrado 2026-07-18 (feedback de partida de Guille)**: el suelo del arquetipo stax
baja de 3 a **2** y se revierten synergy (18→22) y removal ([7,12]→[8,14]). Motivo: con
suelo 3, EDHREC B4 infravalora el stax (es nicho) y el solver rascaba el fondo — Winter
metía Chains of Mephistopheles (score 0.18, la peor carta del mazo) robando slot a Dauthi
Voidwalker/Tinybones (0.55). Con suelo 2 solo entran las 2 mejores piezas y el resto se
gana el slot por score: Winter pasa a Oppression+Painful Quandary (Chains fuera);
Thalia+Gitrog queda con sus 3 mejores (Drana and Linvala, Collector Ouphe, Elesh Norn),
cae Armageddon (0.45)/Thalia (0.36). Lección: en categorías que EDHREC infravalora, un
suelo alto fuerza dregs; mejor suelo bajo + dejar que el score decida.

**REVERTIDA 2026-07-18 (decisión de Guille)**: la categoría/etiqueta `stax` se retira por
completo. Razonamiento de Guille: si un mazo es de prisión, las buenas piezas de stax ya
entran por score vía `synergy`; la etiqueta no hace falta. El dato lo confirmó: tras bajar
el suelo a 2, la banda apenas ataba, y su única aportación era cosmética (fila "Stax N" +
swap por categoría). Se elimina de `CATEGORIES` (config + store), de las bandas de todos
los arquetipos, de la rúbrica (vuelve a v3), del frontend (categoría; el ARQUETIPO stax se
queda) y las 118 etiquetas se quitan del store (87 cartas vuelven a `none`). **Regla
general que queda (criterio para futuras categorías)**: una categoría solo se gana el sitio
si su SUELO carga peso — lands/ramp/card_draw/removal/board_wipe/wincons evitan mazos
degenerados; una etiqueta "de tema" que EDHREC infravalora no aporta, porque forzarla mete
dregs y sin forzarla las buenas ya entran por score. `protection` es la siguiente a vigilar
(su suelo sí ata en voltron/Narset, así que de momento se queda). El arquetipo `stax`
(Winter, Thalia+Gitrog) sigue existiendo: moldea por sus bandas, no por una categoría.

## Decisiones cerradas de partida (charter)
- Cuotas [min, max] por categoría funcional, dependientes de comandante/arquetipo; tierras por método Karsten.
- Motor de recomendación: se decide por experimentos (Fase 2).
- Selector: experimento CP-SAT vs greedy (Fase 3).
- Stack: FastAPI + React, HF Space con Docker. Datos: Scryfall bulk + EDHREC.

## 2026-07-18 — Arte de cartas: español por defecto + selector de ediciones

Decisión de Guille: las cartas se muestran e imprimen **en español** cuando existe un
escaneo español en alta resolución; si no, se mantiene el arte inglés del pool (o el
inglés high-res más reciente si el del pool no lo es). El usuario puede elegir cualquier
edición desde un selector (botón paleta en cada carta); el selector **solo lista
ediciones high-res** salvo que la carta no tenga ninguna (una edición concreta sin
high-res se busca fuera del sistema). **Lo elegido en la UI es lo que exporta el PDF.**

Cómo: `pipeline/prints.py` busca en Scryfall todas las impresiones físicas es/en de un
oracle_id (`unique=prints`, `include_multilingual`, `-is:digital`; el criterio high-res
es `image_status == "highres_scan"` de Scryfall — no hace falta juzgar a ojo) con caché
disco `data/cache/prints/` (borrar un fichero = refrescarlo tras un set nuevo).
Endpoints: `GET /cards/{oracle_id}/prints` (galería filtrada) y `POST
/cards/prints/defaults` (batch ≤25, política es-hi → pool-si-hi → en-hi). El frontend
(`art.ts`) resuelve defaults por chunks tras el build (el mazo aparece ya y va pasando a
español según resuelve; localStorage cachea defaults y elecciones manuales — las
manuales son globales: un Sol Ring elegido aplica a todos los mazos). El PDF recibe
`art_overrides` (name → scryfall_id, nunca URLs: el id se resuelve contra el endpoint de
imagen del propio Scryfall) y un override explícito gana incluso a las básicas Theros.
La primera resolución de un mazo frío tarda unos segundos (una búsqueda Scryfall por
carta, throttled); después es instantánea.
