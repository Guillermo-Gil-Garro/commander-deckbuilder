# ROADMAP

Estado global del proyecto. Lo mantiene la sesión orquestadora.

Leyenda: ⬜ pendiente · 🔄 en curso · ✅ hecho · 🔶 requiere OK de Guille

## Fase 0 — Esqueleto y datos
- ✅ Scaffold inicial del repo (backend/, frontend/, data/, experiments/, docs/)
- ✅ Pipeline de datos Scryfall: bulk oracle cards → filtro legalidad Commander → modelo de carta interno (`backend/pipeline/`, 2026-07-12; 31.622 cartas, 18 tests)
- ✅ Scaffold FastAPI + React + Vite + Dockerfile HF Spaces (2026-07-12; Dockerfile pendiente de probar con build real en HF)
- ✅ Fetcher EDHREC por comandante (recomendaciones + scores), con caché (`backend/pipeline/edhrec.py`, 2026-07-12; verificado con Atraxa y Krenko)
- ✅ 🔶 `banlist.yaml` v1.0-rc (custom decidida por Guille 2026-07-12; los 44 nombres validados contra el pool; `status: pending_group_review` hasta OK del grupo)

## Fase 1 — Sistema de cuotas
- ✅ Cálculo de tierras Karsten (conteo por curva + distribución de color por pips) — portado del TFM a `backend/quotas/` (2026-07-12); la aplicación sobre bandas de categoría va con el esquema de cuotas
- ✅ 🔶 Esquema de cuotas `{categoria: {min, max}}` por arquetipo — valores aprobados por Guille 2026-07-12 (`quotas.yaml` + `backend/quotas/{config,resolver}.py`; 7 arquetipos, 6 diales con memes, overrides por comandante pendientes de su lista)
- ✅ Validador de mazo de 99 (estado por categoría, suelo Karsten infranqueable; `backend/quotas/validator.py`)

## Fase 2 — Experimentos de tagging ✅ (completa 2026-07-13: motor LLM cacheado en producción, store 5.101 cartas, cola de auditoría generada)
- ✅ 4 métodos de tagging implementados sobre el set de test (2026-07-13): regex serio, EDHREC headers (ciego a 4/7 categorías — hallazgo), Scryfall otags, LLM cacheado con rúbrica; predicciones en `experiments/tagging/predictions/`, comparador `evaluate.py` listo
- ✅ 🔶 Set de test ~200 cartas etiquetadas a mano por Guille (`test_set_filled.csv`, 2026-07-13)
- ✅ 🔶 Informe comparativo en DECISIONS.md (LLM 0.89 > regex 0.85 > otags 0.75 > EDHREC 0.08) — decidido 2026-07-13: LLM cacheado primario + regex auditoría + otags señal extra; MDFC hechizo//tierra cuentan como lands

## Fase 3 — Experimentos de selección
- ✅ Greedy por categorías (`backend/selector/greedy.py`, 2026-07-13; tagger provisional otags intercambiable, 5 mazos de prueba en `experiments/selection/decks/`, ~2ms/mazo, maybeboard incluido) — 🔶 pendiente evaluación a ojo de Guille
- ✅ CP-SAT (OR-Tools) — port simplificado del TFM (`backend/selector/cp_sat.py`, 2026-07-13; OPTIMAL <0.05s en los 5 mazos, relajación escalonada, Karsten/banlist/identidad nunca se relajan) — 🔶 pendiente comparativa a ojo vs greedy
- ✅ 🔶 Comparativa y decisión (2026-07-15): **CP-SAT motor único** con 3 arreglos (mins de hechizos no-tierra, fixing recalibrado, razones post-hoc); auditoría en `experiments/selection/AUDITORIA_SELECTORES.md`; greedy queda de baseline
- ✅ Maybeboard (por score + sección de cartas nuevas de EDHREC para arranque en frío)

