"""The day-one guards of a harness, as library calls: the nine tests a new
harness is generated with (templates/README.md, "The tests a new harness
starts with") are each one thin call into this module.

Every function takes the harness root and returns [(label, problems)]:
one check per rule, `problems` empty when the rule holds. `run(results)`
reports them through kit.testing.check. A generated test is

    from kit.testing import suites
    suites.run(suites.gate(ROOT))
    raise SystemExit(finish())

  run_tests     the runner fails a file that exits non-zero, prints no
                RESULT line, ran nothing, reports a failure with exit 0 or
                leaves files in its temp folder; the harness's tests/run.py
                is that runner
  ssot          kit.guards.ssot.check_index, plus the trail: every owner row
                has an agent sibling that is accepted or retired with a
                `decided` answer; asked needs ask; a stage is never signed
                before a step it comes after
  layering      kit.guards.layering.check_layers, and its planted self-test
  boundary      kit.guards.boundary.check_boundaries
  json_contract every read verb on a fixture (facts init, the declaration
                confirmed through the gate) keeps the --json contract; on an
                empty data dir every read verb fails no_db (or a coded usage
                error) and creates nothing
  human_tables  the harness's SPEC holds the kit's human tables unchanged;
                their rows survive a rebuild; a lossy rebuild, a newer file,
                a drifted human table and a DELETE are refused
  gate          confirm and approve need a person (no terminal, no secret:
                refused); a code is bound to exactly what was shown (a wrong
                or swapped code is refused; a replay once the row moved is
                refused); a relayed code needs its audit; no bypass flag on
                any gated verb; the scope enters only through init
  release       kit.guards.release.check_release; once the B8 row of
                ssot/stages.agent.tsv is signed (or on a release tag,
                CI_COMMIT_TAG), no shipped file holds a fill marker (the
                build's "<<" + "fill:" placeholder)
  kit_drift     kit.guards.drift.check_harness

Test: kit/tests/test_suites.py (every suite green on a generated harness,
and each one failing on a planted violation).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from kit import config as _config
from kit.testing.check import check, tmp_dir

Results = list[tuple[str, list[str]]]

BYPASS = ("--yes", "-y", "--force", "--no-confirm", "--skip-confirm",
          "--assume-yes", "--bypass", "--no-gate", "--skip-gate", "--auto")
DECIDED = re.compile(r"^(console:[a-z0-9][a-z0-9._-]*@\d+|chat:\S.*|"
                     r"merge:[0-9a-f]{7,40}|ci:\S+|check:\S.*)$")
ROW_STATUS = {"proposed", "asked", "accepted", "dropped", "retired"}
STAGE_STATUS = {"todo", "doing", "signed", "blocked"}
FILL = "<<" + "fill:"      # spelled apart: this file ships too
SECRET = "suites-gate-secret-0123456789"
# the build's planning file (never shipped); spelled apart so the release
# guard does not read this shipped module as opening it
STAGES = "stages.agent" + ".tsv"


def run(results: Results) -> bool:
    """Report each (label, problems) as one kit.testing.check line."""
    ok = True
    for label, problems in results:
        ok = check(label, not problems, "\n".join(problems)) and ok
    return ok


def _cfg(root: Path | str) -> _config.HarnessConfig:
    return bind(root)


def bind(root: Path | str) -> _config.HarnessConfig:
    """Bind the harness at `root` (its registry decides what counts as
    coded) unless it is bound already."""
    root = Path(root).resolve()
    try:
        if _config.config().root.resolve() == root:
            return _config.config()
    except _config.ConfigError:
        pass
    return _config.use(root)


def _tsv(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    head = lines[0].split("\t")
    return [dict(zip(head, ln.split("\t"))) for ln in lines[1:] if ln]


# ---- run_tests ----------------------------------------------------------------

PLANTED = {
    "test_crash.py": "raise SystemExit(3)\n",
    "test_silent.py": "print('no result line')\n",
    "test_nothing.py": "print('RESULT: 0 passed')\n",
    "test_hidden_fail.py": "print('RESULT: 2 passed, 1 failed')\n",
    "test_litter.py": "import os, tempfile\n"
                      "open(os.path.join(tempfile.gettempdir(), 'left'), 'w')"
                      ".write('x')\nprint('RESULT: 1 passed')\n",
    "test_good.py": "print('RESULT: 3 passed')\n",
}


def run_tests(root: Path | str) -> Results:
    bind(root)
    from kit.testing import run_tests as rt
    root = Path(root)
    d = Path(tmp_dir("suite-runner-"))
    (d / "t").mkdir()
    for name, body in PLANTED.items():
        (d / "t" / name).write_text(body, encoding="utf-8")
    r = subprocess.run([sys.executable, "-c",
                        "import sys; from kit.testing import run_tests; "
                        "raise SystemExit(run_tests.main([], root=sys.argv[1],"
                        " tests_dir='t', timeout_s=60))", str(d)],
                       capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PYTHONPATH": str(
                           Path(rt.__file__).resolve().parents[2])})
    out = r.stdout
    lines = {}
    for ln in out.splitlines():
        m = re.match(r"^(ok|FAIL)\s.*?(test_\w+\.py)", ln)
        if m:
            lines[m.group(2)] = m.group(1)
    probs = []
    for name in PLANTED:
        want = "ok" if name == "test_good.py" else "FAIL"
        if lines.get(name) != want:
            probs.append(f"{name}: the runner said {lines.get(name)!r}, "
                         f"expected {want}")
    if r.returncode == 0:
        probs.append("the runner exited 0 with failing files")
    if not re.search(r"^RESULT: \d+ passed, 5 failed\b", out, re.M):
        probs.append(f"no summary line naming 5 failed files: {out[-300:]!r}")
    runner = root / "tests" / "run.py"
    own = [] if runner.is_file() and "run_tests" in runner.read_text(
        encoding="utf-8") else [f"{runner}: missing, or not the kit's runner"]
    return [("the runner fails a file that crashes, prints no RESULT line, "
             "ran nothing, hides a failure or leaves files behind", probs),
            ("tests/run.py is that runner", own)]


# ---- ssot ---------------------------------------------------------------------

def trail_problems(owner: list[dict], sibling: list[dict], key: str
                   ) -> list[str]:
    out = []
    by_id = {r.get(key): r for r in sibling}
    for r in sibling:
        st = r.get("status", "")
        if st not in ROW_STATUS:
            out.append(f"{r.get(key)}: status {st!r}")
        if st == "asked" and not r.get("ask"):
            out.append(f"{r.get(key)}: asked, but no ask")
        if st in ("accepted", "dropped", "retired") and not DECIDED.match(
                r.get("decided", "")):
            out.append(f"{r.get(key)}: {st}, but no decided answer")
    for r in owner:
        s = by_id.get(r.get(key))
        if s is None:
            out.append(f"{r.get(key)}: an owner row with no sibling")
        elif s.get("status") not in ("accepted", "retired"):
            out.append(f"{r.get(key)}: an owner row whose sibling is "
                       f"{s.get('status')}")
    return out


def stage_problems(stages: list[dict]) -> list[str]:
    out, before, signed = [], set(), set()
    for s in stages:
        after = [x for x in re.split(r"[,\s]+", s.get("after", ""))
                 if x and x not in ("—", "-")]
        if s.get("status") not in STAGE_STATUS:
            out.append(f"{s.get('stage')}: status {s.get('status')!r}")
        if not set(after) <= before:
            out.append(f"{s.get('stage')}: after names a later or unknown "
                       f"step")
        if s.get("status") == "signed":
            if not DECIDED.match(s.get("decided", "")):
                out.append(f"{s.get('stage')}: signed with no reference")
            if not set(after) <= signed:
                out.append(f"{s.get('stage')}: signed before a step it comes "
                           f"after")
            signed.add(s.get("stage"))
        if not s.get("signer"):
            out.append(f"{s.get('stage')}: nobody signs it")
        before.add(s.get("stage"))
    return out


def ssot(root: Path | str) -> Results:
    bind(root)
    from kit.guards import ssot as g
    root = Path(root)
    r = g.rules(root)
    _, rows = g.index_rows(r)
    trail = []
    for row in rows:
        f = row["file"]
        if row["owner"] != "owner" or not f.endswith(".tsv"):
            continue
        sib = root / (f.removesuffix(".tsv") + ".agent.tsv")
        if not sib.is_file():
            continue
        key = row["id"].split("+")[0]
        trail += [f"{f}: {p}" for p in trail_problems(
            _tsv(root / f), _tsv(sib), key)]
    stages_file = root / r.ssot_dir / STAGES
    stages = (stage_problems(_tsv(stages_file)) if stages_file.is_file()
              else [])
    return [("the ssot index, owner files and ids keep their rules",
             g.check_index(root)),
            ("every owner row names its decided answer (the trail)", trail),
            ("no stage is signed before a step it comes after", stages)]


# ---- layering, boundary, drift ---------------------------------------------------

def layering(root: Path | str) -> Results:
    bind(root)
    from kit.guards import layering as g
    return [("the layering rules of harness.toml hold", g.check_layers(root)),
            ("each layering rule catches its planted violation",
             g.self_test())]


def boundary(root: Path | str) -> Results:
    bind(root)
    from kit.guards import boundary as g
    return [("the core names no adapter; a consumer runs only read verbs and "
             "relayed gate verbs", g.check_boundaries(root))]


def kit_drift(root: Path | str) -> Results:
    bind(root)
    from kit.guards import drift
    return [("scripts/kit/ and console/ match their MANIFEST.sha256 and "
             "VERSION", drift.check_harness(root))]


# ---- the harness CLI in a sandbox --------------------------------------------------

class Cli:
    """The harness's own CLI under kit.testing.sandbox's env for one data
    dir; `secret` sets the confirm-code secret (relay mode)."""

    def __init__(self, root: Path | str, data: Path | None = None):
        self.root = Path(root)
        self.cfg = _cfg(root)
        self.data = data or Path(tmp_dir("suite-data-"))
        self.cli = self.root / self.cfg.scripts_dir / f"{self.cfg.cli}.py"

    def env(self, secret: bool) -> dict:
        from kit.testing.sandbox import sandbox_env
        env = sandbox_env(self.data, PYTHONDONTWRITEBYTECODE="1")
        env["KIT_HARNESS_ROOT"] = str(self.root)
        if secret:
            env[self.cfg.env("CONFIRM_CODE_SECRET")] = SECRET
        return env

    def __call__(self, *argv: str, stdin: str | None = None,
                 secret: bool = False) -> tuple[int, Any, str]:
        from kit.testing.sandbox import run as sandbox_run
        rc, out, err = sandbox_run([sys.executable, str(self.cli), *argv],
                                   self.env(secret), stdin=stdin, timeout=120)
        try:
            doc = json.loads(out)
        except ValueError:
            doc = None
        return rc, doc, err

    def facts(self, key: str) -> tuple | None:
        path = self.data / self.cfg.db_file
        if not path.exists():
            return None
        with closing(sqlite3.connect(path)) as c:
            m = self.market
            r = c.execute("SELECT value, is_assumption FROM client_facts "
                          "WHERE market = ? AND key = ?", (m, key)).fetchone()
        return r

    def history(self) -> int:
        path = self.data / self.cfg.db_file
        with closing(sqlite3.connect(path)) as c:
            return c.execute("SELECT COUNT(*) FROM client_facts_history"
                             ).fetchone()[0]

    @property
    def market(self) -> str:
        return self.cfg.markets[0] if self.cfg.markets else "_"

    def verbs(self) -> list:
        from kit import verbs as kv
        return kv.load(self.root / self.cfg.scripts_dir / "verbs.py")

    def has(self, *words: str) -> bool:
        return any(tuple(v.words) == words for v in self.verbs())

    def onboard(self, confirm: bool = True) -> list[str]:
        """facts init (piped), then the declaration confirmed through a
        relayed code: the fixture a read verb has something to read in."""
        probs = []
        stdin = (f"{self.market}\n" if self.cfg.markets else "") + "\n" * 20
        rc, doc, err = self("facts", "init", "--json", stdin=stdin)
        if rc != 0:
            return [f"facts init: exit {rc}: {doc or err[-300:]}"]
        if self.cfg.markets and confirm:
            probs += self.confirm_with_code(
                "facts", "confirm", "market_declared", "--reason",
                "declared at onboarding")
        return probs

    def challenge(self, *argv: str) -> tuple[str | None, dict | None]:
        rc, doc, _ = self(*argv, "--json", secret=True)
        if rc == 2 and isinstance(doc, dict) \
                and doc.get("code") == "confirm_code_required":
            return doc["params"]["confirm_code"], doc
        return None, doc

    def confirm_with_code(self, *argv: str) -> list[str]:
        code, doc = self.challenge(*argv)
        if code is None:
            return [f"{' '.join(argv)}: no challenge: {doc}"]
        rc, doc, err = self(*argv, "--json", "--code", code, "--relay-user",
                            "suite:owner", "--relay-at",
                            "2026-01-01T00:00:00Z", secret=True)
        return [] if rc == 0 else [f"{' '.join(argv)} with its code: exit "
                                   f"{rc}: {doc or err[-300:]}"]


def _code_of(doc: Any) -> str | None:
    return doc.get("code") if isinstance(doc, dict) else None


# ---- json contract -----------------------------------------------------------

def json_contract(root: Path | str) -> Results:
    bind(root)
    from kit.guards import json_contract as g
    cli = Cli(root)
    setup = cli.onboard()
    run = (lambda argv: _raw(cli, argv))
    market = cli.market if cli.cfg.markets else None
    reads = g.check_read_verbs(cli.data, run=run, market=market,
                               verbs=cli.verbs())
    empty = Cli(root)
    no_db = g.check_read_verbs_no_db(
        empty.data, run=lambda argv: _raw(empty, argv), verbs=cli.verbs(),
        codes=("no_db", "usage", "fact_usage"))
    return [("the fixture: facts init and the declaration confirmed", setup),
            ("every read verb on the fixture: one document, meta on a "
             "compute, every message coded, a failure one coded document",
             reads),
            ("every read verb without a DB fails no_db and creates nothing",
             no_db)]


def _raw(cli: Cli, argv: list[str]) -> tuple[int, str, str]:
    from kit.testing.sandbox import run as sandbox_run
    return sandbox_run([sys.executable, str(cli.cli), *argv],
                       cli.env(False), timeout=120)


# ---- human tables --------------------------------------------------------------

PROBE = "kit_probe"


def human_tables(root: Path | str, spec: Any) -> Results:
    bind(root)
    from kit import db, schema_base
    from kit.contract import HarnessError
    out: Results = []
    same = [t for t in schema_base.HUMAN
            if t not in spec.human_tables
            or spec.columns(t) != tuple(schema_base.HUMAN[t]["columns"])]
    out.append(("the harness's SPEC holds the kit's human tables, unchanged",
                [f"{t}: missing or changed" for t in same]))
    with contextlib.redirect_stderr(io.StringIO()):   # the db's own notes
        return out + _human_rows(spec, db, schema_base, HarnessError)


def _human_rows(spec: Any, db: Any, schema_base: Any, HarnessError: type
                ) -> Results:
    out: Results = []
    d = Path(tmp_dir("suite-human-"))
    probe = {"columns": {"date": "TEXT", "n": "INTEGER"}, "pk": ["date"]}
    v1 = db.with_human({**_cache(spec), PROBE: probe},
                       version=spec.version,
                       extra_human=_extra(spec, schema_base))
    path = d / "h.db"
    probs: list[str] = []
    with closing(db.connect(v1, path)) as c:
        c.execute("INSERT INTO client_facts (market, key, value, "
                  "is_assumption, source, updated_at, changed_by) VALUES "
                  "('_', 'probe', '1', 1, 'suite', '2026-01-01', 'suite')")
        c.execute(f"INSERT INTO {PROBE} VALUES ('2026-01-01', 1), "
                  f"('2026-01-02', 2)")
        err = _raises(lambda: c.execute("DELETE FROM client_facts"))
        if err is None:
            probs.append("DELETE FROM client_facts went through")
    out.append(("a trigger in the file refuses deleting a human row", probs))
    wider = {**probe, "columns": {**probe["columns"], "k": "TEXT"},
             "not_null": ["k"]}
    v2 = db.with_human({**_cache(spec), PROBE: wider},
                       version=spec.version + 1,
                       extra_human=_extra(spec, schema_base))
    e = _raises(lambda: db.connect(v2, path, rebuild=[PROBE],
                                   raw_periods={PROBE: {"2026-01-01"}}))
    out.append(("a rebuild raw cannot refill is refused (LossyRebuild)",
                [] if isinstance(e, db.LossyRebuild) else [f"got {e!r}"]))
    probs = []
    try:
        with closing(db.connect(v2, path, rebuild=[PROBE], raw_periods={
                PROBE: {"2026-01-01", "2026-01-02"}})) as c:
            n = c.execute("SELECT COUNT(*) FROM client_facts").fetchone()[0]
            if n != 1:
                probs.append(f"client_facts has {n} rows after the rebuild")
        if not list((d / "backups").glob("human-*.db")):
            probs.append("no human-table backup was written before the "
                         "rebuild")
    except Exception as e:           # noqa: BLE001 - reported as a problem
        probs.append(f"the rebuild failed: {e!r}")
    out.append(("human rows survive a rebuild, backed up first", probs))
    e = _raises(lambda: db.connect(v1, path, read_only=True))
    out.append(("an older tool refuses a newer database (schema_too_new)",
                [] if isinstance(e, db.SchemaTooNew) else [f"got {e!r}"]))
    drift = {t: dict(v) for t, v in schema_base.HUMAN.items()}
    drift["client_facts"] = {**drift["client_facts"], "columns": {
        **drift["client_facts"]["columns"], "extra": "TEXT"}}
    tables = {**v2.tables, "client_facts": drift["client_facts"]}
    e = _raises(lambda: db.connect(db.SchemaSpec(
        tables=tables, human_tables=v2.human_tables, version=v2.version,
        append_only=v2.append_only, frozen_columns=v2.frozen_columns),
        path))
    out.append(("a human table in another shape is refused, never migrated",
                [] if isinstance(e, HarnessError)
                and getattr(e.message, "code", "") == "human_table_drift"
                else [f"got {e!r}"]))
    return out


def _cache(spec: Any) -> dict:
    return {t: spec.tables[t] for t in spec.cache_tables()}


def _extra(spec: Any, schema_base: Any) -> dict:
    return {t: spec.tables[t] for t in spec.human_tables
            if t not in schema_base.HUMAN}


def _raises(fn) -> BaseException | None:
    try:
        fn()
    except BaseException as e:      # noqa: BLE001 - the caller judges it
        return e
    return None


# ---- the gate -----------------------------------------------------------------------

def gate(root: Path | str) -> Results:
    bind(root)
    cli = Cli(root)
    out: Results = []
    key = "threshold_approval_ttl_hours"

    probs = []
    if cli.cfg.markets:
        rc, doc, _ = cli("facts", "set", key, "12", "--source", "suite",
                         "--reason", "before init", "--json")
        if rc != 2 or not str(_code_of(doc)).startswith("market_"):
            probs.append(f"facts set before init: exit {rc}, {doc}")
    probs += cli.onboard(confirm=False)
    if cli.cfg.markets and cli.facts("market_declared") != (cli.market, 1):
        probs.append(f"init did not leave the declaration pending: "
                     f"{cli.facts('market_declared')}")
    out.append(("the scope enters only through init, pending", probs))

    probs = []
    if cli.cfg.markets:
        argv = ("facts", "confirm", "market_declared", "--reason", "r")
        rc, doc, _ = cli(*argv, "--json")
        if rc != 2 or _code_of(doc) != "confirm_needs_human":
            probs.append(f"no terminal, no secret: exit {rc}, {doc}")
        code, _ = cli.challenge(*argv)
        if code is None:
            probs.append("with the secret: no challenge")
        else:
            wrong = f"{(int(code) + 1) % 1000000:06d}"
            rc, doc, _ = cli(*argv, "--json", "--code", wrong, "--relay-user",
                             "u", "--relay-at", "t", secret=True)
            if _code_of(doc) != "confirm_code_mismatch":
                probs.append(f"a wrong code: {doc}")
            rc, doc, _ = cli(*argv, "--json", "--code", code, secret=True)
            if _code_of(doc) != "confirm_relay_audit_missing":
                probs.append(f"a code without --relay-user/--relay-at: {doc}")
            if cli.facts("market_declared") != (cli.market, 1):
                probs.append("a refused confirm wrote the declaration")
            rc, doc, _ = cli(*argv, "--json", "--code", code, "--relay-user",
                             "suite:owner", "--relay-at",
                             "2026-01-01T00:00:00Z", secret=True)
            if rc != 0 or cli.facts("market_declared") != (cli.market, 0):
                probs.append(f"the right code with its audit: exit {rc}, "
                             f"{doc}")
    out.append(("confirming needs a person: no terminal and no secret is "
                "refused; a wrong code, or a code without its audit, writes "
                "nothing", probs))

    probs = []
    rc, doc, _ = cli("facts", "set", key, "12", "--source", "suite",
                     "--reason", "a number to confirm", "--json")
    if rc != 0:
        probs.append(f"facts set: exit {rc}, {doc}")
    argv = ("facts", "confirm", key, "--reason", "confirmed")
    code, doc = cli.challenge(*argv)
    if code is None or doc.get("subject", {}).get("items") != {key: "12"}:
        probs.append(f"the challenge is not bound to {{{key}: 12}}: {doc}")
    else:
        rc, doc, _ = cli(*argv, "--value", "13", "--json", "--code", code,
                         "--relay-user", "u", "--relay-at", "t", secret=True)
        if _code_of(doc) != "confirm_code_mismatch":
            probs.append(f"the code shown for 12 used for 13: {doc}")
        rc, doc, _ = cli(*argv, "--json", "--code", code, "--relay-user", "u",
                         "--relay-at", "t", secret=True)
        if rc != 0 or cli.facts(key) != ("12", 0):
            probs.append(f"the right code: exit {rc}, {doc}")
        cli("facts", "unconfirm", key, "--reason", "doubted", "--json")
        n = cli.history()
        rc, doc, _ = cli(*argv, "--json", "--code", code, "--relay-user", "u",
                         "--relay-at", "t", secret=True)
        if _code_of(doc) != "confirm_code_mismatch" or cli.history() != n:
            probs.append(f"the code replayed once the fact moved: {doc}")
    out.append(("a code is bound to what was shown: a swapped or replayed "
                "code is refused", probs))

    probs = []
    if cli.has("queue", "approve") and cli.has("queue", "add"):
        f = Path(tmp_dir("suite-queue-")) / "proposals.json"
        f.write_text(json.dumps({"meta": {}, "proposals": [{
            "kind": "suite_probe", "market": cli.market,
            "target_ref": "probe-1", "payload": {"n": 1}, "basis": "b1",
            "expected": {"effect": "nothing: a test proposal"}}]}),
            encoding="utf-8")
        rc, doc, _ = cli("queue", "add", "--from", str(f), "--json")
        if rc != 0:
            probs.append(f"queue add --from: exit {rc}, {doc}")
        argv = ("queue", "approve", "1", "--reason", "approved")
        rc, doc, _ = cli(*argv, "--json")
        if _code_of(doc) != "confirm_needs_human":
            probs.append(f"approve with no terminal, no secret: {doc}")
        code, _ = cli.challenge(*argv)
        if code is None:
            probs.append("approve with the secret: no challenge")
        else:
            rc, doc, _ = cli(*argv, "--json", "--code", code, "--relay-user",
                             "u", "--relay-at", "t", secret=True)
            if rc != 0:
                probs.append(f"approve with its code: exit {rc}, {doc}")
            rc, doc, _ = cli(*argv, "--json", "--code", code, "--relay-user",
                             "u", "--relay-at", "t", secret=True)
            if rc == 0:
                probs.append("the approve code replayed went through")
    out.append(("approving needs a person too; a used code is refused",
                probs))

    probs = []
    for v in cli.verbs():
        if v.kind != "gated":
            continue
        rc, _, err = _raw_help(cli, list(v.words))
        text = err
        flags = set(re.findall(r"(?<![\w-])(--?[a-z][a-z-]*)", text))
        hit = sorted(flags & set(BYPASS))
        if hit:
            probs.append(f"{' '.join(v.words)} offers {hit}")
    out.append(("no gated verb offers a bypass flag", probs))
    return out


def _raw_help(cli: Cli, words: list[str]) -> tuple[int, str, str]:
    rc, out, err = _raw(cli, [*words, "--help"])
    return rc, "", out + err


# ---- release -------------------------------------------------------------------------

def release(root: Path | str) -> Results:
    bind(root)
    from kit.guards import release as g
    root = Path(root)
    got = g.shipped(root)
    stages = root / _config.load(root).ssot.get("dir", "ssot") / STAGES
    b8 = stages.is_file() and any(
        r.get("stage") == "B8" and r.get("status") == "signed"
        for r in _tsv(stages))
    due = b8 or bool(os.environ.get("CI_COMMIT_TAG"))
    fills = []
    if got is not None:
        _, ships = got
        for f in sorted(ships):
            p = root / f
            try:
                if p.is_file() and FILL in p.read_text(encoding="utf-8"):
                    fills.append(f)
            except UnicodeDecodeError:
                continue
    if fills and not due:
        print(f"note: {len(fills)} shipped file(s) still hold {FILL} "
              f"(allowed until the B8 row of ssot/stages.agent.tsv is "
              f"signed): {fills}")
    return [("git archive leaves out the internal files and ships the kit, "
             "the console and every must-ship path", g.check_release(root)),
            ("no shipped file holds a fill marker once the docs are signed "
             "(B8) or on a release tag", fills if due else [])]
