import { useEffect, useLayoutEffect, useMemo, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Setup } from './views/Setup';
import { Result } from './views/Result';
import { Sequential } from './views/Sequential';
import {
  buildDeck,
  fetchCommanders,
  sequentialStart,
  type BuildRequest,
  type BuildResult,
  type CommanderListItem,
  type Dials,
  type SequentialStart,
} from './api';

type Theme = 'dark' | 'light';
type Step = 'setup' | 'result' | 'sequential';

const THEME_STORAGE_KEY = 'theme';

function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    if (typeof window === 'undefined') return 'dark';
    return window.localStorage.getItem(THEME_STORAGE_KEY) === 'light'
      ? 'light'
      : 'dark';
  });

  const [commanders, setCommanders] = useState<CommanderListItem[]>([]);
  const [commandersError, setCommandersError] = useState<string | null>(null);
  const [loadingCommanders, setLoadingCommanders] = useState(true);

  const [step, setStep] = useState<Step>('setup');
  const [building, setBuilding] = useState(false);
  const [buildError, setBuildError] = useState<string | null>(null);
  const [builtCommander, setBuiltCommander] = useState<CommanderListItem | null>(
    null,
  );
  const [result, setResult] = useState<BuildResult | null>(null);
  const [buildReq, setBuildReq] = useState<BuildRequest | null>(null);
  // The guided flow's payload: the same build /build would return, plus the
  // cards worth deciding. Only set when the user asked for sequential mode.
  const [start, setStart] = useState<SequentialStart | null>(null);

  useLayoutEffect(() => {
    const isDark = theme === 'dark';
    document.documentElement.classList.toggle('dark', isDark);
    document.documentElement.style.colorScheme = theme;
    // Accent is bound to the active theme: dark -> gold, light -> purple.
    document.documentElement.setAttribute(
      'data-accent',
      isDark ? 'gold' : 'purple',
    );
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    fetchCommanders()
      .then((items) => {
        if (!cancelled) setCommanders(items);
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setCommandersError(
            error instanceof Error ? error.message : 'Error desconocido',
          );
      })
      .finally(() => {
        if (!cancelled) setLoadingCommanders(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Pick one commander art at random (once, when the pool first loads) to use
  // as a blurred full-bleed background. Non-deterministic by design.
  const backgroundArt = useMemo(() => {
    const withArt = commanders.filter((c) => c.image_uri_art_crop);
    if (withArt.length === 0) return null;
    return withArt[Math.floor(Math.random() * withArt.length)]
      .image_uri_art_crop;
  }, [commanders]);

  function toggleTheme() {
    setTheme((current) => (current === 'dark' ? 'light' : 'dark'));
  }

  async function handleBuild(
    commander: CommanderListItem,
    dials: Dials,
    sequential: boolean,
  ) {
    setBuilding(true);
    setBuildError(null);
    setBuiltCommander(commander);
    const req: BuildRequest = { commander: commander.name, dials };
    try {
      if (sequential) {
        // /sequential/start IS a build plus an analysis of its result, so it
        // replaces the /build call rather than following it.
        const started = await sequentialStart(req);
        setStart(started);
        setResult(started.deck);
        setBuildReq(req);
        setStep('sequential');
      } else {
        const built = await buildDeck(req);
        setStart(null);
        setResult(built);
        setBuildReq(req);
        setStep('result');
      }
    } catch (error: unknown) {
      setBuildError(
        error instanceof Error ? error.message : 'Error desconocido',
      );
    } finally {
      setBuilding(false);
    }
  }

  // Leaving the guided flow with the player's choices applied: the deck Result
  // shows from here on is the one they decided, not the one the solver handed us.
  function handleFinishSequential(deck: BuildResult) {
    setResult(deck);
    setStart(null);
    setStep('result');
  }

  return (
    <main className="relative min-h-screen overflow-hidden px-4 py-6 text-[#1b1f24] transition-colors dark:text-zinc-100 sm:px-6 sm:py-8 lg:px-10 lg:py-10">
      {backgroundArt && (
        <div
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 z-0 scale-110 bg-cover bg-center opacity-55 blur-md dark:opacity-50"
          style={{ backgroundImage: `url("${backgroundArt}")` }}
        />
      )}
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 z-0 bg-[radial-gradient(ellipse_at_center,_rgba(244,241,235,0.2)_0%,_rgba(244,241,235,0.66)_100%)] dark:bg-[radial-gradient(ellipse_at_center,_rgba(7,11,17,0.35)_0%,_rgba(5,8,13,0.8)_100%)]"
      />
      <div className="relative z-10 mx-auto flex max-w-[1600px] flex-col gap-6 lg:gap-7">
        <Header theme={theme} onToggleTheme={toggleTheme} />
        {step === 'setup' ? (
          <Setup
            commanders={commanders}
            loading={loadingCommanders}
            loadError={commandersError}
            building={building}
            buildError={buildError}
            onBuild={handleBuild}
          />
        ) : step === 'sequential' && start && buildReq ? (
          <Sequential
            start={start}
            req={buildReq}
            onFinish={handleFinishSequential}
            onBack={() => setStep('setup')}
          />
        ) : result ? (
          <Result
            result={result}
            commander={builtCommander}
            req={buildReq}
            onBack={() => setStep('setup')}
          />
        ) : null}
      </div>
    </main>
  );
}

function Header({
  theme,
  onToggleTheme,
}: {
  theme: Theme;
  onToggleTheme: () => void;
}) {
  return (
    <header className="flex flex-col gap-4 border-b border-black/10 pb-4 dark:border-white/10 lg:flex-row lg:items-center lg:justify-between lg:gap-8 lg:pb-5">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold leading-tight tracking-normal sm:text-3xl">
          <span className="text-slate-950 dark:text-zinc-50">Commander</span>{' '}
          <span className="accent-text">Deckbuilder</span>
        </h1>
        <p className="mt-0.5 text-sm leading-6 text-zinc-700 dark:text-zinc-200">
          <span className="font-semibold text-slate-900 dark:text-zinc-50">
            Tú pones los límites, nosotros resolvemos la combinatoria.
          </span>{' '}
          Elige comandante, mueve los{' '}
          <span className="accent-text font-semibold">diales</span> y un solver
          elige las{' '}
          <span className="accent-text font-semibold">99 óptimas</span>.
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-3 lg:justify-end">
        <button
          type="button"
          onClick={onToggleTheme}
          aria-label={
            theme === 'dark' ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro'
          }
          aria-pressed={theme === 'dark'}
          className="accent-focus inline-flex items-center justify-center gap-2 rounded-lg border accent-border bg-white px-4 py-2.5 text-sm font-semibold accent-text transition hover:accent-soft-bg dark:bg-zinc-900/80"
        >
          {theme === 'dark' ? (
            <Moon className="h-4 w-4" />
          ) : (
            <Sun className="h-4 w-4" />
          )}
          {theme === 'dark' ? 'Oscuro' : 'Claro'}
        </button>
      </div>
    </header>
  );
}

export default App;
