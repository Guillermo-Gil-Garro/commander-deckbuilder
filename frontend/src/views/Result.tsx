// The Result view. Ported from the TFM's `views/Result.tsx`, minus what this
// project deliberately does not have: no price/budget/deck_cost, no brackets, no
// Game Changers, no power_weight (Guille plays with proxies and his own
// banlist), no curve target (our solver has no curve objective). The audit here
// is NOT the TFM's ML-embedding /audit: it is our curated-flag audit (layer 1).

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ArrowLeft,
  ArrowRightLeft,
  Crown,
  Hand,
  Info,
  Loader2,
  Minus,
  Plus,
  Search,
  Sparkles,
  TriangleAlert,
  Wrench,
  X,
} from 'lucide-react';
import { Button, ColorPips, Panel } from '../components/ui';
import { CardImage, CardTile } from '../components/cards';
import { CompositionPanel, DeckView } from '../components/DeckView';
import { ArtPicker } from '../components/ArtPicker';
import { artOverridesForExport, useArtOverrides, withArt } from '../art';
import { categoryLabel } from '../labels';
import { toViewCard, useMutableDeck, type ViewCard } from '../deck';
import { OpeningHand } from './OpeningHand';
import {
  auditDeck,
  fetchMaybeboard,
  searchLegalCards,
  sequentialValidate,
  swapOuts,
  swapReplacements,
  type AuditFlag,
  type AuditReplacement,
  type AuditResult,
  type BuildRequest,
  type BuildResult,
  type CommanderListItem,
  type CurveRow,
  type DeckCard,
  type DeckCardRef,
  type Maybeboard,
  type Notice,
  type SwapOuts,
  type SwapReplacements,
  type SwapValidation,
} from '../api';

// Human-readable, honest explanation of each relaxation stage. Surfacing this
// keeps the result descriptive: a relaxed build is NOT presented as "OPTIMAL".
// Keys mirror the backend's stages; an unknown one falls back to its raw name.
const RELAXATION_NOTES: Record<string, string> = {
  drop_floors: 'Se relajaron los mínimos de algunas categorías funcionales.',
  composition:
    'No se pudo respetar toda la estructura de cuotas; se relajó la composición.',
  base_size_and_lands:
    'Solo se garantizaron el tamaño del mazo y las tierras; la estructura objetivo no se pudo respetar.',
};

// Advanced mode: unlocks free-text card search wherever a card can be swapped
// (search any legal card to bring in; search a card and get what to cut). Off
// by default and persisted, so casual users see the guided flow and power users
// opt in once. Under the player's own responsibility — validation still runs.
const ADVANCED_MODE_KEY = 'advanced-mode-v1';

function useAdvancedMode(): [boolean, (value: boolean) => void] {
  const [advanced, setAdvanced] = useState<boolean>(() => {
    try {
      return localStorage.getItem(ADVANCED_MODE_KEY) === '1';
    } catch {
      return false;
    }
  });
  const set = (value: boolean) => {
    setAdvanced(value);
    try {
      localStorage.setItem(ADVANCED_MODE_KEY, value ? '1' : '0');
    } catch {
      // Private mode / disabled storage: the toggle still works this session.
    }
  };
  return [advanced, set];
}

// The advanced-mode switch. One shared control, rendered both at the top and
// inside every place a card is swapped (Guille 2026-07-19), so it can be flipped
// on right where it is needed. All instances drive the same persisted state.
function AdvancedToggle({
  advanced,
  onChange,
  className = '',
}: {
  advanced: boolean;
  onChange: (value: boolean) => void;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={advanced}
      onClick={() => onChange(!advanced)}
      title="Busca y sustituye cualquier carta a mano, bajo tu responsabilidad"
      className={`accent-focus inline-flex items-center gap-2 rounded-lg border px-3.5 py-2 text-sm font-semibold transition ${
        advanced
          ? 'accent-border accent-text accent-soft-bg'
          : 'border-black/10 text-zinc-600 hover:accent-border dark:border-white/10 dark:text-zinc-300'
      } ${className}`}
    >
      <Wrench className="h-4 w-4" />
      Modo avanzado
      <span
        className={`inline-flex h-4 w-7 items-center rounded-full p-0.5 transition ${
          advanced ? 'accent-bg' : 'bg-zinc-300 dark:bg-zinc-600'
        }`}
      >
        <span
          className={`h-3 w-3 rounded-full bg-white transition ${
            advanced ? 'translate-x-3' : ''
          }`}
        />
      </span>
    </button>
  );
}

