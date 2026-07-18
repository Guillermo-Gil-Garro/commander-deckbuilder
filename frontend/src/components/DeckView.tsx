// Reusable deck presentation. Ported from the TFM's `components/DeckView.tsx`
// with the price and Game Changer columns removed (this project has neither) and
// the export delegated to the API.

import { useMemo, useState, type ReactNode } from 'react';
import { Grid2x2, LayoutList, Loader2, Printer, Tags } from 'lucide-react';
import { Button, Panel } from './ui';
import { CardTile, ManaCost, ScoreBadge } from './cards';
import { categoryLabel } from '../labels';
import { commanderCard, deckCards, type ViewCard } from '../deck';
import { exportProxyPdf, type BuildResult, type CategoryRow } from '../api';

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
type DisplayAxis = 'list' | 'visual';

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

export function DeckView({
  result,
  showExport = true,
  onCardClick,
  activeOutName = null,
}: {
  result: BuildResult;
  showExport?: boolean;
  // Swap entry point: when set, non-basic deck cards become clickable.
  // `activeOutName` is the card currently marked to leave (highlighted red).
  onCardClick?: (card: ViewCard) => void;
  activeOutName?: string | null;
}) {
  const [sort, setSort] = useState<SortAxis>('type');
  const [display, setDisplay] = useState<DisplayAxis>('list');
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);

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
            <Button
              variant="secondary"
              onClick={() => void onExportPdf()}
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
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Vista
          </span>
          <ToggleGroup<DisplayAxis>
            ariaLabel="Modo de visualización"
            value={display}
            onChange={setDisplay}
            options={[
              { value: 'list', label: 'Lista', icon: <LayoutList className="h-4 w-4" /> },
              { value: 'visual', label: 'Visual', icon: <Grid2x2 className="h-4 w-4" /> },
            ]}
          />
        </div>
      </div>

      {display === 'list' ? (
        <ListView
          groups={groups}
          onCardClick={onCardClick}
          activeOutName={activeOutName}
        />
      ) : (
        <VisualGridView
          groups={groups}
          onCardClick={onCardClick}
          activeOutName={activeOutName}
        />
      )}
    </Panel>
  );
}

// Shared swap-affordance props threaded from DeckView to the card renderers.
type SwapProps = {
  onCardClick?: (card: ViewCard) => void;
  activeOutName?: string | null;
};

// Whether a card is a clickable swap source: a non-basic, when a handler is set.
// The TFM tested `count === undefined`; our API sets `count` on every card, so
// the flag from `basic_lands[]` is the test (see deck.ts).
function isSwapSource(card: ViewCard, onCardClick?: (card: ViewCard) => void): boolean {
  return onCardClick !== undefined && !card.basic && !card.commander;
}

// ── LIST view (MTGGoldfish-style): rows grouped by section, image on hover ──
function ListView({
  groups,
  onCardClick,
  activeOutName,
}: { groups: CardGroup[] } & SwapProps) {
  return (
    <div className="gap-x-6 lg:columns-2">
      {groups.map((group) => (
        <div key={group.key} className="mb-5 break-inside-avoid">
          <div className="mb-2 flex items-baseline gap-2 border-b border-black/10 pb-1 dark:border-white/10">
            <h4 className="text-xl font-extrabold tracking-tight accent-text">
              {group.label}
            </h4>
            <span className="text-sm tabular-nums text-zinc-400 dark:text-zinc-500">
              {groupCount(group.cards)}
            </span>
          </div>
          <div className="grid gap-1.5">
            {group.cards.map((card) => (
              <CardRow
                key={card.oracle_id}
                card={card}
                onCardClick={onCardClick}
                active={card.name === activeOutName}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── VISUAL + TYPE (EDHREC-style grid): full card image, score below ──
function VisualGridView({
  groups,
  onCardClick,
  activeOutName,
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
                />
              ) : (
                <CardTile key={card.oracle_id} card={card} />
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
}: {
  card: ViewCard;
  active: boolean;
  onClick: () => void;
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
      <CardTile card={card} />
    </div>
  );
}

function CardRow({
  card,
  onCardClick,
  active = false,
}: {
  card: ViewCard;
  onCardClick?: (card: ViewCard) => void;
  active?: boolean;
}) {
  // Basic land: just "×N name" + hover art, no score/categories (a basic has no
  // EDHREC score — the API sends null). The commander is never a swap source.
  const clickable = onCardClick !== undefined && !card.basic && !card.commander;
  return (
    <div
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? () => onCardClick!(card) : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onCardClick!(card);
              }
            }
          : undefined
      }
      className={`group relative flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border px-3.5 py-2.5 transition ${
        active
          ? 'border-rose-400 bg-rose-50 ring-2 ring-rose-500/50 dark:border-rose-500/60 dark:bg-rose-950/30'
          : 'border-black/5 bg-white/70 hover:border-black/10 hover:bg-white dark:border-white/5 dark:bg-zinc-950/40 dark:hover:border-white/15 dark:hover:bg-zinc-900/60'
      } ${clickable ? 'cursor-pointer' : ''}`}
    >
      {card.basic && (
        <span className="text-[1.05rem] font-bold tabular-nums text-zinc-500 dark:text-zinc-400">
          ×{card.count}
        </span>
      )}
      <span className="text-[1.05rem] font-bold leading-snug tracking-tight text-zinc-900 dark:text-zinc-50">
        {card.name}
      </span>
      {!card.basic && <ManaCost manaCost={card.mana_cost} />}
      {!card.basic && (
        <div className="flex flex-wrap gap-1">
          {card.categories.map((cat) => (
            <span
              key={cat}
              className="rounded bg-zinc-100/70 px-1.5 py-0.5 text-[0.65rem] font-medium uppercase tracking-wide text-zinc-400 dark:bg-zinc-800/60 dark:text-zinc-500"
            >
              {categoryLabel(cat)}
            </span>
          ))}
        </div>
      )}
      {!card.basic && card.score !== null && (
        <span className="ml-auto flex items-center gap-2.5">
          <ScoreBadge score={card.score} />
        </span>
      )}

      {/* Hover preview: medium card image, MTGGoldfish-style. */}
      {card.image_uri_normal && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute left-2 top-full z-50 mt-1 hidden w-[244px] overflow-hidden rounded-xl shadow-2xl ring-1 ring-black/20 group-hover:block"
        >
          <img
            src={card.image_uri_normal}
            alt=""
            className="block w-full"
            loading="lazy"
          />
        </div>
      )}
    </div>
  );
}
