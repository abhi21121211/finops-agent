"""Parse bank statement CSVs in the layouts Indian banks export.

Handles separate withdrawal/deposit columns or a signed amount (optionally with a DR/CR
column), Indian digit grouping, several date formats and preamble rows above the header.
Each row gets a fingerprint so re-uploading the same statement is idempotent.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

HEADER_ALIASES = {
    "date": ("txn date", "transaction date", "date", "tran date", "value date", "value dt"),
    "narration": ("narration", "description", "particulars", "remarks", "details"),
    "reference": (
        "chq/ref no",
        "chq./ref.no.",
        "ref no",
        "reference",
        "ref",
        "cheque no",
        "chq no",
        "utr",
    ),
    "debit": (
        "withdrawal amt",
        "withdrawal amount",
        "withdrawal",
        "debit",
        "debit amount",
        "dr amount",
        "dr",
    ),
    "credit": (
        "deposit amt",
        "deposit amount",
        "deposit",
        "credit",
        "credit amount",
        "cr amount",
        "cr",
    ),
    "amount": ("amount", "txn amount", "transaction amount"),
    "type": ("type", "dr/cr", "cr/dr", "txn type"),
}
DATE_FORMATS = (
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%b-%y",
    "%d.%m.%Y",
)
MAX_ROWS = 20_000


@dataclass(frozen=True)
class ParsedTxn:
    date: date
    amount: Decimal  # negative = money out
    narration: str
    reference: str | None
    fingerprint: str


class StatementError(ValueError):
    pass


def _norm(h: str) -> str:
    """'Withdrawal Amt.' / 'withdrawal_amt' / 'Chq./Ref.No.' → 'withdrawalamt', 'chqrefno'."""
    return re.sub(r"[^a-z0-9]", "", h.lower())


def _map_header(row: list[str]) -> dict[str, int] | None:
    cells = [_norm(c) for c in row]
    found: dict[str, int] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in map(_norm, aliases):  # aliases are in priority order
            if alias in cells and cells.index(alias) not in found.values():
                found[field] = cells.index(alias)
                break
    has_amount = "amount" in found or "debit" in found or "credit" in found
    return found if "date" in found and "narration" in found and has_amount else None


def parse_date(value: str) -> date:
    v = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise StatementError(f"unrecognised date {value!r}")


def parse_amount(value: str) -> Decimal | None:
    v = value.strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    if v in ("", "-", "0", "0.00"):
        return None
    negative = v.startswith("(") and v.endswith(")")
    v = v.strip("()")
    try:
        d = Decimal(v)
    except InvalidOperation as e:
        raise StatementError(f"unrecognised amount {value!r}") from e
    return -d if negative else d


def parse_statement(data: bytes) -> list[ParsedTxn]:
    text = data.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    header_at, cols = next(
        ((n, m) for n, row in enumerate(rows[:30]) if (m := _map_header(row))), (None, None)
    )
    if cols is None:
        raise StatementError(
            "No header row found. Need a date column, a narration/description column and "
            "either withdrawal/deposit or amount columns."
        )

    out: list[ParsedTxn] = []
    seen: dict[str, int] = {}
    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        if not any(c.strip() for c in row):
            continue
        get = _getter(row, cols)
        if not get("date").strip():
            continue  # footer lines such as closing balance
        try:
            amount = _row_amount(get)
        except StatementError as e:
            raise StatementError(f"row {n}: {e}") from e
        if amount is None:
            continue  # no money moved, or summary text such as "Closing balance"
        try:
            when = parse_date(get("date"))
        except StatementError as e:
            raise StatementError(f"row {n}: {e}") from e
        narration = re.sub(r"\s+", " ", get("narration")).strip()
        reference = get("reference").strip() or None
        base = f"{when.isoformat()}|{amount}|{narration}|{reference or ''}"
        # Identical rows in one statement are distinct transactions; number them.
        seen[base] = seen.get(base, 0) + 1
        fingerprint = hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest()
        out.append(ParsedTxn(when, amount, narration, reference, fingerprint))
        if len(out) > MAX_ROWS:
            raise StatementError(f"more than {MAX_ROWS} rows; split the statement")
    return out


def _getter(row: list[str], cols: dict[str, int]):
    def get(field: str) -> str:
        i = cols.get(field)
        return row[i] if i is not None and i < len(row) else ""

    return get


def _row_amount(get) -> Decimal | None:
    debit, credit = parse_amount(get("debit")), parse_amount(get("credit"))
    if debit or credit:
        return (credit or Decimal("0")) - abs(debit or Decimal("0"))
    amount = parse_amount(get("amount"))
    if amount is None:
        return None
    kind = get("type").strip().upper()
    if kind.startswith("DR") or kind == "D" or kind.startswith("DEBIT"):
        return -abs(amount)
    if kind.startswith("CR") or kind == "C" or kind.startswith("CREDIT"):
        return abs(amount)
    return amount
