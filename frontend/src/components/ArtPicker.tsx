// Art/language gallery for one card. Lists the card's printings (the backend
// already filtered to high-res only, unless the card has none at all), Spanish
// and English, newest first. Picking one becomes a manual override that applies
// everywhere — deck views, hover previews and the proxy PDF — and persists
// across decks. A printing the user wants that has no high-res scan is out of
// scope on purpose: they hunt that image outside the system.

import { useEffect, useMemo, useState } from 'react';
import { Loader2, RotateCcw, X } from 'lucide-react';
import { Button } from './ui';
import {
  fetchBasicFullart,
  fetchCardPrints,
  fetchTokenPrints,
  type CardPrint,
  type CardPrints,
} from '../api';

// The label a printing shows under its thumbnail: set and year.
function printCaption(print: CardPrint): string {
  const year = print.released_at.slice(0, 4);
  return `${print.set_name}${year ? ` · ${year}` : ''}`;
}

// High-res first, then soft scans; ties keep the newest-first order the
// backend sends. A stable sort, so "newest" survives within each tier.
function byResolution(prints: CardPrint[]): CardPrint[] {
  return [...prints].sort((a, b) => Number(b.highres) - Number(a.highres));
}

export function ArtPicker({
  oracleId,
  cardName,
  activeScryfallId,
  hasManual,
  basic = false,
  token = false,
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
  /** A basic land: the gallery is full-art only (a different endpoint) and not
   *  split by language (the art is what matters, not the printed name). */
  basic?: boolean;
  /** A token: its arts are fetched by `oracleId` used as the base printing id,
   *  shown as one gallery. */
  token?: boolean;
  onPick: (print: CardPrint) => void;
  onReset: () => void;
  onClose: () => void;
}) {
  const [data, setData] = useState<CardPrints | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = token
      ? fetchTokenPrints(oracleId)
      : basic
        ? fetchBasicFullart(cardName)
        : fetchCardPrints(oracleId);
    load
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
  }, [oracleId, cardName, basic, token]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const hasLowres = data !== null && data.prints.some((p) => !p.highres);

  // Basics: one full-art gallery. Other cards: Spanish first (the house
  // default), then English; each tier high-res before soft, newest first.
  const sections = useMemo(() => {
    if (!data) return [];
    if (basic || token) {
      const label = basic ? 'Full-art' : 'Ediciones';
      return [{ key: 'all', label, prints: byResolution(data.prints) }];
    }
    const spanish = byResolution(data.prints.filter((p) => p.lang === 'es'));
    const english = byResolution(data.prints.filter((p) => p.lang !== 'es'));
    return [
      { key: 'es', label: 'Español', prints: spanish },
      { key: 'en', label: 'Inglés', prints: english },
    ].filter((section) => section.prints.length > 0);
  }, [data, basic, token]);

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
                ' «Baja res» es el veredicto de Scryfall (conservador): a tamaño de carta, muchas se imprimen perfectamente bien.'}
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
          sections.map((section) => (
            <div key={section.key}>
              <div className="mb-2 flex items-baseline gap-2 border-b border-black/10 pb-1 dark:border-white/10">
                <h4 className="text-base font-extrabold tracking-tight accent-text">
                  {section.label}
                </h4>
                <span className="text-sm tabular-nums text-zinc-400 dark:text-zinc-500">
                  {section.prints.length}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {section.prints.map((print) => {
                  // For a basic with no manual pick, mark the Theros default as
                  // in use so the current print is always visible.
                  const effectiveActive =
                    activeScryfallId ??
                    (basic || token ? data.default_scryfall_id : null);
                  const active = print.scryfall_id === effectiveActive;
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
                        {!print.highres && (
                          <span className="absolute left-1.5 top-1.5 rounded bg-amber-500/90 px-1.5 py-0.5 text-[0.65rem] font-bold text-black ring-1 ring-white/25">
                            baja res
                          </span>
                        )}
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
            </div>
          ))
        )}
      </div>
    </div>
  );
}
