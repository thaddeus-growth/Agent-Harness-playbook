"""The harness CLI: `<cli> <verb words> [args…]`, one thin dispatcher over
the verb table (kit.verbs). A harness's `<scripts_dir>/<cli>.py` is:

    import sys
    from kit.cli import Dispatcher
    from verbs import VERBS
    from _lib.schema import SPEC

    if __name__ == "__main__":
        raise SystemExit(Dispatcher(VERBS, scripts_dir=HERE, spec=SPEC).main())

What `main(argv)` does, in order (ported from the reference harness's
dispatcher; its routing table became the verb table):

  1. `<cli>` alone, `-h`, `--help`, `help`: the verbs grouped by kind, each
     with what it answers (kit.verbs.answers), the built-ins, the env chain
     and the epilog. `<cli> --version`: harness and kit versions.
  2. The env chain is loaded ONCE into os.environ (kit.env: process env
     wins, never overridden), so every forwarded script and doctor agree on
     every value.
  3. The first literal `--` is stripped wherever it sits; everything after
     it is the script's verbatim (a second `--` included).
  4. The verb is the longest listed prefix of the words (kit.verbs.match).
     A verb that is not listed is refused (cli_unknown_verb, exit 2), never
     guessed; `<cli> <group> --help` lists that group's verbs.
  5. A verb with needs_data_dir refuses to run without an absolute
     `<P>_DATA_DIR` (data_dir_unset / data_dir_not_absolute: never the
     cwd); asking the script for `-h/--help` always works.
  6. The script (`<scripts_dir>/<script>`) runs as kit.runner.script_cmd
     runs it, with the verb's words after the first as the script's
     sub-verb (`facts confirm X` -> facts.py confirm X), unless the script
     is named after the whole verb (`compute sales` -> compute_sales.py:
     none; kit.verbs.script_args), and every other argument as given; the
     `+ cmd` trace goes to stderr (stdout stays the script's one document;
     a `--code` value is masked); the exit code is the script's (a signal
     = 128 + its number).

Built-ins: `<cli> doctor [--live] [--strict] [--json]` (kit.doctor, with the
harness's own checks and platform-lock hook) and `<cli> verbs [--json]`
(the verb table with kinds, for agents and guards). A refusal of the
dispatcher itself is one coded failure document under --json
(kit.contract.fail), else `error:` / `next:` on stderr.

Test: kit/tests/test_cli.py.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Iterable

from kit import __version__ as KIT_VERSION
from kit import config as _config
from kit import contract, runner, verbs as _verbs
from kit.contract import HarnessError
from kit.messages import msg

HELP = ("-h", "--help")


def strip_dashdash(args: list[str]) -> list[str]:
    """Drop the first literal `--` wherever it sits; anything after it,
    another `--` included, is the script's verbatim."""
    if "--" in args:
        i = args.index("--")
        return args[:i] + args[i + 1:]
    return list(args)


def masked(cmd: list[str]) -> list[str]:
    """The trace of a command: a one-time code is never printed."""
    out, hide = [], False
    for w in cmd:
        if hide:
            out.append("***")
            hide = False
        elif w.startswith("--code="):
            out.append("--code=***")
        else:
            out.append(w)
            hide = w == "--code"
    return out


