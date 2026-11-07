"""Synthetic, licence-clean wire fixtures; no provider requests or credentials."""

import hashlib
import io
import json
from dataclasses import replace
from typing import Any

import pytest
from PIL import Image

from labelloop_core.budget import TokenPrices, TokenUsage
from labelloop_core.domain import AnnotationRevision, SourceAsset
from labelloop_core.review import CorrectionLoop, ReviewContext, decode_patch
from labelloop_llm.reviewer import (
    MAX_IMAGE_BYTES,
    MAX_RESPONSE_BYTES,
    AnthropicMessagesReviewer,
    ClassDefinition,
    LLMReviewer,
    OpenAIChatReviewer,
    ReviewBundle,
    ReviewerError,
    ReviewImage,
)


@pytest.fixture
def bundle() -> ReviewBundle:
    output = io.BytesIO()
    Image.new("RGB", (20, 10), (90, 150, 200)).save(output, format="PNG")
    pixels = output.getvalue()
    digest = hashlib.sha256(pixels).hexdigest()
    asset = SourceAsset("image", "project", digest, f"sha256/{digest[:2]}/{digest}", 20, 10)
    context = ReviewContext(
        asset,
        "ontology-v1",
        frozenset({"target"}),
        frozenset({"scene", "ref"}),
        "scene_object",
        "discovery-1",
        "review-1",
        discovery_complete=True,
    )
    return ReviewBundle(
        context,
        AnnotationRevision(1, (), "model"),
        (ClassDefinition("target", "A target object"),),
        (
            ReviewImage("scene", "full_image", "image/png", pixels),
            ReviewImage("ref", "reference", "image/png", pixels),
        ),
        "Visible extents only; preserve unknown targets.",
    )


@pytest.fixture(params=["chat", "messages"])
def reviewer(request: pytest.FixtureRequest) -> LLMReviewer:
    if request.param == "chat":
        return OpenAIChatReviewer("fixture-model")
    return AnthropicMessagesReviewer("fixture-model")


def patch(**changes: Any) -> str:
    return json.dumps(
        {
            "asset_id": "image",
            "expected_revision": 1,
            "operations": [],
            "full_image_inspected": True,
            "output_truncated": False,
            "unresolved_regions": [],
            **changes,
        }
    )


def response(reviewer: LLMReviewer, text: str | None = None, **changes: Any) -> bytes:
    text = patch() if text is None else text
    if isinstance(reviewer, OpenAIChatReviewer):
        data = {
            "model": "fixture-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": text,
                    },
                }
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
        }
    else:
        data = {
            "type": "message",
            "role": "assistant",
            "model": "fixture-model",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 120, "output_tokens": 60},
        }
    return json.dumps({**data, **changes}).encode()


def test_fixture_probe_prepares_vision_and_validates_negative(
    reviewer: LLMReviewer,
    bundle: ReviewBundle,
) -> None:
    prepared = reviewer.prepare(bundle)
    body = json.loads(prepared.body)
    assert body["model"] == "fixture-model"
    assert body["max_tokens"] == 4096 and body["stream"] is False
    assert not {"tools", "headers", "api_key", "base_url", "fallbacks"} & body.keys()
    assert len(body["messages"]) == (2 if isinstance(reviewer, OpenAIChatReviewer) else 1)
    user = body["messages"][-1]
    assert user["role"] == "user"
    payload = json.loads(user["content"][0]["text"])
    assert payload["objects"] == [] and payload["expected_revision"] == 1
    assert payload["classes"] == [{"class_id": "target", "definition": "A target object"}]
    assert payload["images"] == [
        {"evidence_id": "scene", "kind": "full_image"},
        {"evidence_id": "ref", "kind": "reference"},
    ]
    assert len(user["content"]) == 5
    image = user["content"][2]
    if isinstance(reviewer, OpenAIChatReviewer):
        assert image["image_url"]["url"].startswith("data:image/png;base64,")
        assert body["response_format"] == {"type": "json_object"}
    else:
        assert image["source"]["media_type"] == "image/png"
        assert image["source"]["type"] == "base64"
        assert "system" in body
    assert "objects" not in repr(prepared)
    reply = reviewer.parse(response(reviewer))
    assert reply.status == "complete" and reply.patch is not None
    assert reply.usage == TokenUsage(120, 60)
    assert TokenPrices("fixture", 2_000_000, 4_000_000).cost(reply.usage) == 480
    loop = CorrectionLoop(bundle.current).consume(bundle.context, reply.patch)
    assert loop.status == "accepted" and loop.current.scope_completeness == "exhaustive_for_scope"
    assert loop.current.review_tier == "machine_verified_no_human_review"
    assert not loop.current.training_eligible and not loop.current.gold_eligible