export function Result({
  result,
  commander,
  req,
  onBack,
}: {
  result: BuildResult;
  commander: CommanderListItem | null;
  // The request that produced this build: needed to query swap candidates and
  // the maybeboard. Null keeps the view read-only.
  req: BuildRequest | null;
  onBack: () => void;
}) {
  const isInfeasible =
    result.status === 'INFEASIBLE' || result.infeasible_reason !== null;
  const relaxed = result.relaxation_stage !== 'none' && !isInfeasible;

  const [handOpen, setHandOpen] = useState(false);
  const [advanced, setAdvanced] = useAdvancedMode();

  // The deck is mutable: clicking a card opens a same-role swap.
  const { deck, deckRefs, swapped, swap, reset } = useMutableDeck(result);
  // Re-seed when a fresh build arrives (new commander / dials).
  useEffect(() => {
    reset(result);
  }, [result, reset]);

  // Swaps are offered only for a real, feasible deck with a known build request.
  const swapsEnabled = !isInfeasible && req !== null;

  // Card art: Spanish-by-default resolution plus the user's manual picks.
  // `shownDeck` is the deck with those printings applied — every view renders
  // it, while the swap/audit logic keeps working on `deck` (names and counts
  // are identical; only image URLs differ).
  const { resolved, setManual, clearManual, manualIds } = useArtOverrides(
    isInfeasible ? null : deck,
  );
  const shownDeck = useMemo(() => withArt(deck, resolved), [deck, resolved]);
  // Names in the deck now: the advanced free search greys these out.
  const deckNames = useMemo(
    () =>
      new Set([
        ...deck.nonbasic_cards.map((c) => c.name),
        ...deck.basic_lands.map((c) => c.name),
      ]),
    [deck.nonbasic_cards, deck.basic_lands],
  );
  const pdfArtOverrides = useMemo(
    () => artOverridesForExport(deck, resolved),
    [deck, resolved],
  );
  const [artCard, setArtCard] = useState<ViewCard | null>(null);
  // The audit panel's activation: its own button, or the deck-header shortcut
  // (which also scrolls the panel into view).
  const [auditOpen, setAuditOpen] = useState(false);
  const auditRef = useRef<HTMLDivElement>(null);
  function openAudit() {
    setAuditOpen(true);
    // Next frame, so the activated panel exists before we scroll to it.
    requestAnimationFrame(() => {
      auditRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
    });
  }

  // The 99 cards for the opening-hand modal: non-basics plus each basic repeated
  // `count` times. Derived from the shown deck so swaps and art choices are
  // both reflected.
  const handDeck = useMemo(
    () => [
      ...shownDeck.nonbasic_cards.map((card) => toViewCard(card, false)),
      ...shownDeck.basic_lands.flatMap((basic) =>
        Array.from({ length: basic.count }, () => toViewCard(basic, true)),
      ),
    ],
    [shownDeck.nonbasic_cards, shownDeck.basic_lands],
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="accent-focus inline-flex w-fit items-center gap-2 rounded-lg border accent-border bg-white px-3.5 py-2 text-sm font-semibold accent-text transition hover:accent-soft-bg dark:bg-zinc-900/80"
        >
          <ArrowLeft className="h-4 w-4" /> Volver al setup
        </button>
        {!isInfeasible && (
          <Button variant="secondary" onClick={() => setHandOpen(true)}>
            <Hand className="h-4 w-4" /> Mano de apertura
          </Button>
        )}
        {swapsEnabled && (
          <AdvancedToggle
            advanced={advanced}
            onChange={setAdvanced}
            className="ml-auto"
          />
        )}
      </div>

      {handOpen && (
        <OpeningHand deck={handDeck} onClose={() => setHandOpen(false)} />
      )}

      {artCard && (
        <ArtPicker
          oracleId={artCard.oracle_id}
          cardName={artCard.name}
          activeScryfallId={resolved[artCard.oracle_id]?.scryfall_id ?? null}
          hasManual={manualIds.has(artCard.oracle_id)}
          onPick={(print) => {
            setManual(artCard.oracle_id, print);
            setArtCard(null);
          }}
          onReset={() => {
            clearManual(artCard.oracle_id);
            setArtCard(null);
          }}
          onClose={() => setArtCard(null)}
        />
      )}

      <ResultHeader result={deck} commander={commander} />

      {isInfeasible ? (
        <InfeasiblePanel result={result} />
      ) : (
        <>
          {relaxed && <RelaxationBanner stage={result.relaxation_stage} />}
          <NoticeList notices={result.warnings} kind="warning" />
          <NoticeList notices={result.unresolved} kind="unresolved" />
          <HonestyNote />
          <div className="grid gap-6 lg:grid-cols-2">
            <ConstraintsPanel result={deck} swapped={swapped} />
            <CompositionPanel result={deck} />
          </div>
          {swapsEnabled && req && (
            <div ref={auditRef} className="scroll-mt-4">
              <AuditPanel
                commander={req.commander}
                dials={req.dials}
                deckRefs={deckRefs}
                deckCards={deck.nonbasic_cards}
                onSwap={swap}
                active={auditOpen}
                onActivate={() => setAuditOpen(true)}
              />
            </div>
          )}
          {swapsEnabled && req && advanced && (
            <AdvancedAddPanel
              commander={req.commander}
              dials={req.dials}
              deckRefs={deckRefs}
              deckNames={deckNames}
              onSwap={swap}
            />
          )}
          <CurvePanel curve={deck.curve_breakdown} />
          {swapsEnabled && req ? (
            <SwapWorkspace
              deck={deck}
              displayDeck={shownDeck}
              deckRefs={deckRefs}
              req={req}
              onSwap={swap}
              onArtSelect={setArtCard}
              pdfArtOverrides={pdfArtOverrides}
              onAudit={openAudit}
              advanced={advanced}
              onToggleAdvanced={setAdvanced}
            />
          ) : (
            <DeckView
              result={shownDeck}
              onArtSelect={setArtCard}
              pdfArtOverrides={pdfArtOverrides}
            />
          )}
        </>
      )}
    </div>
  );
}

