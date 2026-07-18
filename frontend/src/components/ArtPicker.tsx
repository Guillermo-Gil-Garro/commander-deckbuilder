// Art/language gallery for one card. Lists the card's printings (the backend
// already filtered to high-res only, unless the card has none at all), Spanish
// and English, newest first. Picking one becomes a manual override that applies
// everywhere — deck views, hover previews and the proxy PDF — and persists
// across decks. A printing the user wants that has no high-res scan is out of
// scope on purpose: they hunt that image outside the system.

import { useEffect, useState } from 'react';
import { Loader2, RotateCcw, X } from 'lucide-react';
import { Button } from './ui';
import { fetchCardPrints, type CardPrint, type CardPrints } from '../api';

// The label a printing shows under its thumbnail: language, set, year.
function printCaption(print: CardPrint): string {
  const year = print.released_at.slice(0, 4);
  return `${print.set_name}${year ? ` · ${year}` : ''}`;
}

export function ArtPicker({
  oracleId,
  cardName,
  activeScryfallId,
  hasManual,
  onPick,
  onReset,
  onClose,
}: {
  oracleId: string;
  cardName: string;
  /** The printing currently shown for this card (manual or Spanish default);
   *  null when the card still wears the pool's stock art. */
  activeScryfallId: string | null;
  hasManual: boolean;
  onPick: (print: CardPrint) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const [data, setData] = useState<CardPrints | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchCardPrints(oracleId)
      .then((prints) => {
        if (active) setData(prints);
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : 'Error desconocido');
        }
      });
    return () => {
      active = false;
    };
  }, [oracleId]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const hasLowres = data !== null && data.prints.some((p) => !p.highres);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Ediciones de ${cardName}`}
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:p-6"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="surface flex max-h-[92vh] w-full max-w-5xl flex-col gap-4 overflow-y-auto rounded-lg p-5 sm:p-7"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold">{cardName}</h3>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Elige la edición a mostrar e imprimir.
              {hasLowres &&
                ' Las marcadas «baja res» son escaneos reales pero blandos.'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {hasManual && (
              <Button variant="secondary" onClick={onReset}>
                <RotateCcw className="h-4 w-4" /> Volver al por defecto
              </Button>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Cerrar"
              className="accent-focus inline-flex h-9 w-9 items-center justify-center rounded-lg border border-black/10 bg-white text-zinc-600 transition hover:bg-zinc-100 dark:border-white/10 dark:bg-zinc-900/80 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {error ? (
          <p className="text-sm text-rose-700 dark:text-rose-300">
            No se pudieron cargar las ediciones ({error}).
          </p>
        ) : data === null ? (
          <p className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin" /> Buscando ediciones…
          </p>
        ) : data.prints.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Scryfall no tiene ediciones imprimibles de esta carta.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {data.prints.map((print) => {
              const active = print.scryfall_id === activeScryfallId;
              return (
                <button
                  key={print.scryfall_id}
                  type="button"
                  onClick={() => onPick(print)}
                  className={`accent-focus group flex flex-col gap-1 rounded-xl text-left transition ${
                    active ? 'ring-2 accent-ring' : 'hover:accent-ring hover:ring-2'
                  }`}
                >
                  <div className="relative overflow-hidden rounded-xl ring-1 ring-black/10 dark:ring-white/10">
                    <img
                      src={print.image_uri_normal}
                      alt={`${cardName} — ${print.set_name}`}
                      loading="lazy"
                      className="aspect-[5/7] w-full object-cover"
                    />
                    <span className="absolute left-1.5 top-1.5 flex items-center gap-1">
                      <span className="rounded bg-black/75 px-1.5 py-0.5 text-[0.65rem] font-bold uppercase tracking-wide text-white ring-1 ring-white/25">
                        {print.lang}
                      </span>
                      {!print.highres && (
                        <span className="rounded bg-amber-500/90 px-1.5 py-0.5 text-[0.65rem] font-bold text-black ring-1 ring-white/25">
                          baja res
                        </span>
                      )}
                    </span>
                    {active && (
                      <span className="absolute bottom-1.5 right-1.5 rounded bg-black/75 px-1.5 py-0.5 text-[0.65rem] font-semibold text-white ring-1 ring-white/25">
                        En uso
                      </span>
                    )}
                  </div>
                  <span className="truncate px-0.5 text-[0.7rem] text-zinc-500 dark:text-zinc-400">
                    {printCaption(print)}
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
