// Opening hand + simple mulligan. Frontend-only: the client already holds the
// built deck, so it shuffles and draws locally. The COMMANDER is NOT part of the
// deck (it lives in the command zone); the 99 cards arrive already expanded (each
// basic repeated `count` times). Mulligan is London with the Commander freebie
// SIMPLIFIED: redraw 7, count mulligans; from the 2nd on we do not implement
// interactive bottoming — only an informational note.
//
// Ported from the TFM's `views/OpeningHand.tsx`.

import { useEffect, useState } from 'react';
import { Hand, RotateCcw, Shuffle, X } from 'lucide-react';
import { Button } from '../components/ui';
import { CardImage } from '../components/cards';
import type { ViewCard } from '../deck';

const HAND_SIZE = 7;

// Fisher-Yates over a copy; never mutate the source deck.
function shuffle(deck: ViewCard[]): ViewCard[] {
  const out = [...deck];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function draw(deck: ViewCard[]): ViewCard[] {
  return shuffle(deck).slice(0, HAND_SIZE);
}

export function OpeningHand({
  deck,
  onClose,
}: {
  deck: ViewCard[];
  onClose: () => void;
}) {
  const [mulligans, setMulligans] = useState(0);
  const [hand, setHand] = useState<ViewCard[]>(() => draw(deck));

  // Esc closes the overlay.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  function mulligan() {
    setMulligans((m) => m + 1);
    setHand(draw(deck));
  }

  function newHand() {
    setMulligans(0);
    setHand(draw(deck));
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Mano de apertura"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="surface flex max-h-[92vh] w-[98vw] flex-col gap-5 overflow-y-auto rounded-lg p-5 sm:p-7"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="inline-flex items-center gap-2 text-lg font-semibold">
            <Hand className="h-5 w-5 accent-text" /> Mano de apertura
          </h3>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar"
            className="accent-focus inline-flex h-9 w-9 items-center justify-center rounded-lg border border-black/10 bg-white text-zinc-600 transition hover:bg-zinc-100 dark:border-white/10 dark:bg-zinc-900/80 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
          {hand.map((card, index) => (
            <div
              key={index}
              className="overflow-hidden rounded-xl ring-1 ring-black/10 dark:ring-white/10"
            >
              <CardImage card={card} className="aspect-[5/7] w-full object-cover" />
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-sm font-medium text-zinc-600 dark:text-zinc-300">
            Mulligans: <span className="tabular-nums">{mulligans}</span>
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" onClick={mulligan}>
              <Shuffle className="h-4 w-4" /> Mulligan
            </Button>
            <Button variant="secondary" onClick={newHand}>
              <RotateCcw className="h-4 w-4" /> Nueva mano
            </Button>
            <Button variant="quiet" onClick={onClose}>
              Cerrar
            </Button>
          </div>
        </div>

        {mulligans >= 2 && (
          <p className="rounded-lg border border-amber-300/70 bg-amber-50 px-3.5 py-2.5 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-100">
            Tras {mulligans} mulligans pondrías {mulligans - 1} carta(s) abajo (no
            implementado aquí: la mano se muestra siempre con 7 cartas).
          </p>
        )}

        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Mano de ejemplo al azar (ilustrativa, no es un análisis estadístico). El
          comandante no entra: va en la zona de mando.
        </p>
      </div>
    </div>
  );
}
