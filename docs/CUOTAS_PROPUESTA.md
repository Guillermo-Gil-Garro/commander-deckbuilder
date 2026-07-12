# Propuesta de cuotas por arquetipo y diales de usuario 🔶

Estado: **borrador para revisión de Guille** (2026-07-12). Nada de esto es config activa;
cuando se apruebe, se convierte en `quotas.yaml` + código.

## 1. Modelo de capas

Las cuotas `[min, max]` de un mazo se resuelven por precedencia:

1. **Fallback global** — si no sabemos nada del comandante.
2. **Arquetipo** — asignado al comandante (ver §5).
3. **Override por comandante** — solo para los comandantes que Guille quiera
   individualizar; pisa categorías sueltas, no el bloque entero.
4. **Diales del usuario** — desplazan la banda resultante (ver §4).

**Karsten es transversal, no una capa**: el suelo de tierras (regresión) y las fuentes
de color (hipergeométrica) se calculan siempre sobre el mazo actual. El `min` efectivo
de tierras es SIEMPRE `max(min_banda, suelo_karsten)`: el suelo es infranqueable
(decisión de Guille 2026-07-12). El dial low de tierras puede acercar la banda al
suelo, nunca cruzarlo.

## 2. Categorías

Las del charter: `tierras`, `ramp`, `robo`, `removal` (puntual), `boardwipe`,
`wincons`, `sinergia` (solo techo — el resto del mazo ES el paquete de sinergia,
el techo evita el mazo-tema-total tipo "Radha con 40 tierras extra").

Notas de membresía:
- Contrahechizos cuentan como `removal` (interacción puntual); no son categoría propia.
- Una carta puede contar en varias categorías (un dork es criatura del paquete Y ramp),
  con supresión de umbrella tags como en el TFM (una fetch es tierra, no tutor).

## 3. Arquetipos y bandas propuestas (mazo de 99)

| Categoría | Fallback (Midrange) | Aggro/Go-wide | Control/Stax | Spellslinger | Voltron | Motor de tumba | Lands-matter |
|---|---|---|---|---|---|---|---|
| tierras | 36–39 | 33–36 | 36–40 | 34–37 | 34–37 | 35–38 | 38–42 |
| ramp | 9–12 | 7–10 | 10–13 | 8–11 | 8–11 | 8–11 | 12–16 |
| robo | 9–12 | 8–11 | 10–14 | 10–14 | 9–12 | 9–13 | 9–12 |
| removal | 8–11 | 6–9 | 10–14 | 8–12 | 7–10 | 7–10 | 6–9 |
| boardwipe | 3–5 | 1–3 | 4–6 | 2–4 | 1–3 | 2–4 | 2–4 |
| wincons | 2–4 | 2–4 | 1–3 | 2–4 | 0–2 (*) | 2–4 | 2–4 |
| sinergia (techo) | ≤28 | ≤35 | ≤20 | ≤30 | ≤30 | ≤30 | ≤18 |

(*) En Voltron la wincon es el comandante; el paquete de equipos/auras vive en sinergia.

Mejoras sobre el fallback del TFM (37/11/9/9/28 plano): bandas en lugar de centros,
diferenciadas por arquetipo, y el techo de sinergia como categoría de primera clase.

## 4. Diales de usuario (UI)

Por categoría ajustable, una **barra de selección** entre dos extremos con frase meme;
la posición central es el punto de partida y **no lleva frase** (decisión de Guille
2026-07-12). Tres posiciones por ahora (low / centro / high); ampliable a 5 si algún
día queremos grados intermedios sin romper el modelo.

Semántica: el dial desplaza la banda entera `±δ` (low: `[min−δ, max−δ]`, high:
`[min+δ, max+δ]`). δ por categoría: tierras ±3, ramp ±3, robo ±3, removal ±3,
boardwipe ±2, sinergia ±6 (solo mueve el techo).

Frases confirmadas por Guille:

| Categoría | Low | High |
|---|---|---|
| tierras | "Mamá se llevó las tierras, qué caradura..." | "¡MOZÁ! ¡TENGO TIERRAS!" |
| removal | "Soy pecifista" (sic, no es errata) | "¡Voy a matar a Moe! Weeeee" |
| boardwipe | "Mi gente, tamo en japón. ¡Gente con cojone!" | "50.000 people used to live here... now it's a ghost town." |
| ramp | "курва (kurwa para los que no leen cirílico)" | "It's raining lands! Hallelujah! It's raining lands!" |
| robo | "Topdicker" | "Piggyhands" |
| sinergia | "5C goodstuff" | "Technologia!" |

Seis diales confirmados (2026-07-12). Sin dial para `wincons` (banda estrecha, poco
juego real) ni para contrahechizos (viven dentro de removal).

## 5. Asignación comandante → arquetipo

- Mapeo manual en `quotas.yaml` para los comandantes que interesen (los define Guille
  cuando quiera; formato abajo).
- Para el resto: heurística por temas de EDHREC (el fetcher ya trae las categorías)
  con fallback a Midrange si no hay señal clara. La heurística se calibra en Fase 2
  con el set de test.

Esquema previsto de `quotas.yaml`:

```yaml
defaults: { archetype: midrange }
archetypes:
  midrange: { tierras: [36, 39], ramp: [9, 12], ... }
  aggro:    { ... }
commanders:
  "Krenko, Mob Boss":
    archetype: aggro
  "Atraxa, Praetors' Voice":
    archetype: midrange
    overrides: { robo: [10, 14] }   # pisa solo esa categoría
```

## 6. Preguntas abiertas 🔶

Resueltas 2026-07-12: suelo Karsten infranqueable; 6 diales confirmados con memes.

1. ¿Validas arquetipos y bandas [min, max] de la tabla de §3? (Cualquier celda es
   discutible; son puntos de partida razonables, no dogma.)
2. ¿δ de los diales OK? (tierras ±3, ramp ±3, robo ±3, removal ±3, boardwipe ±2,
   sinergia ±6)
3. Lista de comandantes a individualizar — Guille la envía cuando quiera.
