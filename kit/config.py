"""The one place a harness binds the kit: `<harness root>/harness.toml`.

Every kit module that needs a name (an env var, the CLI word, a registry
path, the DB file, the market set) asks `config()`; nothing in the kit
hard-codes one. What this module guards:

  * a harness declares itself once, and a malformed declaration fails at
    load with the key that is wrong (not later as a KeyError in a verb);
  * env var names are always `<env_prefix>_<NAME>` (`cfg.env("DATA_DIR")`);
  * which harness is bound: `KIT_HARNESS_ROOT` when set, else the first
    directory at or above the kit (a vendored copy lives at
    `<harness>/scripts/kit/`) that holds harness.toml; never the cwd;
  * `use(root)` (tests) switches the bound harness AND clears every kit
    cache that depends on it: a module registers its clear function with
    `on_reset(fn)`, so a stale registry or env chain never survives a
    switch.

Errors here are plain `ConfigError`s: the message registry itself depends
on the config, so nothing can be coded before it loads.

Test: kit/tests/test_config.py.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FILE = "harness.toml"
ROOT_ENV = "KIT_HARNESS_ROOT"
_PREFIX = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
_WORD = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(RuntimeError):
    """harness.toml is missing or malformed (plain text: see the module
    docstring for why this one error is not a coded message)."""


@dataclass(frozen=True)
class HarnessConfig:
    root: Path
    name: str
    cli: str
    env_prefix: str
    db_file: str
    scripts_dir: str
    languages: tuple[str, ...]
    markets: tuple[str, ...]
    home_env_file: str
    ssot: dict[str, str]
    release: dict
    layers: dict
    raw: dict          # the whole parsed harness.toml, for guards and hooks

    def __hash__(self) -> int:     # the dict fields are not hashable
        return hash((self.root, self.name, self.cli, self.env_prefix))

    def env(self, name: str) -> str:
        """The harness's env var for `name`: env("DATA_DIR") -> "SHOP_DATA_DIR"."""
        return f"{self.env_prefix}_{name}"

    def ssot_path(self, key: str) -> Path | None:
        """<root>/<[ssot].key>, None when the harness declares no such key."""
        rel = self.ssot.get(key)
        return self.root / rel if rel else None


def _str(table: dict, key: str, where: str, *, default: str | None = None,
         pattern: re.Pattern | None = None) -> str:
    v = table.get(key, default)
    if v is None:
        raise ConfigError(f"{where}: [harness].{key} is required")
    if not isinstance(v, str):
        raise ConfigError(f"{where}: [harness].{key} must be a string, "
                          f"got {v!r}")
    if pattern is not None and not pattern.match(v):
        raise ConfigError(f"{where}: [harness].{key} = {v!r} is not valid "
                          f"(expected {pattern.pattern})")
    return v


def _strs(table: dict, key: str, where: str, default: list[str]
          ) -> tuple[str, ...]:
    v = table.get(key, default)
    if not (isinstance(v, list) and all(isinstance(x, str) and x.strip()
                                        for x in v)):
        raise ConfigError(f"{where}: [harness].{key} must be a list of "
                          f"non-empty strings, got {v!r}")
    if len(set(v)) != len(v):
        raise ConfigError(f"{where}: [harness].{key} lists a value twice: "
                          f"{v!r}")
    return tuple(v)


def _table(raw: dict, key: str, where: str) -> dict:
    v = raw.get(key, {})
    if not isinstance(v, dict):
        raise ConfigError(f"{where}: [{key}] must be a table")
    return v


def load(root: Path | str) -> HarnessConfig:
    """Parse `<root>/harness.toml` into a HarnessConfig (no caching)."""
    root = Path(root).expanduser().resolve()
    path = root / FILE
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"no {FILE} in {root}") from None
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from None
    where = str(path)
    h = _table(raw, "harness", where)
    if not h:
        raise ConfigError(f"{where}: the [harness] table is required")
    name = _str(h, "name", where, pattern=_WORD)
    cli = _str(h, "cli", where, pattern=_WORD)
    prefix = _str(h, "env_prefix", where, pattern=_PREFIX)
    db_file = _str(h, "db_file", where, default=f"{cli}.db")
    if not db_file or "/" in db_file or db_file.startswith("."):
        raise ConfigError(f"{where}: [harness].db_file must be a plain file "
                          f"name, got {db_file!r}")
    languages = _strs(h, "languages", where, ["en", "zh"])
    if not languages:
        raise ConfigError(f"{where}: [harness].languages must name at least "
                          f"one language")
    ssot = _table(raw, "ssot", where)
    if not all(isinstance(v, str) for v in ssot.values()):
        raise ConfigError(f"{where}: every [ssot] value must be a path string")
    return HarnessConfig(
        root=root, name=name, cli=cli, env_prefix=prefix, db_file=db_file,
        scripts_dir=_str(h, "scripts_dir", where, default="scripts"),
        languages=languages,
        markets=_strs(h, "markets", where, []),
        home_env_file=_str(h, "home_env_file", where, default=""),
        ssot={"dir": "ssot", **ssot},
        release=_table(raw, "release", where),
        layers=_table(raw, "layers", where),
        raw=raw)


def find_root(start: Path | str) -> Path | None:
    """The first directory at or above `start` that holds harness.toml."""
    p = Path(start).resolve()
    for d in (p, *p.parents):
        if (d / FILE).is_file():
            return d
    return None


_current: HarnessConfig | None = None
_resets: list[Callable[[], None]] = []


def on_reset(fn: Callable[[], None]) -> Callable[[], None]:
    """Register a cache-clear function run whenever the bound harness
    changes (`use`, `reset`). Usable as a decorator; returns `fn`."""
    _resets.append(fn)
    return fn


def reset() -> None:
    """Forget the bound harness and clear every registered kit cache."""
    global _current
    _current = None
    for fn in list(_resets):
        fn()


def config() -> HarnessConfig:
    """The bound harness (cached): KIT_HARNESS_ROOT when set, else the
    first harness.toml at or above the kit's own directory."""
    global _current
    if _current is None:
        env_root = os.environ.get(ROOT_ENV)
        if env_root:
            root = Path(env_root)
        else:
            root = find_root(Path(__file__).resolve().parent)
            if root is None:
                raise ConfigError(
                    f"no {FILE} at or above {Path(__file__).resolve().parent}"
                    f" and {ROOT_ENV} is not set: the kit is not bound to a "
                    f"harness")
        _current = load(root)
    return _current


def use(root: Path | str) -> HarnessConfig:
    """Tests: bind the harness at `root`, clear every kit cache that depends
    on the old one, and export KIT_HARNESS_ROOT so a child process a test
    starts binds the same harness."""
    global _current
    cfg = load(root)
    reset()
    _current = cfg
    os.environ[ROOT_ENV] = str(cfg.root)
    return cfg
