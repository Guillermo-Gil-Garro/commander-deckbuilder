// Spanish labels for the optimizer's functional composition categories, the dials
// that shift them, and the play-style archetypes.
//
// The MTG jargon (ramp, removal, boardwipe) stays in English on purpose: that is
// how the group says it at the table.

import type { DialPosition } from './api';

/** The eight functional categories the quotas are expressed in. */
export const CATEGORY_LABELS: Record<string, string> = {
  lands: 'Tierras',
  ramp: 'Ramp',
  card_draw: 'Robo',
  removal: 'Removal / Interacción',
  board_wipe: 'Boardwipe',
  wincons: 'Wincons',
  protection: 'Protección',
  synergy: 'Sinergia',
};

export function categoryLabel(code: string): string {
  return CATEGORY_LABELS[code] ?? code;
}

export const CATEGORY_HELP: Record<string, string> = {
  lands: 'Fuentes de maná del mazo; el mínimo respeta el suelo de Karsten.',
  ramp: 'Aceleración de maná (rocas, mana dorks, ramp de tierras).',
  card_draw: 'Motores de robo y ventaja de cartas.',
  removal: 'Quitar amenazas puntuales (destruir/exiliar).',
  board_wipe: 'Barridos de mesa (destrucción/daño masivo).',
  wincons: 'Cartas que cierran la partida.',
  protection:
    'Proteger tus permanentes o a ti (hexproof, indestructible, etc.).',
  synergy: 'Cartas afines al comandante que no caen en los roles anteriores.',
};

export function categoryHelp(code: string): string {
  return CATEGORY_HELP[code] ?? '';
}

/** A dial: a bar between Guille's two extremes. The centre is deliberately
 *  unlabelled — it is just "no lo toques", not a third named option. */
export type Dial = {
  category: string;
  low: string;
  high: string;
};

/** The dials, in the order the panel shows them. Mirrors the `dials:` block of
 *  quotas.yaml — only these six categories have a dial (wincons and protection
 *  do not). The low/high copy is Guille's. */
export const DIALS: Dial[] = [
  {
    category: 'lands',
    low: 'Mamá se llevó las tierras, qué caradura...',
    high: '¡MOZÁ! ¡TENGO TIERRAS!',
  },
  {
    category: 'ramp',
    low: 'курва (kurwa para los que no leen cirílico)',
    high: "It's raining lands! Hallelujah! It's raining lands!",
  },
  {
    category: 'card_draw',
    low: 'Topdicker',
    high: 'Piggyhands',
  },
  {
    category: 'removal',
    low: 'Soy pecifista',
    high: '¡Voy a matar a Moe! Weeeee',
  },
  {
    category: 'board_wipe',
    low: 'Mi gente, tamo en japón. ¡Gente con cojone!',
    high: "50.000 people used to live here... now it's a ghost town.",
  },
  {
    category: 'synergy',
    low: '5C goodstuff',
    high: 'Technologia!',
  },
];

/** Slider index <-> dial position. The bar has three stops; the middle one is the
 *  untouched default. */
export const DIAL_POSITIONS: DialPosition[] = ['low', 'center', 'high'];

/** Play-style archetypes (quotas.yaml). Only curated commanders carry a real one
 *  — see `curatedArchetype` in the Setup view. */
export const ARCHETYPE_LABELS: Record<string, string> = {
  aggro: 'Aggro',
  control: 'Control',
  midrange: 'Midrange',
  spellslinger: 'Spellslinger',
  graveyard: 'Cementerio',
  lands_matter: 'Lands matter',
  enchantress: 'Enchantress',
  voltron: 'Voltron',
};

export function archetypeLabel(code: string): string {
  return ARCHETYPE_LABELS[code] ?? code;
}

/** The eight archetypes, ordered for the filter row. */
export const ARCHETYPE_OPTIONS: string[] = [
  'aggro',
  'control',
  'midrange',
  'spellslinger',
  'graveyard',
  'lands_matter',
  'enchantress',
  'voltron',
];
