// The group's banlist and watchlist, as an informational modal. Read-only: it
// exists so the players can see what is forbidden and what is being watched.
// Data comes from GET /banlist (see api.ts); this view never edits anything.

import { useEffect, useState } from 'react';
import { Ban, Eye, Loader2, ShieldAlert, X } from 'lucide-react';
import { Button } from '../components/ui';
import {
  fetchBanlist,
  type Banlist,
  type BanlistCard,
  type WatchlistCard,
} from '../api';

export function BanlistModal({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<Banlist | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchBanlist()
      .then((res) => {
        if (!cancelled) setData(res);
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
  }, []);

  // Esc closes the overlay.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Banlist y watchlist del grupo"
      onClick={onClose}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:p-6"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="surface my-auto flex w-full max-w-4xl flex-col gap-5 rounded-lg p-5 sm:p-7"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="inline-flex items-center gap-2 text-lg font-semibold">
            <ShieldAlert className="h-5 w-5 accent-text" /> Banlist del grupo
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

        <p className="text-sm text-zinc-600 dark:text-zinc-300">
          Cartas prohibidas y vigiladas en el grupo. Es informativo: el
          constructor ya nunca mete una carta baneada.
        </p>

        {loading ? (
          <p className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin" /> Cargando banlist…
          </p>
        ) : error ? (
          <p className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-500/40 dark:bg-rose-950/40 dark:text-rose-200">
            No se pudo cargar la banlist: {error}.
          </p>
        ) : data ? (
          <div className="flex flex-col gap-7">
            <Section
              icon={<Ban className="h-4 w-4" />}
              title="Prohibidas"
              count={data.banned.length}
              tone="banned"
            >
              {data.banned.map((card) => (
                <BanRow key={card.oracle_id} card={card} tone="banned" />
              ))}
            </Section>

            <Section
              icon={<Eye className="h-4 w-4" />}
              title="Vigiladas (watchlist)"
              count={data.watchlist.length}
              tone="watch"
            >
              {data.watchlist.map((card) => (
                <BanRow key={card.oracle_id} card={card} tone="watch" />
              ))}
            </Section>
          </div>
        ) : null}

        <div className="flex justify-end">
          <Button variant="quiet" onClick={onClose}>
            Cerrar
          </Button>
        </div>
      </div>
    </div>
  );
}

function Section({
  icon,
  title,
  count,
  tone,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  count: number;
  tone: 'banned' | 'watch';
  children: React.ReactNode;
}) {
  const toneText =
    tone === 'banned'
      ? 'text-rose-600 dark:text-rose-300'
      : 'text-amber-600 dark:text-amber-300';
  return (
    <section>
      <div className="mb-3 flex items-baseline gap-2 border-b border-black/10 pb-1 dark:border-white/10">
        <h4
          className={`inline-flex items-center gap-1.5 text-lg font-extrabold tracking-tight ${toneText}`}
        >
          {icon}
          {title}
        </h4>
        <span className="text-sm tabular-nums text-zinc-400 dark:text-zinc-500">
          {count}
        </span>
      </div>
      {count === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          No hay cartas en esta lista.
        </p>
      ) : (
        <ul className="grid gap-2.5 sm:grid-cols-2">{children}</ul>
      )}
    </section>
  );
}

// One card row: name + reason, with the card image shown on hover (and a small
// thumbnail always present so it reads even without hovering).
function BanRow({
  card,
  tone,
}: {
  card: BanlistCard | WatchlistCard;
  tone: 'banned' | 'watch';
}) {
  const scope = 'scope' in card ? card.scope : null;
  const ring =
    tone === 'banned'
      ? 'ring-rose-400/30 dark:ring-rose-500/30'
      : 'ring-amber-400/30 dark:ring-amber-500/30';
  return (
    <li className="group relative flex items-start gap-3 rounded-lg border border-black/10 bg-white/70 p-2.5 dark:border-white/10 dark:bg-zinc-950/40">
      {card.image_uri_normal ? (
        <img
          src={card.image_uri_normal}
          alt={card.name}
          loading="lazy"
          className={`h-16 w-[46px] shrink-0 rounded object-cover object-top ring-1 ${ring}`}
        />
      ) : (
        <div className="flex h-16 w-[46px] shrink-0 items-center justify-center rounded bg-zinc-200 text-center text-[0.6rem] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
          {card.name}
        </div>
      )}
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-50">
          {card.name}
          {scope && (
            <span className="rounded bg-zinc-100 px-1.5 py-0.5 text-[0.6rem] font-medium uppercase tracking-wide text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
              {scope}
            </span>
          )}
        </p>
        <p className="mt-0.5 text-xs leading-5 text-zinc-500 dark:text-zinc-400">
          {card.reason}
        </p>
      </div>

      {/* Full card image on hover, like the deck list rows. */}
      {card.image_uri_normal && (
        <div
          aria-hidden="true"
          className="pointer-events-none absolute left-2 top-full z-50 mt-1 hidden w-[220px] overflow-hidden rounded-xl shadow-2xl ring-1 ring-black/20 group-hover:block"
        >
          <img src={card.image_uri_normal} alt="" className="block w-full" loading="lazy" />
        </div>
      )}
    </li>
  );
}
