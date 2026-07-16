// The dial: a bar between two extremes, one per quota category.
//
// Guille's spec, verbatim: "una barra de selección entre el low y el high". So
// there is no third named option — the middle stop is simply where the bar sits
// when you have not touched it, and it renders unlabelled on purpose. The low /
// high copy is his and is the point of the control, so it is always on screen,
// not hidden behind a tooltip.

import { DIAL_POSITIONS, categoryHelp, categoryLabel, type Dial } from '../labels';
import type { DialPosition } from '../api';
import { LabelWithHelp } from './ui';

export function DialBar({
  dial,
  value,
  onChange,
}: {
  dial: Dial;
  /** `undefined` means untouched: the bar sits centred and the key is not sent. */
  value: DialPosition | undefined;
  onChange: (position: DialPosition) => void;
}) {
  const index = DIAL_POSITIONS.indexOf(value ?? 'center');
  const label = categoryLabel(dial.category);

  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          <LabelWithHelp label={label} help={categoryHelp(dial.category)} />
        </span>
        {value && value !== 'center' && (
          <button
            type="button"
            onClick={() => onChange('center')}
            className="text-[0.7rem] font-medium text-zinc-400 underline-offset-2 transition hover:text-zinc-600 hover:underline dark:text-zinc-500 dark:hover:text-zinc-300"
          >
            reset
          </button>
        )}
      </div>

      <input
        type="range"
        min={0}
        max={2}
        step={1}
        value={index}
        onChange={(event) => onChange(DIAL_POSITIONS[Number(event.target.value)])}
        aria-label={`${label}: ${dial.low} ↔ ${dial.high}`}
        aria-valuetext={
          value === 'low' ? dial.low : value === 'high' ? dial.high : 'sin tocar'
        }
        className="accent-accent-color h-2 w-full cursor-pointer"
      />

      {/* Both memes always visible; the active end lights up with the accent. */}
      <div className="flex items-start justify-between gap-3 text-[0.7rem] leading-4">
        <button
          type="button"
          onClick={() => onChange('low')}
          className={`max-w-[46%] text-left transition ${
            value === 'low'
              ? 'accent-text font-semibold'
              : 'text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200'
          }`}
        >
          {dial.low}
        </button>
        <button
          type="button"
          onClick={() => onChange('high')}
          className={`max-w-[46%] text-right transition ${
            value === 'high'
              ? 'accent-text font-semibold'
              : 'text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200'
          }`}
        >
          {dial.high}
        </button>
      </div>
    </div>
  );
}
