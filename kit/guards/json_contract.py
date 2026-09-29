"""The `--json` contract, machine-checked on a harness's own read verbs.

  * one document: a verb's `--json` stdout is exactly one JSON document,
    and never a traceback on stderr;
  * `meta`: a compute's document (its script matches `[layers].compute`)
    carries the one top-level `meta` kit.contract.meta() builds: every
    required key, window {start, end, days_requested, days_found} with
    ISO days, sources [{table, pulled_on}] (pulled_on one UTC format
    `YYYY-MM-DDTHH:MM:SSZ`), stale [{table, last_date, lag_days,
    max_lag_days}] past their lag, coverage {missing_days in the window,
    next commands}, assumed_thresholds {} unless the run assumed some, the
    harness {name, version, kit_version};
  * every English message coded: a message key `k` (the prose keys
    `[contract].prose`, and any key the document itself codes somewhere)
    carries its `k_code` sibling; a message list `ks` (`[contract].
    prose_lists`) its `k_codes`, same length; each code registered with
    exactly its params, nested messages too. A snake_case machine value
    needs no code; `[contract].data_labels` paths (`.rows[].label`) are
    data, not messages, and so is the reason a person typed that the kit's
    own read verbs list (KIT_DATA_LABELS: history rows, queue items);
  * a failure is one {error, next, code, params[, subject]} document, exit
    non-zero, coded (never unclassified_error), `next` a list of commands;
  * read verbs never create the DB: on a data dir without one, each read
    verb fails `no_db` and leaves the data dir as it was.

Deviations (the reference): message keys are the configured prose keys
plus every key the document itself codes somewhere, so one coded row
makes the same key a message in every row; a verb run through the CLI
may print the dispatcher's `+ cmd` trace on stderr, so only a traceback
there is refused (the reference, running scripts directly, required an
empty stderr).

`check_read_verbs(data_dir)` runs every read verb of the verb table with
`--json` on a fixture data dir through the harness CLI
(`<scripts_dir>/<cli>.py <words> -- --json [--market M]`, under
kit.testing.sandbox's env) and applies the rules above.

Test: kit/tests/test_guards.py.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

from kit import contract, messages
from kit.config import HarnessConfig
from kit.guards import harness, report, section, strs, verb_table

PROSE = ("reason", "note", "explanation", "message")
PROSE_LISTS = ("warnings", "reasons", "notes")
# the kit's own read verbs list the reason a person typed (history rows,
# queue items): data, not a message; always on top of [contract].data_labels
KIT_DATA_LABELS = (".history[].reason", ".items[].reason")
CODE = re.compile(r"^[a-z][a-z0-9_]*$")
UTC_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
FAILURE_KEYS = frozenset({"error", "next", "code", "params"})
TRACEBACK = "Traceback (most recent call last)"

Run = Callable[[list[str]], tuple[int, str, str]]


def code_ok(c: Any, registry: dict | None = None) -> bool:
    """A {code, params} object: a registered code, exactly its registered
    params, every nested message param valid too."""
    reg = messages.registry() if registry is None else registry
    if not (isinstance(c, dict) and set(c) == {"code", "params"}
            and c["code"] in reg and isinstance(c["params"], dict)
            and set(c["params"]) == set(reg[c["code"]]["params"])):
        return False
    return all(_nested_ok(v, reg) for v in c["params"].values())


def _nested_ok(v: Any, reg: dict) -> bool:
    if isinstance(v, dict) and set(v) == {"code", "params"}:
        return code_ok(v, reg)
    if isinstance(v, list):
        return all(_nested_ok(x, reg) for x in v)
    return True


def _coded_keys(node: Any, out: set[str]) -> set[str]:
    """Every key `k` the document codes somewhere (has a `k_code`)."""
    if isinstance(node, list):
        for x in node:
            _coded_keys(x, out)
    elif isinstance(node, dict):
        for k, v in node.items():
            if k.endswith("_code") and k[:-5] in node:
                out.add(k[:-5])
            if not k.endswith(("_code", "_codes")):
                _coded_keys(v, out)
    return out


def uncoded(doc: Any, *, prose: tuple[str, ...] | None = None,
            prose_lists: tuple[str, ...] | None = None,
            data_labels: tuple[str, ...] | None = None,
            registry: dict | None = None,
            cfg: HarnessConfig | None = None) -> list[str]:
    """The path of every message in a --json document without a valid code
    beside it (empty = the document keeps the contract). Defaults: the
    bound harness's [contract] prose / prose_lists / data_labels."""
    c = section(cfg or harness(), "contract")
    prose = tuple(prose if prose is not None else strs(c.get("prose"), PROSE))
    prose_lists = tuple(prose_lists if prose_lists is not None
                        else strs(c.get("prose_lists"), PROSE_LISTS))
    data_labels = KIT_DATA_LABELS + tuple(
        data_labels if data_labels is not None else strs(c.get("data_labels")))
    reg = messages.registry() if registry is None else registry
    keys = tuple(dict.fromkeys(prose + tuple(sorted(_coded_keys(doc, set())))))
    bad: list[str] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, list):
            for i, x in enumerate(node):
                walk(x, f"{where}[{i}]")
            return
        if not isinstance(node, dict):
            return
        failure = where == "" and FAILURE_KEYS <= set(node)
        for k in keys + prose_lists:
            if k not in node or re.sub(r"\[\d+\]", "[]", f"{where}.{k}") \
                    in data_labels or (failure and k == "error"):
                continue
            v = node[k]
            if k in prose_lists and isinstance(v, list):
                cs = node.get(f"{k.removesuffix('s')}_codes")
                if not (isinstance(cs, list) and len(cs) == len(v)
                        and all(code_ok(x, reg) for x in cs)):
                    bad.append(f"{where}.{k}")
            elif not (isinstance(v, str) and CODE.match(v)):
                cv = node.get(f"{k}_code", "absent")
                if not (cv is None if v is None else code_ok(cv, reg)):
                    bad.append(f"{where}.{k}")
        for k, v in node.items():
            if k.endswith("_code") and v is not None and not code_ok(v, reg) \
                    and f"{where}.{k[:-5]}" not in bad:
                bad.append(f"{where}.{k}")
            elif k.endswith("_codes") and not (
                    isinstance(v, list) and all(code_ok(x, reg) for x in v)):
                if f"{where}.{k[:-6]}s" not in bad:
                    bad.append(f"{where}.{k}")
            elif not k.endswith(("_code", "_codes")) and not (
                    failure and k == "params"):
                walk(v, f"{where}.{k}")

    walk(doc, "")
    if isinstance(doc, dict) and FAILURE_KEYS <= set(doc) and not code_ok(
            {"code": doc.get("code"), "params": doc.get("params")}, reg):
        bad.append(".code")
    return bad


