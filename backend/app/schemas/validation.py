from enum import StrEnum

from pydantic import BaseModel


class Severity(StrEnum):
    error = "error"  # blocks auto-approval
    warning = "warning"  # shown to the reviewer, does not block


class ValidationIssue(BaseModel):
    check: str  # e.g. "tax_maths"
    field: str | None  # field the issue is about, if any
    severity: Severity
    message: str
    # True when a misread is a plausible cause, so re-extracting with feedback may fix it.
    # False for facts about the world (unknown vendor, duplicate) that no re-read can change.
    fixable: bool
