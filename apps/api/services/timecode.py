from __future__ import annotations

import math
from typing import Any


def parse_timecode(value: Any, *, field_name: str = "time") -> float:
    """Parse HH:MM:SS.s, MM:SS.s, or legacy numeric seconds."""
    if value is None or value == "":
        raise ValueError(f"Missing required time field: {field_name}")
    try:
        if isinstance(value, str) and ":" in value:
            parts = value.strip().replace(",", ".").split(":")
            if len(parts) == 2:
                result = float(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                result = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            else:
                raise ValueError
        else:
            result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid time value for {field_name}: {value}") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"Invalid time value for {field_name}: {value}")
    return result


def format_timecode(seconds: float, *, duration: bool = False) -> str:
    value = parse_timecode(seconds)
    total_tenths = int(round(value * 10))
    whole, tenths = divmod(total_tenths, 10)
    minutes, secs = divmod(whole, 60)
    if duration:
        return f"{minutes:02d}:{secs:02d}.{tenths}" if minutes < 60 else f"{minutes:02d}:{secs:02d}.{tenths}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{tenths}"