// The mutable-deck workspace: the deck (clickable cards), the maybeboard bench,
// and the confirm tray. Mounted only when swaps are enabled, so the bench fetch
// never runs for an INFEASIBLE build.
function SwapWorkspace({
  deck,
  displayDeck,
  deckRefs,
  req,
  onSwap,
  onArtSelect,
  pdfArtOverrides,
  onAudit,
  advanced,
  onToggleAdvanced,
}: {
  deck: BuildResult;
  /** `deck` with the art picker's printings applied — what the views render.
   *  All swap logic stays on `deck` (identical names/counts). */
  displayDeck: BuildResult;
  deckRefs: DeckCardRef[];
  req: BuildRequest;
  onSwap: (outName: string, chosen: DeckCard, validation: SwapValidation) => void;
  onArtSelect: (card: ViewCard) => void;
  pdfArtOverrides: Record<string, string>;
  onAudit: () => void;
  advanced: boolean;
  onToggleAdvanced: (value: boolean) => void;
}) {
  const [bench, setBench] = useState<Maybeboard | null>(null);
  // Active swap: X marked to leave, the same-role candidates, and the chosen Y.
  const [outName, setOutName] = useState<string | null>(null);
  const [cands, setCands] = useState<SwapReplacements | null>(null);
  const [candsLoading, setCandsLoading] = useState(false);
  const [candsError, setCandsError] = useState<string | null>(null);
  const [inCard, setInCard] = useState<DeckCard | null>(null);
  // The API's verdict on the pending (out, in) pair. The client never rules on
  // feasibility itself — see deck.ts.
  const [validation, setValidation] = useState<SwapValidation | null>(null);
  const [validating, setValidating] = useState(false);
  const [validateError, setValidateError] = useState<string | null>(null);

  // Read the freshest deck snapshot inside fetch effects without refiring them
  // on every swap (the candidates effect keys on `outName`, which clears on a swap).
  const deckRefsRef = useRef(deckRefs);
  deckRefsRef.current = deckRefs;

  // Load (and refresh) the bench when the deck composition changes.
  useEffect(() => {
    let cancelled = false;
    fetchMaybeboard({
      commander: req.commander,
      dials: req.dials,
      deck: deckRefs,
    })
      .then((res) => {
        if (!cancelled) setBench(res);
      })
      .catch(() => {
        if (!cancelled) setBench(null);
      });
    return () => {
      cancelled = true;
    };
  }, [req, deckRefs]);

  // Fetch the audit-style, role-aware replacements for the card marked to
  // leave. Same guidance the audit gives its doubtful cards (Guille 2026-07-19),
  // not a flat top-N ranking: up to two same-role, the best card missing and one
  // that reinforces the thinnest category.
  useEffect(() => {
    if (!outName) {
      setCands(null);
      return;
    }
    let cancelled = false;
    setCandsLoading(true);
    setCandsError(null);
    setCands(null);
    swapReplacements({
      commander: req.commander,
      dials: req.dials,
      deck: deckRefsRef.current,
      out: outName,
    })
      .then((res) => {
        if (!cancelled) setCands(res);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setCandsError(err instanceof Error ? err.message : 'Error desconocido');
      })
      .finally(() => {
        if (!cancelled) setCandsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [outName, req]);

  // Validate the pending pair. This is what makes the swap honest: the response
  // carries the backend's post-swap counts, statuses and Karsten floor, which
  // `applySwap` adopts wholesale.
  useEffect(() => {
    if (!outName || !inCard) {
      setValidation(null);
      setValidateError(null);
      return;
    }
    let cancelled = false;
    setValidating(true);
    setValidateError(null);
    setValidation(null);
    sequentialValidate({
      commander: req.commander,
      dials: req.dials,
      deck: deckRefsRef.current,
      out: outName,
      in: inCard.name,
    })
      .then((res) => {
        if (!cancelled) setValidation(res);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setValidateError(
            err instanceof Error ? err.message : 'Error desconocido',
          );
      })
      .finally(() => {
        if (!cancelled) setValidating(false);
      });
    return () => {
      cancelled = true;
    };
  }, [outName, inCard, req]);

  const outCard = useMemo(
    () => deck.nonbasic_cards.find((c) => c.name === outName) ?? null,
    [deck.nonbasic_cards, outName],
  );
  // Names already in the deck: the free search greys these out (re-adding one
  // is a duplicate the validator would reject anyway).
  const deckNames = useMemo(
    () =>
      new Set([
        ...deck.nonbasic_cards.map((c) => c.name),
        ...deck.basic_lands.map((c) => c.name),
      ]),
    [deck.nonbasic_cards, deck.basic_lands],
  );

  function startSwap(card: ViewCard) {
    setOutName(card.name);
    setInCard(null);
  }
  function cancelSwap() {
    setOutName(null);
    setInCard(null);
  }
  function confirmSwap() {
    if (outName && inCard && validation?.feasible) {
      onSwap(outName, inCard, validation);
    }
    setOutName(null);
    setInCard(null);
  }

  return (
    <>
      <DeckView
        result={displayDeck}
        onCardClick={startSwap}
        activeOutName={outName}
        onArtSelect={onArtSelect}
        pdfArtOverrides={pdfArtOverrides}
        onAudit={onAudit}
      />

      {/* Bench only: the active-swap candidates render as a modal instead
          (below), so the player never has to scroll past 100 cards to see
          what they are swapping. */}
      <MaybeboardPanel bench={bench} />

      {outCard && (
        <SwapCandidatesModal
          outCard={outCard}
          cands={cands}
          loading={candsLoading}
          error={candsError}
          selectedInName={inCard?.name ?? null}
          onChooseIn={setInCard}
          onCancel={cancelSwap}
          advanced={advanced}
          onToggleAdvanced={onToggleAdvanced}
          commander={req.commander}
          deckNames={deckNames}
        />
      )}

      {outCard && inCard && (
        <SwapTray
          outCard={outCard}
          inCard={inCard}
          validation={validation}
          validating={validating}
          error={validateError}
          onConfirm={confirmSwap}
          onCancel={cancelSwap}
        />
      )}
    </>
  );
}

// The deck audit: run on demand, then re-run on every swap so it always reflects
// the live deck. It only points — a replacement is applied through the same
// validate-then-swap path as a manual swap, never blindly.
const REPLACEMENT_LABEL: Record<AuditReplacement['kind'], string> = {
  same_role: 'Mismo rol',
  best_overall: 'La mejor que te falta',
  reinforce: 'Refuerzo',
};

// How many out-candidates the placing picker shows. Guided freedom: enough
// room to disagree with the suggestion, not the whole 60-card haystack. Ten
// fills two clean rows of five (Guille 2026-07-19: nine crammed in one row read
// as tiny thumbnails).
const PLACING_OUT_LIMIT = 10;

// The "elige qué sale" step as an overlay (Guille 2026-07-19: inline under the
// deck forced a scroll). Shared by the audit's "buenas que te faltan" and the
// advanced "meter una carta concreta": pick the card to bring in, this shows
// the deck cards to cut for it (first = the recommendation). The active-swap
// tray still floats over it to confirm.
function PlacingModal({
  placingName,
  placingSlot,
  outs,
  loading = false,
  error = null,
  applying,
  onPick,
  onCancel,
}: {
  placingName: string;
  placingSlot: string;
  outs: DeckCard[];
  loading?: boolean;
  error?: string | null;
  /** The `out=>in` key currently applying, so the picked tile shows a spinner. */
  applying: string | null;
  onPick: (outCard: DeckCard) => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onCancel();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Meter ${placingName}`}
      onClick={onCancel}
      className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:p-6"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="surface flex max-h-[92vh] w-full max-w-7xl flex-col gap-4 overflow-y-auto rounded-lg p-5 pb-28 sm:p-7 sm:pb-28"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-lg font-semibold">
            <ArrowRightLeft className="h-5 w-5 accent-text" />
            Meter{' '}
            <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-emerald-700 ring-1 ring-emerald-400/40 dark:text-emerald-200">
              {placingName}
            </span>
            — elige qué sale
          </h3>
          <Button variant="secondary" onClick={onCancel}>
            <X className="h-4 w-4" /> Cancelar
          </Button>
        </div>
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Primero las de su mismo rol ({categoryLabel(placingSlot)}), peor
          puntuadas antes. La primera es la recomendada. El cambio se valida
          antes de aplicarse.
        </p>
        {loading ? (
          <p className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin" /> Buscando qué conviene
            sacar…
          </p>
        ) : error ? (
          <p className="text-sm text-amber-700 dark:text-amber-300">
            No se pudo calcular ({error}).
          </p>
        ) : outs.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No hay ningún cambio factible para meter esta carta sin romper el
            mazo.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {outs.map((card, index) => {
              const busy = applying === `${card.name}=>${placingName}`;
              const suggested = index === 0;
              return (
                <button
                  key={card.oracle_id}
                  type="button"
                  onClick={() => onPick(card)}
                  disabled={applying !== null}
                  title={`Sacar ${card.name}`}
                  className={`accent-focus group relative flex flex-col gap-1 rounded-xl text-left transition hover:ring-2 hover:ring-rose-400 disabled:opacity-60 ${
                    suggested ? 'ring-2 ring-emerald-500/70' : ''
                  }`}
                >
                  <div className="relative overflow-hidden rounded-xl ring-1 ring-black/10 dark:ring-white/10">
                    <CardImage
                      card={toViewCard(card)}
                      className="aspect-[5/7] w-full object-cover"
                    />
                    {suggested && (
                      <span className="absolute left-1 top-1 rounded bg-emerald-600/90 px-1.5 py-0.5 text-[0.6rem] font-bold uppercase tracking-wide text-white ring-1 ring-white/25">
                        Sugerida
                      </span>
                    )}
                    {busy && (
                      <span className="absolute inset-0 flex items-center justify-center bg-black/50">
                        <Loader2 className="h-6 w-6 animate-spin text-white" />
                      </span>
                    )}
                  </div>
                  <span
                    className="truncate px-0.5 text-xs font-medium text-zinc-600 dark:text-zinc-300"
                    title={card.name}
                  >
                    {card.name}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function AuditPanel({
  commander,
  dials,
  deckRefs,
  deckCards,
  onSwap,
  active,
  onActivate,
}: {
  commander: string;
  dials: BuildRequest['dials'];
  deckRefs: DeckCardRef[];
  /** The live non-basics: the out-candidates when placing a missing card. */
  deckCards: DeckCard[];
  onSwap: (
    outName: string,
    chosen: DeckCard,
    validation: SwapValidation,
  ) => void;
  /** Controlled activation, so the deck-header shortcut can open it too. */
  active: boolean;
  onActivate: () => void;
}) {
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  // Reverse swap: the missing card the player wants IN; they then pick what
  // leaves. Cleared when the swap lands (the audit re-runs on the new deck).
  const [placing, setPlacing] = useState<DeckCard | null>(null);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    auditDeck({ commander, dials, deck: deckRefs })
      .then((res) => {
        if (!cancelled) setAudit(res);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Error desconocido');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [active, commander, dials, deckRefs]);

  async function applySwapPair(outName: string, chosen: DeckCard) {
    setApplying(`${outName}=>${chosen.name}`);
    setApplyError(null);
    try {
      const validation = await sequentialValidate({
        commander,
        dials,
        deck: deckRefs,
        out: outName,
        in: chosen.name,
      });
      if (!validation.feasible) {
        setApplyError(
          `Ahora mismo no se puede cambiar ${outName} por ${chosen.name}.`,
        );
        return;
      }
      onSwap(outName, chosen, validation);
      setPlacing(null);
      // deckRefs changes → the effect re-audits automatically.
    } catch (err: unknown) {
      setApplyError(err instanceof Error ? err.message : 'Error desconocido');
    } finally {
      setApplying(null);
    }
  }

  // Out-candidates for the card being placed: same-slot cards first (they
  // compete for the same job), then the rest; worst score first within each
  // tier — the weakest link is the natural cut. Capped: guided freedom, not
  // the whole haystack. The first one is the recommendation.
  const outCandidates = useMemo(() => {
    if (!placing) return [];
    const slot = placing.slot;
    return [...deckCards]
      .sort((a, b) => {
        const aSame = a.categories.includes(slot) ? 0 : 1;
        const bSame = b.categories.includes(slot) ? 0 : 1;
        return aSame - bSame || (a.score ?? 0) - (b.score ?? 0);
      })
      .slice(0, PLACING_OUT_LIMIT);
  }, [placing, deckCards]);

  if (!active) {
    return (
      <Panel>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-lg font-semibold">Auditoría del mazo</h3>
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Señala cartas dudosas y buenas que te faltan. No cambia nada: tú
              decides.
            </p>
          </div>
          <Button onClick={onActivate}>
            <Sparkles className="h-4 w-4" /> Auditar mazo
          </Button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel>
      <div className="mb-4 flex items-center gap-2">
        <h3 className="inline-flex items-center gap-2 text-lg font-semibold">
          <Sparkles className="h-5 w-5 accent-text" /> Auditoría del mazo
        </h3>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />}
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}
      {applyError && (
        <p className="mb-3 text-sm text-amber-600 dark:text-amber-400">
          {applyError}
        </p>
      )}

      {audit &&
        audit.doubtful.length === 0 &&
        audit.missing.length === 0 &&
        !loading && (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Nada que señalar: el mazo no tiene dudosas conocidas.
          </p>
        )}

      {audit && audit.doubtful.length > 0 && (
        <div className="mb-6 flex flex-col gap-4">
          <h4 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
            Dudosas
          </h4>
          {audit.doubtful.map((flag) => (
            <AuditFlagRow
              key={flag.card.name}
              flag={flag}
              applying={applying}
              onApply={(card) => void applySwapPair(flag.card.name, card)}
            />
          ))}
        </div>
      )}

      {audit && audit.missing.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-semibold uppercase tracking-wide text-zinc-500">
            Buenas que te faltan
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Las mejores cartas que este comandante quiere y no llevas. Haz clic
            en una para meterla eligiendo qué sale del mazo.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {audit.missing.map((card) => {
              const isPlacing = placing?.name === card.name;
              return (
                <button
                  key={card.name}
                  type="button"
                  onClick={() => setPlacing(isPlacing ? null : card)}
                  aria-pressed={isPlacing}
                  className={`accent-focus flex flex-col gap-1.5 rounded-xl text-left transition ${
                    isPlacing
                      ? 'ring-2 accent-ring'
                      : 'cursor-pointer hover:accent-ring hover:ring-2'
                  }`}
                >
                  <div className="overflow-hidden rounded-xl ring-1 ring-black/10 dark:ring-white/10">
                    <CardImage
                      card={toViewCard(card)}
                      className="aspect-[5/7] w-full object-cover"
                    />
                  </div>
                  <span
                    className="truncate px-0.5 text-xs font-medium text-zinc-600 dark:text-zinc-300"
                    title={card.name}
                  >
                    {card.name}
                  </span>
                </button>
              );
            })}
          </div>

        </div>
      )}

      {placing && (
        <PlacingModal
          placingName={placing.name}
          placingSlot={placing.slot}
          outs={outCandidates}
          applying={applying}
          onPick={(card) => void applySwapPair(card.name, placing)}
          onCancel={() => setPlacing(null)}
        />
      )}
    </Panel>
  );
}

// Advanced mode's reverse flow: search any legal card to bring IN, and the
// system recommends which deck cards to cut for it (same feasibility + ranking
// as the audit's placing picker, but for a card you chose, not one it flagged).
function AdvancedAddPanel({
  commander,
  dials,
  deckRefs,
  deckNames,
  onSwap,
}: {
  commander: string;
  dials: BuildRequest['dials'];
  deckRefs: DeckCardRef[];
  deckNames: Set<string>;
  onSwap: (outName: string, chosen: DeckCard, validation: SwapValidation) => void;
}) {
  const [placingIn, setPlacingIn] = useState<DeckCard | null>(null);
  const [outs, setOuts] = useState<SwapOuts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);

  useEffect(() => {
    if (!placingIn) {
      setOuts(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setOuts(null);
    swapOuts({ commander, dials, deck: deckRefs, in: placingIn.name, limit: 10 })
      .then((res) => {
        if (!cancelled) setOuts(res);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : 'Error desconocido');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [placingIn, commander, dials, deckRefs]);

  async function applySwapPair(outName: string, chosen: DeckCard) {
    setApplying(`${outName}=>${chosen.name}`);
    setApplyError(null);
    try {
      const validation = await sequentialValidate({
        commander,
        dials,
        deck: deckRefs,
        out: outName,
        in: chosen.name,
      });
      if (!validation.feasible) {
        setApplyError(
          `Ahora mismo no se puede cambiar ${outName} por ${chosen.name}.`,
        );
        return;
      }
      onSwap(outName, chosen, validation);
      setPlacingIn(null);
    } catch (err: unknown) {
      setApplyError(err instanceof Error ? err.message : 'Error desconocido');
    } finally {
      setApplying(null);
    }
  }

  return (
    <Panel>
      <h3 className="mb-1 flex items-center gap-2 text-lg font-semibold">
        <Wrench className="h-5 w-5 accent-text" /> Meter una carta concreta
      </h3>
      <p className="mb-4 text-sm text-zinc-500 dark:text-zinc-400">
        Busca la carta que quieres meter y te recomendamos qué sacar (lo más
        flojo de su rol primero). Cada cambio se valida antes de aplicarse.
      </p>
      <CardSearchBox
        commander={commander}
        deckNames={deckNames}
        selectedName={placingIn?.name ?? null}
        onPick={setPlacingIn}
        placeholder="Busca la carta que quieres meter…"
      />

      {applyError && (
        <p className="mt-3 text-sm text-amber-600 dark:text-amber-400">
          {applyError}
        </p>
      )}

      {placingIn && (
        <PlacingModal
          placingName={placingIn.name}
          placingSlot={placingIn.slot}
          outs={outs?.outs ?? []}
          loading={loading}
          error={error}
          applying={applying}
          onPick={(card) => void applySwapPair(card.name, placingIn)}
          onCancel={() => setPlacingIn(null)}
        />
      )}
    </Panel>
  );
}

// Chip colours per replacement kind — the sequential mode's visual grammar:
// every tile in the row is the same size, its role told by the header chip.
const REPLACEMENT_CHIP: Record<AuditReplacement['kind'], string> = {
  same_role:
    'bg-amber-500/15 text-amber-700 ring-amber-400/40 dark:text-amber-200',
  best_overall:
    'bg-violet-500/15 text-violet-700 ring-violet-400/40 dark:text-violet-200',
  reinforce: 'bg-sky-500/15 text-sky-700 ring-sky-400/40 dark:text-sky-200',
};

function AuditFlagRow({
  flag,
  applying,
  onApply,
}: {
  flag: AuditFlag;
  applying: string | null;
  onApply: (card: DeckCard) => void;
}) {
  return (
    <div className="rounded-xl border border-amber-300/50 bg-amber-50/40 p-4 dark:border-amber-500/20 dark:bg-amber-500/5">
      <div className="mb-3 flex items-start gap-2">
        <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <p className="text-sm text-zinc-700 dark:text-zinc-200">
          <span className="font-semibold">{flag.card.name}:</span> {flag.reason}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {/* The flagged card, same size as its alternatives (sequential-mode
            grammar): the chip says which one is currently in the deck. */}
        <div className="flex flex-col overflow-hidden rounded-xl border border-rose-400/50 bg-white/60 dark:bg-zinc-950/40">
          <span className="flex items-center justify-center gap-1 bg-rose-500/15 px-2 py-1 text-[0.65rem] font-bold uppercase tracking-wide text-rose-700 dark:text-rose-200">
            Actual · {categoryLabel(flag.card.slot)}
          </span>
          <CardImage
            card={toViewCard(flag.card)}
            className="aspect-[5/7] w-full object-cover"
          />
          <span className="truncate px-2 py-1.5 text-xs font-semibold">
            {flag.card.name}
          </span>
        </div>
        {flag.replacements.map((rep) => {
          const busy = applying === `${flag.card.name}=>${rep.card.name}`;
          return (
            <button
              key={`${rep.kind}:${rep.card.name}`}
              type="button"
              onClick={() => onApply(rep.card)}
              disabled={applying !== null}
              title={rep.note}
              className="accent-focus flex flex-col overflow-hidden rounded-xl border border-black/10 bg-white/60 text-left transition hover:accent-ring hover:ring-2 disabled:opacity-60 dark:border-white/10 dark:bg-zinc-950/40"
            >
              <span
                className={`flex items-center justify-center gap-1 px-2 py-1 text-[0.65rem] font-bold uppercase tracking-wide ring-0 ${REPLACEMENT_CHIP[rep.kind]}`}
              >
                {REPLACEMENT_LABEL[rep.kind]} · {categoryLabel(rep.card.slot)}
              </span>
              <span className="relative block">
                <CardImage
                  card={toViewCard(rep.card)}
                  className="aspect-[5/7] w-full object-cover"
                />
                {busy && (
                  <span className="absolute inset-0 flex items-center justify-center bg-black/50">
                    <Loader2 className="h-6 w-6 animate-spin text-white" />
                  </span>
                )}
              </span>
              <span className="flex items-center justify-between gap-2 px-2 py-1.5">
                <span className="truncate text-xs font-semibold">
                  {rep.card.name}
                </span>
                <ArrowRightLeft className="h-3.5 w-3.5 shrink-0 accent-text" />
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ResultHeader({
  result,
  commander,
}: {
  result: BuildResult;
  commander: CommanderListItem | null;
}) {
  const art =
    commander?.image_uri_art_crop ?? result.commander.image_uri_art_crop ?? null;
  const isInfeasible =
    result.status === 'INFEASIBLE' || result.infeasible_reason !== null;
  const relaxed = result.relaxation_stage !== 'none' && !isInfeasible;

  const statusLabel = isInfeasible
    ? 'Sin solución'
    : relaxed
      ? 'Resuelto con relajación'
      : result.status === 'OPTIMAL'
        ? 'Óptimo'
        : 'Factible';

  const statusClass = isInfeasible
    ? 'bg-rose-500/15 text-rose-200 ring-rose-400/40'
    : relaxed
      ? 'bg-amber-500/15 text-amber-100 ring-amber-400/40'
      : 'bg-emerald-500/15 text-emerald-100 ring-emerald-400/40';

  return (
    <section className="relative overflow-hidden rounded-lg border border-white/15 bg-zinc-950 shadow-sm dark:border-white/10">
      {art ? (
        <span
          aria-hidden="true"
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: `url("${art}")` }}
        />
      ) : (
        <span
          aria-hidden="true"
          className="absolute inset-0 bg-gradient-to-br from-zinc-800 via-zinc-950 to-emerald-950"
        />
      )}
      <span
        aria-hidden="true"
        className="absolute inset-0 bg-gradient-to-r from-black/95 via-black/75 to-black/35"
      />
      <div className="relative z-10 flex flex-col gap-3 p-6 sm:p-7">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide accent-text">
          <Crown className="h-4 w-4" /> Comandante
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <h2 className="text-2xl font-bold text-white drop-shadow sm:text-3xl">
            {result.commander_name}
          </h2>
          {commander && <ColorPips colors={commander.color_identity} />}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span
            className={`inline-flex items-center rounded-lg px-3 py-1.5 text-sm font-semibold ring-1 ${statusClass}`}
          >
            {statusLabel}
          </span>
          {!isInfeasible && (
            <span className="text-sm text-zinc-200">
              {result.deck_size} cartas · resuelto en{' '}
              {result.solve_time_seconds.toFixed(1)} s
            </span>
          )}
        </div>
      </div>
    </section>
  );
}

function RelaxationBanner({ stage }: { stage: string }) {
  const note = RELAXATION_NOTES[stage] ?? `Se aplicó una relajación: ${stage}.`;
  return (
    <div className="flex items-start gap-3 rounded-lg border border-amber-300/70 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-100">
      <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
      <p>
        <span className="font-semibold">Relajación aplicada.</span> {note} El
        mazo respeta las 99 cartas y el suelo de tierras, pero no es un óptimo
        sin compromisos.
      </p>
    </div>
  );
}

// The API's own notices, verbatim. `warnings` are Notice objects; `unresolved`
// are raw EDHREC name strings (occasionally empty — EDHREC noise, filtered
// out so the banner never shows a blank bullet or renders for nothing).
function NoticeList({
  notices,
  kind,
}: {
  notices: (Notice | string)[];
  kind: 'warning' | 'unresolved';
}) {
  const messages = notices
    .map((notice) => (typeof notice === 'string' ? notice : notice.message))
    .filter((message) => message && message.trim().length > 0);
  if (messages.length === 0) return null;
  return (
    <div className="flex items-start gap-3 rounded-lg border border-amber-300/70 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-100">
      <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
      <div>
        <p className="font-semibold">
          {kind === 'warning' ? 'Avisos del solver' : 'Cartas sin resolver'}
        </p>
        <ul className="mt-1 list-inside list-disc">
          {messages.map((message, index) => (
            <li key={`${kind}-${index}`}>{message}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function HonestyNote() {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-black/10 bg-white/70 px-4 py-3 text-sm text-zinc-600 dark:border-white/10 dark:bg-zinc-900/40 dark:text-zinc-300">
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400" />
      <p>
        Esta vista es <span className="font-semibold">descriptiva</span>. El{' '}
        <span className="font-semibold">score</span> es el de{' '}
        <span className="font-semibold">EDHREC</span> (sinergia e inclusión con
        este comandante): mide lo que la comunidad juega, no la calidad de la
        carta ni la potencia del mazo. Las bandas describen la composición frente
        a la estructura objetivo; la curva es el histograma real del mazo (no hay
        curva objetivo que cumplir).
      </p>
    </div>
  );
}

function InfeasiblePanel({ result }: { result: BuildResult }) {
  return (
    <Panel>
      <div className="flex items-start gap-3">
        <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0 text-rose-500" />
        <div>
          <h3 className="text-lg font-semibold">No se encontró un mazo válido</h3>
          <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">
            {result.infeasible_reason ??
              'Las restricciones no permiten construir un mazo de 99 cartas.'}
          </p>
          <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
            Esto es un resultado informativo, no un error. Prueba a mover los
            diales: exigir mucho de varias categorías a la vez deja al solver sin
            hueco.
          </p>
          <NoticeList notices={result.unresolved} kind="unresolved" />
        </div>
      </div>
    </Panel>
  );
}

function ConstraintsPanel({
  result,
  swapped,
}: {
  result: BuildResult;
  swapped: boolean;
}) {
  const colors = Object.entries(result.color_source_breakdown);
  return (
    <Panel>
      <h3 className="mb-4 text-lg font-semibold">Restricciones</h3>
      <dl className="grid gap-3 text-sm">
        <StatRow label="Tamaño del mazo" value={`${result.deck_size}`} />
        <StatRow
          label="Suelo de Karsten (tierras)"
          value={`${result.karsten_floor}`}
          help="Mínimo de tierras que la curva del mazo exige. Es infranqueable: ningún mazo por debajo valida."
        />
        <StatRow
          label="Tierras objetivo"
          value={`${result.lands_target}`}
          help="Las tierras que el solver se propuso meter."
        />
        <StatRow
          label="Estructura objetivo"
          value={
            result.target_structure_source === 'commander'
              ? 'Del comandante'
              : 'Del arquetipo'
          }
          help="De dónde salen las bandas: una entrada propia del comandante o el arquetipo genérico."
        />
      </dl>

      {colors.length > 0 && (
        <>
          <h4 className="mb-1 mt-5 text-sm font-semibold">Fuentes de color</h4>
          <p className="mb-3 text-xs text-zinc-500 dark:text-zinc-400">
            Fuentes de cada color frente a la demanda de los pips del mazo
            (método Karsten).
            {swapped && (
              <span className="ml-1 font-medium text-amber-700 dark:text-amber-300">
                Cambiar cartas puede afectar al fixing de color, y esto no se
                reoptimiza: estas cifras son las del mazo que resolvió el solver.
              </span>
            )}
          </p>
          <dl className="grid gap-3 text-sm">
            {colors.map(([color, row]) => (
              <StatRow
                key={color}
                label={
                  <span className="inline-flex items-center gap-2">
                    <i className={`ms ms-${color.toLowerCase()} ms-cost`} aria-hidden="true" />
                    {color}
                  </span>
                }
                value={`${row.sources} / ${row.demand}`}
                tone={row.deficit > 0 ? 'bad' : 'ok'}
                help={
                  row.deficit > 0
                    ? `Déficit de ${row.deficit} fuentes frente a la demanda.`
                    : 'Sin déficit.'
                }
              />
            ))}
          </dl>
        </>
      )}
    </Panel>
  );
}

function StatRow({
  label,
  value,
  tone = 'neutral',
  help,
}: {
  label: ReactNode;
  value: string;
  tone?: 'ok' | 'bad' | 'neutral';
  help?: string;
}) {
  const toneClass =
    tone === 'bad'
      ? 'text-rose-700 dark:text-rose-300'
      : tone === 'ok'
        ? 'text-emerald-700 dark:text-emerald-300'
        : 'text-zinc-900 dark:text-zinc-100';
  return (
    <div className="flex items-center justify-between gap-4 border-b border-black/5 pb-2 last:border-0 dark:border-white/5">
      <dt className="text-zinc-500 dark:text-zinc-400" title={help}>
        {label}
      </dt>
      <dd className={`font-semibold tabular-nums ${toneClass}`} title={help}>
        {value}
      </dd>
    </div>
  );
}

// Canonical CMC bucket order so "7+" never sorts before "2".
const CURVE_BUCKET_ORDER = ['0', '1', '2', '3', '4', '5', '6', '7+'];

// The real histogram and nothing else. The TFM drew a dashed per-bucket target
// line; our solver has no curve objective, so there is no target to draw and
// inventing one would be a lie.
function CurvePanel({ curve }: { curve: Record<string, CurveRow> }) {
  const entries = CURVE_BUCKET_ORDER.filter((b) => b in curve).map(
    (b) => [b, curve[b]] as const,
  );
  if (entries.length === 0) return null;
  const maxCount = Math.max(1, ...entries.map(([, row]) => row.count));
  const ticks = Array.from({ length: maxCount + 1 }, (_, i) => i).filter(
    (i) => maxCount <= 8 || i % Math.ceil(maxCount / 6) === 0 || i === maxCount,
  );
  const PLOT_HEIGHT = 180;

  return (
    <Panel>
      <h3 className="mb-1 text-lg font-semibold">Curva de maná</h3>
      <p className="mb-5 text-xs text-zinc-500 dark:text-zinc-400">
        Cartas por coste de maná (CMC); las tierras no cuentan. Es el histograma
        real del mazo: no hay curva objetivo — el solver no optimiza la curva,
        solo la usa para calcular el suelo de tierras.
      </p>
      <div className="flex gap-3">
        {/* Y axis ticks */}
        <div
          className="relative w-5 shrink-0 text-right text-[10px] tabular-nums text-zinc-400 dark:text-zinc-500"
          style={{ height: PLOT_HEIGHT }}
          aria-hidden="true"
        >
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute right-0 -translate-y-1/2"
              style={{ bottom: `${(t / maxCount) * 100}%` }}
            >
              {t}
            </span>
          ))}
        </div>
        {/* Plot */}
        <div className="min-w-0 flex-1">
          <div
            className="relative flex items-end gap-2 border-b border-l border-black/10 dark:border-white/10"
            style={{ height: PLOT_HEIGHT }}
          >
            {ticks.map((t) => (
              <span
                key={t}
                aria-hidden="true"
                className="absolute left-0 right-0 border-t border-dashed border-black/5 dark:border-white/5"
                style={{ bottom: `${(t / maxCount) * 100}%` }}
              />
            ))}
            {entries.map(([bucket, row]) => {
              const height = (row.count / maxCount) * 100;
              return (
                <div
                  key={bucket}
                  className="relative flex h-full min-w-0 flex-1 items-end justify-center"
                  title={`CMC ${bucket}: ${row.count} carta(s)`}
                >
                  <div
                    className="relative w-full max-w-[44px] rounded-t accent-bg"
                    style={{ height: `${Math.max(height, row.count > 0 ? 3 : 0)}%` }}
                  >
                    <span className="absolute -top-5 left-0 right-0 text-center text-xs font-semibold tabular-nums text-zinc-700 dark:text-zinc-200">
                      {row.count}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          {/* X axis labels */}
          <div className="mt-1.5 flex gap-2">
            {entries.map(([bucket]) => (
              <span
                key={bucket}
                className="min-w-0 flex-1 text-center text-xs font-medium tabular-nums text-zinc-500 dark:text-zinc-400"
              >
                {bucket}
              </span>
            ))}
          </div>
        </div>
      </div>
    </Panel>
  );
}

// Advanced mode's free card search: type a name, get the legal-to-add cards
// (colour identity + minus banlist), pick one. Cards already in the deck are
// shown greyed — re-adding one is a duplicate the validator would reject. The
// selected card gets an accent ring so it reads as chosen.
function CardSearchBox({
  commander,
  deckNames,
  selectedName,
  onPick,
  placeholder,
}: {
  commander: string;
  deckNames: Set<string>;
  selectedName: string | null;
  onPick: (card: DeckCard) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<DeckCard[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setResults([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const timer = setTimeout(() => {
      searchLegalCards(commander, query, 12)
        .then((res) => {
          if (!cancelled) setResults(res.cards);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [q, commander]);

  return (
    <div className="flex flex-col gap-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" />
        <input
          type="text"
          value={q}
          onChange={(event) => setQ(event.target.value)}
          placeholder={placeholder ?? 'Busca cualquier carta legal…'}
          className="accent-focus w-full rounded-lg border border-black/10 bg-white/70 py-2 pl-9 pr-3 text-sm dark:border-white/10 dark:bg-zinc-950/40"
        />
        {loading && (
          <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-zinc-400" />
        )}
      </div>
      {q.trim().length >= 2 && !loading && results.length === 0 && (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Ninguna carta legal coincide (revisa la identidad de color).
        </p>
      )}
      {results.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {results.map((card) => {
            const inDeck = deckNames.has(card.name);
            const selected = card.name === selectedName;
            return (
              <button
                key={card.oracle_id}
                type="button"
                disabled={inDeck}
                onClick={() => onPick(card)}
                aria-pressed={selected}
                title={inDeck ? `${card.name} ya está en el mazo` : card.name}
                className={`accent-focus flex flex-col overflow-hidden rounded-xl border bg-white/60 text-left transition disabled:opacity-40 dark:bg-zinc-950/40 ${
                  selected
                    ? 'border-transparent ring-2 accent-ring'
                    : 'border-black/10 hover:accent-ring hover:ring-2 dark:border-white/10'
                }`}
              >
                <CardImage
                  card={toViewCard(card)}
                  className="aspect-[5/7] w-full object-cover"
                />
                <span className="flex items-center justify-between gap-1 px-2 py-1.5">
                  <span className="truncate text-xs font-semibold">
                    {card.name}
                  </span>
                  {inDeck ? (
                    <span className="shrink-0 text-[0.6rem] uppercase text-zinc-400">
                      en mazo
                    </span>
                  ) : (
                    <span className="shrink-0 text-[0.6rem] uppercase text-zinc-400">
                      {categoryLabel(card.slot)}
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// The active-swap candidates as an overlay (Guille 2026-07-19: the below-deck
// panel forced a scroll past 100 cards; a popup like the opening hand does not).
// The confirm tray (fixed bottom, higher z) floats over it, so choosing and
// confirming happen without leaving the modal.
function SwapCandidatesModal({
  outCard,
  cands,
  loading,
  error,
  selectedInName,
  onChooseIn,
  onCancel,
  advanced,
  onToggleAdvanced,
  commander,
  deckNames,
}: {
  outCard: DeckCard;
  cands: SwapReplacements | null;
  loading: boolean;
  error: string | null;
  selectedInName: string | null;
  onChooseIn: (card: DeckCard) => void;
  onCancel: () => void;
  advanced: boolean;
  onToggleAdvanced: (value: boolean) => void;
  commander: string;
  deckNames: Set<string>;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onCancel();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  // Audit-style palette: same-role, best-you're-missing, reinforce. The chip on
  // each tile says which axis it came from — the same grammar as the audit rows.
  const replacements = cands?.replacements ?? [];
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Cambiando ${outCard.name}`}
      onClick={onCancel}
      className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:p-6"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="surface flex max-h-[92vh] w-full max-w-7xl flex-col gap-4 overflow-y-auto rounded-lg p-5 pb-28 sm:p-7 sm:pb-28"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-lg font-semibold">
            <ArrowRightLeft className="h-5 w-5 accent-text" />
            Cambiando{' '}
            <span className="rounded bg-rose-500/15 px-2 py-0.5 text-rose-700 ring-1 ring-rose-400/40 dark:text-rose-200">
              {outCard.name}
            </span>
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            <AdvancedToggle advanced={advanced} onChange={onToggleAdvanced} />
            <Button variant="secondary" onClick={onCancel}>
              <X className="h-4 w-4" /> Cancelar cambio
            </Button>
          </div>
        </div>
        <p className="text-sm text-zinc-600 dark:text-zinc-300">
          Las mejores alternativas por rol, como en la auditoría: mismo rol, la
          mejor que te falta y un refuerzo. Todas dejan el mazo válido. Pulsa una
          para previsualizar el cambio y confírmalo abajo.
          {cands && cands.feasible_count > replacements.length && (
            <span className="ml-1 text-zinc-500 dark:text-zinc-400">
              Hay {cands.feasible_count} cambios factibles en total.
            </span>
          )}
        </p>
        {loading ? (
          <p className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin" /> Buscando alternativas…
          </p>
        ) : error ? (
          <p className="text-sm text-amber-700 dark:text-amber-300">
            No se pudieron cargar las alternativas ({error}).
          </p>
        ) : replacements.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No hay alternativas factibles para esta carta.
          </p>
        ) : (
          // Current card + up to four suggestions: five tiles, one row, same
          // sequential-mode grammar as the audit (Guille 2026-07-19).
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <div className="flex flex-col overflow-hidden rounded-xl border border-rose-400/50 bg-white/60 dark:bg-zinc-950/40">
              <span className="flex items-center justify-center gap-1 bg-rose-500/15 px-2 py-1 text-[0.65rem] font-bold uppercase tracking-wide text-rose-700 dark:text-rose-200">
                Actual · {categoryLabel(outCard.slot)}
              </span>
              <CardImage
                card={toViewCard(outCard)}
                className="aspect-[5/7] w-full object-cover"
              />
              <span className="truncate px-2 py-1.5 text-xs font-semibold">
                {outCard.name}
              </span>
            </div>
            {replacements.map((rep) => {
              const selected = rep.card.name === selectedInName;
              return (
                <button
                  key={`${rep.kind}:${rep.card.name}`}
                  type="button"
                  onClick={() => onChooseIn(rep.card)}
                  aria-pressed={selected}
                  title={rep.note}
                  className={`accent-focus flex flex-col overflow-hidden rounded-xl border bg-white/60 text-left transition hover:accent-ring hover:ring-2 dark:bg-zinc-950/40 ${
                    selected
                      ? 'border-transparent ring-2 accent-ring'
                      : 'border-black/10 dark:border-white/10'
                  }`}
                >
                  <span
                    className={`flex items-center justify-center gap-1 px-2 py-1 text-[0.65rem] font-bold uppercase tracking-wide ring-0 ${REPLACEMENT_CHIP[rep.kind]}`}
                  >
                    {REPLACEMENT_LABEL[rep.kind]} · {categoryLabel(rep.card.slot)}
                  </span>
                  <CardImage
                    card={toViewCard(rep.card)}
                    className="aspect-[5/7] w-full object-cover"
                  />
                  <span className="flex items-center justify-between gap-2 px-2 py-1.5">
                    <span className="truncate text-xs font-semibold">
                      {rep.card.name}
                    </span>
                    <ArrowRightLeft className="h-3.5 w-3.5 shrink-0 accent-text" />
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {advanced && (
          <div className="mt-2 border-t border-black/10 pt-4 dark:border-white/10">
            <p className="mb-3 flex items-center gap-2 text-sm font-semibold accent-text">
              <Wrench className="h-4 w-4" /> Modo avanzado: mete la carta que
              quieras
            </p>
            <p className="mb-3 text-xs text-zinc-500 dark:text-zinc-400">
              Bajo tu responsabilidad. El cambio se valida igual: verás en rojo
              lo que rompe el mazo y en ámbar lo que no recomendamos.
            </p>
            <CardSearchBox
              commander={commander}
              deckNames={deckNames}
              selectedName={selectedInName}
              onPick={onChooseIn}
              placeholder={`Busca la carta que entra por ${outCard.name}…`}
            />
          </div>
        )}
      </div>
    </div>
  );
}

// The maybeboard bench: best non-selected cards per role. The active-swap
// candidates live in SwapCandidatesModal, not here.
function MaybeboardPanel({
  bench,
}: {
  bench: Maybeboard | null;
}) {
  // Standing bench: best non-selected cards grouped by role.
  const roles = bench
    ? Object.entries(bench).filter(([, cards]) => cards.length > 0)
    : [];
  return (
    <Panel>
      <h3 className="mb-1 text-lg font-semibold">Banquillo (maybeboard)</h3>
      <p className="mb-4 text-sm text-zinc-500 dark:text-zinc-400">
        Las mejores cartas que se quedaron fuera, por rol. Para cambiar una carta,
        haz clic en cualquier carta del mazo de arriba.
      </p>
      {!bench ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Cargando banquillo…
        </p>
      ) : roles.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No hay cartas adicionales elegibles para el banquillo.
        </p>
      ) : (
        <div className="flex flex-col gap-6">
          {roles.map(([role, cards]) => (
            <div key={role}>
              <div className="mb-3 flex items-baseline gap-2 border-b border-black/10 pb-1 dark:border-white/10">
                <h4 className="text-lg font-extrabold tracking-tight accent-text">
                  {categoryLabel(role)}
                </h4>
                <span className="text-sm tabular-nums text-zinc-400 dark:text-zinc-500">
                  {cards.length}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
                {cards.map((card) => (
                  <CardTile key={card.oracle_id} card={toViewCard(card)} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// The swap confirmation tray: a pinned bar making the change unambiguous.
// ENTRA (Y, accent + plus) ↔ SALE (X, rose + minus). Confirm stays disabled
// until the API has ruled the pair feasible — the client never decides that.
//
// `fixed`, not the TFM's `sticky`: our <main> is `overflow-hidden` (it clips the
// blurred full-bleed art), and an overflow-hidden ancestor makes a sticky child
// scroll away — the tray landed ~5000px below the fold, so picking a candidate
// appeared to do nothing.
function SwapTray({
  outCard,
  inCard,
  validation,
  validating,
  error,
  onConfirm,
  onCancel,
}: {
  outCard: DeckCard;
  inCard: DeckCard;
  validation: SwapValidation | null;
  validating: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const blocked = validation !== null && !validation.feasible;
  return (
    <div className="fixed inset-x-4 bottom-4 z-50 mx-auto max-w-3xl rounded-xl border border-black/10 bg-white/95 p-4 shadow-2xl backdrop-blur dark:border-white/15 dark:bg-zinc-900/95">
      <div className="flex flex-col items-stretch gap-4 sm:flex-row sm:items-center">
        <div className="flex flex-1 items-center justify-center gap-3 sm:justify-start">
          <SwapSide card={inCard} kind="in" />
          <ArrowRightLeft className="h-5 w-5 shrink-0 text-zinc-400" />
          <SwapSide card={outCard} kind="out" />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="secondary" onClick={onCancel}>
            <X className="h-4 w-4" /> Cancelar
          </Button>
          <Button
            onClick={onConfirm}
            disabled={validating || blocked || validation === null}
          >
            {validating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Validando…
              </>
            ) : (
              <>
                <ArrowRightLeft className="h-4 w-4" /> Confirmar cambio
              </>
            )}
          </Button>
        </div>
      </div>
      {error && (
        <p className="mt-3 text-sm text-rose-700 dark:text-rose-300">
          No se pudo validar el cambio ({error}).
        </p>
      )}
      {blocked && (
        <div className="mt-3 rounded-lg border border-rose-300/70 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-900 dark:border-rose-500/40 dark:bg-rose-950/40 dark:text-rose-100">
          <p className="font-semibold">Este cambio dejaría el mazo fuera de rango:</p>
          <ul className="mt-1 list-inside list-disc">
            {validation.blockers.map((blocker, index) => (
              <li key={`${blocker.code}-${index}`}>{blocker.message}</li>
            ))}
          </ul>
        </div>
      )}
      {validation?.feasible && validation.warnings.length > 0 && (
        <div className="mt-3 rounded-lg border border-amber-300/70 bg-amber-50 px-3.5 py-2.5 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-100">
          <ul className="list-inside list-disc">
            {validation.warnings.map((warning, index) => (
              <li key={`${warning.code}-${index}`}>{warning.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function SwapSide({ card, kind }: { card: DeckCard; kind: 'in' | 'out' }) {
  const isIn = kind === 'in';
  return (
    <div
      className={`flex min-w-0 items-center gap-2 rounded-lg border px-3 py-2 ${
        isIn
          ? 'accent-border accent-soft-bg'
          : 'border-rose-400/60 bg-rose-50 opacity-80 dark:bg-rose-950/30'
      }`}
    >
      <span
        className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${
          isIn ? 'accent-bg' : 'bg-rose-500/20 text-rose-700 dark:text-rose-200'
        }`}
      >
        {isIn ? <Plus className="h-4 w-4" /> : <Minus className="h-4 w-4" />}
      </span>
      <div className="min-w-0">
        <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          {isIn ? 'Entra' : 'Sale'}
        </p>
        <p className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-50">
          {card.name}
        </p>
        {card.score !== null && (
          <p className="text-xs tabular-nums text-zinc-500 dark:text-zinc-400">
            Score {card.score.toFixed(2)}
          </p>
        )}
      </div>
    </div>
  );
}
