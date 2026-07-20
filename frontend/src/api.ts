// Thin client for the Commander Deckbuilder API (FastAPI).
// Same-origin relative paths by default: in production FastAPI serves this SPA
// itself, and in dev Vite proxies the API routes to :8000 (see vite.config.ts),
// so neither environment needs CORS. VITE_API_BASE overrides it for a split
// deploy.

import type { ColorCode } from './components/ui';

const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? '';

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
  /** The whole card, readable: the picker renders this so players who do not
   *  know a commander can read what it does. */
  image_uri_normal: string | null;
  image_uri_art_crop: string | null;
  /** Back face of a double-faced commander (Kefka, Sephiroth, Etali…). Empty
   *  string `""` for single-faced cards — treat any falsy value as "no back". */
  image_uri_back_normal: string;
  image_uri_back_art_crop: string;
  archetype: string;
  /** The group's curated shortlist (featured_commanders.yaml). Also the only
   *  commanders whose `archetype` is a real judgement — see `curatedArchetype`. */
  featured: boolean;
  /** The shortlist's one-line pitch, straight from featured_commanders.yaml.
   *  `null` for every commander outside it, which is most of them. */
  description: string | null;
  /** EDHREC deck count — how many decks run this commander. The server already
   *  orders `/commanders` by it (desc), so the client just preserves arrival
   *  order. Shown discreetly on the pick, EDHREC-style. */
  num_decks: number;
};

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
  image_uri_back_normal: string;
  image_uri_back_art_crop: string;
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

/** A card as `/build` and friends answer it (`DeckCardView`). `score` is null for
 *  basic lands: a basic has no EDHREC score. `count` is present on EVERY card —
 *  1 for non-basics, N for a basic's copies — so it is NOT a "this is a basic"
 *  test the way it is in the TFM. See `ViewCard` in deck.ts. */
export type DeckCard = {
  name: string;
  oracle_id: string;
  scryfall_id: string;
  color_identity: ColorCode[];
  type_line: string | null;
  mana_cost: string;
  cmc: number;
  image_uri_normal: string | null;
  image_uri_art_crop: string | null;
  /** Back face of a double-faced card, empty `""` for single-faced ones. Lets
   *  the deck views offer a front↔back flip on transforming cards. */
  image_uri_back_normal: string;
  image_uri_back_art_crop: string;
  /** Scryfall USD price, a relative "cheap vs expensive" signal only (the group
   *  plays proxies). `null` when the pool card has no USD price — Reserved List
   *  staples (dual lands, Wheel of Fortune) among them. Only the "Caras y
   *  buenas" section shows it; elsewhere the UI ignores it. */
  price_usd: number | null;
  categories: string[];
  count: number;
  slot: string;
  reason: string;
  score: number | null;
};

/** One category's line in the composition panel.
 *
 *  `within_band` is deliberately NOT `lo <= count <= hi`: for `lands` the
 *  effective minimum is `max(lo, karsten_floor)`, and the floor is derived from
 *  the deck's own curve. Only the API can rule on it — see `deck.ts`. */
export type CategoryRow = {
  count: number;
  lo: number;
  hi: number;
  /** `hard`: both bounds bind (lands: the Karsten floor is unbreachable).
   *  `ceiling_only`: only the cap binds (synergy has no floor by nature).
   *  `soft_no_lower`: the cap binds; `lo` is a target the solver aims at. */
  band: 'hard' | 'ceiling_only' | 'soft_no_lower';
  within_band: boolean;
};

/** A mana-curve bucket. Only a count: our solver has no curve objective, so
 *  there is no `target`/`deviation` to draw (unlike the TFM). */
export type CurveRow = { count: number };

export type ColorSourceRow = {
  sources: number;
  demand: number;
  deficit: number;
};

export type Notice = { code: string; message: string };

