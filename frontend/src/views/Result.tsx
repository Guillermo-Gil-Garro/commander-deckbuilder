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
  Sparkles,
  TriangleAlert,
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
  sequentialCandidates,
  sequentialValidate,
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
  type SwapCandidates,
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
  const pdfArtOverrides = useMemo(
    () => artOverridesForExport(deck, resolved),
    [deck, resolved],
  );
  const [artCard, setArtCard] = useState<ViewCard | null>(null);

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
            />
          ) : (
            <DeckView
              result={shownDeck}
              onArtSelect={setArtCard}
              pdfArtOverrides={pdfArtOverrides}
            />
          )}
          {swapsEnabled && req && (
            <AuditPanel
              commander={req.commander}
              dials={req.dials}
              deckRefs={deckRefs}
              onSwap={swap}
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
}) {
  const [bench, setBench] = useState<Maybeboard | null>(null);
  // Active swap: X marked to leave, the same-role candidates, and the chosen Y.
  const [outName, setOutName] = useState<string | null>(null);
  const [cands, setCands] = useState<SwapCandidates | null>(null);
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

  // Fetch the same-role, feasible candidates for the card marked to leave.
  useEffect(() => {
    if (!outName) {
      setCands(null);
      return;
    }
    let cancelled = false;
    setCandsLoading(true);
    setCandsError(null);
    setCands(null);
    sequentialCandidates({
      commander: req.commander,
      dials: req.dials,
      deck: deckRefsRef.current,
      out: outName,
      limit: 20,
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
      />

      <MaybeboardPanel
        bench={bench}
        outCard={outCard}
        cands={cands}
        candsLoading={candsLoading}
        candsError={candsError}
        selectedInName={inCard?.name ?? null}
        onChooseIn={setInCard}
        onCancel={cancelSwap}
      />

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

function AuditPanel({
  commander,
  dials,
  deckRefs,
  onSwap,
}: {
  commander: string;
  dials: BuildRequest['dials'];
  deckRefs: DeckCardRef[];
  onSwap: (
    outName: string,
    chosen: DeckCard,
    validation: SwapValidation,
  ) => void;
}) {
  const [active, setActive] = useState(false);
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);

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

  async function applyReplacement(flaggedName: string, card: DeckCard) {
    setApplying(`${flaggedName}=>${card.name}`);
    setApplyError(null);
    try {
      const validation = await sequentialValidate({
        commander,
        dials,
        deck: deckRefs,
        out: flaggedName,
        in: card.name,
      });
      if (!validation.feasible) {
        setApplyError(
          `Ahora mismo no se puede cambiar ${flaggedName} por ${card.name}.`,
        );
        return;
      }
      onSwap(flaggedName, card, validation);
      // deckRefs changes → the effect re-audits automatically.
    } catch (err: unknown) {
      setApplyError(err instanceof Error ? err.message : 'Error desconocido');
    } finally {
      setApplying(null);
    }
  }

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
          <Button onClick={() => setActive(true)}>
            <Sparkles className="h-4 w-4" /> Auditar mazo
          </Button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel>
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-lg font-semibold">Auditoría del mazo</h3>
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
              onApply={(card) => applyReplacement(flag.card.name, card)}
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
            Las mejores cartas que este comandante quiere y no llevas. Para
            meterlas, elige en un swap qué sacar.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6">
            {audit.missing.map((card) => (
              <div key={card.name} className="flex flex-col gap-1.5">
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
              </div>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}

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
    <div className="rounded-xl border border-amber-300/50 bg-amber-50/40 p-3 dark:border-amber-500/20 dark:bg-amber-500/5">
      <div className="flex gap-3">
        <div className="w-16 shrink-0 overflow-hidden rounded-lg ring-1 ring-black/10 dark:ring-white/10">
          <CardImage
            card={toViewCard(flag.card)}
            className="aspect-[5/7] w-full object-cover"
          />
        </div>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <TriangleAlert className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <span className="font-semibold">{flag.card.name}</span>
          </div>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-300">
            {flag.reason}
          </p>
        </div>
      </div>
      {flag.replacements.length > 0 && (
        <div className="mt-3">
          <p className="mb-1.5 text-xs font-medium text-zinc-500">Cámbiala por:</p>
          <div className="flex flex-wrap gap-2">
            {flag.replacements.map((rep) => (
              <button
                key={`${rep.kind}:${rep.card.name}`}
                type="button"
                onClick={() => onApply(rep.card)}
                disabled={applying === `${flag.card.name}=>${rep.card.name}`}
                title={rep.note}
                className="inline-flex items-center gap-2 rounded-lg border accent-border bg-white px-3 py-2 text-left text-sm transition hover:accent-soft-bg disabled:opacity-50 dark:bg-zinc-900/80"
              >
                {applying === `${flag.card.name}=>${rep.card.name}` ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ArrowRightLeft className="h-4 w-4 accent-text" />
                )}
                <span className="flex flex-col">
                  <span className="font-semibold">{rep.card.name}</span>
                  <span className="text-xs text-zinc-500">
                    {REPLACEMENT_LABEL[rep.kind]} · {categoryLabel(rep.card.slot)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
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

// The API's own notices, verbatim. `warnings` are things worth knowing;
// `unresolved` are cards it could not resolve. Neither is invented here.
function NoticeList({
  notices,
  kind,
}: {
  notices: Notice[];
  kind: 'warning' | 'unresolved';
}) {
  if (notices.length === 0) return null;
  return (
    <div className="flex items-start gap-3 rounded-lg border border-amber-300/70 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-100">
      <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
      <div>
        <p className="font-semibold">
          {kind === 'warning' ? 'Avisos del solver' : 'Cartas sin resolver'}
        </p>
        <ul className="mt-1 list-inside list-disc">
          {notices.map((notice, index) => (
            <li key={`${notice.code}-${index}`}>{notice.message}</li>
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

// The maybeboard bench. Two modes: a standing bench (best non-selected cards per
// role) when no swap is in progress, and the same-role candidates for the card
// marked to leave once a swap starts.
function MaybeboardPanel({
  bench,
  outCard,
  cands,
  candsLoading,
  candsError,
  selectedInName,
  onChooseIn,
  onCancel,
}: {
  bench: Maybeboard | null;
  outCard: DeckCard | null;
  cands: SwapCandidates | null;
  candsLoading: boolean;
  candsError: string | null;
  selectedInName: string | null;
  onChooseIn: (card: DeckCard) => void;
  onCancel: () => void;
}) {
  if (outCard) {
    // One flat list: we have a single scorer, so there is no synergy/power split
    // to de-duplicate the way the TFM had to.
    const candidates = cands?.candidates ?? [];
    return (
      <Panel>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-lg font-semibold">
            <ArrowRightLeft className="h-5 w-5 accent-text" />
            Cambiando{' '}
            <span className="rounded bg-rose-500/15 px-2 py-0.5 text-rose-700 ring-1 ring-rose-400/40 dark:text-rose-200">
              {outCard.name}
            </span>
          </h3>
          <Button variant="secondary" onClick={onCancel}>
            <X className="h-4 w-4" /> Cancelar cambio
          </Button>
        </div>
        <p className="mb-4 text-sm text-zinc-600 dark:text-zinc-300">
          Alternativas que mantienen el mazo válido. Pulsa una para previsualizar
          el cambio.
          {cands && cands.feasible_count > candidates.length && (
            <span className="ml-1 text-zinc-500 dark:text-zinc-400">
              Se muestran {candidates.length} de {cands.feasible_count}{' '}
              factibles.
            </span>
          )}
        </p>
        {candsLoading ? (
          <p className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin" /> Buscando alternativas…
          </p>
        ) : candsError ? (
          <p className="text-sm text-amber-700 dark:text-amber-300">
            No se pudieron cargar las alternativas ({candsError}).
          </p>
        ) : candidates.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            No hay alternativas factibles para esta carta.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {candidates.map((card) => (
              <BenchTile
                key={card.oracle_id}
                card={card}
                selected={card.name === selectedInName}
                onClick={() => onChooseIn(card)}
              />
            ))}
          </div>
        )}
      </Panel>
    );
  }

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

// A clickable bench/candidate tile: a CardTile that selects this card as the one
// to bring in. The selected card gets an accent ring.
function BenchTile({
  card,
  selected,
  onClick,
}: {
  card: DeckCard;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    // A div, not a button: CardTile hosts a flip control for double-faced cards,
    // and nesting a button inside a button lets the inner click reach the outer.
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
      aria-pressed={selected}
      className={`accent-focus block rounded-xl text-left transition ${
        selected ? 'accent-ring' : 'cursor-pointer hover:accent-ring'
      }`}
    >
      <CardTile card={toViewCard(card)} />
    </div>
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
    <div className="fixed inset-x-4 bottom-4 z-40 mx-auto max-w-3xl rounded-xl border border-black/10 bg-white/95 p-4 shadow-2xl backdrop-blur dark:border-white/15 dark:bg-zinc-900/95">
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
