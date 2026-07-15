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
- ✅ `AppState` + lifespan: carga única del pool/tags/reglas, `request.app.state`, degradación explícita (sin pool → arranca, `/api/health` dice `degraded`, endpoints de mazo 503 en español)
- ✅ Endpoints: `/api/health`, `/api/commanders?q=`, `/api/commanders/featured`, `POST /api/deck`, `POST /api/deck/swap/candidates`, `POST /api/deck/swap/validate`, `POST /api/deck/export`
- ✅ Swap sin re-resolver (`selector/swap.py` + `selector/constraints.py`): **mediana 4,2ms end-to-end** (requisito <100ms), 0,079ms la función pura; test de contrato que impide que el checker diverja del CP-SAT
- ✅ Deudas saldadas: banlist unificada al resolver formal (los 5 mazos no cambian), `format_archidekt` → `backend/selector/export.py` (+ label `protection`), Dockerfile copia todos los paquetes + YAML + tags (⚠️ **build no probado**: no hay Docker en la máquina)
- ✅ `scripts/precache_edhrec.py` (55 destacados; ⚠️ `data/cache/` gitignorado → **no llega al Space**, es optimización de dev)
- 🔶 Pendiente de revisión de Guille: probar la API a mano

## Fase 5 — Frontend
- ⬜ Buscador, vista por categorías, semáforos de cuotas, swap, export
- ⬜ 🔶 Revisión de UX con Guille

## Fase 6 — Despliegue
- ⬜ HF Space Docker (FastAPI + build React), datos precacheados, refresco manual
- ⬜ **`cards.jsonl` (16MB) está gitignorado → el Space arrancaría degradado.** Opción más simple: `RUN python -m pipeline.build` en el Dockerfile (rebuild = refresco manual). Alternativas: storage persistente del Space, o git-lfs
- ⬜ Gemelo del anterior: `data/cache/edhrec/` también gitignorado → cada comandante paga su primer fetch (~1s). Decidir si versionar los 55 optimized (~11MB)
- ⬜ ⚠️ **Verificar que el Space deja salir a `json.edhrec.com`**: todo el diseño on-demand lo asume
- ⬜ Probar el `docker build` de verdad (nunca se ha ejecutado)
