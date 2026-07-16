// Client-side deck state and the swap engine. The backend is stateless: the deck
// lives here, and every call carries it.
//
// Adapted from the TFM's `deck.ts`, with three contract differences:
//
//  1. The deck travels by NAME (`{name, count}`), not by oracle_id.
//  2. `count` is set on EVERY card (1 for non-basics), so it cannot double as
//     the "is this a basic?" test the TFM used. Basics are the cards the API put
//     in `basic_lands[]`; `ViewCard.basic` records that at the boundary rather
//     than sniffing `type_line`.
//  3. `within_band` is NOT recomputed here. For `lands` the effective minimum is
//     `max(lo, karsten_floor)`, and the floor is derived from the deck's own
//     curve — so a swap can move the floor itself. Only `/sequential/validate`
//     can rule on it, and `applySwap` adopts its verdict.

import { useCallback, useMemo, useState } from 'react';
import type {
  BuildResult,
  CategoryRow,
  CurveRow,
  DeckCard,
  DeckCardRef,
  SwapValidation,
} from './api';

const LANDS_CATEGORY = 'lands';
/** CMCs at or above this collapse into the "7+" bucket (quotas/lands.py). */
const CURVE_TOP_BUCKET = 7;

/** A card plus what the API's response shape told us about it: whether it came
 *  from `basic_lands[]`. Carried explicitly because `count` cannot say so. */
export type ViewCard = DeckCard & { basic: boolean };

export function toViewCard(card: DeckCard, basic = false): ViewCard {
  return { ...card, basic };
}

/** The whole deck as view cards: non-basics then basics, each tagged. */
export function deckCards(deck: BuildResult): ViewCard[] {
  return [
    ...deck.nonbasic_cards.map((card) => toViewCard(card, false)),
    ...deck.basic_lands.map((card) => toViewCard(card, true)),
  ];
}

/** The deck as the API wants it: `{name, count}` for all 99 cards. */
export function expandDeck(deck: BuildResult): DeckCardRef[] {
  return [...deck.nonbasic_cards, ...deck.basic_lands].map((card) => ({
    name: card.name,
    count: card.count,
  }));
}

/** Bucket label for a mana value — a replica of `curve_bucket` in
 *  quotas/lands.py (truncate toward zero, clamp at 0, collapse the 7+ tail). */
export function curveBucket(cmc: number): string {
  const value = Math.max(0, Math.trunc(cmc));
  return value >= CURVE_TOP_BUCKET ? '7+' : String(value);
}

/** Histogram the non-land cards by curve bucket — a replica of
 *  `_curve_breakdown` in app/service.py (lands excluded by category, copies
 *  counted). Exact rather than stale: unlike the TFM we get a real `cmc` on
 *  every card, so a swapped deck's curve is recomputed honestly instead of
 *  being labelled "curva del mazo inicial". */
export function recomputeCurve(deck: BuildResult): Record<string, CurveRow> {
  const curve: Record<string, number> = {};
  for (const card of deck.nonbasic_cards) {
    if (card.categories.includes(LANDS_CATEGORY)) continue;
    const bucket = curveBucket(card.cmc);
    curve[bucket] = (curve[bucket] ?? 0) + card.count;
  }
  const rows: Record<string, CurveRow> = {};
  for (const [bucket, count] of Object.entries(curve)) rows[bucket] = { count };
  return rows;
}

/** Fold the API's verdict for a swap into the breakdown. `counts` omits a
 *  category that sits at zero, hence the `?? 0`; `statuses` carries all of them.
 *  Bands (lo/hi/band) are structural — a swap never moves them. */
function breakdownFromValidation(
  breakdown: Record<string, CategoryRow>,
  validation: SwapValidation,
): Record<string, CategoryRow> {
  const rows: Record<string, CategoryRow> = {};
  for (const [code, row] of Object.entries(breakdown)) {
    rows[code] = {
      ...row,
      count: validation.counts[code] ?? 0,
      within_band: validation.statuses[code] === 'in_range',
    };
  }
  return rows;
}

/** Replace `outName` with `chosen` among the non-basics, adopting the API's
 *  post-swap verdict. Pure: returns a new BuildResult and mutates nothing.
 *  No-op (same ref) if `outName` is not a current non-basic.
 *
 *  `validation` must be the `/sequential/validate` response for THIS swap on
 *  THIS deck — that is what makes the new breakdown the backend's judgement
 *  rather than a client-side guess at the Karsten floor. */
export function applySwap(
  deck: BuildResult,
  outName: string,
  chosen: DeckCard,
  validation: SwapValidation,
): BuildResult {
  if (!deck.nonbasic_cards.some((card) => card.name === outName)) return deck;
  const nonbasics = deck.nonbasic_cards
    .filter((card) => card.name !== outName)
    .concat(chosen);
  const next: BuildResult = { ...deck, nonbasic_cards: nonbasics };
  return {
    ...next,
    category_breakdown: breakdownFromValidation(
      deck.category_breakdown,
      validation,
    ),
    karsten_floor: validation.karsten_floor,
    curve_breakdown: recomputeCurve(next),
  };
}

/** Mutable deck state with the client-side swap engine. `deckRefs` is the
 *  `{name, count}` payload every API call needs. `reset` re-seeds on a fresh
 *  build. */
export function useMutableDeck(initial: BuildResult): {
  deck: BuildResult;
  deckRefs: DeckCardRef[];
  swapped: boolean;
  swap: (outName: string, chosen: DeckCard, validation: SwapValidation) => void;
  reset: (next: BuildResult) => void;
} {
  const [deck, setDeck] = useState<BuildResult>(initial);
  // Whether the deck has drifted from the solved one. The colour-fixing rows are
  // the solver's and are not re-optimised on a swap, so the view has to say so.
  const [swapped, setSwapped] = useState(false);

  const deckRefs = useMemo(() => expandDeck(deck), [deck]);

  const swap = useCallback(
    (outName: string, chosen: DeckCard, validation: SwapValidation) => {
      setDeck((current) => applySwap(current, outName, chosen, validation));
      setSwapped(true);
    },
    [],
  );

  const reset = useCallback((next: BuildResult) => {
    setDeck(next);
    setSwapped(false);
  }, []);

  return { deck, deckRefs, swapped, swap, reset };
}
