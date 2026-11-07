"""Vision callout parsing and client configuration."""

import pytest

from shop_drawings.extraction import (
    DesignDocument,
    PageImage,
    VisionCallout,
    build_prompt,
    parse_callouts,
)
from shop_drawings.extraction.vision import (
    AnthropicVisionClient,
    VisionError,
    VisionNotConfiguredError,
)


def test_parse_callouts_reads_the_documented_shape() -> None:
    payload = (
        '{"callouts": ['
        '{"text": "DEPTH 380", "page": 1, "region": [0.1, 0.2, 0.3, 0.25], '
        '"role": "depth", "part_label": "SIDE PANEL", "confidence": 0.82},'
        '{"text": "18MM OAK", "page": 1, "region": [0.1, 0.3, 0.3, 0.35], '
        '"role": "material", "part_label": null, "confidence": 0.91}]}'
    )
    callouts = parse_callouts(payload)
    assert len(callouts) == 2
    first = callouts[0]
    assert first.text == "DEPTH 380"
    assert first.region == (0.1, 0.2, 0.3, 0.25)
    assert first.role == "depth"
    assert first.part_label == "SIDE PANEL"
    assert first.confidence == 0.82
    assert callouts[1].part_label is None


def test_parse_callouts_accepts_fenced_json() -> None:
    payload = (
        '```json\n{"callouts": [{"text": "500", "page": 2, '
        '"region": [0.1, 0.1, 0.2, 0.2], "role": "width", '
        '"part_label": null, "confidence": 0.5}]}\n```'
    )
    callouts = parse_callouts(payload)
    assert len(callouts) == 1
    assert callouts[0].page == 2


def test_malformed_entries_are_skipped_not_fatal() -> None:
    payload = (
        '{"callouts": ['
        '"a bare string",'
        '{"text": "   ", "page": 1, "region": [0, 0, 1, 1], "role": null, "confidence": 0.5},'
        '{"text": "no region", "page": 1, "role": "width", "confidence": 0.5},'
        '{"text": "short region", "page": 1, "region": [0.1, 0.1, 0.2], '
        '"role": "width", "confidence": 0.5},'
        '{"text": "page zero", "page": 0, "region": [0.1, 0.1, 0.2, 0.2], '
        '"role": "width", "confidence": 0.5},'
        '{"text": "degenerate", "page": 1, "region": [0.2, 0.2, 0.2, 0.5], '
        '"role": "width", "confidence": 0.5},'
        '{"text": "KEEP 1", "page": 1, "region": [0.1, 0.1, 0.2, 0.2], '
        '"role": "width", "confidence": 0.5}'
        "]}"
    )
    callouts = parse_callouts(payload)
    assert [c.text for c in callouts] == ["KEEP 1"]


def test_out_of_range_regions_are_clamped_into_the_page() -> None:
    payload = (
        '{"callouts": [{"text": "X", "page": 1, "region": [-0.1, 0, 1.3, 0.5], '
        '"role": "width", "part_label": null, "confidence": 0.9}]}'
    )
    callouts = parse_callouts(payload)
    assert callouts[0].region == (0.0, 0.0, 1.0, 0.5)


def test_unknown_roles_come_back_as_none() -> None:
    payload = (
        '{"callouts": [{"text": "SOMETHING", "page": 1, "region": [0, 0, 1, 1], '
        '"role": "decorative_flourish", "part_label": null, "confidence": 0.9}]}'
    )
    callouts = parse_callouts(payload)
    assert callouts[0].role is None


def test_response_without_json_raises() -> None:
    with pytest.raises(VisionError):
        parse_callouts("I could not read this drawing, sorry.")


def test_response_with_broken_json_raises() -> None:
    with pytest.raises(VisionError):
        parse_callouts('{"callouts": [{')


def test_callouts_not_a_list_raises() -> None:
    with pytest.raises(VisionError):
        parse_callouts('{"callouts": "everything"}')


def test_prompt_names_the_roles_and_coordinate_system() -> None:
    prompt = build_prompt()
    assert "overall_width" in prompt
    assert "part_label" in prompt
    assert "TOP-LEFT" in prompt
    assert "confidence" in prompt


def test_client_without_a_key_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = AnthropicVisionClient(model="claude-test-model")
    document = DesignDocument(
        source_name="sketch.png",
        pages=(PageImage(page=1, png=b"\x89PNG fake"),),
        blocks=(),
    )
    with pytest.raises(VisionNotConfiguredError):
        client.extract(document)


def test_callout_rejects_bad_confidence() -> None:
    with pytest.raises(ValueError):
        VisionCallout(
            text="X",
            page=1,
            region=(0.0, 0.0, 1.0, 1.0),
            role=None,
            part_label=None,
            confidence=1.5,
        )
