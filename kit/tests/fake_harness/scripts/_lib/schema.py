"""The shop harness's schema: one cache table beside the kit's human
tables (kit.db.with_human)."""

from kit import db

SPEC = db.with_human({
    "stock": {"columns": {"market": "TEXT", "sku": "TEXT", "units": "INTEGER"},
              "pk": ["market", "sku"], "doc": "units on hand per product"},
}, version=1)
