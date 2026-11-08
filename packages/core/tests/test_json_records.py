"""JSON normalization preserves every accepted field and scalar type."""

import pytest

from labelloop_core.json_records import canonical_json


@pytest.mark.parametrize(
    "key,text", [(1, "1"), (1.5, "1.5"), (True, "true"), (False, "false"), (None, "null")]
)
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_normalization_rejects_colliding_object_keys(
    key: object, text: str, reverse: bool, nested: bool
) -> None:
    pairs = [(key, "original"), (text, "replacement")]
    value: object = dict(reversed(pairs) if reverse else pairs)
    if nested:
        value = {"nested": ({"values": value},)}
    with pytest.raises(ValueError, match="colliding JSON object keys"):
        canonical_json(value)


def test_normalization_retains_tuple_key_and_readback_identity() -> None:
    assert canonical_json({"values": {2: "two", 10: "ten"}, "flags": (True, False)}) == (
        b'{"flags":[true,false],"values":{"10":"ten","2":"two"}}'
    )
    assert canonical_json({"first": {1: "one"}, "second": {"1": "other"}}) == (
        b'{"first":{"1":"one"},"second":{"1":"other"}}'
    )


@pytest.mark.parametrize("first,second", [(True, 1), (False, 0)])
def test_normalization_preserves_scalar_types(first: bool, second: int) -> None:
    assert canonical_json({"nested": [first]}) != canonical_json({"nested": [second]})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_normalization_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"nested": [value]})
