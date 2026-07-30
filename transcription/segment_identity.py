"""Canonical, versioned temporal identities for transcript segments."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
from typing import Any


TRANSCRIPT_SCHEMA_VERSION = 2
SEGMENT_ID_VERSION = 1
SEGMENT_ID_SCHEME = "sha256_time_range_centiseconds"


class SegmentIdentityError(ValueError):
    """Raised when a segment does not have one valid temporal identity."""


def parse_time_to_decimal(value: Any) -> Decimal:
    """Parse persisted numeric or HMS-style times without accepting bools."""

    if isinstance(value, bool):
        raise SegmentIdentityError("Transcript times must be numeric, not booleans.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise SegmentIdentityError("Transcript times must be finite.")
        result = Decimal(str(value))
    elif isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            raise SegmentIdentityError("Transcript times cannot be empty.")
        if text.startswith("-"):
            raise SegmentIdentityError("Transcript times must not be negative.")
        parts = text.split(":")
        try:
            if len(parts) == 3:
                result = Decimal(parts[0]) * 3600 + Decimal(parts[1]) * 60 + Decimal(parts[2])
            elif len(parts) == 2:
                result = Decimal(parts[0]) * 60 + Decimal(parts[1])
            elif len(parts) == 1:
                result = Decimal(parts[0])
            else:
                raise SegmentIdentityError("Transcript time has an invalid format.")
        except (InvalidOperation, ValueError) as exc:
            raise SegmentIdentityError("Transcript time has an invalid format.") from exc
    else:
        raise SegmentIdentityError("Transcript times must be numeric or strings.")
    if not result.is_finite():
        raise SegmentIdentityError("Transcript times must be finite.")
    return result


def time_to_centiseconds(value: Any) -> int:
    """Convert seconds to centiseconds using the persisted ID rounding rule."""

    seconds = parse_time_to_decimal(value)
    return int((seconds * 100).to_integral_value(rounding=ROUND_HALF_UP))


def canonical_time_range(start: Any, end: Any) -> tuple[int, int]:
    start_centiseconds = time_to_centiseconds(start)
    end_centiseconds = time_to_centiseconds(end)
    if start_centiseconds < 0:
        raise SegmentIdentityError("Transcript segment start must not be negative.")
    if end_centiseconds <= start_centiseconds:
        raise SegmentIdentityError("Transcript segment end must be after start.")
    return start_centiseconds, end_centiseconds


def canonical_segment_id(start: Any, end: Any) -> str:
    """Return the v1 ID for a segment's canonical centisecond time range."""

    start_centiseconds, end_centiseconds = canonical_time_range(start, end)
    return canonical_segment_id_from_centiseconds(start_centiseconds, end_centiseconds)


def canonical_segment_id_from_centiseconds(start_centiseconds: int, end_centiseconds: int) -> str:
    """Hash an already validated canonical time range without float conversion."""

    if isinstance(start_centiseconds, bool) or not isinstance(start_centiseconds, int):
        raise SegmentIdentityError("Segment start centiseconds must be an integer.")
    if isinstance(end_centiseconds, bool) or not isinstance(end_centiseconds, int):
        raise SegmentIdentityError("Segment end centiseconds must be an integer.")
    if start_centiseconds < 0:
        raise SegmentIdentityError("Transcript segment start must not be negative.")
    if end_centiseconds <= start_centiseconds:
        raise SegmentIdentityError("Transcript segment end must be after start.")
    payload = {
        "segment_id_version": SEGMENT_ID_VERSION,
        "start_centiseconds": start_centiseconds,
        "end_centiseconds": end_centiseconds,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"seg_v{SEGMENT_ID_VERSION}_{hashlib.sha256(encoded.encode('ascii')).hexdigest()}"


def centiseconds_to_seconds(value: int) -> float:
    return float(Decimal(int(value)) / Decimal(100))


def centiseconds_to_hms(value: int) -> str:
    """Format a canonical centisecond value in the compatibility time format."""

    centiseconds = int(value)
    if centiseconds < 0:
        raise SegmentIdentityError("Transcript times must not be negative when serialized.")
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, hundredths = divmod(remainder, 100)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{hundredths:02d}"
    return f"{minutes:02d}:{seconds:02d}.{hundredths:02d}"
