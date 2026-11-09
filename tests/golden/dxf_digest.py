"""Canonical DXF comparison for golden files.

An ezdxf-written sheet is not byte-stable, and the variation is not just
timestamps: the HEADER section carries writer dates ($TDCREATE…), GUIDs, and
a writer-stamp comment, and the CLASSES section is written in hash-seed
order, so class entries swap position between processes. None of that is
fabrication content. The ENTITIES section — every layer, contour, and
annotation the shop cuts from — is byte-stable between runs. The golden
check therefore digests exactly that section.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def entities_section_text(path: Path) -> str:
    """The DXF ENTITIES section: all fabrication geometry, nothing else."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = lines.index("ENTITIES")
    end = lines.index("ENDSEC", start)
    # Group-code/value pairs, one per line — keep them as-is so the digest
    # sees exactly what ezdxf wrote for the geometry.
    return "\n".join(lines[start + 1 : end]) + "\n"


def canonical_dxf_text(path: Path) -> str:
    """DXF text reduced to its stable fabrication content."""
    return entities_section_text(path)


def digest_dxf(path: Path) -> str:
    """Content digest of a DXF sheet, independent of writer bookkeeping."""
    return hashlib.sha256(canonical_dxf_text(path).encode("utf-8")).hexdigest()
