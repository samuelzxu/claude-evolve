"""Program dataclass for claude-evolve database."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Program:
    """Represents an evolved program stored in the database."""

    id: int
    code: str
    combined_score: float = 0.0
    correct: bool = False
    generation: int = 0
    parent_id: Optional[int] = None
    island_idx: int = 0
    patch_type: str = "full"
    metadata: dict = field(default_factory=dict)
    embedding: Optional[list[float]] = None
    text_feedback: Optional[str] = None
    status: str = "ok"  # "ok", "failed", "eval_failed"
    created_at: str = ""  # ISO timestamp
    children_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dict, cleaning non-finite floats."""
        return {
            "id": self.id,
            "code": self.code,
            "combined_score": _clean_float(self.combined_score),
            "correct": self.correct,
            "generation": self.generation,
            "parent_id": self.parent_id,
            "island_idx": self.island_idx,
            "patch_type": self.patch_type,
            "metadata": _clean_dict(self.metadata),
            "embedding": self.embedding,
            "text_feedback": self.text_feedback,
            "status": self.status,
            "created_at": self.created_at,
            "children_count": self.children_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Program":
        """Create a Program from a dict (e.g., from DB row)."""
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        # Coerce types that SQLite may return as ints
        for bool_field in ("correct",):
            if bool_field in filtered:
                filtered[bool_field] = bool(filtered[bool_field])
        return cls(**filtered)


def _clean_float(value: Any) -> Any:
    """Replace NaN/Inf with None for JSON safety."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _clean_dict(obj: Any) -> Any:
    """Recursively clean non-finite floats in a structure."""
    if isinstance(obj, dict):
        return {k: _clean_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_dict(v) for v in obj]
    return _clean_float(obj)
