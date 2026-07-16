import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Crown,
  Loader2,
  Play,
  Search,
  Star,
} from 'lucide-react';
import {
  Button,
  ColorPip,
  ColorPips,
  Field,
  Input,
  Panel,
  COLOR_OPTIONS,
  colorLabel,
  type ColorCode,
} from '../components/ui';
import { ParamsIcon } from '../components/icons';
import { CardFlipButton } from '../components/cards';
import { DialBar } from '../components/dials';
import { type CommanderListItem, type Dials } from '../api';
import { DIALS, archetypeLabel, ARCHETYPE_OPTIONS } from '../labels';

// Page size tuned to the card grid (fills ~3 rows per page).
const PAGE_SIZE = 24;

/** The archetype of a commander, but only when it is a real judgement.
 *
 *  `/commanders` returns an `archetype` for all 3.288 commanders, but only the
 *  55 curated ones were actually classified by hand: every other commander comes
 *  back as "midrange" because that is the quota resolver's default block, not an
 *  opinion about how the deck plays. Showing that would be inventing a claim, so
 *  the picker treats archetype as unknown for everything outside the shortlist.
 */
export function curatedArchetype(commander: CommanderListItem): string | null {
  return commander.featured ? commander.archetype : null;
}

export function Setup({
  commanders,
  loading,
  loadError,
  building,
  buildError,
  onBuild,
}: {
  commanders: CommanderListItem[];
  loading: boolean;
  loadError: string | null;
  building: boolean;
  buildError: string | null;
  onBuild: (commander: CommanderListItem, dials: Dials) => void;
}) {
  const [search, setSearch] = useState('');
  const [identityFilter, setIdentityFilter] = useState<Set<ColorCode>>(
    new Set(),
  );
  const [archetypeFilter, setArchetypeFilter] = useState<string | null>(null);
  const [selected, setSelected] = useState<CommanderListItem | null>(null);
  const [page, setPage] = useState(0);
  const [dials, setDials] = useState<Dials>({});
  // Anchor for the picker grid: paging scrolls it back into view so the player
  // is not left at the bottom of the previous page.
  const gridRef = useRef<HTMLDivElement>(null);

  function goToPage(next: number) {
    setPage(next);
    gridRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  function toggleIdentity(color: ColorCode) {
    setIdentityFilter((current) => {
      const next = new Set(current);
      if (next.has(color)) next.delete(color);
      else next.add(color);
      return next;
    });
  }

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const wanted = identityFilter;
    // Colorless (C) is exclusive: it only ever describes an empty color identity.
    const wantColorless = wanted.has('C');
    const wantedColors = [...wanted].filter((c) => c !== 'C');
    return commanders.filter((commander) => {
      if (needle && !commander.name.toLowerCase().includes(needle)) return false;
      if (archetypeFilter && curatedArchetype(commander) !== archetypeFilter) {
        return false;
      }
      if (wanted.size > 0) {
        // Exact-identity filter: the commander's color_identity must equal the
        // selected set (no superset). Selecting C means strictly colorless.
        const ci = commander.color_identity;
        if (wantColorless && wantedColors.length === 0) {
          if (ci.length !== 0) return false;
        } else {
          if (ci.length !== wantedColors.length) return false;
          for (const color of wantedColors) {
            if (!ci.includes(color)) return false;
          }
        }
      }
      return true;
    });
  }, [commanders, search, identityFilter, archetypeFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const shown = filtered.slice(
    safePage * PAGE_SIZE,
    safePage * PAGE_SIZE + PAGE_SIZE,
  );

  // Reset to the first page whenever the result set changes (search/filter).
  useEffect(() => {
    setPage(0);
  }, [search, identityFilter, archetypeFilter]);

  function setDial(category: string, position: 'low' | 'center' | 'high') {
    setDials((current) => {
      const next = { ...current };
      // "center" is the untouched default: drop the key so the request does not
      // carry it and the API derives the band from quotas.yaml.
      if (position === 'center') delete next[category];
      else next[category] = position;
      return next;
    });
  }

  const canBuild = Boolean(selected) && !building;

  function handleSubmit() {
    if (!selected || !canBuild) return;
    onBuild(selected, dials);
  }

  return (
    <div className="grid gap-6 lg:gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
      <Panel>
        <div className="mb-5 flex items-center gap-3">
          <Crown className="h-6 w-6 accent-text" />
          <h2 className="text-xl font-semibold">Comandante</h2>
          {!loading && !loadError && (
            <span className="ml-auto text-xs text-zinc-500 dark:text-zinc-400">
              {filtered.length} comandantes disponibles
            </span>
          )}
        </div>

        <div className="mb-6 rounded-lg border border-black/10 bg-white/70 p-4 dark:border-white/10 dark:bg-zinc-950/40 sm:p-5">
          <Field label="Buscar comandante">
            <Input
              value={search}
              onChange={setSearch}
              placeholder="Buscar por nombre"
              leadingIcon={<Search className="h-4 w-4" />}
            />
          </Field>

          <div className="mt-4">
            <p className="mb-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Identidad de color (exacta)
            </p>
            <div
              className="flex flex-wrap gap-2"
              aria-label="Filtro de identidad"
            >
              {COLOR_OPTIONS.map((color) => {
                const active = identityFilter.has(color);
                return (
                  <button
                    key={color}
                    type="button"
                    onClick={() => toggleIdentity(color)}
                    aria-pressed={active}
                    aria-label={`Filtrar: ${colorLabel[color]}`}
                    className={`flex h-8 w-8 items-center justify-center rounded-full text-[0.95rem] font-bold ring-1 transition ${
                      active
                        ? 'accent-bg accent-border'
                        : 'bg-white text-zinc-500 ring-black/10 hover:accent-border dark:bg-zinc-900/70 dark:text-zinc-400 dark:ring-white/10'
                    }`}
                  >
                    <ColorPip color={color} />
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mt-4">
            <p className="mb-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Estilo de juego
            </p>
            <div className="flex flex-wrap gap-2" aria-label="Filtro de estilo">
              {ARCHETYPE_OPTIONS.map((archetype) => {
                const active = archetypeFilter === archetype;
                return (
                  <button
                    key={archetype}
                    type="button"
                    onClick={() =>
                      setArchetypeFilter(active ? null : archetype)
                    }
                    aria-pressed={active}
                    className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                      active
                        ? 'accent-bg accent-border'
                        : 'border-black/10 bg-white text-zinc-600 hover:accent-border dark:border-white/10 dark:bg-zinc-900/70 dark:text-zinc-300'
                    }`}
                  >
                    {archetypeLabel(archetype)}
                  </button>
                );
              })}
            </div>
            <p className="mt-2 text-xs leading-5 text-zinc-500 dark:text-zinc-400">
              El estilo de juego solo está curado para los{' '}
              <span className="font-medium text-zinc-600 dark:text-zinc-300">
                destacados
              </span>
              ; filtrar por estilo muestra solo esos.
            </p>
          </div>
        </div>

        {loading ? (
          <p className="flex items-center gap-2 text-sm text-zinc-500 dark:text-zinc-400">
            <Loader2 className="h-4 w-4 animate-spin" /> Cargando comandantes…
          </p>
        ) : loadError ? (
          <p className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-3 text-sm text-rose-800 dark:border-rose-500/40 dark:bg-rose-950/40 dark:text-rose-200">
            No se pudo cargar el pool de comandantes: {loadError}. ¿Está el API
            arrancado?
          </p>
        ) : (
          <>
            <div
              ref={gridRef}
              className="scroll-mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:gap-5 xl:grid-cols-4"
            >
              {shown.map((commander) => (
                <CommanderPick
                  key={commander.name}
                  commander={commander}
                  selected={selected?.name === commander.name}
                  onSelect={() => setSelected(commander)}
                />
              ))}
            </div>
            {filtered.length === 0 ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                Ningún comandante coincide con el filtro.
              </p>
            ) : pageCount > 1 ? (
              <div className="mt-5 flex items-center justify-between gap-3">
                <Button
                  variant="secondary"
                  onClick={() => goToPage(Math.max(0, safePage - 1))}
                  disabled={safePage === 0}
                >
                  <ChevronLeft className="h-4 w-4" /> Anterior
                </Button>
                <span className="text-xs tabular-nums text-zinc-500 dark:text-zinc-400">
                  Página {safePage + 1} de {pageCount}
                </span>
                <Button
                  variant="secondary"
                  onClick={() => goToPage(Math.min(pageCount - 1, safePage + 1))}
                  disabled={safePage >= pageCount - 1}
                >
                  Siguiente <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            ) : null}
          </>
        )}
      </Panel>

      <aside>
        <Panel>
          <div className="mb-5 flex items-center gap-3">
            <ParamsIcon className="h-5 w-5 accent-text" />
            <h2 className="text-xl font-semibold">Personalización</h2>
          </div>
          <div className="grid gap-5">
            <div className="rounded-lg border border-black/10 bg-white/70 px-3.5 py-3 text-sm dark:border-white/10 dark:bg-zinc-950/40">
              <p className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Comandante seleccionado
              </p>
              <p className="mt-1 font-semibold">
                {selected ? selected.name : 'Ninguno'}
              </p>
            </div>

            <p className="text-xs leading-5 text-zinc-500 dark:text-zinc-400">
              Mueve una barra solo si quieres desviarte. Lo que no toques se
              queda con la cuota que el comandante ya tiene.
            </p>

            {/* Min/max legend: the memes hide which end is which, so spell it out
                once, aligned with the bars below (left = min, right = max). */}
            <div aria-hidden="true" className="-mb-1">
              <div className="flex items-center gap-3 text-[0.7rem] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                <span className="accent-text">mín</span>
                <span className="h-px flex-1 border-t border-dashed border-zinc-400/60 dark:border-zinc-500/60" />
                <span className="accent-text">máx</span>
              </div>
              <p className="mt-1 text-[0.7rem] leading-4 text-zinc-500 dark:text-zinc-400">
                Izquierda = mínimo de la cuota · derecha = máximo.
              </p>
            </div>

            <div className="grid gap-5">
              {DIALS.map((dial) => (
                <DialBar
                  key={dial.category}
                  dial={dial}
                  value={dials[dial.category]}
                  onChange={(position) => setDial(dial.category, position)}
                />
              ))}
            </div>

            {buildError && (
              <p className="rounded-lg border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-500/40 dark:bg-rose-950/40 dark:text-rose-200">
                {buildError}
              </p>
            )}

            <Button fullWidth onClick={handleSubmit} disabled={!canBuild}>
              {building ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Play className="h-4 w-4" />
              )}
              {building ? 'Construyendo…' : 'Construir mazo'}
            </Button>
            {!building && !selected && (
              <p className="-mt-2 text-xs text-zinc-500 dark:text-zinc-400">
                Selecciona un comandante para iniciar.
              </p>
            )}
          </div>
        </Panel>
      </aside>
    </div>
  );
}

function CommanderPick({
  commander,
  selected,
  onSelect,
}: {
  commander: CommanderListItem;
  selected: boolean;
  onSelect: () => void;
}) {
  // The whole card, not a crop: the point is that someone who has never seen the
  // commander can read what it does.
  const [showBack, setShowBack] = useState(false);
  const hasBack = Boolean(commander.image_uri_back_normal);
  const image =
    showBack && hasBack ? commander.image_uri_back_normal : commander.image_uri_normal;
  const description = commander.description;
  const archetype = curatedArchetype(commander);

  return (
    // Wrapper (not a button) so the flip control can be a SIBLING of the select
    // button, not a descendant: a button nested in a button both is invalid and
    // does not reliably stop the outer button from firing on an inner click.
    <div
      className={`group relative flex flex-col overflow-hidden rounded-xl border transition ${
        selected
          ? 'accent-border accent-ring'
          : 'border-black/10 hover:accent-border dark:border-white/10'
      }`}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className="accent-focus flex flex-col text-left"
      >
        <div className="relative aspect-[5/7] w-full overflow-hidden bg-zinc-200 dark:bg-zinc-800">
          {image ? (
            <img
              src={image}
              alt={commander.name}
              loading="lazy"
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="flex h-full w-full items-center justify-center p-2 text-center text-xs font-medium text-zinc-600 dark:text-zinc-300">
              {commander.name}
            </span>
          )}

          {/* Hover/focus description — only the curated commanders have one. */}
          {description && (
            <span className="pointer-events-none absolute inset-x-0 bottom-0 translate-y-full bg-gradient-to-t from-black/95 via-black/85 to-black/40 p-3 text-xs font-medium leading-5 text-zinc-100 opacity-0 transition duration-300 group-hover:translate-y-0 group-hover:opacity-100 group-focus-visible:translate-y-0 group-focus-visible:opacity-100">
              {description}
            </span>
          )}
        </div>

        {/* Name/archetype live below the art, never over it: the card face has to
            stay readable, which is the whole point of showing the full card. */}
        <div className="flex w-full items-start justify-between gap-2 bg-white/70 px-2.5 py-2 dark:bg-zinc-950/40">
          <div className="min-w-0">
            <p className="truncate text-xs font-bold leading-tight text-zinc-900 dark:text-zinc-50">
              {commander.name}
            </p>
            <p className="mt-0.5 flex items-center gap-1 text-[0.7rem] font-medium text-zinc-500 dark:text-zinc-400">
              {commander.featured && (
                <span
                  title="Destacado: elegido a mano, con estilo de juego y descripción."
                  className="accent-text inline-flex shrink-0 items-center gap-0.5 font-semibold"
                >
                  <Star className="h-3 w-3 fill-current" />
                  Destacado
                </span>
              )}
              {commander.featured && archetype && (
                <span aria-hidden="true" className="text-zinc-300 dark:text-zinc-600">
                  ·
                </span>
              )}
              {archetype && <span className="truncate">{archetypeLabel(archetype)}</span>}
            </p>
            {/* Popularity, EDHREC-style: the server already sorts by it, so this
                is just showing the number the order is based on. */}
            {commander.num_decks > 0 && (
              <p className="mt-0.5 text-[0.65rem] tabular-nums text-zinc-400 dark:text-zinc-500">
                {commander.num_decks.toLocaleString('es-ES')} mazos
              </p>
            )}
          </div>
          <div className="shrink-0 scale-90 origin-top-right">
            <ColorPips colors={commander.color_identity} />
          </div>
        </div>
      </button>

      {/* Flip to the back face of a transforming commander (Kefka, Etali…), so a
          player can read the other side. Sibling of the select button, so it
          never selects the commander. */}
      {hasBack && (
        <CardFlipButton
          showBack={showBack}
          onToggle={() => setShowBack((value) => !value)}
        />
      )}
    </div>
  );
}