## Fase 4 — API ✅ (completa 2026-07-15: 7 endpoints, 465 tests, swap end-to-end en 4,2ms)
- ✅ `AppState` + lifespan: carga única del pool/tags/reglas, `request.app.state`, degradación explícita (sin pool → arranca, `/health` dice `degraded`, endpoints de mazo 503 en español)
- ✅ Endpoints (superficie alineada con la del TFM 2026-07-16, sin prefijo `/api`): `GET /health`, `GET /commanders`, `GET /commanders/search?q=`, `GET /structure?commander=`, `POST /build`, `POST /sequential/candidates`, `POST /sequential/validate`, `POST /maybeboard`, `POST /export`
- 🔶 **Pendiente de decisión de Guille tras usarla**: el TFM tenía `/sequential/start` con lista de `decisions` (cartas dudosas por codo de score) = el "switcheo semiinteractivo" del charter, más `/why-not`, `/audit` y `/cards/search`. Ninguno construido — Guille quiere manejar la API antes de decidir cuáles quiere
- ✅ Swap sin re-resolver (`selector/swap.py` + `selector/constraints.py`): **mediana 4,2ms end-to-end** (requisito <100ms), 0,079ms la función pura; test de contrato que impide que el checker diverja del CP-SAT
- ✅ Deudas saldadas: banlist unificada al resolver formal (los 5 mazos no cambian), `format_archidekt` → `backend/selector/export.py` (+ label `protection`), Dockerfile copia todos los paquetes + YAML + tags (⚠️ **build no probado**: no hay Docker en la máquina)
- ✅ `scripts/precache_edhrec.py` (55 destacados; ⚠️ `data/cache/` gitignorado → **no llega al Space**, es optimización de dev)
- 🔶 Pendiente de revisión de Guille: probar la API a mano

