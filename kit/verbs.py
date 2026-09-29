"""The verb table: every command a harness answers, with what kind of thing
it does. A harness lists its verbs once, in `<scripts_dir>/verbs.py`:

    from kit.verbs import Verb

    VERBS = [
        Verb(("facts", "list"), "facts.py", "read"),
        Verb(("facts", "set"), "facts.py", "human"),
        Verb(("facts", "confirm"), "facts.py", "gated"),
        Verb(("compute", "sales"), "compute_sales.py", "read",
             answers="What sold, per product and day"),
        Verb(("pull", "orders"), "pull_orders.py", "external"),
        Verb(("test",), "../tests/run.py", "dev", False, False),
    ]

That file only builds the list (no I/O, no other import): the dispatcher
(kit.cli), the story checks (kit.stories), the boundary and JSON guards
(kit.guards) and every agent (`<cli> verbs --json`) read the same table.

Kinds, the effect a verb may have (prior art #11: least privilege by
declaration; a verb that is not listed is refused, never guessed):

    read      opens the database read-only, or reads nothing at all; writes
              nothing, calls nothing outside
    human     writes a human table: a pending value, or lowers trust
    gated     passes the human gate (a retype at the terminal, or a relayed
              one-time code) before it writes
    external  calls an external system: a pull is an external read, execute
              an external write
    dev       a developer's tool (the test runner): no client data

What this module guards:

  * a verb is a tuple of plain lower-case words, one script (a path under
    the scripts dir, never absolute, never leaving it except the declared
    `../tests/run.py` form) and one of the five kinds; anything else fails
    when the table loads, not when the verb runs;
  * no verb is listed twice, and no verb takes a built-in word (`doctor`,
    `verbs`, `help`: the dispatcher answers those itself);
  * routing is the longest listed prefix (`match()`); what the script is
    given is fixed by the table alone (`script_args()`): a script that
    serves several verbs (facts.py behind `facts list|set|confirm…`) gets
    the words after the first one (its own sub-verb), a script that serves
    one verb (compute_sales.py) gets none of them.

Addition to the SPEC (§verbs): an optional last field `answers`, the one
line `<cli> --help` and `<cli> verbs --json` show for the verb (default:
the first line of the script's docstring). Every SPEC call form
(`Verb(words, script, kind, takes_market, needs_data_dir)`) is unchanged.

Test: kit/tests/test_cli.py.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

KINDS: dict[str, str] = {
    "read": "reads only: opens the database read-only, writes nothing",
    "human": "writes a human table: a pending value, or lowers trust",
    "gated": "needs a person: a retype at the terminal or a relayed "
             "one-time code",
    "external": "calls an external system (pull = read; execute = write)",
    "dev": "developer tools: no client data",
}
BUILTIN = ("doctor", "verbs", "help")
WORD = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class VerbTableError(ValueError):
    """The verb table is malformed (plain text: it is a developer's bug,
    found when the table loads)."""


@dataclass(frozen=True)
class Verb:
    words: tuple[str, ...]
    script: str
    kind: str
    takes_market: bool = True
    needs_data_dir: bool = True
    answers: str = ""

    def __post_init__(self) -> None:
        words = self.words
        if isinstance(words, str):
            words = tuple(words.split())
        object.__setattr__(self, "words", tuple(words))
        problem = verb_problem(self)
        if problem:
            raise VerbTableError(problem)

    @property
    def command(self) -> str:
        return " ".join(self.words)


def verb_problem(v: Any) -> str | None:
    """What is wrong with one verb (a Verb or any object with its fields),
    or None."""
    words = tuple(getattr(v, "words", ()) or ())
    label = " ".join(map(str, words)) or "(no words)"
    if not words or not all(isinstance(w, str) and WORD.match(w)
                            for w in words):
        return (f"verb {label!r}: words must be plain lower-case words "
                f"(letters, digits, - and _)")
    if words[0] in BUILTIN:
        return (f"verb {label!r}: {words[0]!r} is a built-in word of the "
                f"dispatcher")
    kind = getattr(v, "kind", None)
    if kind not in KINDS:
        return f"verb {label!r}: kind {kind!r} is not one of {', '.join(KINDS)}"
    script = getattr(v, "script", None)
    if not isinstance(script, str) or not script.endswith(".py"):
        return f"verb {label!r}: script must be a .py path, got {script!r}"
    p = Path(script)
    parts = p.parts
    if p.is_absolute() or (".." in parts and parts[:1] != ("..",)) \
            or parts.count("..") > 1:
        return (f"verb {label!r}: script {script!r} must be a path under the "
                f"scripts dir (or ../<dir>/<file>.py)")
    for flag in ("takes_market", "needs_data_dir"):
        if not isinstance(getattr(v, flag, True), bool):
            return f"verb {label!r}: {flag} must be True or False"
    if not isinstance(getattr(v, "answers", ""), str):
        return f"verb {label!r}: answers must be a string"
    return None


def check(verbs: Iterable[Any]) -> list[Any]:
    """The table as a list, or VerbTableError for its first problem."""
    table = list(verbs)
    seen: set[tuple[str, ...]] = set()
    for v in table:
        problem = verb_problem(v)
        if problem:
            raise VerbTableError(problem)
        w = tuple(v.words)
        if w in seen:
            raise VerbTableError(f"verb {' '.join(w)!r} is listed twice")
        seen.add(w)
    if not table:
        raise VerbTableError("the verb table is empty")
    return table


def default_path() -> Path:
    """`<root>/<scripts_dir>/verbs.py` of the bound harness."""
    from kit.config import config
    cfg = config()
    return cfg.root / cfg.scripts_dir / "verbs.py"


def load(path: Path | str | None = None) -> list[Verb]:
    """VERBS of a harness's verbs.py (default: the bound harness's),
    checked. The file is executed as a module of its own name; it must
    only build the list."""
    path = Path(path) if path is not None else default_path()
    if not path.is_file():
        raise VerbTableError(f"no verb table at {path}: list the verbs in "
                             f"VERBS = [...] there")
    name = f"_kit_verbs_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise VerbTableError(f"{path}: not a Python file")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(name, None)
    table = getattr(mod, "VERBS", None)
    if not isinstance(table, (list, tuple)):
        raise VerbTableError(f"{path}: defines no VERBS list")
    return check(table)


def of_kind(verbs: Iterable[Any], *kinds: str) -> list[Any]:
    """The verbs of the given kinds, in table order."""
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise VerbTableError(f"unknown kind(s): {', '.join(unknown)}")
    return [v for v in verbs if v.kind in kinds]


def targets(verbs: Iterable[Any]) -> dict[tuple[str, ...], str]:
    """{words: script}, the routing table."""
    return {tuple(v.words): v.script for v in verbs}


def match(args: list[str], verbs: Iterable[Any]) -> Any:
    """The verb whose words are the longest prefix of `args`, else None."""
    best = None
    for v in verbs:
        w = tuple(v.words)
        if tuple(args[:len(w)]) == w and (best is None
                                          or len(w) > len(best.words)):
            best = v
    return best


def group(args: list[str], verbs: Iterable[Any]) -> list[Any]:
    """The verbs that start with the plain words `args` begins with (for a
    group's help, or a refusal that names the candidates)."""
    head: list[str] = []
    for a in args:
        if a.startswith("-"):
            break
        head.append(a)
    for n in range(len(head), 0, -1):
        hits = [v for v in verbs if tuple(v.words[:n]) == tuple(head[:n])]
        if hits:
            return hits
    return []


def shared(v: Any, verbs: Iterable[Any]) -> bool:
    """True when the verb's script serves more than one verb."""
    return sum(1 for x in verbs if x.script == v.script) > 1


def script_args(v: Any, verbs: Iterable[Any]) -> list[str]:
    """The verb's own words the script is given: the words after the first
    for a script that serves several verbs (its sub-verb), none otherwise."""
    table = list(verbs)
    return list(v.words[1:]) if shared(v, table) else []


def answers(v: Any, scripts_dir: Path | str | None = None) -> str:
    """The verb's one-line description: its `answers`, else the first line
    of its script's docstring (read with ast, never imported), else ''."""
    text = getattr(v, "answers", "") or ""
    if text or scripts_dir is None:
        return text.strip()
    try:
        src = (Path(scripts_dir) / v.script).read_text(encoding="utf-8")
        doc = ast.get_docstring(ast.parse(src)) or ""
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return ""
    return doc.strip().split("\n", 1)[0].strip() if doc.strip() else ""


def as_dict(v: Any, cli: str, scripts_dir: Path | str | None = None) -> dict:
    return {"command": f"{cli} {' '.join(v.words)}", "words": list(v.words),
            "kind": v.kind, "script": v.script,
            "takes_market": bool(getattr(v, "takes_market", True)),
            "needs_data_dir": bool(getattr(v, "needs_data_dir", True)),
            "answers": answers(v, scripts_dir)}
