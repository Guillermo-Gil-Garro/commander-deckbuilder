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

## Decisiones cerradas de partida (charter)
- Cuotas [min, max] por categoría funcional, dependientes de comandante/arquetipo; tierras por método Karsten.
- Motor de recomendación: se decide por experimentos (Fase 2).
- Selector: experimento CP-SAT vs greedy (Fase 3).
- Stack: FastAPI + React, HF Space con Docker. Datos: Scryfall bulk + EDHREC.