export type BuildResult = {
  commander_id: string;
  commander_name: string;
  commander: CardView;
  dials: Dials;
  status: string;
  deck_size: number;
  selected_count: number;
  nonbasic_cards: DeckCard[];
  basic_lands: DeckCard[];
  maybeboard: DeckCard[];
  new_cards: DeckCard[];
  /** The "Caras y buenas" section: cards EDHREC's *expensive* page for this
   *  commander runs that the *optimized* (popularity) page underweights BECAUSE
   *  of price — not power. It does NOT mean "play these"; for a proxy group they
   *  may be worth it, but the player decides. Each carries `price_usd` to show
   *  (null = Reserved List). Empty when EDHREC has no expensive page. */
  expensive_cards: DeckCard[];
  category_breakdown: Record<string, CategoryRow>;
  curve_breakdown: Record<string, CurveRow>;
  color_source_breakdown: Record<string, ColorSourceRow>;
  karsten_floor: number;
  lands_target: number;
  target_structure_source: 'commander' | 'archetype';
  relaxation_stage: string;
  objective_value: number;
  solve_time_seconds: number;
  infeasible_reason: string | null;
  warnings: Notice[];
  /** EDHREC recommendations our pool could not resolve — raw name strings,
   *  not Notice objects (and occasionally an empty string from EDHREC). */
  unresolved: string[];
};

/** How the deck travels to the API: by NAME, not oracle_id (unlike the TFM).
 *  The backend is stateless — the deck lives in the client. */
export type DeckCardRef = { name: string; count: number };

export type SwapCandidatesRequest = {
  commander: string;
  dials: Dials;
  deck: DeckCardRef[];
  out: string;
  limit?: number;
};

/** `/sequential/candidates`. One flat `candidates[]` list: we have a single
 *  scorer, so there is no synergy/power split to show. */
export type SwapCandidates = {
  current: DeckCard;
  candidates: DeckCard[];
  feasible_count: number;
  limit: number;
};

/** `/sequential/validate`. The authoritative post-swap verdict: `counts` and
 *  `statuses` are the backend's own, including the recomputed Karsten floor. */
export type SwapValidation = {
  feasible: boolean;
  blockers: Notice[];
  warnings: Notice[];
  counts: Record<string, number>;
  statuses: Record<string, string>;
  karsten_floor: number;
  deck_size: number;
};

export type Maybeboard = Record<string, DeckCard[]>;

/** One suggested replacement for a flagged card. `kind` is the axis it came
 *  from: `same_role` (its own category), `best_overall` (the best card you're
 *  missing, any role) or `reinforce` (the thinnest category). Every one is a
 *  feasible swap for the flagged card. */
export type AuditReplacement = {
  kind: 'same_role' | 'best_overall' | 'reinforce';
  note: string;
  card: DeckCard;
};

/** One doubtful card, why it is doubtful, and up to four feasible replacements. */
export type AuditFlag = {
  card: DeckCard;
  reason: string;
  replacements: AuditReplacement[];
};

/** The deck audit: doubtful cards and good cards the deck is missing. The audit
 *  only points — nothing here changes the deck until the player picks a swap. */
export type AuditResult = {
  doubtful: AuditFlag[];
  missing: DeckCard[];
};

/** One card on the group's banlist: the reason it is banned, plus its image so
 *  the panel can show what the card is. */
export type BanlistCard = {
  name: string;
  reason: string;
  image_uri_normal: string | null;
  oracle_id: string;
  /** Archetypes where this otherwise-banned card is legal (e.g. thematic
   *  staples allowed in enchantress decks). Empty for a flat ban. */
  legal_in_archetypes: string[];
};

/** One card on the watchlist: like a banned card, but `scope` says where the
 *  concern applies (e.g. a commander-only worry). `null` when it is general. */
export type WatchlistCard = BanlistCard & {
  scope: string | null;
};

export type Banlist = {
  banned: BanlistCard[];
  watchlist: WatchlistCard[];
};

export type WhyNotResult = {
  commander_name: string;
  card_name: string;
  eligible: boolean;
  reason_bucket: string;
  reason: string;
};

/** One printing of a card, as the art/language picker consumes it. Every row
 *  is a real scan (the backend drops placeholders); `highres: false` means a
 *  soft (low-res) scan, which the picker badges. */