## Fase 5 — Frontend (réplica del TFM, en curso desde 2026-07-16)
Referencia: el Space vivo de Guille (https://caskis-commander-deckbuilder.hf.space) + `frontend/` del repo TFM.
Stack copiado tal cual: React 19 + Vite + TS estricto + Tailwind v4 (plugin de Vite, config en CSS), Exo 2, `lucide-react`, `mana-font`. Sin router, sin store, sin axios.
- ✅ Base: tema oscuro/dorado y claro/morado (`data-accent` atado al tema), `.surface`, fondo art_crop aleatorio desenfocado, `api.ts`, `labels.ts`, `ui.tsx`
- ✅ Vista **Setup**: picker de los 3.288 con **carta entera legible** (`image_uri_normal`, NO art_crop — petición explícita de Guille: sus amigos no conocen los comandantes), filtro de color exacto, **filtro de estilo de juego**, **descripción al hover**, paginación de 24, destacados primero
- ✅ Panel de **diales con los memes de Guille** (sustituye al panel de presupuesto/bracket/potencia del TFM, que aquí no aplica). Sin etiqueta "balanced" en el centro
- ✅ Vista **Result**: DeckView (toggles Tipo/Categoría y Lista/Visual), CompositionPanel con bandas emerald/amber, curva real, swap workspace, maybeboard, why-not (typeahead, debounce 160ms), mano de apertura
- ✅ ~~Vista Sequential~~ **retirada 2026-07-16** por decisión de Guille ("prefiero que el mazo salga tal cual"). El swap manual en Result se conserva. Sustituto futuro = auditoría de mazo (señalar dudosas sin forzar), ver DECISIONS
- ✅ Ronda de retoques de Guille (2026-07-16): solo modo oscuro; picker ordenado por popularidad EDHREC (`data/edhrec_ranking.json`, unión de 32 páginas por color, ~59% cobertura, resto al final); cara trasera de los DFC con botón de flip (Kefka/Sephiroth/Etali); panel modal de banlist+watchlist (`GET /banlist`); "Diales"→"Personalización" con leyenda MÍN/MÁX; scroll-arriba al paginar; gzip
- ⬜ 🔶 **Revisión de UX con Guille** (en curso: Guille lo está usando y pidiendo cambios)

**Ronda de jugador 2026-07-17** (ver DECISIONS): 4 arquetipos nuevos (aristocrats, mill,
big_mana, stax) + 12 comandantes reasignados; excepción de banlist por arquetipo
(Rhystic/Remora/Tithe en enchantress); Giada→aggro, Zur→enchantress, lands_matter
synergy→24; Black Market→watchlist; PDF de proxies 3×3 con básicas de Theros; ataque al
sesgo de precio en 3 capas (fixing prefer con inyección arreglada + sección "caras y
buenas" + método C).

**Retoques del PDF 2026-07-17**: guías de corte movidas a **ticks en el margen** (nada
sobre las cartas, un corte por borde compartido); **tokens rellenando huecos** con
`include_tokens` (fuente `all_parts` de Scryfall → campo `tokens` en el pool; copias
inteligentes 1/2; desborda a páginas extra).

**Feedback Ur-Dragon 2026-07-17 (noche)** (ver DECISIONS): manabase **fuerza** las
duales/fetches como autoinclude (preferred-lands → `x==1`) y **reserva ≥1 básica/color**
(arregla "duales fuera / cero básicas"); **capa 2 "Caras y buenas" retirada** (era
popularidad-como-calidad; la señal buena la dará la auditoría).

### Auditoría de mazo — feature en curso (MVP 2026-07-17)
Sustituto del modo secuencial: sobre el mazo construido, **señala sin forzar** (dudosas
dentro + buenas que faltan), reusando `swap-candidates` (es el swap iniciado por el
sistema). Diseño completo en DECISIONS.
- ✅ **MVP (2026-07-18)**: `POST /audit` + panel "Auditar mazo". Capa 1 (lista curada
  `selector/audit.py`: ciclo "gratis con comandante" con predicado CMC≥5; no reusa el DSL
  `when`, es código self-contained más simple) + abanico de 4 reemplazos factibles (2
  mismo rol / 1 mejor general / 1 refuerzo de categoría más justa) + lado "buenas que
  faltan". Reusa swap-candidates/swap_is_feasible; no re-resuelve. 10 tests. Verificado en
  Ur-Dragon (marca Fierce Guardianship, ofrece Force of Will / Toxic Deluge / refuerzo).
- ✅ **Capa 2 — filler de baja sinergia** (2026-07-18): sinergia EDHREC ≤0 **y** inclusión
  global <25%. La barra de inclusión sustituye la allowlist curada (Sol Ring/Swords la
  pasan solos: se auto-mantiene). Tierras, capa-1 y always exentos; sin dato EDHREC no hay
  veredicto. Calibración en vivo: ~1 flag por mazo (señal, no ruido). Umbrales tunables en
  `selector/audit.py`.
- ⬜ **Capa 3 — auditoría LLM**: pase LLM cacheado sobre comandante+mazo, la lectura de
  calidad genérica con matiz. **Pospuesta a después de Fase 6** (decisión de Guille
  2026-07-18): capas 1+2 dan señal limpia; la arquitectura (runtime con API key vs
  offline batch) se decidirá con la app ya desplegada.

**Pendiente de VALIDACIÓN EN PARTIDA de Guille** (todo fácil de dial back):
- **Las 3 capas de precio cambian la composición del mainboard** — jugarlas antes de dar por buenas. `C_WEIGHT=0` y quitar el prefer las revierten.
- ~~**Relajaciones de wincons/board_wipe/card_draw** (8 comandantes)~~ CERRADO 2026-07-18:
  titanes Eldrazi etiquetados `wincons` (fiel a rúbrica: annihilator = reloj del plan) +
  6 overrides honestos con motivo inline (Talrand, Wilhelt, Etali, Winota, Kinnan, Lumra).
  **Los 61 destacados construyen OPTIMAL stage=none — cero relajaciones.**
- **stax**: la categoría/etiqueta se probó (rúbrica v4, 118 etiquetas) y se **revirtió**
  el 2026-07-18 (decisión de Guille + feedback de partida): forzaba piezas de prisión flojas
  y las buenas ya entran por score en synergy. El ARQUETIPO stax se queda (moldea por bandas,
  no por categoría). Ver DECISIONS. Regla que queda: una categoría solo vale si su suelo
  carga peso; `protection` es la siguiente a vigilar.

**Decisiones de jugador aún pendientes** (ninguna urgente):
- ~~El **resto de dudosos**~~ revisados 2026-07-18 (ver DECISIONS): arquetipo `artifacts`
  nuevo (Emry+Urza), Narset +protección, Obeka −wincons, Kona añadida (big_mana). Baral/
  Ketramose/Locust/Kefka/Arcades se dejan. ~~Pendiente: capa `stax` en el tagger~~ HECHO
  2026-07-18 (rúbrica v4, 118 etiquetas). ~~crear más comandantes de `artifacts`~~ HECHO
  2026-07-18 (Osgir, Breya, Jhoira WC, Sai, Sydri añadidos; featured 56→61).
- ~~**Auditoría de mazo**~~ MVP hecho 2026-07-18 (ver sección Auditoría arriba); pendiente capas 2/3.
- **Inconsistencia cosmética**: el método C no se aplica en el re-scoring de `service.py` (swap/maybeboard) → el score mostrado de una carta cara puede diferir ≤0.15 entre contextos (no afecta a legalidad).

**Pendientes conocidos de la Fase 5**:
- ~~Los encabezados por categoría del DeckView no cuadran con el panel de composición~~
  CERRADO 2026-07-18: son dos semánticas correctas (partición vs multi-pertenencia); la UI
  ahora lo explica justo al agrupar por categoría. No se unifica: cuadrarlas rompería una
  de las dos lecturas.
- El maybeboard y el `color_source_breakdown` **no se reoptimizan** al swapear (avisado en ámbar en la UI).
- Una carta que solo cubre `protection` sigue con `slot=synergy` (el `FILL_ORDER` vive en `greedy.py`, congelado). Por eso el DeckView agrupa por `categories`, no por `slot`.

**Del TFM se descarta** (decisión de Guille): presupuesto y `price_eur` (juegan con proxies), brackets y Game Changers (política de WotC, no la banlist del grupo), slider Sinergia↔Potencia y columna "power" (el TFM tiene dos scorers ML; nosotros uno), `/audit` y los badges "Revisar" (usan embeddings de coherencia que no tenemos), y `curve_breakdown.target`/`deviation` (nuestro solver no tiene objetivo de curva).

## Fase 6 — Despliegue ✅ (2026-07-19)
Desplegado en https://huggingface.co/spaces/Caskis/commander-deckbuilder
(sustituye la versión TFM). `/health = ok`, no degraded.
- ✅ HF Space Docker (FastAPI + build React), puerto 7860; README con front-matter `sdk: docker`
- ✅ **`cards.jsonl` (26MB)**: resuelto con **Git LFS** (COPY en la imagen). Refresco = regenerar el .jsonl y redeploy. (Se descartó `RUN pipeline.build` por builds lentos/dependientes de Scryfall.)
- ✅ `data/cache/edhrec/`: **versionados los 61 optimized** (~5.7MB) y copiados a la imagen → primer clic de destacados instantáneo. No-destacados: on-demand.
- ✅ Egress a `json.edhrec.com` verificado (Atraxa no-destacada construyó on-demand) y a `api.scryfall.com` (prints/imágenes)
- ✅ Primer `docker build` real ejecutado (en HF) — OK

## Fase 7 — Actualización de datos ✅ (2026-07-20)
El sistema ya no es un snapshot congelado: los datos se refrescan solos.
- ✅ **Recs EDHREC (inclusión %, sinergia): TTL de 7 días** en `pipeline/edhrec.py`
  (`DECKBUILDER_EDHREC_TTL_DAYS`, 0 desactiva). Un refetch fallido sirve la caché
  vieja (EDHREC flojo nunca rompe un build). Auto-refresco por request, sin infra.
- ✅ **Pool (sets nuevos) + ranking + precache: GitHub Actions** (`.github/workflows/deploy.yml`).
  GitHub es la fuente de verdad; el repo está en `Guillermo-Gil-Garro/commander-deckbuilder`.
  Cada run **regenera** (`pipeline.build` + `precache_edhrec_ranking` + `precache_edhrec`)
  y hace push al Space. Triggers: push a `main`, **cron semanal (lunes 06:00 UTC)** y
  dispatch manual. La data regenerada se force-adde solo para el push a HF, nunca se
  commitea a GitHub → el LFS de GitHub no crece y un push de código nunca revierte un
  refresco. Secret: `COMMANDER_DECKBUILDER_HF_SPACE` (token HF).
- ⏸️ **Tagging de cartas nuevas (capa 3): pospuesto a la fase LLM.** Las cartas de sets
  nuevos entran disponibles pero caen a `synergy` hasta que el tagger LLM las procese.
- **Flujo nuevo:** código → `git push origin master:main` (rama local `master` → `main`),
  el Action despliega. Cada deploy tarda unos minutos (reconstruye el pool). Se descartó
  cachear la data en GitHub (crecería el LFS) y regenerar solo en el cron (un push de
  código revertiría el refresco).

## Pendiente (próximas sesiones)
- **Arte de tokens de Magic** (features 3 y 4, "te coronas"): elegir el arte de los
  tokens que el mazo genera para el PDF (mainboard + maybeboard), y arte distinto por
  copia cuando se imprimen dos. Requiere: guardar el `oracle_id` del token en el pipeline
  (hoy solo el id de impresión), exponer los tokens en la respuesta/UI, picker y override
  en el PDF. Aparcado a propósito.
- **Capa 3 — auditoría/tagging con LLM:** brief a escribir con el feedback real de partidas.
- **Flujo de despliegue con token más limpio** (revisar cuando Guille quiera).
