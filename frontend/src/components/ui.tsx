import type { ReactNode } from 'react';
import { Info } from 'lucide-react';

export type ColorCode = 'W' | 'U' | 'B' | 'R' | 'G' | 'C';

export const COLOR_OPTIONS: ColorCode[] = ['W', 'U', 'B', 'R', 'G', 'C'];

export const colorLabel: Record<ColorCode, string> = {
  W: 'Blanco',
  U: 'Azul',
  B: 'Negro',
  R: 'Rojo',
  G: 'Verde',
  C: 'Incoloro',
};

const manaSymbolClass: Record<ColorCode, string> = {
  W: 'ms-w',
  U: 'ms-u',
  B: 'ms-b',
  R: 'ms-r',
  G: 'ms-g',
  C: 'ms-c',
};

const colorClass: Record<string, string> = {
  W: 'bg-stone-100 text-stone-800 ring-stone-300 dark:bg-stone-200 dark:text-stone-950 dark:ring-stone-400',
  U: 'bg-sky-100 text-sky-800 ring-sky-300 dark:bg-sky-500/20 dark:text-sky-100 dark:ring-sky-400/40',
  B: 'bg-zinc-800 text-white ring-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:ring-zinc-500',
  R: 'bg-rose-100 text-rose-800 ring-rose-300 dark:bg-rose-500/20 dark:text-rose-100 dark:ring-rose-400/40',
  G: 'bg-emerald-100 text-emerald-800 ring-emerald-300 dark:bg-emerald-500/20 dark:text-emerald-100 dark:ring-emerald-400/40',
};

export const panelClass = 'surface rounded-lg p-5 sm:p-6 lg:p-7';

export const fieldControlClass =
  'accent-focus w-full rounded-lg border border-black/10 bg-white px-3.5 py-2.5 text-zinc-950 outline-none transition placeholder:text-zinc-400 dark:border-white/10 dark:bg-zinc-950/70 dark:text-zinc-100 dark:placeholder:text-zinc-500';

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`${panelClass} ${className ?? ''}`}>{children}</section>
  );
}

export function Button({
  children,
  onClick,
  disabled,
  type = 'button',
  fullWidth = false,
  variant = 'primary',
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: 'button' | 'submit';
  fullWidth?: boolean;
  variant?: 'primary' | 'secondary' | 'quiet';
}) {
  const classes = {
    primary: 'accent-bg accent-bg-hover',
    // Accent-tinted outline button: themed border + text, soft fill on hover.
    secondary:
      'accent-focus border accent-border accent-text bg-white hover:accent-soft-bg dark:bg-zinc-900/80',
    quiet:
      'bg-zinc-100 text-zinc-800 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700',
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2.5 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-55 ${
        fullWidth ? 'w-full' : ''
      } ${classes[variant]}`}
    >
      {children}
    </button>
  );
}

export function Input({
  value,
  placeholder,
  inputMode,
  leadingIcon,
  onChange,
}: {
  value?: string;
  placeholder?: string;
  inputMode?: 'text' | 'decimal' | 'numeric';
  leadingIcon?: ReactNode;
  onChange?: (value: string) => void;
}) {
  if (leadingIcon) {
    return (
      <span className="accent-focus flex items-center gap-2 rounded-lg border border-black/10 bg-white px-3.5 py-2.5 text-zinc-950 transition dark:border-white/10 dark:bg-zinc-950/70 dark:text-zinc-100">
        <span className="shrink-0 text-zinc-400 dark:text-zinc-500">
          {leadingIcon}
        </span>
        <input
          value={value}
          onChange={(event) => onChange?.(event.target.value)}
          inputMode={inputMode}
          placeholder={placeholder}
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-zinc-400 dark:placeholder:text-zinc-500"
        />
      </span>
    );
  }
  return (
    <input
      value={value}
      onChange={(event) => onChange?.(event.target.value)}
      inputMode={inputMode}
      placeholder={placeholder}
      className={fieldControlClass}
    />
  );
}

export function Field({
  label,
  children,
}: {
  label: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-1.5 text-sm">
      <span className="font-medium text-zinc-700 dark:text-zinc-300">
        {label}
      </span>
      {children}
    </label>
  );
}

export function LabelWithHelp({ label, help }: { label: string; help: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {label}
      <span
        className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-zinc-100 text-zinc-500 ring-1 ring-black/10 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-white/10"
        title={help}
        aria-label={help}
      >
        <Info className="h-3 w-3" />
      </span>
    </span>
  );
}

export function Pill({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-lg bg-zinc-100 px-2.5 py-1.5 text-xs font-medium text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">
      {children}
    </span>
  );
}

export function ColorPip({ color }: { color: ColorCode }) {
  return (
    <>
      <i className={`ms ${manaSymbolClass[color]} ms-cost`} aria-hidden="true" />
      <span className="sr-only">{colorLabel[color]}</span>
    </>
  );
}

export function ColorPips({ colors }: { colors: ColorCode[] }) {
  const displayedColors = colors.length > 0 ? colors : (['C'] as ColorCode[]);
  return (
    <div
      className="flex gap-1.5"
      aria-label={`Identidad de color: ${displayedColors
        .map((color) => colorLabel[color])
        .join(', ')}`}
    >
      {displayedColors.map((color) => (
        <span
          key={color}
          className={`flex h-6 w-6 items-center justify-center rounded-full text-[0.8rem] font-bold ring-1 ${
            colorClass[color] ??
            'bg-zinc-100 text-zinc-700 ring-zinc-300 dark:bg-zinc-800 dark:text-zinc-200 dark:ring-zinc-600'
          }`}
        >
          <ColorPip color={color} />
        </span>
      ))}
    </div>
  );
}
