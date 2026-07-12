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

## Decisiones cerradas de partida (charter)
- Cuotas [min, max] por categoría funcional, dependientes de comandante/arquetipo; tierras por método Karsten.
- Motor de recomendación: se decide por experimentos (Fase 2).
- Selector: experimento CP-SAT vs greedy (Fase 3).
- Stack: FastAPI + React, HF Space con Docker. Datos: Scryfall bulk + EDHREC.
