// Thin client for the Commander Deckbuilder API (FastAPI).
// The base URL is configurable via VITE_API_BASE; without it, dev builds target the
// local API port and production builds use same-origin relative paths (the SPA is
// served by FastAPI itself).

import type { ColorCode } from './components/ui';

const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '');

/** Dial positions the API accepts. There is deliberately no "balanced" label: the
 *  centre is just the untouched middle of a low↔high bar. */
export type DialPosition = 'low' | 'center' | 'high';

/** Dial positions keyed by category. A category the user never touched is simply
 *  absent — the API derives its bands from quotas.yaml. */
export type Dials = Partial<Record<string, DialPosition>>;

export type CommanderListItem = {
  name: string;
  oracle_id: string;
  color_identity: ColorCode[];
  image_uri_art_crop: string | null;
  archetype: string;
  /** The group's curated shortlist (featured_commanders.yaml). Also the only
   *  commanders whose `archetype` is a real judgement — see `curatedArchetype`. */
  featured: boolean;
};

/** The full, readable card image for a commander.
 *
 *  `GET /commanders` ships only `image_uri_art_crop` (it was built for a picker
 *  that rendered cropped art), but this picker shows the whole card so players
 *  who do not know a commander can read what it does. Scryfall serves every size
 *  of a given printing under the same path with only the size segment changing,
 *  so the full image is a pure rewrite of the crop URL — verified byte-identical
 *  against the `image_uri_normal` that `/structure` returns for the same cards.
 *
 *  This is a stopgap: the honest fix is for `/commanders` to return
 *  `image_uri_normal` directly.
 */
export function normalImageUri(artCropUri: string | null): string | null {
  if (!artCropUri) return null;
  return artCropUri.replace('/art_crop/', '/normal/');
}

export type StructureBand = {
  lo: number;
  hi: number;
};

export type CardView = {
  name: string;
  oracle_id: string;
  scryfall_id: string;
  color_identity: ColorCode[];
  type_line: string | null;
  mana_cost: string;
  cmc: number;
  image_uri_normal: string | null;
  image_uri_art_crop: string | null;
};

export type CommanderStructure = {
  commander: CardView;
  dials: Dials;
  categories: Record<string, StructureBand>;
  archetype: string;
  source: 'commander' | 'archetype';
};

export type BuildRequest = {
  commander: string;
  dials: Dials;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      // Non-JSON error body; keep the status line.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function fetchCommanders(): Promise<CommanderListItem[]> {
  const data = await request<{ count: number; commanders: CommanderListItem[] }>(
    '/commanders',
  );
  return data.commanders;
}

/** The bands a build would target, for previewing what the dials do. */
export async function fetchCommanderStructure(
  name: string,
  dials: Dials,
): Promise<CommanderStructure> {
  const params = new URLSearchParams({ commander: name });
  for (const [category, position] of Object.entries(dials)) {
    if (position) params.append('dial', `${category}:${position}`);
  }
  return request<CommanderStructure>(`/structure?${params.toString()}`);
}

// The deck shape is owned by the Result view (a separate task); `/build` is typed
// as `unknown` here on purpose rather than guessed at.
export async function buildDeck(req: BuildRequest): Promise<unknown> {
  return request<unknown>('/build', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

export async function sequentialStart(req: BuildRequest): Promise<unknown> {
  return request<unknown>('/sequential/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}
