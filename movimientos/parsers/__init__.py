"""Statement parsers; `detect` picks the one that recognises a file."""
from . import account_pdf, account_xlsx, card_xlsx, galicia_pdf, galicia_xlsx, legacy_xls

PARSERS = (card_xlsx, account_xlsx, galicia_xlsx, account_pdf, galicia_pdf, legacy_xls)


def detect(path):
    for module in PARSERS:
        try:
            if module.matches(path):
                return module
        except Exception:
            continue
    return None


def parse(path):
    module = detect(path)
    return module.parse(path) if module else None
