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

## Fase 2 — Experimentos de tagging
- ✅ 4 métodos de tagging implementados sobre el set de test (2026-07-13): regex serio, EDHREC headers (ciego a 4/7 categorías — hallazgo), Scryfall otags, LLM cacheado con rúbrica; predicciones en `experiments/tagging/predictions/`, comparador `evaluate.py` listo
- ✅ 🔶 Set de test ~200 cartas etiquetadas a mano por Guille (`test_set_filled.csv`, 2026-07-13)
- ✅ 🔶 Informe comparativo en DECISIONS.md (LLM 0.89 > regex 0.85 > otags 0.75 > EDHREC 0.08) — decidido 2026-07-13: LLM cacheado primario + regex auditoría + otags señal extra; MDFC hechizo//tierra cuentan como lands

## Fase 3 — Experimentos de selección
- ✅ Greedy por categorías (`backend/selector/greedy.py`, 2026-07-13; tagger provisional otags intercambiable, 5 mazos de prueba en `experiments/selection/decks/`, ~2ms/mazo, maybeboard incluido) — 🔶 pendiente evaluación a ojo de Guille
- ✅ CP-SAT (OR-Tools) — port simplificado del TFM (`backend/selector/cp_sat.py`, 2026-07-13; OPTIMAL <0.05s en los 5 mazos, relajación escalonada, Karsten/banlist/identidad nunca se relajan) — 🔶 pendiente comparativa a ojo vs greedy
- ⬜ 🔶 Comparativa (calidad la evalúa Guille) + maybeboard

## Fase 4 — API
- ⬜ Endpoints: buscar comandante, generar 99 + maybeboard, validar swap (<100ms), exportar decklist

## Fase 5 — Frontend
- ⬜ Buscador, vista por categorías, semáforos de cuotas, swap, export
- ⬜ 🔶 Revisión de UX con Guille

## Fase 6 — Despliegue
- ⬜ HF Space Docker (FastAPI + build React), datos precacheados, refresco manual
