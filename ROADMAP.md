# ROADMAP

Estado global del proyecto. Lo mantiene la sesión orquestadora.

Leyenda: ⬜ pendiente · 🔄 en curso · ✅ hecho · 🔶 requiere OK de Guille

## Fase 0 — Esqueleto y datos
- ✅ Scaffold inicial del repo (backend/, frontend/, data/, experiments/, docs/)
- ✅ Pipeline de datos Scryfall: bulk oracle cards → filtro legalidad Commander → modelo de carta interno (`backend/pipeline/`, 2026-07-12; 31.622 cartas, 18 tests)
- ✅ Scaffold FastAPI + React + Vite + Dockerfile HF Spaces (2026-07-12; Dockerfile pendiente de probar con build real en HF)
- ✅ Fetcher EDHREC por comandante (recomendaciones + scores), con caché (`backend/pipeline/edhrec.py`, 2026-07-12; verificado con Atraxa y Krenko)
- ⬜ 🔶 `banlist.yaml` (oficial + custom; las custom las decide Guille)

## Fase 1 — Sistema de cuotas
- ⬜ Cálculo de tierras Karsten (conteo por curva + distribución de color por pips)
- ⬜ 🔶 Esquema de cuotas `{categoria: {min, max}}` por arquetipo (valores los valida Guille)
- ⬜ Validador de mazo de 99 (estado por categoría)

## Fase 2 — Experimentos de tagging
- ⬜ 3-4 métodos de tagging sobre el mismo pool de test
- ⬜ 🔶 Set de test ~200 cartas etiquetadas a mano (propuesta → revisión Guille)
- ⬜ 🔶 Informe comparativo en DECISIONS.md → Guille decide

## Fase 3 — Experimentos de selección
- ⬜ Greedy por categorías
- ⬜ CP-SAT (OR-Tools)
- ⬜ 🔶 Comparativa (calidad la evalúa Guille) + maybeboard

## Fase 4 — API
- ⬜ Endpoints: buscar comandante, generar 99 + maybeboard, validar swap (<100ms), exportar decklist

## Fase 5 — Frontend
- ⬜ Buscador, vista por categorías, semáforos de cuotas, swap, export
- ⬜ 🔶 Revisión de UX con Guille

## Fase 6 — Despliegue
- ⬜ HF Space Docker (FastAPI + build React), datos precacheados, refresco manual