class Dispatcher:
    """`Dispatcher(verbs, *, scripts_dir, epilog, doctor_checks)` per the
    SPEC; `spec` (the harness's SchemaSpec: doctor checks the DB's shape),
    `platform_lock` (doctor's hook) and `description` are additions."""

    def __init__(self, verbs: Iterable[Any], *, scripts_dir: Path | str,
                 epilog: str = "", doctor_checks: Iterable[Callable] = (),
                 spec: Any = None, platform_lock: Callable | None = None,
                 description: str = ""):
        self.verbs = _verbs.check(verbs)
        self.scripts_dir = Path(scripts_dir).resolve()
        self.epilog = epilog
        self.doctor_checks = tuple(doctor_checks)
        self.spec = spec
        self.platform_lock = platform_lock
        self.description = description

    # ---- help ---------------------------------------------------------------

    @property
    def cli(self) -> str:
        return _config.config().cli

    def table(self) -> dict:
        """The `verbs --json` document."""
        cfg = _config.config()
        rows = [_verbs.as_dict(v, cfg.cli, self.scripts_dir)
                for v in self.verbs]
        builtins = [
            {"command": f"{cfg.cli} doctor", "words": ["doctor"],
             "kind": "read", "script": None, "takes_market": False,
             "needs_data_dir": False, "builtin": True,
             "answers": "Is this install healthy: env and sources, data dir, "
                        "database, markets, write switches"},
            {"command": f"{cfg.cli} verbs", "words": ["verbs"],
             "kind": "read", "script": None, "takes_market": False,
             "needs_data_dir": False, "builtin": True,
             "answers": "This table: every verb and its kind"}]
        return {"harness": cfg.name, "cli": cfg.cli,
                "kinds": dict(_verbs.KINDS), "verbs": rows,
                "builtins": builtins}

    def help_text(self, only: list[Any] | None = None) -> str:
        cfg = _config.config()
        table = only if only is not None else self.verbs
        rows = [(f"{cfg.cli} {v.command}", _verbs.answers(v, self.scripts_dir),
                 v.kind) for v in table]
        width = min(max([len(r[0]) for r in rows] + [len(cfg.cli) + 30]), 44)
        out = [f"usage: {cfg.cli} <verb> [args…]   (args after `--` go to "
               f"the script verbatim)", ""]
        if self.description and only is None:
            out += [self.description, ""]
        for kind, what in _verbs.KINDS.items():
            mine = [r for r in rows if r[2] == kind]
            if not mine:
                continue
            out.append(f"{kind} — {what}")
            out += [f"  {c:<{width}}  {a}".rstrip() for c, a, _ in mine]
            out.append("")
        if only is None:
            out += ["built-in",
                    f"  {cfg.cli + ' doctor [--live] [--strict] [--json]':<{width}}"
                    f"  Is this install healthy (env and sources, data dir, "
                    f"database, markets, write switches)",
                    f"  {cfg.cli + ' verbs [--json]':<{width}}  Every verb "
                    f"and its kind, for agents and guards", ""]
            p = cfg.env_prefix
            home = f" -> {cfg.home_env_file}" if cfg.home_env_file else ""
            out.append(f"env: process env{home} -> ${p}_DATA_DIR/.env "
                       f"(process env wins); {p}_AUTH_ENV_PATHS=none (or "
                       f"empty) = process env only. `{cfg.cli} doctor` names "
                       f"each value's source.")
            if self.epilog:
                out += ["", self.epilog.rstrip()]
        return "\n".join(out).rstrip() + "\n"

    # ---- main ---------------------------------------------------------------

    def refuse(self, e: HarnessError, argv: list[str]) -> int:
        head = argv[:argv.index("--")] if "--" in argv else argv
        return contract.fail(e, cmd=[self.cli, *argv],
                             as_json="--json" in head)

    def unknown(self, args: list[str]) -> HarnessError:
        cli = self.cli
        words = " ".join(a for a in args[:3] if not a.startswith("-")) \
            or " ".join(args[:1])
        near = _verbs.group(args, self.verbs)
        cands = [f"{cli} {v.command}" for v in near]
        return HarnessError(
            msg("cli_unknown_verb", f"`{cli} {words}` is not a verb of this "
                f"harness" + (f"; did you mean one of: {', '.join(cands)}"
                              if cands else ""),
                cli=cli, words=words, candidates=cands),
            [f"{cli} {near[0].words[0]} --help" if near else f"{cli} --help"])

    def main(self, argv: list[str] | None = None) -> int:
        argv = list(sys.argv[1:] if argv is None else argv)
        if not argv or argv[0] in (*HELP, "help"):
            text = self.help_text()
            if not argv:
                sys.stderr.write(text)
                return 2
            sys.stdout.write(text)
            return 0
        if argv[0] == "--version":
            cfg = _config.config()
            print(f"{cfg.name} {contract.harness_version() or 'unversioned'}"
                  f" (kit {KIT_VERSION})")
            return 0

        from kit import env
        env.chain().load_into_environ()

        args = strip_dashdash(argv)
        if args[0] == "doctor":
            from kit import doctor
            return doctor.main(args[1:], checks=self.doctor_checks,
                               spec=self.spec, verbs=self.verbs,
                               scripts_dir=self.scripts_dir,
                               platform_lock=self.platform_lock)
        if args[0] == "verbs":
            if set(args[1:]) & set(HELP):
                print(f"usage: {self.cli} verbs [--json]")
                return 0
            if "--json" in args[1:]:
                contract.emit(self.table())
            else:
                sys.stdout.write(self.help_text())
            return 0

        v = _verbs.match(args, self.verbs)
        asks_help = bool(set(args) & set(HELP))
        if v is None:
            near = _verbs.group(args, self.verbs)
            if asks_help and near:
                sys.stdout.write(self.help_text(only=near))
                return 0
            return self.refuse(self.unknown(args), argv)
        rest = args[len(v.words):]
        if v.needs_data_dir and not (set(rest) & set(HELP)):
            from kit import paths
            try:
                paths.data_dir()
            except HarnessError as e:
                return self.refuse(e, argv)
        script = self.scripts_dir / v.script
        if not script.is_file():
            return self.refuse(HarnessError(
                msg("cli_script_missing", f"`{self.cli} {v.command}` has no "
                    f"script at {script}", verb=v.command, script=v.script),
                [f"{self.cli} doctor"]), argv)
        cmd = runner.script_cmd(script, [*_verbs.script_args(v, self.verbs),
                                         *rest])
        print(f"+ {shlex.join(masked(cmd))}", file=sys.stderr, flush=True)
        try:
            rc = subprocess.run(cmd, env=os.environ).returncode
        except KeyboardInterrupt:
            return 130
        return 128 - rc if rc < 0 else rc
