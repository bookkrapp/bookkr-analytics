"""PII masking applied to everything copied out of the source database.

Regex masking catches emails, phone numbers, long digit runs (cards, IDs) and
passport-like codes. It does NOT catch personal names inside free text — that
needs NER/LLM masking (planned for the Friction Finder phase).
"""
import json
import re
from typing import Any

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
LONG_DIGITS = re.compile(r"(?<!\d)\d{11,19}(?!\d)")
PASSPORT = re.compile(r"\b[A-PR-WYa-pr-wy][1-9]\d\s?\d{4}[1-9]\b")

# JSON keys whose values are always dropped (personal data).
PII_KEYS = {
    "travellers", "travelers", "passengers_detail", "email", "mobile", "phone", "contactEmail",
    "contactPhone", "address", "registeredName", "dob", "dateOfBirth", "passport", "passportNumber",
    "passportNo", "gstInfo", "gstNumber", "deliveryInfo", "firstName", "lastName", "fullName",
    "emails", "contacts", "phoneNumber",
}


def mask_text(s: str | None, max_len: int | None = None) -> str | None:
    if not isinstance(s, str):  # None / NaN from pandas
        return None
    s = EMAIL.sub("[email]", s)
    s = PHONE.sub("[phone]", s)
    s = LONG_DIGITS.sub("[number]", s)
    s = PASSPORT.sub("[id]", s)
    if max_len and len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def mask_obj(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: ("[redacted]" if k in PII_KEYS else mask_obj(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [mask_obj(x) for x in v]
    if isinstance(v, str):
        return mask_text(v)
    return v


def mask_json(v: Any) -> str | None:
    """Mask a JSON value (dict/list/str from psycopg) and return it serialised."""
    if v is None or (isinstance(v, float) and v != v):  # None / NaN
        return None
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return json.dumps(mask_text(v))
    return json.dumps(mask_obj(v), ensure_ascii=False, default=str)
