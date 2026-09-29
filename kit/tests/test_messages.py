#!/usr/bin/env python3
"""kit.messages: a closed registry, both ways, and one placement rule.

  * the registry = kit base (message_codes.tsv + message_codes.d/*.tsv,
    sorted) + the harness's; a code defined twice, a missing meaning, a
    malformed row = error at load;
  * a Msg is its text, with a registered code and exactly its params;
  * coded() / failure() / relayed() / joined() place codes one way;
  * a harness's fragments (<ssot dir>/message_codes.d/*.tsv, sorted) load
    beside [ssot].message_codes, same rules: a code defined twice anywhere
    (two fragments, a fragment and the registry, a fragment and the kit) is
    an error naming both files; the ssot index row of the registry covers
    them;
  * check_registry_closed() finds a non-literal code, an unknown code, a
    params mismatch, `**params`, and a harness code nobody emits (planted
    bad calls), and passes the shop harness and the kit's foundation.
"""

import copy
import json
import pickle
import sys
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
from kit import config, messages  # noqa: E402
from kit.messages import Msg, code, coded, failure, joined, msg, relayed  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402

HEAD = "code\tparams\tmeaning_en\tmeaning_zh\n"
SHARED = ("unclassified_error", "data_dir_unset", "no_db", "joined",
          "reason_required")
# The foundation modules and the shared codes they must emit themselves
# (reason_required is emitted by the human gate, kit/human.py).
FOUNDATION = ["__init__.py", "config.py", "messages.py", "contract.py",
              "dates.py", "atomic.py", "single_instance.py", "runner.py",
              "raw.py", "paths.py", "env.py", "testing/*.py", "tools/*.py"]


def harness(toml_extra: str, codes: str) -> Path:
    d = Path(tmp_dir("h-"))
    (d / "harness.toml").write_text(
        '[harness]\nname = "t"\ncli = "t"\nenv_prefix = "T"\n' + toml_extra
        + '\n[ssot]\nmessage_codes = "codes.tsv"\n', encoding="utf-8")
    (d / "codes.tsv").write_text(codes, encoding="utf-8")
    return d


def load_error(toml_extra: str, codes: str) -> Exception | None:
    config.use(harness(toml_extra, codes))
    try:
        return raises(messages.registry, messages.RegistryError)
    finally:
        _shop.use()


def fragment_harness(frags: dict[str, str], codes: str = HEAD,
                     declared: bool = True) -> Path:
    d = Path(tmp_dir("hf-")).resolve()
    (d / "ssot" / "message_codes.d").mkdir(parents=True)
    (d / "harness.toml").write_text(
        '[harness]\nname = "t"\ncli = "t"\nenv_prefix = "T"\n[ssot]\n'
        'dir = "ssot"\n' + ('message_codes = "ssot/message_codes.tsv"\n'
                            if declared else ""), encoding="utf-8")
    (d / "ssot" / "message_codes.tsv").write_text(codes, encoding="utf-8")
    for name, text in frags.items():
        (d / "ssot" / "message_codes.d" / name).write_text(text,
                                                          encoding="utf-8")
    return d


def frag_load(d: Path):
    config.use(d)
    try:
        return raises(messages.registry, messages.RegistryError), \
            (messages.harness_files(), dict(messages.registry())
             if raises(messages.registry) is None else {})
    finally:
        _shop.use()


