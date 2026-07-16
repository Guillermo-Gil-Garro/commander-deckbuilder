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

## Decisiones cerradas de partida (charter)
- Cuotas [min, max] por categoría funcional, dependientes de comandante/arquetipo; tierras por método Karsten.
- Motor de recomendación: se decide por experimentos (Fase 2).
- Selector: experimento CP-SAT vs greedy (Fase 3).
- Stack: FastAPI + React, HF Space con Docker. Datos: Scryfall bulk + EDHREC.