def _iso(x: Any) -> bool:
    try:
        return date.fromisoformat(x).isoformat() == x
    except (TypeError, ValueError):
        return False


def check_meta(doc: Any, *, assumed: bool = False,
               cfg: HarnessConfig | None = None) -> list[str]:
    """Problems of a compute document's top-level `meta` (the shape
    kit.contract.meta() builds). `assumed`: the run passed --assume, so
    assumed_thresholds must be non-empty (else it must be {})."""
    cfg = cfg or harness()
    m = doc.get("meta") if isinstance(doc, dict) else None
    if not isinstance(m, dict):
        return ["no top-level meta object"]
    out = []
    missing = [k for k in contract.REQUIRED_KEYS if k not in m]
    if missing:
        out.append(f"meta lacks {missing}")
    w = m.get("window")
    span = None
    if w is not None:
        if not (isinstance(w, dict) and set(contract.WINDOW_KEYS) <= set(w)
                and _iso(w.get("start")) and _iso(w.get("end"))
                and w["start"] <= w["end"]
                and isinstance(w.get("days_requested"), int)
                and isinstance(w.get("days_found"), int)
                and 0 <= w["days_found"] <= w["days_requested"]):
            out.append(f"meta.window is not {{start, end, days_requested, "
                       f"days_found}} with ISO days: {w!r}")
        else:
            span = (date.fromisoformat(w["end"])
                    - date.fromisoformat(w["start"])).days + 1
            if w["days_requested"] != span:
                out.append(f"meta.window.days_requested {w['days_requested']}"
                           f" is not the window's {span} days")
    src = m.get("sources")
    if not (isinstance(src, list) and all(
            isinstance(s, dict) and set(contract.SOURCE_KEYS) <= set(s)
            and isinstance(s["table"], str) and s["table"]
            and (s["pulled_on"] is None or (isinstance(s["pulled_on"], str)
                                            and UTC_TS.match(s["pulled_on"])))
            for s in src)):
        out.append(f"meta.sources is not [{{table, pulled_on: UTC "
                   f"YYYY-MM-DDTHH:MM:SSZ | null}}]: {src!r}")
    stale = m.get("stale")
    if not (isinstance(stale, list) and all(
            isinstance(x, dict) and set(contract.STALE_KEYS) <= set(x)
            and isinstance(x["lag_days"], (int, float))
            and isinstance(x["max_lag_days"], (int, float))
            and x["lag_days"] > x["max_lag_days"] for x in stale)):
        out.append(f"meta.stale is not a list of tables past their lag: "
                   f"{stale!r}")
    cov = m.get("coverage")
    if cov is not None:
        miss = cov.get("missing_days") if isinstance(cov, dict) else None
        nxt = cov.get("next") if isinstance(cov, dict) else None
        if not (isinstance(miss, list) and all(_iso(d) for d in miss)
                and miss == sorted(miss)):
            out.append(f"meta.coverage.missing_days is not a sorted list of "
                       f"ISO days: {miss!r}")
        elif isinstance(w, dict) and span is not None and not (
                all(w["start"] <= d <= w["end"] for d in miss)
                and w["days_found"] + len(miss) == span):
            out.append(f"meta.coverage.missing_days does not match the "
                       f"window ({w['days_found']} found + {len(miss)} "
                       f"missing != {span} days)")
        if not (isinstance(nxt, list)
                and all(isinstance(x, str) and x for x in nxt)):
            out.append(f"meta.coverage.next is not a list of commands: "
                       f"{nxt!r}")
    for k in ("thresholds_overridden", "assumed_thresholds"):
        if not isinstance(m.get(k), dict):
            out.append(f"meta.{k} is not an object: {m.get(k)!r}")
    if isinstance(m.get("assumed_thresholds"), dict) and bool(
            m["assumed_thresholds"]) != assumed:
        out.append("meta.assumed_thresholds is not {} on a run without "
                   "--assume" if not assumed else
                   "meta.assumed_thresholds is empty on a run with --assume")
    if m.get("evidence_level") is not None and not isinstance(
            m.get("evidence_level"), str):
        out.append(f"meta.evidence_level is not a string or null: "
                   f"{m.get('evidence_level')!r}")
    h = m.get("harness")
    if not (isinstance(h, dict)
            and {"name", "version", "kit_version"} <= set(h)
            and h["name"] == cfg.name):
        out.append(f"meta.harness is not {{name: {cfg.name!r}, version, "
                   f"kit_version}}: {h!r}")
    return out


