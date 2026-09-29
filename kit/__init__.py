"""The harness kit: the generic half of an agent harness (stdlib only,
Python >= 3.11). A harness binds it once with `harness.toml` (kit.config).

Importing `kit` loads nothing but the version, so a puller can import
`kit.dates` or `kit.atomic` without a harness config, a DB or a registry.
Guarded by kit/tests/test_config.py (the version matches kit/VERSION).
"""

from __future__ import annotations

from pathlib import Path

KIT_DIR = Path(__file__).resolve().parent
__version__ = (KIT_DIR / "VERSION").read_text(encoding="utf-8").strip()
