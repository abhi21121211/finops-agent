"""GSTIN structure: 2-digit state code + 10-char PAN + entity number + 'Z' (default) +
check character. The check character is a mod-36 Luhn-style checksum over the first 14."""

import re

B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

# 01–38 are states/UTs; 97 other territory; 99 centre jurisdiction.
VALID_STATE_CODES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}


def checksum_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        prod = B36.index(ch) * (1 if i % 2 == 0 else 2)
        total += prod // 36 + prod % 36
    return B36[(36 - total % 36) % 36]


def gstin_problem(gstin: str) -> str | None:
    """None if valid, otherwise a short reason."""
    if len(gstin) != 15:
        return f"must be 15 characters, got {len(gstin)}"
    if not GSTIN_RE.match(gstin):
        return "does not match the GSTIN pattern (state code, PAN, entity, Z, check)"
    if gstin[:2] not in VALID_STATE_CODES:
        return f"unknown state code {gstin[:2]}"
    expected = checksum_char(gstin[:14])
    if gstin[14] != expected:
        return f"check character should be {expected}, got {gstin[14]}"
    return None
