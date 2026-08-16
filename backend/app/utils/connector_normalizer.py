"""
Normalizes the station CSV's free-text connector_types field (~30 raw
variants: casing/formatting differences, comma-separated multi-connector
entries, ambiguous/brand-name strings) into a small fixed taxonomy, so
route_optimizer can check connector compatibility against a vehicle profile
instead of string-matching ~30 variants directly.

Bucket meanings:
- CCS2: CCS Type 2 DC fast charging -- the current Indian/global EV DC
  fast-charging standard.
- AC_TYPE2: Type 2 AC charging (socket or tethered) -- the standard AC
  connector in India.
- CHADEMO: CHAdeMO DC fast charging (legacy Nissan/Japanese standard,
  still present at some Indian stations).
- BHARAT_DC: Bharat DC-001, the Indian-specific (GB/T-derived) DC fast
  standard -- a distinct physical connector from CCS2, not interchangeable.
- TYPE1_16A: basic/legacy single-phase AC sockets -- 16A AC, AC Type 1,
  domestic 3-pin (3PIN-15AMP), and CEE/IEC 60309 3-pin (the same physical
  plug standard, just phrased two different ways in the source data).
- UNKNOWN: "Unknown", missing, or a string that doesn't name a real
  standard ("DC fast charger" -- no named standard; "Relux FC" -- a
  charger brand, not a connector standard). These are deliberately *not*
  guessed into a real bucket: a plug we can't identify should never be
  silently treated as compatible (or silently dropped) -- it's surfaced
  as UNKNOWN so callers can flag it rather than assume either way.
"""

import math
from typing import Optional

CCS2 = "CCS2"
AC_TYPE2 = "AC_TYPE2"
CHADEMO = "CHADEMO"
BHARAT_DC = "BHARAT_DC"
TYPE1_16A = "TYPE1_16A"
UNKNOWN = "UNKNOWN"

ALL_CONNECTOR_BUCKETS = (CCS2, AC_TYPE2, CHADEMO, BHARAT_DC, TYPE1_16A, UNKNOWN)

# Lookup is on the individual token after a multi-connector cell is split on
# "," and each piece is stripped/casefolded -- this is what collapses
# formatting variants like "16A AC" / "16A (AC)" into one bucket.
_TOKEN_TO_BUCKET = {
    "ccs (type 2)": CCS2,
    "ccs2 (dc)": CCS2,
    "ccs2": CCS2,

    "type 2 (ac)": AC_TYPE2,
    "ac type 2": AC_TYPE2,
    "ac type 2 (level 2)": AC_TYPE2,
    "type 2 (tethered connector)": AC_TYPE2,
    "type 2 (tethered)": AC_TYPE2,

    "chademo": CHADEMO,
    "chademo (dc)": CHADEMO,

    "bharat dc-001": BHARAT_DC,

    "16a (ac)": TYPE1_16A,
    "16a ac": TYPE1_16A,
    "ac type 1": TYPE1_16A,
    "3pin-15amp (level 1)": TYPE1_16A,
    "cee 3 pin": TYPE1_16A,
    "iec 60309 3-pin": TYPE1_16A,  # same physical plug as "CEE 3 Pin", different phrasing
}


def normalize_connector_types(raw: Optional[str]) -> set[str]:
    """
    Parse one station's raw connector_types cell into a set of normalized
    buckets. Comma-separated multi-connector stations produce multiple
    buckets (any one of them being compatible is enough). A token that
    doesn't match a known standard -- including a missing cell or the
    literal "Unknown" -- maps to {UNKNOWN}, never guessed into a real
    standard (see module docstring).
    """
    if raw is None or (isinstance(raw, float) and math.isnan(raw)):
        return {UNKNOWN}

    raw = str(raw).strip()
    if not raw or raw.lower() == "unknown":
        return {UNKNOWN}

    buckets = {_TOKEN_TO_BUCKET.get(token.strip().lower(), UNKNOWN) for token in raw.split(",") if token.strip()}
    return buckets or {UNKNOWN}