def one_doc(out: str) -> tuple[Any, list[str]]:
    """(the one JSON document on stdout, problems)."""
    try:
        return json.loads(out), []
    except ValueError as e:
        return None, [f"stdout is not exactly one JSON document ({e}): "
                      f"{out[:300]!r}"]


def check_failure(rc: int, out: str, err: str, *,
                  registry: dict | None = None,
                  codes: set[str] | None = None) -> list[str]:
    """Problems of a failed --json run: exit non-zero, one {error, next,
    code, params[, subject]} document, coded (never unclassified_error;
    one of `codes` when given), no traceback."""
    reg = messages.registry() if registry is None else registry
    probs = [] if rc != 0 else ["exit 0 on a failure"]
    doc, bad = one_doc(out)
    probs += bad
    if TRACEBACK in err:
        probs.append(f"a traceback on stderr: {err[-300:]!r}")
    if doc is None:
        return probs
    if not (isinstance(doc, dict)
            and FAILURE_KEYS <= set(doc) <= FAILURE_KEYS | {"subject"}):
        return probs + [f"not {{error, next, code, params[, subject]}}: "
                        f"{sorted(doc) if isinstance(doc, dict) else doc!r}"]
    if not (isinstance(doc["error"], str) and doc["error"].strip()):
        probs.append(f"error is not a sentence: {doc['error']!r}")
    if not (isinstance(doc["next"], list)
            and all(isinstance(c, str) and c for c in doc["next"])):
        probs.append(f"next is not a list of commands: {doc['next']!r}")
    if not code_ok({"code": doc["code"], "params": doc["params"]}, reg):
        probs.append(f"code {doc['code']!r} with params {doc['params']!r} is "
                     f"not a registered code with exactly its params")
    elif doc["code"] == messages.UNCLASSIFIED:
        probs.append(f"unclassified_error: give this failure its own code "
                     f"({doc['error'][:200]!r})")
    if codes is not None and doc["code"] not in codes:
        probs.append(f"code {doc['code']!r} is not one of {sorted(codes)}")
    return probs


def is_compute(cfg: HarnessConfig, verb: Any) -> bool:
    """The verb's script is a compute ([layers].compute, default compute_*)."""
    stem = Path(str(verb.script)).stem
    return any(fnmatch(stem, p)
               for p in strs(cfg.layers.get("compute"), ("compute_*",)))


