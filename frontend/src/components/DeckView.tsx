// Reusable deck presentation. Ported from the TFM's `components/DeckView.tsx`
// with the price and Game Changer columns removed (this project has neither) and
// the export delegated to the API.

import { useMemo, useState, type ReactNode } from 'react';
import {
  LayoutList,
  Loader2,
  Printer,
  Sparkles,
  Tags,
  TriangleAlert,
  X,
} from 'lucide-react';
import { Button, Panel } from './ui';
import { CardTile } from './cards';
import { categoryLabel } from '../labels';
import { commanderCard, deckCards, type ViewCard } from '../deck';
import {
  exportProxyPdf,
  type BuildResult,
  type CategoryRow,
  type ColorSourceRow,
} from '../api';

// Spanish labels for the primary card type (derived from type_line), MTGGoldfish-style.
const TYPE_LABELS: Record<string, string> = {
  Creature: 'Criaturas',
  Instant: 'Instantáneos',
  Sorcery: 'Conjuros',
  Artifact: 'Artefactos',
  Enchantment: 'Encantamientos',
  Land: 'Tierras',
  Planeswalker: 'Planeswalkers',
  Battle: 'Batallas',
  Other: 'Otros',
};

// Display order for primary-type groups (MTGGoldfish convention: lands last).
const TYPE_ORDER = [
  'Creature',
  'Instant',
  'Sorcery',
  'Artifact',
  'Enchantment',
  'Planeswalker',
  'Battle',
  'Land',
  'Other',
] as const;

// Priority to derive a card's PRIMARY type from a (possibly multi-type) type_line.
// Functional spell types first, permanents next, lands last (a creature-land reads
// as a creature; an artifact-creature as a creature — its defining play pattern).
const TYPE_PRIORITY = [
  'Planeswalker',
  'Creature',
  'Instant',
  'Sorcery',
  'Battle',
  'Artifact',
  'Enchantment',
  'Land',
] as const;

function primaryType(typeLine: string | null): string {
  if (!typeLine) return 'Other';
  // Use the front face only (split/MDFC cards: "A // B").
  const front = typeLine.split('//', 1)[0];
  for (const t of TYPE_PRIORITY) {
    if (front.includes(t)) return t;
  }
  return 'Other';
}

// Our eight categories, in the order labels.ts declares them. One list serves as
// both the assignment priority and the display order.
//
// `lands` FIRST is deliberate and differs from the TFM (which sank it): a land
// that also ramps is still a land to a player looking for his mana base, and the
// backend agrees (it slots Ancient Tomb under `lands`). `synergy` sits last as
// the umbrella it is defined to be — "cartas afines que no caen en los roles
// anteriores" (labels.ts).
const CATEGORY_PRIORITY = [
  'lands',
  'ramp',
  'card_draw',
  'removal',
  'board_wipe',
  'wincons',
  'protection',
  'synergy',
] as const;

const UNCATEGORIZED = '__uncategorized__';
const COMMANDER_GROUP = '__commander__';

function primaryCategory(categories: string[]): string {
  for (const c of CATEGORY_PRIORITY) {
    if (categories.includes(c)) return c;
  }
  return categories[0] ?? UNCATEGORIZED;
}

function categoryGroupLabel(code: string): string {
  if (code === UNCATEGORIZED) return 'Sin categoría';
  return categoryLabel(code);
}

export function CompositionPanel({ result }: { result: BuildResult }) {
  const breakdown = result.category_breakdown;
  return (
    <Panel>
      <h3 className="mb-1 text-lg font-semibold">Composición vs estructura</h3>
      <p className="mb-4 text-xs text-zinc-500 dark:text-zinc-400">
        Conteo por categoría frente a la banda objetivo. Una carta cuenta en
        todas sus categorías (una tierra que rampea suma en Tierras y en Ramp),
        así que los totales suman más de 99.
      </p>
      <div className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
        {Object.entries(breakdown).map(([code, row]) => (
          <CategoryBar
            key={code}
            code={code}
            row={row}
            karstenFloor={result.karsten_floor}
          />
        ))}
      </div>
    </Panel>
  );
}

// What each band kind actually binds, said plainly. Nothing here is inferred
// from the numbers: `band` is the API's own statement about the category.
const BAND_NOTE: Record<CategoryRow['band'], string> = {
  hard: 'Banda dura: el suelo de Karsten es infranqueable.',
  ceiling_only: 'Solo techo: esta categoría no tiene mínimo por naturaleza.',
  soft_no_lower:
    'Techo firme; el mínimo es un objetivo que el solver persigue, no una barrera.',
};

