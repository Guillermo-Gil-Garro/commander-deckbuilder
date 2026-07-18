// Card-art state: Spanish-by-default resolution plus the user's manual picks.
//
// Two layers, resolved per oracle_id:
//   1. `manual` — printings the user chose in the ArtPicker. Global (a Sol Ring
//      pick applies to every deck) and persisted in localStorage.
//   2. `defaults` — the backend's Spanish-first policy (es-highres, else keep
//      pool art, else en-highres), fetched in chunks after a build and also
//      cached in localStorage so a repeat deck resolves without the network.
//      `null` is a real (negative) answer: "the pool art is already right".
//
// `withArt` rewrites a BuildResult's image URLs so every consumer (DeckView,
// hover previews, the swap tiles) shows the chosen art with zero wiring.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchPrintDefaults, type BuildResult, type CardPrint } from './api';

const MANUAL_KEY = 'art-overrides-v1';
// v2 (2026-07-18): the default policy started admitting low-res Spanish scans,
// so v1's cached answers (null where only soft Spanish existed) are stale.
const DEFAULTS_KEY = 'print-defaults-v2';
/** PrintDefaultsRequest cap, mirrored: the backend rejects bigger batches. */
const CHUNK = 25;

type ManualMap = Record<string, CardPrint>;
type DefaultsMap = Record<string, CardPrint | null>;

function readStore<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null; // corrupt/blocked storage: behave as empty, never crash
  }
}

function writeStore(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Quota/blocked storage: choices just will not survive a reload.
  }
}

/** Every oracle_id whose art can be overridden: commander + non-basics.
 *  Basics stay out — the PDF prints them in the Theros house style and a
 *  gallery of 500 Mountain printings helps nobody. */
function deckOracleIds(deck: BuildResult): string[] {
  const ids = deck.nonbasic_cards.map((card) => card.oracle_id);
  ids.push(deck.commander.oracle_id);
  return ids;
}

export function useArtOverrides(deck: BuildResult | null): {
  /** oracle_id -> the printing to show (manual pick, else Spanish default). */
  resolved: Record<string, CardPrint>;
  setManual: (oracleId: string, print: CardPrint) => void;
  clearManual: (oracleId: string) => void;
  manualIds: Set<string>;
} {
  const [manual, setManualMap] = useState<ManualMap>(
    () => readStore<ManualMap>(MANUAL_KEY) ?? {},
  );
  const [defaults, setDefaults] = useState<DefaultsMap>(
    () => readStore<DefaultsMap>(DEFAULTS_KEY) ?? {},
  );

  // Resolve the Spanish defaults for whatever cards of this deck we have not
  // answered before. Chunked; results apply as each chunk lands, so the deck
  // turns Spanish progressively on a cold cache. Stale responses are dropped.
  useEffect(() => {
    if (!deck) return;
    const known = readStore<DefaultsMap>(DEFAULTS_KEY) ?? {};
    const pending = deckOracleIds(deck).filter((id) => !(id in known));
    if (pending.length === 0) return;
    let active = true;
    void (async () => {
      for (let start = 0; start < pending.length; start += CHUNK) {
        const chunk = pending.slice(start, start + CHUNK);
        try {
          const answers = await fetchPrintDefaults(chunk);
          if (!active) return;
          setDefaults((current) => {
            const next = { ...current, ...answers };
            writeStore(DEFAULTS_KEY, next);
            return next;
          });
        } catch {
          // Scryfall down or offline: the deck simply stays in pool art.
          return;
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [deck]);

  const setManual = useCallback((oracleId: string, print: CardPrint) => {
    setManualMap((current) => {
      const next = { ...current, [oracleId]: print };
      writeStore(MANUAL_KEY, next);
      return next;
    });
  }, []);

  const clearManual = useCallback((oracleId: string) => {
    setManualMap((current) => {
      if (!(oracleId in current)) return current;
      const next = { ...current };
      delete next[oracleId];
      writeStore(MANUAL_KEY, next);
      return next;
    });
  }, []);

  const resolved = useMemo(() => {
    const map: Record<string, CardPrint> = {};
    for (const [id, print] of Object.entries(defaults)) {
      if (print) map[id] = print;
    }
    for (const [id, print] of Object.entries(manual)) {
      map[id] = print;
    }
    return map;
  }, [manual, defaults]);

  const manualIds = useMemo(() => new Set(Object.keys(manual)), [manual]);

  return { resolved, setManual, clearManual, manualIds };
}

/** A BuildResult whose card images follow `resolved`. Same object when nothing
 *  applies, so referential equality keeps memos downstream intact. */
export function withArt(
  deck: BuildResult,
  resolved: Record<string, CardPrint>,
): BuildResult {
  if (Object.keys(resolved).length === 0) return deck;
  let touched = false;
  const swapImages = <T extends {
    oracle_id: string;
    image_uri_normal: string | null;
    image_uri_back_normal: string;
  }>(card: T): T => {
    const print = resolved[card.oracle_id];
    if (!print) return card;
    touched = true;
    return {
      ...card,
      image_uri_normal: print.image_uri_normal,
      image_uri_back_normal: print.image_uri_back_normal,
    };
  };
  const nonbasics = deck.nonbasic_cards.map(swapImages);
  const commander = swapImages(deck.commander);
  if (!touched) return deck;
  return { ...deck, nonbasic_cards: nonbasics, commander };
}

/** The art cache as a plain read (no hook, no network): Spanish defaults with
 *  the user's manual picks on top. For surfaces outside the deck (the Setup's
 *  commander picker) that should show already-known art without triggering any
 *  resolution — an unknown card simply keeps its stock image. */
export function readArtCache(): Record<string, CardPrint> {
  const map: Record<string, CardPrint> = {};
  const defaults = readStore<DefaultsMap>(DEFAULTS_KEY) ?? {};
  for (const [id, print] of Object.entries(defaults)) {
    if (print) map[id] = print;
  }
  const manual = readStore<ManualMap>(MANUAL_KEY) ?? {};
  for (const [id, print] of Object.entries(manual)) {
    map[id] = print;
  }
  return map;
}

/** The export map the PDF endpoint wants: card NAME -> chosen scryfall_id,
 *  covering the commander and every non-basic with an override in effect. */
export function artOverridesForExport(
  deck: BuildResult,
  resolved: Record<string, CardPrint>,
): Record<string, string> {
  const out: Record<string, string> = {};
  const claim = (name: string, oracleId: string) => {
    const print = resolved[oracleId];
    if (print) out[name] = print.scryfall_id;
  };
  claim(deck.commander_name, deck.commander.oracle_id);
  for (const card of deck.nonbasic_cards) claim(card.name, card.oracle_id);
  return out;
}
