"""Vision callout parsing and client configuration."""

import pytest

from shop_drawings.extraction import (
    DesignDocument,
    PageImage,
    VisionCallout,
    build_prompt,
    parse_callouts,
    vision_client_from_env,
)
from shop_drawings.extraction import vision as vision_module
from shop_drawings.extraction.vision import (
    AnthropicVisionClient,
    VisionError,
    VisionNotConfiguredError,
    salvage_callouts,
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


def test_env_factory_is_none_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert vision_client_from_env() is None


def test_env_factory_wires_the_anthropic_client_with_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    client = vision_client_from_env()
    assert isinstance(client, AnthropicVisionClient)


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


_VALID_PAYLOAD = (
    '{"callouts": [{"text": "DEPTH 380", "page": 1, "region": [0.1, 0.2, 0.3, 0.25], '
    '"role": "depth", "part_label": null, "confidence": 0.82}]}'
)

#: The live-failure shape: valid entries up front, then the reply cut off
#: mid-object (job 20261109-174312-6f0705fa, "Expecting ',' delimiter").
_TRUNCATED_PAYLOAD = (
    '{"callouts": [\n'
    '{"text": "DEPTH 380", "page": 1, "region": [0.1, 0.2, 0.3, 0.25], '
    '"role": "depth", "part_label": null, "confidence": 0.82},\n'
    '{"text": "CUT OFF MID-OBJECT", "page": 1, "regio'
)

_DOCUMENT = DesignDocument(
    source_name="sketch.png",
    pages=(PageImage(page=1, png=b"\x89PNG fake"),),
    blocks=(),
)


def _client_with_replies(
    monkeypatch: pytest.MonkeyPatch, replies: list[str]
) -> tuple[AnthropicVisionClient, list[str]]:
    """An ``AnthropicVisionClient`` whose API round trips replay ``replies``."""
    calls: list[str] = []

    def fake_request_text(client: object, model: str, document: DesignDocument) -> str:
        reply = replies[len(calls)]
        calls.append(reply)
        return reply

    monkeypatch.setattr(vision_module, "_request_text", fake_request_text)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    return AnthropicVisionClient(model="claude-test-model"), calls


def test_malformed_reply_is_retried_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client_with_replies(monkeypatch, ['{"callouts": [{', _VALID_PAYLOAD])
    callouts = client.extract(_DOCUMENT)
    assert len(calls) == 2
    assert [c.text for c in callouts] == ["DEPTH 380"]


def test_valid_first_reply_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    client, calls = _client_with_replies(monkeypatch, [_VALID_PAYLOAD])
    callouts = client.extract(_DOCUMENT)
    assert len(calls) == 1
    assert [c.text for c in callouts] == ["DEPTH 380"]


def test_salvage_keeps_fragments_when_the_retry_is_also_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client_with_replies(monkeypatch, ['{"callouts": [{', _TRUNCATED_PAYLOAD])
    # No exception escapes: the complete entry from the broken reply survives.
    callouts = client.extract(_DOCUMENT)
    assert len(calls) == 2
    assert [c.text for c in callouts] == ["DEPTH 380"]
    assert callouts[0].page == 1
    assert callouts[0].region == (0.1, 0.2, 0.3, 0.25)


def test_persistent_malformed_replies_raise_when_nothing_salvages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, calls = _client_with_replies(
        monkeypatch, ['{"callouts": [{', "I could not read this drawing, sorry."]
    )
    with pytest.raises(VisionError):
        client.extract(_DOCUMENT)
    assert len(calls) == 2


def test_salvage_skips_incomplete_fragments() -> None:
    callouts = salvage_callouts(_TRUNCATED_PAYLOAD)
    assert [c.text for c in callouts] == ["DEPTH 380"]
    # Nothing decodable at all — salvage yields nothing, error path stands.
    assert salvage_callouts("I could not read this drawing, sorry.") == ()