function CategoryBar({
  code,
  row,
  karstenFloor,
}: {
  code: string;
  row: CategoryRow;
  karstenFloor: number;
}) {
  const lo = row.lo;
  const hi = Math.max(row.hi, lo);
  const scaleMax = Math.max(hi, row.count, 1);
  const bandStart = (lo / scaleMax) * 100;
  const bandWidth = ((hi - lo) / scaleMax) * 100;
  const countPos = (row.count / scaleMax) * 100;
  // For `lands` the real minimum is max(lo, karsten_floor) — the printed band
  // cannot show it, so `within_band` may be false on a count that looks inside.
  // Saying so is the whole point of the tooltip.
  const note =
    code === 'lands'
      ? `${BAND_NOTE.hard} Suelo efectivo: ${Math.max(lo, karstenFloor)}.`
      : BAND_NOTE[row.band];
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2 text-sm">
        <span className="font-medium" title={note}>
          {categoryLabel(code)}
        </span>
        <span
          className={`tabular-nums ${
            row.within_band
              ? 'text-zinc-500 dark:text-zinc-400'
              : 'font-semibold text-amber-700 dark:text-amber-300'
          }`}
          title={note}
        >
          {row.count}
          <span className="text-zinc-400 dark:text-zinc-500">
            {' '}
            ({lo}–{hi})
          </span>
        </span>
      </div>
      <div className="relative h-2.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
        <span
          aria-hidden="true"
          className="absolute top-0 h-full rounded-full bg-emerald-500/25"
          style={{ left: `${bandStart}%`, width: `${Math.max(bandWidth, 1)}%` }}
        />
        <span
          aria-hidden="true"
          className={`absolute top-0 h-full w-1.5 -translate-x-1/2 rounded-full ${
            row.within_band ? 'bg-emerald-500' : 'bg-amber-500'
          }`}
          style={{ left: `${Math.min(countPos, 100)}%` }}
        />
      </div>
    </div>
  );
}

type SortAxis = 'type' | 'category';

type CardGroup = { key: string; label: string; cards: ViewCard[] };

// Card count for a group header: basics count as their copy multiplier
// (Mountain ×6 is 6 cards, not 1).
function groupCount(cards: ViewCard[]): number {
  return cards.reduce((sum, card) => sum + card.count, 0);
}

// Group cards by the active axis. Within a group, cards keep score-desc order
// (basics carry `score: null` and sink to the bottom of Tierras).
//
// NOTE: both axes are a PARTITION of the 99 — each card lands in exactly one
// group, so the headers sum to 99. That is why the category headers do NOT
// match the composition panel, which counts a card in every category it has.
// The commander is prepended as its own group in BOTH axes: it is the deck's
// centrepiece, not a spell type or a quota slot.
function groupCards(
  cards: ViewCard[],
  sort: SortAxis,
  commander: ViewCard,
): CardGroup[] {
  const sorted = [...cards].sort(
    (a, b) => (b.score ?? 0) - (a.score ?? 0) || a.name.localeCompare(b.name),
  );
  const buckets = new Map<string, ViewCard[]>();
  for (const card of sorted) {
    const key =
      sort === 'type'
        ? primaryType(card.type_line)
        : primaryCategory(card.categories);
    const list = buckets.get(key);
    if (list) list.push(card);
    else buckets.set(key, [card]);
  }

  const commanderGroup: CardGroup = {
    key: COMMANDER_GROUP,
    label: 'Comandante',
    cards: [commander],
  };

  if (sort === 'type') {
    return [
      commanderGroup,
      ...TYPE_ORDER.filter((t) => buckets.has(t)).map((t) => ({
        key: t,
        label: TYPE_LABELS[t] ?? t,
        cards: buckets.get(t)!,
      })),
    ];
  }
  const order = [...CATEGORY_PRIORITY, UNCATEGORIZED];
  return [
    commanderGroup,
    ...order
      .filter((c) => buckets.has(c))
      .map((c) => ({ key: c, label: categoryGroupLabel(c), cards: buckets.get(c)! })),
  ];
}