def verb_argv(words: list[str], rest: list[str], flags: list[str]
              ) -> list[str]:
    """argv after the CLI word: the verb's words, `--` (the dispatcher strips
    the first literal `--` and forwards the rest verbatim) unless `rest`
    already has one, then `rest` and `flags`."""
    return [*words, *([] if "--" in rest else ["--"]), *rest, *flags]


def cli_runner(data_dir: Path | str, cfg: HarnessConfig | None = None,
               **env: str | None) -> Run:
    """run(argv) -> (rc, out, err) of `<scripts_dir>/<cli>.py argv` under
    kit.testing.sandbox's env for `data_dir` (+ `env`)."""
    from kit.testing import sandbox
    cfg = cfg or harness()
    cli = cfg.root / cfg.scripts_dir / f"{cfg.cli}.py"
    base = sandbox.sandbox_env(data_dir, **env)

    def run(argv: list[str]) -> tuple[int, str, str]:
        return sandbox.run([sys.executable, str(cli), *argv], base)
    return run


def _reads(cfg: HarnessConfig, verbs: Any) -> list:
    return [v for v in verb_table(cfg, verbs) if v.kind == "read"]


def _flags(cfg: HarnessConfig, v: Any, market: str | None) -> list[str]:
    flags = ["--json"]
    if market and getattr(v, "takes_market", True) and cfg.markets:
        flags += ["--market", market]
    return flags


def check_read_verbs(data_dir: Path | str, *, verbs: Any = None,
                     run: Run | None = None, market: str | None = None,
                     skip: tuple[str, ...] = (),
                     registry: dict | None = None) -> list[str]:
    """Every read verb run with --json on the fixture `data_dir`: one
    document; a success has every message coded (and a compute its meta);
    a failure is the one coded failure document. `skip` = verbs (as typed)
    not to run."""
    cfg = harness()
    run = run or cli_runner(data_dir, cfg)
    out = []
    for v in _reads(cfg, verbs):
        label = " ".join(v.words)
        if label in skip:
            continue
        rc, stdout, err = run(verb_argv(list(v.words), [],
                                        _flags(cfg, v, market)))
        if rc != 0:
            out += [f"{label}: {p}" for p in check_failure(
                rc, stdout, err, registry=registry)]
            continue
        doc, bad = one_doc(stdout)
        out += [f"{label}: {p}" for p in bad]
        if TRACEBACK in err:
            out.append(f"{label}: a traceback on stderr")
        if doc is None:
            continue
        out += [f"{label}: uncoded message at {p}"
                for p in uncoded(doc, registry=registry, cfg=cfg)]
        if is_compute(cfg, v):
            out += [f"{label}: {p}" for p in check_meta(doc, cfg=cfg)]
    return out


def _listing(d: Path) -> list[str]:
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*"))


def check_read_verbs_no_db(data_dir: Path | str, *, verbs: Any = None,
                           run: Run | None = None, market: str | None = None,
                           allow_ok: tuple[str, ...] = (),
                           registry: dict | None = None) -> list[str]:
    """On a data dir without a DB, every read verb that needs the data dir
    fails `no_db` (one coded failure document) and creates nothing.
    `allow_ok` = verbs (as typed) that may succeed without a DB."""
    cfg = harness()
    d = Path(data_dir)
    run = run or cli_runner(d, cfg)
    out = []
    if (d / cfg.db_file).exists():
        return [f"fixture: {d / cfg.db_file} exists; give an empty data dir"]
    before = _listing(d)
    for v in _reads(cfg, verbs):
        if not getattr(v, "needs_data_dir", True):
            continue
        label = " ".join(v.words)
        rc, stdout, err = run(verb_argv(list(v.words), [],
                                        _flags(cfg, v, market)))
        after = _listing(d)
        if after != before:
            out.append(f"{label}: created {sorted(set(after) - set(before))}"
                       f" in the data dir (a read verb never creates the DB)")
            before = after
        if rc == 0 and label in allow_ok:
            continue
        out += [f"{label}: {p}" for p in check_failure(
            rc, stdout, err, registry=registry, codes={"no_db"})]
    return out


def check_json_contract(data_dir: Path | str, **kw: Any) -> bool:
    """check_read_verbs() as one kit.testing.check line."""
    return report("json contract: every read verb's --json keeps the "
                  "contract", check_read_verbs(data_dir, **kw))
