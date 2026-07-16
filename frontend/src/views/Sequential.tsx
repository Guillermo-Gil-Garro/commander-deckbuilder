// The Sequential view: the deck's doubtful cards, one at a time. Ported from the
// TFM's `views/Sequential.tsx` with four contract differences:
//
//  1. ONE scorer. `/sequential/candidates` answers a single `candidates[]` list,
//     not the TFM's `{synergy[3], power}` split. So there is no "Potencia"
//     column and no `power`/anti-accent tone: an empty second axis would be
//     paperwork, not parity. The row is current + 4 candidates, which keeps the
//     TFM's 5-column grid exactly.
//  2. A swap MUST be validated. `applySwap` (deck.ts) needs the
//     `/sequential/validate` response because `within_band` for lands depends on
//     the Karsten floor, which the deck's own curve moves. The TFM swapped
//     client-side; we ask the API and adopt its verdict.
//  3. The deck travels by NAME, and the current card is resolved out of the deck
//     by `oracle_id` (a DecisionView is a pointer into `deck.nonbasic_cards`),
//     so it renders instantly instead of waiting behind the TFM's placeholder.
//  4. No price/budget/brackets/power_weight — this project has none.

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ArrowLeft,
  Check,
  FlagTriangleRight,
  Info,
  Loader2,
  SkipForward,
  Sparkles,
  TriangleAlert,
} from 'lucide-react';
import { Button, Panel } from '../components/ui';
import { CardTile, ManaCost, SCORE_TOOLTIP } from '../components/cards';
import { CompositionPanel, DeckView } from '../components/DeckView';
import { categoryLabel } from '../labels';
import { toViewCard, useMutableDeck } from '../deck';
import {
  sequentialCandidates,
  sequentialValidate,
  type BuildRequest,
  type BuildResult,
  type DeckCard,
  type SequentialDecision,
  type SequentialStart,
  type SwapCandidates,
} from '../api';

// Four candidates + the current card = the TFM's five columns. Enough to make a
// real choice, few enough to read at a glance; `feasible_count` tells the player
// how many were left out.
const CANDIDATE_LIMIT = 4;