def test_fixture_probe_missing_object_requires_reinspection(
    reviewer: LLMReviewer,
    bundle: ReviewBundle,
) -> None:
    proposed = patch(
        operations=[
            {
                "action": "add",
                "object_id": "found",
                "class_id": "target",
                "box": [1, 2, 5, 8],
                "reason": "Visible target outside detector proposals",
                "evidence_ids": ["scene", "ref"],
                "representation": "scene_object",
            }
        ]
    )
    reply = reviewer.parse(response(reviewer, proposed))
    assert reply.patch is not None
    assert decode_patch(reply.patch).operations[0].box is not None
    loop = CorrectionLoop(bundle.current).consume(bundle.context, reply.patch)
    assert loop.status == "running" and loop.current.scope_completeness == "partial"
    assert len(loop.current.objects) == 1


@pytest.mark.parametrize("reason", ["truncated", "refused", "unknown"])
def test_finish_reason_is_authoritative(reviewer: LLMReviewer, reason: str) -> None:
    data = json.loads(response(reviewer))
    if isinstance(reviewer, OpenAIChatReviewer):
        data["choices"][0]["finish_reason"] = {
            "truncated": "length",
            "refused": "content_filter",
            "unknown": "tool_calls",
        }[reason]
    else:
        data["stop_reason"] = {
            "truncated": "max_tokens",
            "refused": "refusal",
            "unknown": "tool_use",
        }[reason]
    raw = json.dumps(data).encode()
    if reason == "unknown":
        with pytest.raises(ReviewerError, match="^unsupported_response$"):
            reviewer.parse(raw)
    else:
        reply = reviewer.parse(raw)
        assert reply.status == reason and reply.patch is None
        assert reply.usage == TokenUsage(120, 60)


def test_incomplete_json_is_not_repaired(reviewer: LLMReviewer) -> None:
    with pytest.raises(ReviewerError, match="^invalid_patch$"):
        reviewer.parse(response(reviewer, '{"asset_id":'))
    for raw in (b"", b"[]", b"{", b"\xff", b"[" * 2000, b"x" * (MAX_RESPONSE_BYTES + 1)):
        with pytest.raises(ReviewerError, match="^invalid_response$"):
            reviewer.parse(raw)


@pytest.mark.parametrize(
    "changes",
    [
        {"human_accepted": True},
        {"full_image_inspected": "yes"},
        {"expected_revision": True},
        {"operations": [{"action": "execute", "endpoint": "ignored"}]},
    ],
)
def test_forbidden_patch_fields_fail_closed(reviewer: LLMReviewer, changes: dict[str, Any]) -> None:
    with pytest.raises(ReviewerError, match="^invalid_patch$"):
        reviewer.parse(response(reviewer, patch(**changes)))


def test_model_and_envelope_are_pinned(reviewer: LLMReviewer) -> None:
    for changes in ({"model": "substitute"}, {"error": {"message": "private-canary"}}):
        with pytest.raises(ReviewerError, match="^invalid_response$") as error:
            reviewer.parse(response(reviewer, **changes))
        assert "private-canary" not in str(error.value)
    raw = response(reviewer).decode().replace('"model":', '"model":"other", "model":')
    with pytest.raises(ReviewerError, match="^invalid_response$"):
        reviewer.parse(raw.encode())


@pytest.mark.parametrize(
    "status,code",
    [
        (402, "insufficient_credits"),
        (429, "provider_error"),
        (500, "provider_error"),
        (201, "provider_error"),
    ],
)
def test_http_failures_never_return_a_patch_or_echo_body(
    reviewer: LLMReviewer,
    status: int,
    code: str,
) -> None:
    with pytest.raises(ReviewerError, match=f"^{code}$") as error:
        reviewer.parse(b"private-canary", http_status=status)
    assert "private-canary" not in repr(error.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        True,
        {"input_tokens": True, "output_tokens": 1},
        {"prompt_tokens": 1, "completion_tokens": -1},
    ],
)
def test_missing_or_bad_usage_remains_unconfirmed(reviewer: LLMReviewer, value: Any) -> None:
    reply = reviewer.parse(response(reviewer, usage=value))
    assert reply.status == "complete" and reply.usage is None


