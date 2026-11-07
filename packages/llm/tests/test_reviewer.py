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
            ReviewImage("ref", "reference", "image/png", pixels, class_id="target"),
        ),
        "Visible extents only; preserve unknown targets.",
    )


@pytest.fixture
def two_class_bundle(bundle: ReviewBundle) -> ReviewBundle:
    output = io.BytesIO()
    Image.new("RGB", (10, 10), (200, 90, 150)).save(output, format="PNG")
    images = (
        bundle.images[0],
        replace(bundle.images[1], evidence_id="e-9f"),
        ReviewImage("e-2c", "reference", "image/png", output.getvalue(), class_id="other"),
    )
    return replace(
        bundle,
        context=replace(
            bundle.context,
            class_ids=frozenset({"target", "other"}),
            evidence_ids=frozenset(image.evidence_id for image in images),
        ),
        classes=(*bundle.classes, ClassDefinition("other", "Another permitted object")),
        images=images,
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
        {"evidence_id": "ref", "kind": "reference", "class_id": "target"},
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


def test_reference_class_bindings_survive_both_wire_formats(
    reviewer: LLMReviewer,
    two_class_bundle: ReviewBundle,
) -> None:
    prepared = reviewer.prepare(two_class_bundle)
    content = json.loads(prepared.body)["messages"][-1]["content"]
    prompt = json.loads(content[0]["text"])
    assert prompt["images"] == [
        {"evidence_id": "scene", "kind": "full_image"},
        {"evidence_id": "e-9f", "kind": "reference", "class_id": "target"},
        {"evidence_id": "e-2c", "kind": "reference", "class_id": "other"},
    ]
    images = two_class_bundle.images
    swapped = replace(
        two_class_bundle,
        images=(
            images[0],
            replace(images[1], class_id="other"),
            replace(images[2], class_id="target"),
        ),
    )
    swapped_prepared = reviewer.prepare(swapped)
    swapped_content = json.loads(swapped_prepared.body)["messages"][-1]["content"]
    assert prepared.body != swapped_prepared.body
    assert content[1:] == swapped_content[1:]
    assert json.loads(swapped_content[0]["text"])["images"] == [
        prompt["images"][0],
        {"evidence_id": "e-9f", "kind": "reference", "class_id": "other"},
        {"evidence_id": "e-2c", "kind": "reference", "class_id": "target"},
    ]


@pytest.mark.parametrize("class_id", [None, "", " ", True, 1, []])
def test_reference_rejects_missing_or_malformed_label(bundle: ReviewBundle, class_id: Any) -> None:
    with pytest.raises(ValueError):
        replace(bundle.images[1], class_id=class_id)


def test_reference_rejects_unknown_class(two_class_bundle: ReviewBundle) -> None:
    images = two_class_bundle.images
    with pytest.raises(ValueError, match="reference class labels.*pinned ontology"):
        replace(
            two_class_bundle,
            images=(images[0], replace(images[1], class_id="unapproved"), images[2]),
        )


@pytest.mark.parametrize("kind", ["full_image", "target_crop", "context_crop"])
def test_non_reference_rejects_inconsistent_class_label(bundle: ReviewBundle, kind: Any) -> None:
    with pytest.raises(ValueError, match="only reference evidence"):
        replace(bundle.images[1], kind=kind)


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


@pytest.mark.parametrize(
    "metadata",
    [
        {"service_tier": "standard"},
        {"inference_geo": "global"},
        {"speed": "standard"},
        {"cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}},
        {"server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}},
        {
            "service_tier": "standard",
            "inference_geo": "global",
            "speed": "standard",
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        },
    ],
)
def test_unmapped_messages_pricing_metadata_keeps_estimate(
    metadata: dict[str, Any],
) -> None:
    reviewer = AnthropicMessagesReviewer("fixture-model")
    usage = {"input_tokens": 120, "output_tokens": 60, **metadata}
    reply = reviewer.parse(response(reviewer, usage=usage))
    assert reply.status == "complete" and reply.patch == patch()
    assert reply.usage is None


@pytest.mark.parametrize(
    "tier", ["flex", "priority", "default", "auto", "unknown", None, 0, {}, []]
)
@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
def test_chat_envelope_service_tier_keeps_usage_unconfirmed(tier: Any, finish_reason: str) -> None:
    reviewer = OpenAIChatReviewer("fixture-model")
    data = json.loads(response(reviewer, service_tier=tier))
    data["choices"][0]["finish_reason"] = finish_reason
    reply = reviewer.parse(json.dumps(data).encode())
    assert (
        reply.status
        == {"stop": "complete", "length": "truncated", "content_filter": "refused"}[finish_reason]
    )
    assert reply.patch == (patch() if finish_reason == "stop" else None)
    assert reply.usage is None
    del data["service_tier"]
    control = reviewer.parse(json.dumps(data).encode())
    assert control.status == reply.status and control.patch == reply.patch
    assert control.usage == TokenUsage(120, 60)


def test_chat_explicit_refusal_with_service_tier_keeps_usage_unconfirmed() -> None:
    reviewer = OpenAIChatReviewer("fixture-model")
    data = json.loads(response(reviewer, service_tier="priority"))
    data["choices"][0]["message"]["refusal"] = "Cannot inspect"
    reply = reviewer.parse(json.dumps(data).encode())
    assert reply.status == "refused" and reply.patch is None and reply.usage is None


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


@pytest.mark.parametrize("malformed", [{}, {"text": None}, {"text": 1}, {"text": []}])
@pytest.mark.parametrize("malformed_first", [False, True])
def test_malformed_text_block_invalidates_otherwise_complete_reply(
    malformed: dict[str, Any], malformed_first: bool
) -> None:
    reviewer = AnthropicMessagesReviewer("fixture-model")
    blocks = [{"type": "text", "text": patch()}, {"type": "text", **malformed}]
    if malformed_first:
        blocks.reverse()
    with pytest.raises(ReviewerError, match="^unsupported_response$"):
        reviewer.parse(response(reviewer, content=blocks))


@pytest.mark.parametrize("kind", [[], {}, None, True, 1])
def test_malformed_messages_block_type_uses_safe_error(kind: Any) -> None:
    reviewer = AnthropicMessagesReviewer("fixture-model")
    with pytest.raises(ReviewerError, match="^unsupported_response$"):
        reviewer.parse(response(reviewer, content=[{"type": kind}]))


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


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool_calls", {}),
        ("tool_calls", False),
        ("tool_calls", 0),
        ("tool_calls", ""),
        ("tool_calls", "private-canary"),
        ("tool_calls", [{"function": {"name": "write"}}]),
        ("function_call", False),
        ("function_call", 0),
        ("function_call", ""),
        ("function_call", []),
        ("function_call", {}),
        ("function_call", {"name": "write", "arguments": "{}"}),
        ("refusal", []),
        ("refusal", {}),
        ("refusal", False),
        ("refusal", True),
        ("refusal", 0),
        ("refusal", 1),
    ],
)
@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
def test_chat_rejects_malformed_or_active_control_fields(
    field: str, value: Any, finish_reason: str
) -> None:
    reviewer = OpenAIChatReviewer("fixture-model")
    data = json.loads(response(reviewer))
    data["choices"][0]["finish_reason"] = finish_reason
    data["choices"][0]["message"][field] = value
    with pytest.raises(ReviewerError, match="^unsupported_response$") as error:
        reviewer.parse(json.dumps(data).encode())
    assert "private-canary" not in repr(error.value)


@pytest.mark.parametrize(
    "controls",
    [
        {},
        {"tool_calls": None, "function_call": None, "refusal": None},
        {"tool_calls": [], "function_call": None, "refusal": ""},
    ],
)
@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
@pytest.mark.parametrize("with_tier", [False, True])
def test_chat_supported_inactive_controls_preserve_status_and_usage(
    controls: dict[str, Any], finish_reason: str, with_tier: bool
) -> None:
    reviewer = OpenAIChatReviewer("fixture-model")
    data = json.loads(response(reviewer))
    data["choices"][0]["finish_reason"] = finish_reason
    data["choices"][0]["message"].update(controls)
    if with_tier:
        data["service_tier"] = "priority"
    reply = reviewer.parse(json.dumps(data).encode())
    assert (
        reply.status
        == {"stop": "complete", "length": "truncated", "content_filter": "refused"}[finish_reason]
    )
    assert reply.patch == (patch() if finish_reason == "stop" else None)
    assert reply.usage == (None if with_tier else TokenUsage(120, 60))


@pytest.mark.parametrize("finish_reason", ["stop", "length", "content_filter"])
@pytest.mark.parametrize("with_tier", [False, True])
def test_chat_valid_refusal_discards_patch_and_preserves_usage(
    finish_reason: str, with_tier: bool
) -> None:
    reviewer = OpenAIChatReviewer("fixture-model")
    data = json.loads(response(reviewer, "not a patch"))
    data["choices"][0]["finish_reason"] = finish_reason
    data["choices"][0]["message"].update(
        {"tool_calls": [], "function_call": None, "refusal": "Cannot inspect"}
    )
    if with_tier:
        data["service_tier"] = "priority"
    reply = reviewer.parse(json.dumps(data).encode())
    assert reply.status == "refused" and reply.patch is None
    assert reply.usage == (None if with_tier else TokenUsage(120, 60))


def test_bundle_refuses_wrong_source_missing_overview_and_unapproved_evidence(
    bundle: ReviewBundle,
) -> None:
    with pytest.raises(ValueError, match="full source image"):
        replace(
            bundle,
            images=(
                replace(bundle.images[0], kind="reference", class_id="target"),
                bundle.images[1],
            ),
        )
    with pytest.raises(ValueError, match="pinned source"):
        replace(bundle, images=(replace(bundle.images[0], data=b"different"), bundle.images[1]))
    with pytest.raises(ValueError, match="unique available"):
        replace(bundle, images=(bundle.images[0],))
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
    images = (bundle.images[0],) + tuple(
        ReviewImage(
            f"ref-{index}", "reference", "image/png", b"x" * MAX_IMAGE_BYTES, class_id="target"
        )
        for index in range(3)
    )
    context = replace(bundle.context, evidence_ids=frozenset(item.evidence_id for item in images))
    with pytest.raises(ValueError, match="combined image"):
        replace(bundle, context=context, images=images)
    classes = tuple(ClassDefinition(f"class-{index}", "x" * 2048) for index in range(128))
    context = replace(bundle.context, class_ids=frozenset(item.class_id for item in classes))
    large = replace(
        bundle,
        context=context,
        classes=classes,
        images=(bundle.images[0], replace(bundle.images[1], class_id="class-0")),
    )
    with pytest.raises(ValueError, match="prompt exceeds"):
        OpenAIChatReviewer("fixture-model").prepare(large)
    for cap in (True, 0, -1, 16_385, 1.5):
        for cls in (OpenAIChatReviewer, AnthropicMessagesReviewer):
            with pytest.raises(ValueError, match="token cap"):
                cls("fixture-model", cap)  # type: ignore[arg-type]
    reply = OpenAIChatReviewer("fixture-model").parse(response(OpenAIChatReviewer("fixture-model")))
    assert "full_image_inspected" not in repr(reply)
    assert "data=" not in repr(bundle.images[0])
    assert "data=" not in repr(bundle)
