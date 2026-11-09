"""Fixtures for extraction tests: the representative cabinet drawing PDF."""

from collections.abc import Callable
from pathlib import Path

import pytest

from shop_drawings.extraction import DesignDocument, VisionCallout
from tests.fixtures.cabinet_pdf import create_cabinet_pdf


@pytest.fixture(scope="session")
def cabinet_pdf(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The representative A3 cabinet drawing, rendered once per session."""
    return create_cabinet_pdf(tmp_path_factory.mktemp("cabinet") / "entry-cabinet-001.pdf")


class StubVisionClient:
    """A ``VisionClient`` that returns canned callouts — no network."""

    def __init__(self, *callouts: VisionCallout) -> None:
        self._callouts = callouts

    def extract(self, document: DesignDocument) -> tuple[VisionCallout, ...]:
        return self._callouts


@pytest.fixture
def stub_vision() -> Callable[..., StubVisionClient]:
    """Build a stub vision client from canned ``VisionCallout`` args."""

    def factory(*callouts: VisionCallout) -> StubVisionClient:
        return StubVisionClient(*callouts)

    return factory