def test_separately_priced_usage_remains_unconfirmed(reviewer: LLMReviewer) -> None:
    data = json.loads(response(reviewer))
    if isinstance(reviewer, OpenAIChatReviewer):
        extras = [
            {"prompt_tokens_details": {"cached_tokens": 15}},
            {"completion_tokens_details": {"reasoning_tokens": 20}},
            {"completion_tokens_details": {"audio_tokens": 1}},
            {"total_tokens": 999},
            {"new_billing_category": 1},
        ]
        zero = {"prompt_tokens_details": {"cached_tokens": 0}}
    else:
        extras = [
            {"cache_read_input_tokens": 10},
            {"cache_creation_input_tokens": 10},
            {"iterations": [{"input_tokens": 1}]},
            {"server_tool_use": {"web_search": 1}},
        ]
        zero = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for extra in extras:
        raw = response(reviewer, usage={**data["usage"], **extra})
        assert reviewer.parse(raw).usage is None
    assert reviewer.parse(response(reviewer, usage={**data["usage"], **zero})).usage == TokenUsage(
        120, 60
    )


def test_text_blocks_are_joined_but_tools_are_never_executed() -> None:
    reviewer = AnthropicMessagesReviewer("fixture-model")
    raw_patch = patch()
    blocks = [
        {"type": "thinking", "thinking": "ignored"},
        {"type": "text", "text": raw_patch[:20]},
        {"type": "text", "text": raw_patch[20:]},
    ]
    assert reviewer.parse(response(reviewer, content=blocks)).patch == raw_patch
    for block in ({"type": "tool_use", "name": "write"}, {"type": "fallback"}, 1):
        with pytest.raises(ReviewerError):
            reviewer.parse(response(reviewer, content=[block]))
    with pytest.raises(ReviewerError):
        reviewer.parse(response(reviewer, content=[]))


def test_chat_rejects_ambiguous_choices_tools_and_accepts_refusal() -> None:
    reviewer = OpenAIChatReviewer("fixture-model")
    data = json.loads(response(reviewer))
    for choices in ([], data["choices"] * 2, [{}], [1]):
        with pytest.raises(ReviewerError):
            reviewer.parse(response(reviewer, choices=choices))
    data["choices"][0]["message"]["tool_calls"] = [{"function": {"name": "write"}}]
    with pytest.raises(ReviewerError, match="^unsupported_response$"):
        reviewer.parse(json.dumps(data).encode())
    data["choices"][0]["message"].pop("tool_calls")
    data["choices"][0]["message"]["refusal"] = "Cannot inspect"
    assert reviewer.parse(json.dumps(data).encode()).status == "refused"


def test_bundle_refuses_wrong_source_missing_overview_and_unapproved_evidence(
    bundle: ReviewBundle,
) -> None:
    with pytest.raises(ValueError, match="full source image"):
        replace(bundle, images=(bundle.images[1],))
    with pytest.raises(ValueError, match="pinned source"):
        replace(bundle, images=(replace(bundle.images[0], data=b"different"),))
    with pytest.raises(ValueError, match="unique available"):
        replace(bundle, images=(*bundle.images, replace(bundle.images[1], evidence_id="private")))
    with pytest.raises(ValueError, match="unique available"):
        replace(bundle, images=(*bundle.images, bundle.images[1]))
    with pytest.raises(ValueError, match="pinned ontology"):
        replace(bundle, classes=(ClassDefinition("new", "Not approved"),))


def test_resource_caps_and_opaque_representations(bundle: ReviewBundle) -> None:
    with pytest.raises(ValueError, match="byte limit"):
        ReviewImage("large", "reference", "image/png", b"x" * (MAX_IMAGE_BYTES + 1))
    with pytest.raises(ValueError, match="counts"):
        replace(bundle, images=bundle.images * 9)
    for cap in (True, 0, -1, 16_385, 1.5):
        for cls in (OpenAIChatReviewer, AnthropicMessagesReviewer):
            with pytest.raises(ValueError, match="token cap"):
                cls("fixture-model", cap)  # type: ignore[arg-type]
    reply = OpenAIChatReviewer("fixture-model").parse(response(OpenAIChatReviewer("fixture-model")))
    assert "full_image_inspected" not in repr(reply)
    assert "data=" not in repr(bundle.images[0])
    assert "data=" not in repr(bundle)