export function Sequential({
  start,
  req,
  onFinish,
  onBack,
}: {
  start: SequentialStart;
  req: BuildRequest;
  onFinish: (deck: BuildResult) => void;
  onBack: () => void;
}) {
  const decisions = start.decisions;
  const total = decisions.length;

  // The live deck and the swap engine, shared with Result: `swap` adopts the
  // API's post-swap verdict rather than re-deriving it here.
  const { deck, deckRefs, swap } = useMutableDeck(start.deck);

  const [index, setIndex] = useState(0);
  const [candidates, setCandidates] = useState<SwapCandidates | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The swap being validated, and why the API refused it if it did.
  const [swapping, setSwapping] = useState<string | null>(null);
  const [blockers, setBlockers] = useState<string[] | null>(null);

  // Read the freshest deck snapshot inside the fetch effect without re-running it
  // on every deck change (the deck and `index` update together on a swap).
  const deckRefsRef = useRef(deckRefs);
  deckRefsRef.current = deckRefs;

  const decision: SequentialDecision | null =
    index < total ? decisions[index] : null;
  const hasNoCandidates = candidates ? candidates.candidates.length === 0 : false;

  // The current card, straight out of the deck: a DecisionView is a pointer by
  // `oracle_id` into `deck.nonbasic_cards`, so we already hold the whole card.
  const currentCard = useMemo(() => {
    if (!decision) return undefined;
    return (
      deck.nonbasic_cards.find((c) => c.oracle_id === decision.oracle_id) ??
      candidates?.current
    );
  }, [decision, deck.nonbasic_cards, candidates]);

  useEffect(() => {
    if (!decision) {
      setCandidates(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCandidates(null);
    setBlockers(null);
    sequentialCandidates({
      commander: req.commander,
      dials: req.dials,
      deck: deckRefsRef.current,
      out: decision.name,
      limit: CANDIDATE_LIMIT,
    })
      .then((result) => {
        if (!cancelled) setCandidates(result);
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
    // `decision` is derived from `index`; the deck is read via ref (see above).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, req]);

  // Ask the API to rule on the pair, then adopt its verdict. The candidates are
  // already feasible by construction, but the response is also what carries the
  // post-swap counts and the recomputed Karsten floor — so this is not a
  // formality, it is where the new composition comes from.
  async function swapDecision(chosen: DeckCard) {
    if (!decision || swapping) return;
    setSwapping(chosen.name);
    setBlockers(null);
    try {
      const validation = await sequentialValidate({
        commander: req.commander,
        dials: req.dials,
        deck: deckRefsRef.current,
        out: decision.name,
        in: chosen.name,
      });
      if (!validation.feasible) {
        setBlockers(validation.blockers.map((b) => b.message));
        return;
      }
      swap(decision.name, chosen, validation);
      setIndex((i) => i + 1);
    } catch (err: unknown) {
      setBlockers([err instanceof Error ? err.message : 'Error desconocido']);
    } finally {
      setSwapping(null);
    }
  }

  function keepCurrent() {
    setBlockers(null);
    setIndex((i) => i + 1);
  }

  return (
    <div className="flex flex-col gap-6">
      <button
        type="button"
        onClick={onBack}
        className="accent-focus inline-flex w-fit items-center gap-2 rounded-lg border accent-border bg-white px-3.5 py-2 text-sm font-semibold accent-text transition hover:accent-soft-bg dark:bg-zinc-900/80"
      >
        <ArrowLeft className="h-4 w-4" /> Volver al setup
      </button>

      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide accent-text">
          <FlagTriangleRight className="h-4 w-4" /> Modo secuencial
        </div>
        <h2 className="text-2xl font-bold sm:text-3xl">
          {start.deck.commander_name}
        </h2>
        <p className="text-sm text-zinc-600 dark:text-zinc-300">
          Decide las cartas dudosas del mazo una a una. Para cada una, elige un
          candidato del mismo rol o mantén la actual.
        </p>
      </header>

      <HonestyNote />

      {total === 0 ? (
        <Panel>
          <div className="flex items-start gap-3">
            <Info className="mt-0.5 h-5 w-5 shrink-0 text-zinc-400" />
            <div>
              <h3 className="text-lg font-semibold">
                No hay cartas dudosas que decidir
              </h3>
              <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-300">
                El mazo construido no dejó decisiones pendientes
                {start.deck.status === 'INFEASIBLE'
                  ? ' (no se encontró un mazo válido con tus restricciones).'
                  : ': ninguna categoría es lo bastante grande como para que haya un codo claro.'}
              </p>
              <div className="mt-4">
                <Button onClick={() => onFinish(deck)}>Ver resultado</Button>
              </div>
            </div>
          </div>
        </Panel>
      ) : (
        <>
          {decision ? (
            <Panel>
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-lg font-semibold">
                  Decisión {index + 1} de {total}
                </h3>
                <ProgressDots total={total} index={index} />
              </div>

              <div className="flex flex-col gap-4">
                <p className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                  {loading ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Buscando
                      candidatos del mismo rol…
                    </>
                  ) : candidates && !hasNoCandidates ? (
                    `${candidates.feasible_count} ${
                      candidates.feasible_count === 1
                        ? 'opción factible del mismo rol'
                        : 'opciones factibles del mismo rol'
                    }; se muestran ${candidates.candidates.length}. Pulsa una para sustituir, o mantén la actual.`
                  ) : (
                    'Elige un candidato del mismo rol o mantén la carta actual.'
                  )}
                </p>

                {/* The five cards in a single row: actual + 4 candidatos.
                    Horizontal scroll on narrow screens; on wider ones a 5-column grid
                    that fills the FULL container width (each column = 1/5, image w-full). */}
                <div className="-mx-1 flex gap-3 overflow-x-auto px-1 pb-1 sm:grid sm:grid-cols-5 sm:gap-4 sm:overflow-visible sm:px-0">
                  <CardColumn
                    icon={<FlagTriangleRight className="h-3.5 w-3.5" />}
                    text={`Actual · ${categoryLabel(decision.category)}`}
                    tone="current"
                  >
                    <CurrentCard
                      decision={decision}
                      current={currentCard}
                      onKeep={keepCurrent}
                    />
                  </CardColumn>

                  {!loading &&
                    !error &&
                    candidates?.candidates.map((card) => (
                      <CardColumn
                        key={card.oracle_id}
                        icon={<Sparkles className="h-3.5 w-3.5" />}
                        text="Candidato"
                        tone="synergy"
                      >
                        <DecisionCard
                          card={card}
                          tone="synergy"
                          onClick={() => void swapDecision(card)}
                          actionLabel={
                            swapping === card.name ? 'Validando…' : 'Sustituir'
                          }
                          busy={swapping !== null}
                        />
                      </CardColumn>
                    ))}
                </div>

                {error ? (
                  <div className="flex items-start gap-3 rounded-lg border border-amber-300/70 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-100">
                    <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" />
                    <p>
                      No se pudieron cargar los candidatos ({error}). Puedes
                      mantener la carta actual o terminar.
                    </p>
                  </div>
                ) : !loading && candidates && hasNoCandidates ? (
                  <p className="text-sm text-zinc-500 dark:text-zinc-400">
                    No hay candidatos factibles del mismo rol para esta carta.
                    Mantén la actual.
                  </p>
                ) : null}

                {blockers && (
                  <div className="rounded-lg border border-rose-300/70 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-500/40 dark:bg-rose-950/40 dark:text-rose-100">
                    <p className="font-semibold">
                      Ese cambio no se pudo aplicar:
                    </p>
                    <ul className="mt-1 list-inside list-disc">
                      {blockers.map((message, i) => (
                        <li key={i}>{message}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-black/5 pt-5 dark:border-white/5">
                <Button variant="secondary" onClick={keepCurrent}>
                  <SkipForward className="h-4 w-4" /> Mantener actual
                </Button>
                <Button variant="secondary" onClick={() => onFinish(deck)}>
                  <Check className="h-4 w-4" /> Terminar ahora
                </Button>
                <span
                  title={SCORE_TOOLTIP}
                  className="ml-auto text-xs text-zinc-500 dark:text-zinc-400"
                >
                  Score de EDHREC: lo que la comunidad juega, no calidad
                </span>
              </div>
            </Panel>
          ) : (
            <Panel>
              <div className="flex flex-col items-start gap-4">
                <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300">
                  <Check className="h-5 w-5" />
                  <h3 className="text-lg font-semibold">
                    Decisiones completadas
                  </h3>
                </div>
                <p className="text-sm text-zinc-600 dark:text-zinc-300">
                  Has revisado las {total} cartas dudosas. Mira el mazo final con
                  tus elecciones aplicadas.
                </p>
                <Button onClick={() => onFinish(deck)}>Ver mazo final</Button>
              </div>
            </Panel>
          )}

          {/* Live deck: updates with every swap so the user decides in context.
              The composition is the API's own verdict, adopted by `applySwap`. */}
          <section className="flex flex-col gap-4">
            <h3 className="text-lg font-bold tracking-tight">Mazo actual</h3>
            <CompositionPanel result={deck} />
            <DeckView result={deck} showExport={false} />
          </section>
        </>
      )}
    </div>
  );
}

function ProgressDots({ total, index }: { total: number; index: number }) {
  // Cap the dots so a 12-decision flow stays compact.
  const dots = Math.min(total, 12);
  return (
    <div className="flex items-center gap-1.5" aria-hidden="true">
      {Array.from({ length: dots }, (_, i) => {
        const scaled = Math.round((i / dots) * total);
        const done = scaled < index;
        const current = scaled === index || i === Math.min(index, dots - 1);
        return (
          <span
            key={i}
            className={`h-2 w-2 rounded-full ${
              done
                ? 'accent-bg'
                : current
                  ? 'bg-zinc-400 dark:bg-zinc-500'
                  : 'bg-zinc-200 dark:bg-zinc-700'
            }`}
          />
        );
      })}
    </div>
  );
}

// No `power` tone: the TFM's third tone existed for its second scorer, and we
// have one. `current` is neutral/translucent, `synergy` uses the theme accent.
type Tone = 'current' | 'synergy';

const FRAME: Record<Tone, string> = {
  current: 'border-black/10 bg-black/5 dark:border-white/10 dark:bg-white/5',
  synergy: 'accent-border accent-soft-bg',
};
const HOVER_RING: Record<Tone, string> = {
  current: 'hover:ring-2 hover:ring-zinc-400/50',
  synergy: 'hover:accent-ring',
};
const LABEL_TONE: Record<Tone, string> = {
  current: 'text-zinc-500 dark:text-zinc-400',
  synergy: 'accent-text',
};

// Uniform card width for ALL five cards. The card fills its column: a fixed width
// while the row scrolls (narrow), full width inside the 5-column grid (sm+), so
// the current card is never smaller than the candidates.
const CARD_W = 'w-[150px] sm:w-full';

// One column of the decision row: a colour-coded one-line label above a card,
// fixed to the shared card width so the five columns align and scroll together.
function CardColumn({
  icon,
  text,
  tone,
  children,
}: {
  icon: ReactNode;
  text: string;
  tone: Tone;
  children: ReactNode;
}) {
  return (
    <div className={`flex shrink-0 flex-col gap-2 ${CARD_W}`}>
      <span
        title={text}
        className={`flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide ${LABEL_TONE[tone]}`}
      >
        {icon}
        <span className="truncate">{text}</span>
      </span>
      {children}
    </div>
  );
}

// One uniformly-sized card in a colour-coded rounded frame. Clicking runs `onClick`
// (swap for candidates, keep for the current card); the hint names the action.
function DecisionCard({
  card,
  tone,
  onClick,
  actionLabel,
  busy = false,
}: {
  card: DeckCard;
  tone: Tone;
  onClick: () => void;
  actionLabel: string;
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={`accent-focus group block ${CARD_W} cursor-pointer rounded-2xl border p-2.5 text-left transition disabled:cursor-wait ${FRAME[tone]} ${HOVER_RING[tone]}`}
    >
      <CardTile card={toViewCard(card)} />
      <div className="mt-1.5 flex items-start justify-between gap-2 px-0.5">
        <span className="text-sm font-semibold leading-snug text-zinc-900 dark:text-zinc-50">
          {card.name}
        </span>
        <ManaCost manaCost={card.mana_cost} />
      </div>
      <span
        className={`mt-1 inline-flex items-center gap-1 px-0.5 text-xs font-semibold opacity-0 transition group-hover:opacity-100 ${LABEL_TONE[tone]}`}
      >
        <Check className="h-3.5 w-3.5" /> {actionLabel}
      </span>
    </button>
  );
}

// The current card. It is a card of the deck we already hold, so it renders in
// full immediately; the name/score placeholder is only a fallback for the case
// where the lookup somehow misses.
function CurrentCard({
  decision,
  current,
  onKeep,
}: {
  decision: SequentialDecision;
  current: DeckCard | undefined;
  onKeep: () => void;
}) {
  if (current) {
    return (
      <DecisionCard
        card={current}
        tone="current"
        onClick={onKeep}
        actionLabel="Mantener"
      />
    );
  }
  return (
    <button
      type="button"
      onClick={onKeep}
      className={`accent-focus group block ${CARD_W} cursor-pointer rounded-2xl border p-2.5 text-left transition ${FRAME.current} ${HOVER_RING.current}`}
    >
      <div className="flex aspect-[5/7] w-full items-center justify-center rounded-xl bg-zinc-200/70 p-3 text-center text-sm font-semibold text-zinc-600 dark:bg-zinc-800/70 dark:text-zinc-300">
        {decision.name}
      </div>
      <p className="mt-1.5 px-0.5 text-xs text-zinc-500 dark:text-zinc-400">
        Score {decision.score.toFixed(2)}
      </p>
    </button>
  );
}

// The TFM's note said "score del modelo (predicción de inclusión)" and that the
// curve is not recomputed. Neither is true here: our score is EDHREC's, and we
// have a real `cmc` on every card, so the curve IS exact after a swap. What does
// NOT get reoptimised is the colour fixing and the maybeboard.
function HonestyNote() {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-black/10 bg-white/70 px-4 py-3 text-sm text-zinc-600 dark:border-white/10 dark:bg-zinc-900/40 dark:text-zinc-300">
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-zinc-400" />
      <p>
        Estas no son cartas <span className="font-semibold">malas</span>: son las
        que puntúan por debajo del codo de su propia categoría, el sitio donde tu
        criterio gana a una media de EDHREC. Los candidatos van{' '}
        <span className="font-semibold">ordenados por el score de EDHREC</span>{' '}
        (sinergia e inclusión con este comandante): mide lo que la comunidad
        juega, no la calidad de la carta. La composición y la curva se recalculan
        con cada cambio, pero el{' '}
        <span className="font-semibold">fixing de color</span> y el maybeboard no
        se reoptimizan.
      </p>
    </div>
  );
}