def test_harness_fragments() -> None:
    d = fragment_harness({"steer.tsv": HEAD + "steer_low\tsku\tLow {sku}\t低 {sku}\n",
                          "alerts.tsv": HEAD + "alerts_spike\t\tSpike\t激增\n",
                          "notes.txt": "not a registry\n"},
                         HEAD + "t_main\t\tMain\t主\n")
    err, (files, reg) = frag_load(d)
    check("fragments load after the registry, sorted by name, .tsv only",
          err is None and [f.relative_to(d).as_posix() for f in files]
          == ["ssot/message_codes.tsv", "ssot/message_codes.d/alerts.tsv",
              "ssot/message_codes.d/steer.tsv"], (err, files))
    check("their codes are harness codes, with their own file",
          reg["steer_low"]["origin"] == "harness"
          and reg["steer_low"]["params"] == ("sku",)
          and reg["steer_low"]["file"].endswith("message_codes.d/steer.tsv")
          and reg["t_main"]["origin"] == "harness"
          and reg["no_db"]["origin"] == "kit")
    for label, frags, codes, needles in (
            ("two fragments", {"a.tsv": HEAD + "dup_x\t\ta\tb\n",
                               "b.tsv": HEAD + "dup_x\t\ta\tb\n"}, HEAD,
             ("dup_x", "a.tsv", "b.tsv")),
            ("a fragment and the registry",
             {"a.tsv": HEAD + "dup_x\t\ta\tb\n"},
             HEAD + "dup_x\t\ta\tb\n", ("dup_x", "message_codes.tsv",
                                          "a.tsv")),
            ("a fragment and the kit", {"a.tsv": HEAD + "no_db\tpath\ta\tb\n"},
             HEAD, ("no_db", "a.tsv"))):
        err, _ = frag_load(fragment_harness(frags, codes))
        check(f"a code defined twice ({label}) is an error naming both",
              err is not None and all(n in str(err) for n in needles), err)
    err, _ = frag_load(fragment_harness({"a.tsv": HEAD + "t_y\t\tonly en\t\n"}))
    check("a fragment row without a declared language's meaning is an error",
          err is not None and "meaning_zh" in str(err), err)
    err, (files, reg) = frag_load(fragment_harness(
        {"a.tsv": HEAD + "t_y\t\ty\t乙\n"}, declared=False))
    check("fragments load even when [ssot].message_codes is not declared",
          err is None and "t_y" in reg and len(files) == 1, (err, files))
    from kit.guards import ssot as ssot_guard
    idx = ("file\towner\treader\ttest\tid_column\tpurpose\n"
           "ssot/index.tsv\tregistry\t—\ttests/t.py\tfile\tthe index\n"
           "ssot/message_codes.tsv\tregistry\tkit/messages.py\ttests/t.py\t"
           "code\tmessage codes\n")
    d = fragment_harness({"steer.tsv": HEAD + "steer_low\t\tLow\t低\n"})
    (d / "ssot" / "index.tsv").write_text(idx, encoding="utf-8")
    (d / "tests").mkdir()
    (d / "tests" / "t.py").write_text("", encoding="utf-8")
    (d / "kit").mkdir()
    (d / "kit" / "messages.py").write_text("", encoding="utf-8")
    problems = ssot_guard.check_index(d)
    check("the ssot index: the registry's row covers its fragments",
          problems == [], problems)
    (d / "ssot" / "index.tsv").write_text(idx.rsplit("ssot/message_codes",
                                                     1)[0], encoding="utf-8")
    problems = ssot_guard.check_index(d)
    check("…and without that row the fragments are unlisted too",
          any("message_codes.d/steer.tsv" in p for p in problems), problems)