function ToggleGroup<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: { value: T; label: string; icon: ReactNode }[];
  onChange: (value: T) => void;
  ariaLabel: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="inline-flex rounded-lg border border-black/10 bg-white/70 p-0.5 dark:border-white/10 dark:bg-zinc-950/40"
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(opt.value)}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
              active
                ? 'accent-bg'
                : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100'
            }`}
          >
            {opt.icon}
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

// A composition issue the export gate warns about, derived purely from the live
// `category_breakdown` (no fetch). `obligation` = below a real floor (e.g. lands
// under the Karsten minimum): the deck is illegal/broken and should be fixed.
// `recommendation` = over a ceiling: allowed, but worth a trim.
type CompositionIssue = {
  category: string;
  level: 'obligation' | 'recommendation';
  message: string;
};

// Categories whose floor is treated as an obligation even though the solver
// bands them soft: interaction and board wipes. Below these the export check
// blocks (with an override), not just nudges (Guille 2026-07-19).
const HARD_FLOOR_CATEGORIES = new Set(['removal', 'board_wipe']);

function compositionIssues(
  result: BuildResult,
  liveColors: Record<string, ColorSourceRow> | null,
  colorBaseline: Record<string, ColorSourceRow> | null,
): CompositionIssue[] {
  const issues: CompositionIssue[] = [];
  for (const [category, row] of Object.entries(result.category_breakdown)) {
    if (row.within_band) continue;
    const label = categoryLabel(category);
    if (row.count < row.lo) {
      // Obligations are the floors the deck must not break: the hard Karsten
      // land floor, plus interaction and board wipes (Guille 2026-07-19: leave
      // those hard or people skip them). Every other category's floor is a soft
      // target — wincons, synergy, ramp, protection, draw — so under it is a
      // recommendation, not a block.
      const isHardFloor =
        row.band === 'hard' || HARD_FLOOR_CATEGORIES.has(category);
      issues.push({
        category,
        level: isHardFloor ? 'obligation' : 'recommendation',
        message: `${label}: ${row.count}, por debajo del mínimo (${row.lo}).`,
      });
    } else if (row.count > row.hi) {
      issues.push({
        category,
        level: 'recommendation',
        message: `${label}: ${row.count}, por encima del máximo (${row.hi}).`,
      });
    }
  }
  // Color fixing is a *soft* objective the solver already balanced, so a
  // multicolor deck legitimately ships with deficits — flagging those on every
  // export would be noise. What matters is whether the player's *edits* made a
  // color worse than the build delivered: compare the live deficit against the
  // build baseline and warn (as a recommendation) only when it grew.
  // Compare live against the same-counter baseline (the freshly-built deck),
  // falling back to the build's own numbers only until the baseline arrives.
  const base = colorBaseline ?? result.color_source_breakdown;
  const live = liveColors ?? base;
  for (const [color, row] of Object.entries(live)) {
    const baseDeficit = base[color]?.deficit ?? 0;
    if (row.deficit > baseDeficit) {
      issues.push({
        category: `color-${color}`,
        level: 'recommendation',
        message: `Fuentes de ${color}: ${row.sources} (demanda ${row.demand}); tus cambios dejaron el fixing peor que el mazo original.`,
      });
    }
  }
  // Obligations first: the modal leads with what actually breaks the deck.
  return issues.sort((a, b) =>
    a.level === b.level ? 0 : a.level === 'obligation' ? -1 : 1,
  );
}

// The export safety net, shown only when the deck sits outside its bands. It
// separates obligations (below a floor — the deck is broken) from
// recommendations (over a ceiling — a trim), and never blocks: the player can
// always export anyway, they are just no longer doing it unwarned.
function ExportGuardModal({
  issues,
  onCancel,
  onExportAnyway,
}: {
  issues: CompositionIssue[];
  onCancel: () => void;
  onExportAnyway: () => void;
}) {
  const obligations = issues.filter((i) => i.level === 'obligation');
  const recommendations = issues.filter((i) => i.level === 'recommendation');
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Revisar antes de exportar"
      onClick={onCancel}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="surface flex w-full max-w-lg flex-col gap-4 rounded-lg p-6"
      >
        <h3 className="flex items-center gap-2 text-lg font-semibold">
          <TriangleAlert className="h-5 w-5 text-amber-500" />
          Revisa el mazo antes de exportar
        </h3>
        <p className="text-sm text-zinc-600 dark:text-zinc-300">
          Has cambiado el mazo y ahora su composición se sale de lo previsto.
          Puedes exportar igualmente, pero conviene revisarlo.
        </p>
        {obligations.length > 0 && (
          <div>
            <p className="mb-1 text-sm font-semibold text-rose-700 dark:text-rose-300">
              Deberías arreglar
            </p>
            <ul className="list-inside list-disc text-sm text-zinc-700 dark:text-zinc-200">
              {obligations.map((issue) => (
                <li key={issue.category}>{issue.message}</li>
              ))}
            </ul>
          </div>
        )}
        {recommendations.length > 0 && (
          <div>
            <p className="mb-1 text-sm font-semibold text-amber-700 dark:text-amber-300">
              Recomendado revisar
            </p>
            <ul className="list-inside list-disc text-sm text-zinc-700 dark:text-zinc-200">
              {recommendations.map((issue) => (
                <li key={issue.category}>{issue.message}</li>
              ))}
            </ul>
          </div>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button onClick={onCancel}>
            <X className="h-4 w-4" /> Revisar el mazo
          </Button>
          <Button variant="secondary" onClick={onExportAnyway}>
            <Printer className="h-4 w-4" /> Exportar igualmente
          </Button>
        </div>
      </div>
    </div>
  );
}

export function DeckView({
  result,
  showExport = true,
  onCardClick,
  activeOutName = null,
  onArtSelect,
  pdfArtOverrides,
  onAudit,
  liveColors = null,
  colorBaseline = null,
  tokenOverrides,
}: {
  result: BuildResult;
  showExport?: boolean;
  // Swap entry point: when set, non-basic deck cards become clickable.
  // `activeOutName` is the card currently marked to leave (highlighted red).
  onCardClick?: (card: ViewCard) => void;
  activeOutName?: string | null;
  // Art picker entry point: when set, cards grow a corner button to change
  // their printing/language.
  onArtSelect?: (card: ViewCard) => void;
  // name -> chosen printing scryfall_id; the proxy PDF prints those.
  pdfArtOverrides?: Record<string, string>;
  // Audit entry point: when set, an "Auditar mazo" button joins the header.
  onAudit?: () => void;
  // Fresh color sources for the current deck (the export check uses these over
  // the build's frozen numbers). Null falls back to `result`'s own.
  liveColors?: Record<string, ColorSourceRow> | null;
  // The unswapped-deck baseline the export check compares live sources against.
  colorBaseline?: Record<string, ColorSourceRow> | null;
  // Token art for the PDF: base token scryfall_id -> the id per copy.
  tokenOverrides?: Record<string, string[]>;
}) {
  const [sort, setSort] = useState<SortAxis>('type');
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  // Export safety net: re-evaluate composition on the way out, so nobody prints
  // a deck below its minimums (or over a ceiling) without being warned first.
  const [confirmExport, setConfirmExport] = useState(false);
  const issues = useMemo(
    () => compositionIssues(result, liveColors, colorBaseline),
    [result, liveColors, colorBaseline],
  );

  const groups = useMemo(
    () => groupCards(deckCards(result), sort, commanderCard(result)),
    [result, sort],
  );
  // Commander + the 99 = a legal Commander deck.
  const totalCards =
    result.nonbasic_cards.reduce((sum, c) => sum + c.count, 0) +
    result.basic_lands.reduce((sum, b) => sum + b.count, 0) +
    1;

  // The proxy sheet: the commander, every non-basic card (each once) and the
  // basic lands by their count (Guille prints the whole deck). The backend swaps
  // each basic's art for the Theros Beyond Death full-art, so the manabase looks
  // uniform. Built from the live deck, so swaps are reflected. First render can
  // take a few seconds while the backend fetches uncached card images.
  async function onExportPdf() {
    setPdfError(null);
    setPdfLoading(true);
    try {
      await exportProxyPdf({
        commander: result.commander_name,
        cards: [...result.nonbasic_cards, ...result.basic_lands].map((card) => ({
          name: card.name,
          count: card.count,
        })),
        // Fill the last page's empty cells with the deck's tokens.
        includeTokens: true,
        // What you see is what you print: the art picker's choices.
        artOverrides: pdfArtOverrides,
        tokenOverrides,
      });
    } catch (error: unknown) {
      setPdfError(error instanceof Error ? error.message : 'Error desconocido');
    } finally {
      setPdfLoading(false);
    }
  }

  return (
    <Panel>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-lg font-semibold">
          Mazo · {totalCards} cartas
          <span className="ml-2 text-sm font-normal text-zinc-500 dark:text-zinc-400">
            comandante + 99
          </span>
        </h3>
        {showExport && (
          <div className="flex flex-wrap items-center gap-2">
            {onAudit && (
              <Button variant="secondary" onClick={onAudit}>
                <Sparkles className="h-4 w-4" /> Auditar mazo
              </Button>
            )}
            <Button
              variant="secondary"
              onClick={() =>
                issues.length > 0 ? setConfirmExport(true) : void onExportPdf()
              }
              disabled={pdfLoading}
            >
              {pdfLoading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> Generando PDF…
                </>
              ) : (
                <>
                  <Printer className="h-4 w-4" /> Descargar PDF (proxies 3×3)
                </>
              )}
            </Button>
          </div>
        )}
      </div>

      {confirmExport && (
        <ExportGuardModal
          issues={issues}
          onCancel={() => setConfirmExport(false)}
          onExportAnyway={() => {
            setConfirmExport(false);
            void onExportPdf();
          }}
        />
      )}
      {pdfError && (
        <p className="mb-4 text-sm text-rose-700 dark:text-rose-300">
          No se pudo generar el PDF ({pdfError}).
        </p>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-x-5 gap-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Agrupar
          </span>
          <ToggleGroup<SortAxis>
            ariaLabel="Agrupar por"
            value={sort}
            onChange={setSort}
            options={[
              { value: 'type', label: 'Tipo', icon: <LayoutList className="h-4 w-4" /> },
              { value: 'category', label: 'Categoría', icon: <Tags className="h-4 w-4" /> },
            ]}
          />
        </div>
        {sort === 'category' && (
          <span className="basis-full text-xs text-zinc-500 dark:text-zinc-400">
            Cada carta aparece en un solo grupo (su rol principal); el panel de
            composición la cuenta en todas sus categorías, por eso sus números
            son mayores.
          </span>
        )}
      </div>

      <VisualGridView
        groups={groups}
        onCardClick={onCardClick}
        activeOutName={activeOutName}
        onArtSelect={onArtSelect}
      />
    </Panel>
  );
}

// Shared swap/art-affordance props threaded from DeckView to the card renderers.
type SwapProps = {
  onCardClick?: (card: ViewCard) => void;
  activeOutName?: string | null;
  onArtSelect?: (card: ViewCard) => void;
};

// Whether a card is a clickable swap source: a non-basic, when a handler is set.
// The TFM tested `count === undefined`; our API sets `count` on every card, so
// the flag from `basic_lands[]` is the test (see deck.ts).
function isSwapSource(card: ViewCard, onCardClick?: (card: ViewCard) => void): boolean {
  return onCardClick !== undefined && !card.basic && !card.commander;
}

// ── VISUAL + TYPE (EDHREC-style grid): full card image, score below ──
function VisualGridView({
  groups,
  onCardClick,
  activeOutName,
  onArtSelect,
}: { groups: CardGroup[] } & SwapProps) {
  return (
    <div className="flex flex-col gap-6">
      {groups.map((group) => (
        <div key={group.key}>
          <div className="mb-3 flex items-baseline gap-2 border-b border-black/10 pb-1 dark:border-white/10">
            <h4 className="text-xl font-extrabold tracking-tight accent-text">
              {group.label}
            </h4>
            <span className="text-sm tabular-nums text-zinc-400 dark:text-zinc-500">
              {groupCount(group.cards)}
            </span>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {group.cards.map((card) =>
              isSwapSource(card, onCardClick) ? (
                <SwapTileButton
                  key={card.oracle_id}
                  card={card}
                  active={card.name === activeOutName}
                  onClick={() => onCardClick!(card)}
                  onArtSelect={onArtSelect}
                />
              ) : (
                <CardTile
                  key={card.oracle_id}
                  card={card}
                  onArtSelect={onArtSelect}
                />
              ),
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// A CardTile wrapped as a clickable swap source (visual grid). The active card
// (the one marked to leave) gets a red ring; others get an accent hover ring.
// A div, not a button: the tile hosts a flip control (for double-faced cards),
// and a button-in-button both is invalid and lets the inner click reach the
// outer button. As a div with role=button, the flip's stopPropagation reliably
// keeps a flip from starting a swap.
function SwapTileButton({
  card,
  active,
  onClick,
  onArtSelect,
}: {
  card: ViewCard;
  active: boolean;
  onClick: () => void;
  onArtSelect?: (card: ViewCard) => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      }}
      aria-pressed={active}
      className={`accent-focus block rounded-xl text-left transition ${
        active ? 'ring-2 ring-rose-500' : 'cursor-pointer hover:accent-ring'
      }`}
    >
      <CardTile card={card} onArtSelect={onArtSelect} />
    </div>
  );
}

