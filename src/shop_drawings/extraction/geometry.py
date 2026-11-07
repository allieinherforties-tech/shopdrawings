"""Normalized page-region geometry.

Source references (schema ``SourceReference``) use a normalized
``(x0, y0, x1, y1)`` crop on the page, origin at the top-left, so the same
coordinates work for PDF text layers, rendered rasters, and vision-model
responses alike. Everything in this module is pure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    """A rectangular crop, normalized to the page: 0 <= x0 < x1 <= 1."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if not (0 <= self.x0 < self.x1 <= 1 and 0 <= self.y0 < self.y1 <= 1):
            raise ValueError(
                f"region must satisfy 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1, got {self}"
            )

    def as_tuple(self) -> tuple[float, float, float, float]:
        """The schema's ``SourceReference.region`` shape."""
        return (self.x0, self.y0, self.x1, self.y1)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def padded(self, fraction: float = 0.25) -> Region:
        """Grow each side by ``fraction`` of the region's size, clamped to the page."""
        dx = self.width * fraction
        dy = self.height * fraction
        return Region(
            max(0.0, self.x0 - dx),
            max(0.0, self.y0 - dy),
            min(1.0, self.x1 + dx),
            min(1.0, self.y1 + dy),
        )

    def union(self, other: Region) -> Region:
        return Region(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def contains(self, other: Region) -> bool:
        """True when ``other``'s center lies inside this region."""
        cx, cy = other.center
        return self.x0 <= cx <= self.x1 and self.y0 <= cy <= self.y1

    def center_distance(self, other: Region) -> float:
        """Euclidean distance between centers, in normalized page units."""
        (ax, ay), (bx, by) = self.center, other.center
        return math.hypot(ax - bx, ay - by)