def main() -> int:
    _shop.use()
    reg = messages.registry()

    print("[1] registry = kit base ∪ harness")
    check("kit base codes are there, origin kit",
          reg["no_db"]["origin"] == "kit" and reg["no_db"]["params"] == ("path",))
    check("harness codes are there, origin harness",
          reg["shop_stock_low"]["origin"] == "harness"
          and reg["shop_stock_low"]["params"] == ("sku", "units"))
    check("the shared codes are in kit/message_codes.tsv",
          all(reg[c]["file"] == str(messages.BASE_TSV) for c in SHARED))
    base_head = messages.BASE_TSV.read_text(encoding="utf-8").splitlines()[0]
    check("kit base header = code, params, meaning_en, meaning_zh",
          base_head.split("\t") == ["code", "params", "meaning_en", "meaning_zh"])
    check("every kit base row has en + zh meanings",
          all(r["meaning_en"] and r["meaning_zh"]
              for r in messages.base_registry().values()))
    check("lint: snake_case codes, placeholders ⊆ params",
          messages.lint_registry(reg) == [], messages.lint_registry(reg))
    check("kit_files = base then message_codes.d/*.tsv sorted",
          messages.kit_files() == [messages.BASE_TSV]
          + sorted(messages.FRAGMENTS_DIR.glob("*.tsv")))

    print("\n[2] load errors")
    e = load_error("", HEAD + "no_db\tpath\tagain\t又\n")
    check("a code in kit base and harness = error naming both files",
          e is not None and "no_db" in str(e) and "message_codes.tsv" in str(e)
          and "codes.tsv" in str(e), e)
    e = load_error("", HEAD + "t_x\t\tSomething\t\n")
    check("an empty required meaning = error", e is not None
          and "meaning_zh" in str(e), e)
    e = load_error("", "code\tparams\tmeaning_en\nt_x\t\tSomething\n")
    check("a header without a declared language = error",
          e is not None and "meaning_zh" in str(e), e)
    e = load_error("", HEAD + "t_x\t\tSomething\t某\textra\n")
    check("a row with more cells than the header = error",
          e is not None and "cells" in str(e), e)
    e = load_error("", HEAD + "t_x\t\ta\tb\nt_x\t\ta\tb\n")
    check("a code twice in one file = error", e is not None, e)
    e = load_error('languages = ["en", "ja"]',
                   "code\tparams\tmeaning_en\tmeaning_ja\nt_x\t\ta\tb\n")
    check("a harness language beyond en/zh: harness rows carry it, the kit "
          "base still loads (deviation, see module docstring)", e is None, e)
    e = load_error('languages = ["en", "ja"]', HEAD + "t_x\t\ta\tb\n")
    check("… and a harness row without it = error",
          e is not None and "meaning_ja" in str(e), e)

    frag = Path(tmp_dir("frag-"))
    (frag / "b.tsv").write_text(HEAD + "zz_b\t\tb\tb\n", encoding="utf-8")
    (frag / "a.tsv").write_text(HEAD + "zz_a\tn\ta {n}\ta {n}\n",
                                encoding="utf-8")
    saved = messages.FRAGMENTS_DIR
    messages.FRAGMENTS_DIR = frag
    try:
        config.reset()
        _shop.use()
        r = messages.registry()
        check("fragments load after the base, sorted, origin kit",
              [f.name for f in messages.kit_files()[1:]] == ["a.tsv", "b.tsv"]
              and r["zz_a"]["origin"] == "kit" and "zz_b" in r)
        (frag / "c.tsv").write_text(HEAD + "zz_a\tn\tagain\t又\n",
                                    encoding="utf-8")
        _shop.use()
        check("two fragments defining one code = error",
              raises(messages.registry, messages.RegistryError) is not None)
    finally:
        messages.FRAGMENTS_DIR = saved
        _shop.use()

    print("\n[3] Msg")
    m = msg("no_db", "No database at x", path="x")
    check("a Msg is its text, with code + params beside it",
          m == "No database at x" and json.dumps(m) == '"No database at x"'
          and m.code == "no_db" and m.params == {"path": "x"})
    for label, fn in (("an unregistered code", lambda: msg("no_such", "x")),
                      ("a missing param", lambda: msg("no_db", "x")),
                      ("an extra param",
                       lambda: msg("no_db", "x", path="x", other=1))):
        check(f"refused: {label}", raises(fn, ValueError) is not None)
    check("copy / deepcopy / pickle keep the code",
          all(isinstance(c, Msg) and c.code == "no_db" and c == m
              and c.params == m.params
              for c in (copy.copy(m), copy.deepcopy(m),
                        pickle.loads(pickle.dumps(m)))))

    print("\n[4] the placement rule")
    check("coded(k, Msg) -> k + k_code",
          coded("reason", m) == {"reason": m, "reason_code":
                                 {"code": "no_db", "params": {"path": "x"}}})
    check("coded(k, None) -> k_code None",
          coded("reason", None) == {"reason": None, "reason_code": None})
    check("coded(warnings, [...]) -> warning_codes, same order",
          coded("warnings", [m, m])["warning_codes"]
          == [{"code": "no_db", "params": {"path": "x"}}] * 2)
    check("a plain str is refused (TypeError)",
          raises(lambda: code("plain"), TypeError) is not None
          and raises(lambda: coded("note", "plain"), TypeError) is not None)
    s = msg("shop_stock_low", "A has only 2 unit(s) left", sku="A", units=2)
    nested = joined([m, s])
    check("joined: texts joined, each part a nested code",
          nested == "No database at x; A has only 2 unit(s) left"
          and code(nested) == {"code": "joined", "params": {"parts": [
              {"code": "no_db", "params": {"path": "x"}},
              {"code": "shop_stock_low", "params": {"sku": "A", "units": 2}}]}})
    d = msg("shop_price_missing", "No price on file for …",
            sku=date(2026, 9, 1))
    check("a date param becomes ISO; the doc is JSON-safe",
          code(d)["params"]["sku"] == "2026-09-01"
          and json.loads(json.dumps(code(nested))) == code(nested))

    print("\n[5] failure() and relayed()")
    check("failure: the first Msg in args",
          failure(RuntimeError("ctx", m)) == code(m))
    check("failure: no Msg -> unclassified_error{detail}",
          failure(RuntimeError("boom")) == {
              "code": "unclassified_error", "params": {"detail": "boom"}})
    check("relayed keeps a registered child code",
          code(relayed(code(s), "child text")) == code(s)
          and relayed(code(s), "child text") == "child text")
    for label, c in (("no code (a crash)", None),
                     ("an unknown code", {"code": "nope", "params": {}}),
                     ("other params",
                      {"code": "no_db", "params": {"file": "x"}})):
        r = relayed(c, "t")
        check(f"relayed: {label} -> unclassified_error",
              r.code == "unclassified_error" and r.params == {"detail": "t"})

    print("\n[5b] a harness's message-code fragments")
    test_harness_fragments()

    print("\n[6] check_registry_closed: planted bad calls")
    check("the shop harness is closed (kit codes exempt, both shop codes "
          "emitted)", messages.check_registry_closed(
              _shop.SHOP, ["scripts/**/*.py"]) == [],
          messages.check_registry_closed(_shop.SHOP, ["scripts/**/*.py"]))
    bad = Path(tmp_dir("bad-"))
    (bad / "scripts").mkdir()
    (bad / "scripts" / "bad.py").write_text(
        "from kit.messages import msg\n"
        "from kit import messages\n"
        "CODE = 'no_db'\n"
        "a = msg(CODE, 'x', path='p')\n"                         # line 4
        "b = msg('not_registered', 'x')\n"                       # line 5
        "c = messages.msg('no_db', 'x', file='p')\n"             # line 6
        "d = msg('shop_price_missing', 'x', **{'sku': 1})\n"     # line 7
        "e = msg('no_db', 'x', path='p')\n", encoding="utf-8")   # fine
    probs = messages.check_registry_closed(bad, ["scripts/**/*.py"])
    text = "\n".join(probs)
    check("a non-literal code is found (with file:line)",
          "scripts/bad.py:4: msg() code is not a string literal" in text, text)
    check("an unregistered code is found",
          "scripts/bad.py:5: code 'not_registered' is not registered" in text,
          text)
    check("a params mismatch is found (x.msg form too)",
          "scripts/bad.py:6: no_db takes params ['path'], the call passes "
          "['file']" in text, text)
    check("**params is found", "scripts/bad.py:7: shop_price_missing: params "
          "passed as **mapping" in text, text)
    check("a harness code nobody emits is found",
          "code 'shop_stock_low' is registered but no msg() call emits it"
          in text, text)
    check("kit codes are exempt unless strict_kit",
          "'data_dir_unset' is registered" not in text
          and "'data_dir_unset' is registered" in "\n".join(
              messages.check_registry_closed(bad, ["scripts/**/*.py"],
                                             strict_kit=True)))
    check("nothing else is flagged", len(probs) == 5, probs)

    print("\n[7] the foundation modules keep the registry closed")
    kit_only = {c: r for c, r in reg.items() if r["origin"] == "kit"}
    probs = messages.check_registry_closed(_shop.KIT, FOUNDATION, kit_only)
    check("every foundation msg() call: literal, registered, exact params",
          probs == [], probs)
    files = sorted({p for g in FOUNDATION for p in _shop.KIT.glob(g)})
    emitted = {c for _, c, _ in messages.msg_calls(files, _shop.KIT)}
    shared = {r["code"] for r in messages.read_rows(messages.BASE_TSV,
                                                    ("en", "zh"))}
    check("every code of kit/message_codes.tsv is emitted by a foundation "
          "module (reason_required: by kit/human.py)",
          shared - {"reason_required"} <= emitted,
          sorted(shared - {"reason_required"} - emitted))
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