export type CardPrint = {
  scryfall_id: string;
  set_code: string;
  set_name: string;
  collector_number: string;
  lang: string;
  released_at: string;
  image_status: string;
  highres: boolean;
  image_uri_normal: string;
  image_uri_back_normal: string;
};

export type CardPrints = {
  oracle_id: string;
  name: string;
  prints: CardPrint[];
  default_scryfall_id: string | null;
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

async function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchCommanders(): Promise<CommanderListItem[]> {
  const data = await request<{ count: number; commanders: CommanderListItem[] }>(
    '/commanders',
  );
  return data.commanders;
}

/** The group's banlist and watchlist, for the informational panel. */
export async function fetchBanlist(): Promise<Banlist> {
  return request<Banlist>('/banlist');
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

export async function buildDeck(req: BuildRequest): Promise<BuildResult> {
  return post<BuildResult>('/build', req);
}

/** Same-role, still-feasible alternatives for the card marked to leave. */
export async function sequentialCandidates(
  req: SwapCandidatesRequest,
): Promise<SwapCandidates> {
  return post<SwapCandidates>('/sequential/candidates', req);
}

/** `/swap/replacements`. The audit's role-aware palette (same_role /
 *  best_overall / reinforce) for a manually chosen out card, instead of a flat
 *  top-N ranking. `feasible_count` is the total number of legal swaps. */
export type SwapReplacements = {
  current: DeckCard;
  replacements: AuditReplacement[];
  feasible_count: number;
};

/** Audit-style replacements for the card marked to leave: the same guidance the
 *  audit gives its doubtful cards, applied wherever a card is removed. */
export async function swapReplacements(
  req: SwapCandidatesRequest,
): Promise<SwapReplacements> {
  return post<SwapReplacements>('/swap/replacements', req);
}

/** `/cards/legal`. Cards legal to add for a commander (colour identity + minus
 *  banlist), with art. Advanced mode's "search any card to swap in" source. */
export type LegalCardSearch = {
  count: number;
  cards: DeckCard[];
};

/** Search cards legal to add for `commander`. Typeahead: debounce it. */
export async function searchLegalCards(
  commander: string,
  q: string,
  limit = 12,
): Promise<LegalCardSearch> {
  const params = new URLSearchParams({ commander, q, limit: String(limit) });
  return request<LegalCardSearch>(`/cards/legal?${params.toString()}`);
}

/** `/swap/outs`. The reverse swap: the best deck cards to cut for a chosen `in`
 *  card, weakest first. `current` is the in card; the first `out` is suggested. */
export type SwapOuts = {
  current: DeckCard;
  outs: DeckCard[];
  feasible_count: number;
};

/** Given a card to bring in, ask which deck cards are the best to take out. */
export async function swapOuts(req: {
  commander: string;
  dials: Dials;
  deck: DeckCardRef[];
  in: string;
  limit?: number;
}): Promise<SwapOuts> {
  return post<SwapOuts>('/swap/outs', req);
}

/** `/evaluate`. The live manabase re-evaluation: color sources recomputed over
 *  the deck as it stands (the build's froze at solve time). */
export type DeckEvaluation = {
  color_source_breakdown: Record<string, ColorSourceRow>;
};

/** Recompute the deck's color fixing on its current state (no re-solve). */
export async function evaluateDeck(req: {
  commander: string;
  dials: Dials;
  deck: DeckCardRef[];
}): Promise<DeckEvaluation> {
  return post<DeckEvaluation>('/evaluate', req);
}

/** Validate one prospective swap. The response is the source of truth for the
 *  post-swap category verdicts — the client must not re-derive them. */
export async function sequentialValidate(req: {
  commander: string;
  dials: Dials;
  deck: DeckCardRef[];
  out: string;
  in: string;
}): Promise<SwapValidation> {
  return post<SwapValidation>('/sequential/validate', req);
}

export async function fetchMaybeboard(req: {
  commander: string;
  dials: Dials;
  deck: DeckCardRef[];
  limit?: number;
}): Promise<Maybeboard> {
  const data = await post<{ maybeboard: Maybeboard; limit: number }>(
    '/maybeboard',
    req,
  );
  return data.maybeboard;
}

/** Audit a deck: doubtful cards (with replacement palettes) and good cards it is
 *  missing. Reads the live deck, so it reflects the swaps already made. */
export async function auditDeck(req: {
  commander: string;
  dials: Dials;
  deck: DeckCardRef[];
}): Promise<AuditResult> {
  return post<AuditResult>('/audit', req);
}

export async function whyNotCard(
  commander: string,
  card: string,
): Promise<WhyNotResult> {
  const params = new URLSearchParams({ commander, card });
  return request<WhyNotResult>(`/why-not?${params.toString()}`);
}

export async function searchCardNames(
  query: string,
  limit = 20,
): Promise<string[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  const data = await request<{ count: number; names: string[] }>(
    `/cards/search?${params.toString()}`,
  );
  return data.names;
}

/** The decklist as text. The Archidekt format lives in the backend on purpose:
 *  re-implementing it here would be a second, drifting copy. `slot` is the
 *  section the player sees a card in, which only the client knows after swaps. */
export async function exportDeck(req: {
  commander: string;
  deck: { name: string; count: number; slot: string }[];
  maybeboard?: { name: string }[];
  new_cards?: { name: string }[];
}): Promise<string> {
  const response = await fetch(`${API_BASE}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...req, format: 'archidekt' }),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.text();
}

/** Pull the `filename="…"` out of a Content-Disposition header, or fall back. */
/** The printings gallery for one card (already high-res-filtered server-side). */
export async function fetchCardPrints(oracleId: string): Promise<CardPrints> {
  return request<CardPrints>(`/cards/${encodeURIComponent(oracleId)}/prints`);
}

/** Full-art printings a player may choose for one basic land. Only full-art
 *  (the house look), defaulting to Theros — or the TDM "dragon eye" printing
 *  when `theme` is 'dragon' (Dragon decks). */
export async function fetchBasicFullart(
  name: string,
  theme?: string,
): Promise<CardPrints> {
  const q = theme ? `?theme=${encodeURIComponent(theme)}` : '';
  return request<CardPrints>(
    `/cards/basics/${encodeURIComponent(name)}/fullart${q}`,
  );
}

/** Default (Spanish-first) printing for up to 25 cards. `null` for a card means
 *  "keep the art you already have". Cold cards cost the backend one Scryfall
 *  search each, so the first resolution of a deck takes a few seconds. */
export async function fetchPrintDefaults(
  oracleIds: string[],
): Promise<Record<string, CardPrint | null>> {
  const data = await request<{ defaults: Record<string, CardPrint | null> }>(
    '/cards/prints/defaults',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ oracle_ids: oracleIds }),
    },
  );
  return data.defaults;
}

function filenameFromDisposition(header: string | null, fallback: string): string {
  const match = header?.match(/filename="?([^";]+)"?/i);
  return match ? match[1] : fallback;
}

/** Download the deck as a print-and-cut proxy PDF (3×3, real card size). Send
 *  the commander plus the cards to print — the caller decides what those are
 *  (the non-basics plus the basic lands by count; the backend prints basics in
 *  the Theros Beyond Death full-art). The PDF is rendered server-side and
 *  streamed back as a blob, which the browser saves under the name the backend
 *  chose (Content-Disposition), or `<slug>_proxies.pdf` if that header is lost. */
export async function exportProxyPdf(req: {
  commander: string;
  cards: { name: string; count: number }[];
  includeTokens?: boolean;
  /** Art picker choices: card name -> chosen printing's scryfall_id. The PDF
   *  prints those printings instead of the pool's default art. */
  artOverrides?: Record<string, string>;
}): Promise<void> {
  const { includeTokens, artOverrides, ...rest } = req;
  const response = await fetch(`${API_BASE}/export/pdf`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...rest,
      include_tokens: includeTokens ?? false,
      art_overrides: artOverrides ?? {},
    }),
  });
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
  const blob = await response.blob();
  const slug = req.commander.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
  const filename = filenameFromDisposition(
    response.headers.get('Content-Disposition'),
    `${slug}_proxies.pdf`,
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
