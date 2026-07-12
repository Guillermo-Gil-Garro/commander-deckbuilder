"""Deck quotas: Karsten mana-base math plus the archetype quota system.

Karsten axes ported from the author's TFM optimizer (``lands`` land-floor
regression and ``color_sources`` hypergeometric axis) — pure, deterministic,
stdlib-only. The quota system layers on top: ``config`` (quotas.yaml models
and loader), ``resolver`` (defaults -> archetype -> commander overrides ->
dials) and ``validator`` (per-category status with the unbreachable Karsten
land floor). No EDHREC average-deck logic and no solver here.
"""
