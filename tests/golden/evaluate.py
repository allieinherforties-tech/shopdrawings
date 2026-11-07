"""Golden-file evaluation metrics (blueprint §5).

Four numbers measure the extraction pipeline against a fixture's ground
truth: per-dimension extraction accuracy, association accuracy, questions
per job, and review time per job. The expectations manifest states what a
correct extraction of the fixture drawing contains; the runner scores an
extracted spec against it. Today the manifest covers the synthetic cabinet
fixture; real studio designs join as more manifests, one per job.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shop_drawings.schema import FurnitureSpec

#: Ground truth for the ReportLab cabinet drawing — every dimension the
#: drawing states, where it attaches, and how many questions it must raise.
EXPECTATIONS_FILE = Path(__file__).parent / "expectations" / "entry-cabinet-001.json"


@dataclass(frozen=True)
class Evaluation:
    """The four blueprint §5 numbers plus the mismatches that explain them."""

    extraction_accuracy: float
    association_accuracy: float
    questions_per_job: int
    review_seconds: float
    missed_values: tuple[str, ...]
    misassociated_values: tuple[str, ...]
    unexpected_values: tuple[str, ...]
    question_mismatches: tuple[str, ...]


def _expected_records(expectations: dict[str, Any]) -> list[tuple[str, str | None, str, float]]:
    """(part_id, feature_type|None, kind, value) for every dimension in the manifest."""
    records: list[tuple[str, str | None, str, float]] = []
    for part in expectations["parts"]:
        for kind, value in part["dimensions"].items():
            records.append((part["id"], None, kind, float(value)))
        for feature in part["features"]:
            for kind, value in feature["dimensions"].items():
                records.append((part["id"], feature["type"], kind, float(value)))
    return records


def _extracted_records(spec: FurnitureSpec) -> list[tuple[str, str | None, str, float]]:
    """Same shape as ``_expected_records``, from the extracted spec itself."""
    records: list[tuple[str, str | None, str, float]] = []
    for part in spec.parts:
        for kind, dim in part.dimensions.items():
            records.append((part.id, None, kind.value, dim.value))
        for feature in part.features:
            for kind, dim in feature.dimensions.items():
                records.append((part.id, feature.type.value, kind.value, dim.value))
    return records


def _miss_count(expected: Counter[str], extracted: Counter[str]) -> int:
    """How many expected entries the extracted multiset lacks, counting duplicates."""
    return sum((expected - extracted).values())


def _miss_detail(expected: Counter[str], extracted: Counter[str]) -> tuple[str, ...]:
    """Human-readable list of the missing entries."""
    return tuple(
        sorted(f"{key} (missing {count})" for key, count in sorted((expected - extracted).items()))
    )


def evaluate_extraction(
    spec: FurnitureSpec, expectations: dict[str, Any], review_seconds: float
) -> Evaluation:
    """Score an extracted spec against a fixture's ground truth."""
    expected = _expected_records(expectations)
    extracted = _extracted_records(spec)

    # Extraction accuracy: was each expected dimension read with the right
    # value, anywhere in the spec? (kind, value) matched as a multiset so a
    # repeated dimension (two parts at 380) must be found twice.
    expected_values = Counter(f"{kind}={value:g}" for _, _, kind, value in expected)
    extracted_values = Counter(f"{kind}={value:g}" for _, _, kind, value in extracted)
    value_misses = _miss_count(expected_values, extracted_values)
    extraction_accuracy = (len(expected) - value_misses) / len(expected)

    # Association accuracy: was it also attached to the right part and feature?
    expected_assoc = Counter(
        f"{part_id}/{feature or '-'}:{kind}={value:g}" for part_id, feature, kind, value in expected
    )
    extracted_assoc = Counter(
        f"{part_id}/{feature or '-'}:{kind}={value:g}"
        for part_id, feature, kind, value in extracted
    )
    assoc_misses = _miss_count(expected_assoc, extracted_assoc)
    association_accuracy = (len(expected) - assoc_misses) / len(expected)

    unexpected = tuple(
        sorted(
            f"{key} (extra {count})"
            for key, count in sorted((extracted_assoc - expected_assoc).items())
        )
    )

    # Questions: the fixture's unplaceable findings must surface as
    # questions, not guesses — neither missing nor inflated.
    expected_questions = int(expectations["unresolved_questions"])
    actual_questions = len(spec.unresolved_questions)
    question_mismatches: tuple[str, ...] = ()
    if actual_questions != expected_questions:
        question_mismatches = (
            f"expected {expected_questions} unresolved question(s), got {actual_questions}",
        )

    return Evaluation(
        extraction_accuracy=extraction_accuracy,
        association_accuracy=association_accuracy,
        questions_per_job=actual_questions,
        review_seconds=review_seconds,
        missed_values=_miss_detail(expected_values, extracted_values),
        misassociated_values=_miss_detail(expected_assoc, extracted_assoc),
        unexpected_values=unexpected,
        question_mismatches=question_mismatches,
    )


def load_expectations() -> dict[str, Any]:
    """The cabinet fixture's ground-truth manifest."""
    data: dict[str, Any] = json.loads(EXPECTATIONS_FILE.read_text(encoding="utf-8"))
    return data


def render_report(evaluation: Evaluation) -> str:
    """One-line-per-metric summary written beside the run as evidence."""
    return (
        "golden-file evaluation (blueprint §5)\n"
        f"  extraction accuracy:    {evaluation.extraction_accuracy:.3f}\n"
        f"  association accuracy:   {evaluation.association_accuracy:.3f}\n"
        f"  questions per job:      {evaluation.questions_per_job}\n"
        f"  review time (API path): {evaluation.review_seconds:.3f}s\n"
        f"  missed values:          {list(evaluation.missed_values) or 'none'}\n"
        f"  misassociated values:   {list(evaluation.misassociated_values) or 'none'}\n"
        f"  unexpected values:      {list(evaluation.unexpected_values) or 'none'}\n"
        f"  question mismatches:    {list(evaluation.question_mismatches) or 'none'}\n"
    )
