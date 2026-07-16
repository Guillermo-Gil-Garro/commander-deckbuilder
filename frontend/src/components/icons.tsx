import { useEffect, useState } from 'react';
import { Gauge, SlidersHorizontal } from 'lucide-react';

// Cycles through icons that evoke the dials with a soft crossfade. Pure CSS
// transition on a key swap.
const PARAM_ICONS = [SlidersHorizontal, Gauge] as const;

export function ParamsIcon({ className }: { className?: string }) {
  const [index, setIndex] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => {
      setIndex((i) => (i + 1) % PARAM_ICONS.length);
    }, 1800);
    return () => window.clearInterval(id);
  }, []);
  const Icon = PARAM_ICONS[index];
  return (
    <span className={`relative inline-grid ${className ?? ''}`}>
      {PARAM_ICONS.map((Candidate, i) => (
        <Candidate
          key={i}
          className={`col-start-1 row-start-1 h-full w-full transition-opacity duration-500 ${
            i === index ? 'opacity-100' : 'opacity-0'
          }`}
          aria-hidden="true"
        />
      ))}
      <span className="sr-only">{Icon.displayName ?? 'diales'}</span>
    </span>
  );
}
