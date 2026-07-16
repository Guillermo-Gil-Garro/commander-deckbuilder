"""Generate `frontend/src/commanderDescriptions.ts` from `featured_commanders.yaml`.

Why this exists: the picker shows a one-line Spanish description on hover, but
`GET /commanders` does not expose `description` — the field lives only in
`featured_commanders.yaml`. Rather than hand-copy 55 strings into TypeScript
(which would drift silently), this script vendors them as generated code with
the YAML as the single source of truth. Re-run it whenever the YAML changes.

The right long-term fix is for `/commanders` to return `description`; when that
lands, delete this script and the generated module.

Name resolution: the YAML keys double-faced commanders by their front-face name
("Sephiroth, Fabled SOLDIER") while the API returns the full name
("Sephiroth, Fabled SOLDIER // Sephiroth, One-Winged Angel"). The generated map
is keyed by the YAML name; the frontend looks up the full API name first and
falls back to the front face, so both spellings resolve.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
YAML_PATH = REPO_ROOT / "featured_commanders.yaml"
OUT_PATH = REPO_ROOT / "frontend" / "src" / "commanderDescriptions.ts"

HEADER = """\
// GENERATED FILE — do not edit by hand.
// Source: featured_commanders.yaml
// Regenerate: python scripts/gen_commander_descriptions.py
//
// One-line Spanish descriptions for the curated commanders, shown on hover in
// the Setup picker. This module exists only because `GET /commanders` does not
// expose `description`; when the API returns it, delete this file, its
// generator, and read the field from the API instead.
//
// Keys are the names as written in the YAML: double-faced commanders appear
// under their FRONT-FACE name, while the API returns "front // back". Use
// `commanderDescription()` — it resolves both.

export const COMMANDER_DESCRIPTIONS: Record<string, string> = """


FOOTER = """;

/** Description for a commander, or `null` when it is not one of the curated ones. */
export function commanderDescription(name: string): string | null {
  const exact = COMMANDER_DESCRIPTIONS[name];
  if (exact !== undefined) return exact;
  // Double-faced cards: the API's "front // back" is keyed by its front face.
  const frontFace = name.split(' // ')[0];
  return COMMANDER_DESCRIPTIONS[frontFace] ?? null;
}
"""


def main() -> None:
    featured = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))["featured"]
    descriptions: dict[str, str] = {}
    missing: list[str] = []
    for entry in featured:
        name = entry["name"]
        description = entry.get("description")
        if not description:
            missing.append(name)
            continue
        descriptions[name] = description

    if missing:
        raise SystemExit(
            f"{len(missing)} featured commanders have no description: {missing}"
        )

    body = json.dumps(descriptions, indent=2, ensure_ascii=False, sort_keys=True)
    OUT_PATH.write_text(HEADER + body + FOOTER, encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)} with {len(descriptions)} entries")


if __name__ == "__main__":
    main()
