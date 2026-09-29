"""Shared set-up for the kit's own tests (not a test file: no test_ prefix).

    import _shop                        # after the sys.path line
    cfg = _shop.use()                   # bind the fake "shop" harness
    data = _shop.data_dir()             # a sandbox SHOP_DATA_DIR, exported

`use()` binds kit/tests/fake_harness (cli "shop", env prefix SHOP, markets
US/CA, languages en/zh) through kit.config.use, which clears every kit
cache. `data_dir()` makes a fresh sandbox dir (removed when the file
exits), strips every inherited SHOP_* var from os.environ, and exports
SHOP_DATA_DIR=<it> and SHOP_AUTH_ENV_PATHS=none.
"""

from __future__ import annotations

import os
from pathlib import Path

from kit import config
from kit.testing.check import tmp_dir

HERE = Path(__file__).resolve().parent
SHOP = HERE / "fake_harness"
KIT = HERE.parent
PLAYBOOK = KIT.parent


def use() -> config.HarnessConfig:
    return config.use(SHOP)


def data_dir(prefix: str = "shop-data-") -> Path:
    cfg = config.config()
    for k in [k for k in os.environ if k.startswith(cfg.env_prefix + "_")]:
        del os.environ[k]
    d = Path(tmp_dir(prefix))
    os.environ[cfg.env("DATA_DIR")] = str(d)
    os.environ[cfg.env("AUTH_ENV_PATHS")] = "none"
    return d
