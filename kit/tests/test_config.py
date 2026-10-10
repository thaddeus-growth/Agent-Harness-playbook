#!/usr/bin/env python3
"""kit.config: harness.toml is the one binding, and switching it clears
every kit cache that depends on it.

  * the fake "shop" harness loads with every field typed as the SPEC says;
  * env names are <env_prefix>_<NAME>; ssot_path is root-relative or None;
  * defaults for the optional keys; a malformed harness.toml fails at load
    naming the key;
  * use() re-binds, clears registered caches (messages.registry among
    them) and exports KIT_HARNESS_ROOT; config() honours it and otherwise
    walks up from the kit, never from the cwd;
  * importing kit, kit.dates, kit.atomic, kit.single_instance binds no
    harness (pullers import them).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import _shop  # noqa: E402
import kit  # noqa: E402
from kit import config, messages  # noqa: E402
from kit.testing.check import check, finish, raises, tmp_dir  # noqa: E402


def harness(body: str, codes: str | None = None) -> Path:
    d = Path(tmp_dir("harness-"))
    (d / "harness.toml").write_text(body, encoding="utf-8")
    if codes is not None:
        (d / "ssot").mkdir()
        (d / "ssot" / "message_codes.tsv").write_text(codes, encoding="utf-8")
    return d


MINIMAL = '[harness]\nname = "b"\ncli = "bee"\nenv_prefix = "BEE"\n'


def main() -> int:
    print("[1] the shop harness loads, typed")
    cfg = _shop.use()
    check("name / cli / env_prefix / db_file / scripts_dir",
          (cfg.name, cfg.cli, cfg.env_prefix, cfg.db_file, cfg.scripts_dir)
          == ("shop-harness", "shop", "SHOP", "shop.db", "scripts"), cfg)
    check("languages and markets are tuples, in declared order",
          cfg.languages == ("en", "zh") and cfg.markets == ("US", "CA"))
    check("root is the resolved harness dir", cfg.root == _shop.SHOP.resolve())
    check("home_env_file '' = no HOME file", cfg.home_env_file == "")
    check("ssot / release / layers / raw kept",
          cfg.ssot["message_codes"] == "ssot/message_codes.tsv"
          and cfg.release["must_ship"][-1] == "scripts/shop.py"
          and cfg.layers["writer"] == "_lib/writer"
          and cfg.raw["contract"]["no_db_next"] == ["facts init"])
    check("env('DATA_DIR') -> SHOP_DATA_DIR",
          cfg.env("DATA_DIR") == "SHOP_DATA_DIR"
          and cfg.env("CONFIRM_CODE_SECRET") == "SHOP_CONFIRM_CODE_SECRET")
    check("ssot_path: root-relative path, None when undeclared",
          cfg.ssot_path("message_codes")
          == cfg.root / "ssot" / "message_codes.tsv"
          and cfg.ssot_path("no_such_registry") is None)
    check("the config is frozen",
          raises(lambda: setattr(cfg, "cli", "x"), AttributeError) is not None)
    check("… and hashable (a cache may key on it); equal loads are equal",
          hash(cfg) == hash(config.load(_shop.SHOP))
          and cfg == config.load(_shop.SHOP))
    check("kit.__version__ = kit/VERSION, a plain X.Y.Z",
          kit.__version__ == (_shop.KIT / "VERSION").read_text().strip()
          and bool(re.fullmatch(r"\d+\.\d+\.\d+", kit.__version__)))

    print("\n[2] defaults of the optional keys")
    b = config.load(harness(MINIMAL))
    check("db_file defaults to <cli>.db, scripts_dir to scripts",
          b.db_file == "bee.db" and b.scripts_dir == "scripts")
    check("languages default en, zh; markets default () (no partition)",
          b.languages == ("en", "zh") and b.markets == ())
    check("ssot defaults to {dir: ssot}; no message_codes declared",
          b.ssot == {"dir": "ssot"} and b.ssot_path("message_codes") is None)
    check("release / layers default to {}", b.release == {} and b.layers == {})

    print("\n[3] a malformed harness.toml fails at load, naming the key")
    bad = {
        "no file": None,
        "no [harness]": '[ssot]\ndir = "ssot"\n',
        "[harness].cli": '[harness]\nname = "b"\nenv_prefix = "BEE"\n',
        "[harness].env_prefix = 'bee'": MINIMAL.replace('"BEE"', '"bee"'),
        "[harness].env_prefix = 'BEE_'": MINIMAL.replace('"BEE"', '"BEE_"'),
        "[harness].markets twice": MINIMAL + 'markets = ["US", "US"]\n',
        "[harness].languages not a list": MINIMAL + 'languages = "en"\n',
        "[harness].languages empty": MINIMAL + "languages = []\n",
        "[harness].db_file with a slash": MINIMAL + 'db_file = "a/b.db"\n',
        "[harness].name not a string": MINIMAL.replace('"b"', "3"),
        "TOML syntax": MINIMAL + "markets = [\n",
    }
    for label, body in bad.items():
        d = Path(tmp_dir("bad-")) if body is None else harness(body)
        e = raises(lambda: config.load(d), config.ConfigError)
        key = label.split(" ")[0].split("=")[0].strip()
        check(f"refused: {label}", e is not None
              and (key in str(e) or label in ("no file", "TOML syntax",
                                               "no [harness]")), e)

    print("\n[4] use() re-binds and clears every registered kit cache")
    calls = []
    config.on_reset(lambda: calls.append(1))
    other = harness(MINIMAL + '\n[ssot]\nmessage_codes = "ssot/message_codes.tsv"\n',
                    "code\tparams\tmeaning_en\tmeaning_zh\n"
                    "bee_only\t\tOnly in bee\t只在 bee\n")
    check("shop registry holds shop codes", "shop_stock_low" in messages.registry())
    config.use(other)
    check("use() ran the registered reset hooks", calls == [1], calls)
    check("messages.registry was cleared: bee codes, no shop codes",
          "bee_only" in messages.registry()
          and "shop_stock_low" not in messages.registry())
    check("use() exports KIT_HARNESS_ROOT",
          os.environ[config.ROOT_ENV] == str(other.resolve()))
    _shop.use()
    check("back to shop: shop codes again, bee gone",
          "shop_stock_low" in messages.registry()
          and "bee_only" not in messages.registry() and len(calls) == 2)

    print("\n[5] config(): KIT_HARNESS_ROOT, else walk up from the kit")
    config.reset()
    os.environ[config.ROOT_ENV] = str(other)
    check("KIT_HARNESS_ROOT decides", config.config().cli == "bee")
    tree = Path(tmp_dir("tree-"))
    (tree / "h" / "scripts" / "kit").mkdir(parents=True)
    (tree / "h" / "harness.toml").write_text(MINIMAL)
    check("find_root walks up from a vendored kit dir to the harness",
          config.find_root(tree / "h" / "scripts" / "kit") == (tree / "h").resolve())
    check("find_root: None with no harness.toml above",
          config.find_root(tree) is None
          or config.find_root(tree) != (tree / "h").resolve())
    config.reset()
    del os.environ[config.ROOT_ENV]
    cwd = os.getcwd()
    os.chdir(tree / "h")          # a harness.toml in the cwd must not bind
    try:
        if config.find_root(_shop.KIT) is None:
            e = raises(config.config, config.ConfigError)
            check("unbound kit, harness.toml in the cwd: ConfigError, the "
                  "cwd is never used", e is not None and "KIT_HARNESS_ROOT"
                  in str(e), e)
        else:
            check("the kit binds the harness above it, not the cwd's",
                  config.config().root != (tree / "h").resolve())
    finally:
        os.chdir(cwd)
    _shop.use()

    print("\n[6] pullers' modules bind nothing")
    r = subprocess.run(
        [sys.executable, "-B", "-c",
         "import sys; sys.path.insert(0, %r)\n"
         "import kit, kit.dates, kit.atomic, kit.single_instance\n"
         "print(sorted(m for m in sys.modules if m.startswith('kit')))"
         % str(_shop.PLAYBOOK)],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != config.ROOT_ENV})
    check("import kit / dates / atomic / single_instance loads no config",
          r.returncode == 0 and "kit.config" not in r.stdout, r)
    return finish()


if __name__ == "__main__":
    raise SystemExit(main())
