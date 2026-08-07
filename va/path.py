from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


@dataclass(frozen=True)
class PathEntry:
    date: date
    target_value: Decimal
    lower_band: Decimal
    upper_band: Decimal


class VAPath:
    def __init__(self, entries: dict[date, PathEntry]):
        self._entries = entries

    @classmethod
    def from_csv(cls, path: Path) -> "VAPath":
        if not path.is_file():
            raise ValueError(f"VA path CSV was not found: {path}")
        entries: dict[date, PathEntry] = {}
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"date", "target_value", "lower_band", "upper_band"}
            if set(reader.fieldnames or []) != required:
                raise ValueError(f"VA path CSV columns must be exactly: {', '.join(sorted(required))}")
            for line_number, row in enumerate(reader, start=2):
                try:
                    day = date.fromisoformat(row["date"].strip())
                    entry = PathEntry(
                        date=day,
                        target_value=Decimal(row["target_value"].strip()),
                        lower_band=Decimal(row["lower_band"].strip()),
                        upper_band=Decimal(row["upper_band"].strip()),
                    )
                except (ValueError, InvalidOperation, AttributeError) as exc:
                    raise ValueError(f"Invalid VA path value at CSV line {line_number}.") from exc
                if day in entries:
                    raise ValueError(f"Duplicate VA path date: {day.isoformat()}")
                if not (Decimal("0") < entry.lower_band < entry.target_value < entry.upper_band):
                    raise ValueError(f"VA path must satisfy 0 < lower < target < upper at {day.isoformat()}.")
                entries[day] = entry
        if not entries:
            raise ValueError("VA path CSV is empty.")
        return cls(entries)

    def for_date(self, day: date) -> PathEntry:
        try:
            return self._entries[day]
        except KeyError as exc:
            raise ValueError(f"No VA path entry for {day.isoformat()}.") from exc

