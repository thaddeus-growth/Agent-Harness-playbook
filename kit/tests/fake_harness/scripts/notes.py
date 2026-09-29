"""The shop harness's own messages: emits both of its registered codes
(the kit's registry-closure test scans this file)."""

from kit.messages import Msg, msg


def price_missing(sku: str) -> Msg:
    return msg("shop_price_missing", f"No price on file for {sku}", sku=sku)


def stock_low(sku: str, units: int) -> Msg:
    return msg("shop_stock_low", f"{sku} has only {units} unit(s) left",
               sku=sku, units=units)
