// Shared card-rendering primitives. Ported from the TFM's `components/cards.tsx`,
// minus the price and Game Changer affordances: Guille plays with proxies (the
// price is irrelevant) and runs his own banlist, so this project has neither.

import { useState } from 'react';
import { FlipHorizontal2, Palette, Sparkles } from 'lucide-react';
import type { ViewCard } from '../deck';

// Parse a Scryfall mana-cost string ("{2}{R}{R}", "{W/U}", "{X}") into the symbol
// codes mana-font expects: lowercase, slashes dropped ({W/U} -> "wu", {2} -> "2").
export function parseManaSymbols(manaCost: string): string[] {
  const matches = manaCost.match(/\{[^}]+\}/g);
  if (!matches) return [];
  return matches.map((token) =>
    token.slice(1, -1).replace(/\//g, '').toLowerCase(),
  );
}

// Our score is EDHREC's (synergy + inclusion for this commander) — not a model we
// trained, and not a measure of card quality. The tooltip says exactly that.
export const SCORE_TOOLTIP =
  'Score de EDHREC: sinergia e inclusión con este comandante (no es calidad)';

// Score pill, labelled so the number is unambiguous. `tone="light"` renders on a
// dark/image overlay (visual views); `tone="default"` on light list surfaces.
export function ScoreBadge({
  score,
  tone = 'default',
}: {
  score: number;
  tone?: 'default' | 'light';
}) {
  const cls =
    tone === 'light'
      ? 'bg-black/55 text-white ring-white/20'
      : 'bg-zinc-100 text-zinc-700 ring-black/5 dark:bg-zinc-800 dark:text-zinc-200 dark:ring-white/10';
  return (
    <span
      title={SCORE_TOOLTIP}
      aria-label={`${SCORE_TOOLTIP}: ${score.toFixed(2)}`}
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-semibold tabular-nums ring-1 ${cls}`}
    >
      <Sparkles className="h-3 w-3 opacity-70" aria-hidden="true" />
      <span className="uppercase tracking-wide opacity-70">Score</span>
      {score.toFixed(2)}
    </span>
  );
}

// Render a mana cost with mana-font (mtg/keyrune). Empty for costless cards (lands).
export function ManaCost({ manaCost }: { manaCost: string }) {
  const symbols = parseManaSymbols(manaCost);
  if (symbols.length === 0) return null;
  return (
    <span
      className="inline-flex items-center gap-0.5"
      aria-label={`Coste de maná ${manaCost}`}
    >
      {symbols.map((symbol, index) => (
        <i
          key={`${symbol}-${index}`}
          className={`ms ms-${symbol} ms-cost`}
          aria-hidden="true"
        />
      ))}
    </span>
  );
}

// Shared card-image renderer with a graceful no-image fallback (the name).
// `showBack` flips to the back face of a double-faced card when it has one.
export function CardImage({
  card,
  className,
  showBack = false,
}: {
  card: ViewCard;
  className?: string;
  showBack?: boolean;
}) {
  const uri =
    showBack && card.image_uri_back_normal
      ? card.image_uri_back_normal
      : card.image_uri_normal;
  if (uri) {
    return (
      <img src={uri} alt={card.name} loading="lazy" className={className} />
    );
  }
  return (
    <div
      className={`flex items-center justify-center bg-zinc-200 p-2 text-center text-xs font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300 ${className ?? ''}`}
    >
      {card.name}
    </div>
  );
}

// Corner control to flip a double-faced card front↔back. A `span[role=button]`,
// not a `<button>`, so it can live inside another clickable card (swap tiles,
// commander picks) without nesting native buttons; it stops propagation so the
// flip never triggers the parent's select/swap.
export function CardFlipButton({
  showBack,
  onToggle,
  className,
}: {
  showBack: boolean;
  onToggle: () => void;
  className?: string;
}) {
  const label = showBack ? 'Ver la cara frontal' : 'Ver la cara trasera';
  return (
    <span
      role="button"
      tabIndex={0}
      aria-label={label}
      aria-pressed={showBack}
      title={label}
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        onToggle();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          onToggle();
        }
      }}
      className={`absolute right-2 top-2 z-10 inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg bg-black/65 text-white ring-1 ring-white/25 backdrop-blur transition hover:bg-black/85 ${
        showBack ? 'accent-text' : ''
      } ${className ?? ''}`}
    >
      <FlipHorizontal2 className="h-4 w-4" aria-hidden="true" />
    </span>
  );
}

// Corner control to open the art/language picker for a card. Same span-not-
// button rationale as CardFlipButton: it lives inside clickable tiles and must
// never bubble into a swap. Sits top-LEFT (the flip control owns top-right).
export function CardArtButton({
  onOpen,
  className,
}: {
  onOpen: () => void;
  className?: string;
}) {
  const label = 'Cambiar la edición / idioma de la carta';
  return (
    <span
      role="button"
      tabIndex={0}
      aria-label={label}
      title={label}
      onClick={(event) => {
        event.stopPropagation();
        event.preventDefault();
        onOpen();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          onOpen();
        }
      }}
      className={`absolute left-2 top-2 z-10 inline-flex h-8 w-8 cursor-pointer items-center justify-center rounded-lg bg-black/65 text-white opacity-0 ring-1 ring-white/25 backdrop-blur transition hover:bg-black/85 focus-visible:opacity-100 group-hover/tile:opacity-100 ${className ?? ''}`}
    >
      <Palette className="h-4 w-4" aria-hidden="true" />
    </span>
  );
}

// EDHREC-style tile: the full card image (5:7) with the score below. Basic lands
// show a ×N badge instead and omit the score — a basic has no EDHREC score (the
// API sends `score: null` for them). `onArtSelect` (deck views only) reveals the
// art-picker corner button on hover.
export function CardTile({
  card,
  onArtSelect,
}: {
  card: ViewCard;
  onArtSelect?: (card: ViewCard) => void;
}) {
  const [showBack, setShowBack] = useState(false);
  const hasBack = Boolean(card.image_uri_back_normal);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="group/tile relative overflow-hidden rounded-xl ring-1 ring-black/10 dark:ring-white/10">
        <CardImage
          card={card}
          showBack={showBack}
          className="aspect-[5/7] w-full object-cover"
        />
        {hasBack && (
          <CardFlipButton
            showBack={showBack}
            onToggle={() => setShowBack((value) => !value)}
          />
        )}
        {onArtSelect && !card.basic && (
          <CardArtButton onOpen={() => onArtSelect(card)} />
        )}
        {card.basic && (
          <span className="absolute bottom-2 right-2 rounded-lg bg-black/80 px-3 py-1.5 text-2xl font-extrabold tabular-nums text-white shadow-lg ring-1 ring-white/25">
            ×{card.count}
          </span>
        )}
      </div>
      {!card.basic && card.score !== null && (
        <div className="flex items-center justify-between gap-2 px-0.5">
          <ScoreBadge score={card.score} />
        </div>
      )}
    </div>
  );
}
