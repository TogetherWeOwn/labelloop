from dataclasses import astuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

from labelloop_core.geometry import Box, Crop, full_frame_transform, normalize_box, target_flags


@given(
    width=st.integers(2, 4096),
    height=st.integers(2, 4096),
    output_size=st.integers(2, 1280),
    data=st.data(),
)
def test_unclipped_box_affine_and_normalized_round_trip(width, height, output_size, data):
    x_min = data.draw(st.floats(0, width - 1))
    y_min = data.draw(st.floats(0, height - 1))
    x_max = data.draw(st.floats(x_min + 1e-6, width))
    y_max = data.draw(st.floats(y_min + 1e-6, height))
    assert x_max > x_min and y_max > y_min
    box = Box(x_min, y_min, x_max, y_max)
    transform = full_frame_transform(width, height, output_size)
    mapped = transform.apply(box)
    assert mapped is not None
    restored = transform.restore(mapped)
    assert restored is not None
    for value, target in zip(astuple(restored), astuple(box), strict=True):
        assert value == pytest.approx(target)
    normalized = normalize_box(mapped, output_size, output_size)
    recovered = Box(
        (normalized[0] - normalized[2] / 2) * output_size,
        (normalized[1] - normalized[3] / 2) * output_size,
        (normalized[0] + normalized[2] / 2) * output_size,
        (normalized[1] + normalized[3] / 2) * output_size,
    )
    recovered_source = transform.restore(recovered)
    assert recovered_source is not None
    for value, target in zip(astuple(recovered_source), astuple(box), strict=True):
        assert value == pytest.approx(target)
    for axis in range(2):
        assert transform.forward[axis][axis] * transform.inverse[axis][axis] == pytest.approx(1)
    for point in ((20.0, 10.0), (0.0, 0.0), (float(width), float(height))):
        assert transform.points(*transform.points(*point), inverse=True) == pytest.approx(point)


@given(crop_x=st.integers(0, 79), output_size=st.integers(16, 640))
def test_crop_recover_only_retained_source_intersection(crop_x, output_size):
    transform = full_frame_transform(200, 150, output_size, crop=Crop(crop_x, 0, 100, 150))
    original = Box(10, 10, 90, 120)
    mapped = transform.apply(original)
    if mapped is not None:
        restored = transform.restore(mapped)
        expected = original.intersect(transform.crop.box)
        assert restored is not None and expected is not None
        for value, target in zip(astuple(restored), astuple(expected), strict=True):
            assert value == pytest.approx(target)
    if crop_x > 80:
        assert "clipped" in target_flags(transform, original, 1)


def test_actual_gain_and_padding_clip():
    transform = full_frame_transform(20, 10, 4)
    assert (transform.resized_width, transform.resized_height) == (4, 2)
    assert (transform.scale_x, transform.scale_y) == (4 / 20, 2 / 10)
    assert transform.apply(Box(0, 0, 20, 10)) == Box(0, 1, 4, 3)
    assert transform.restore(Box(0, 0, 1, 1)) is None


def test_outside_crop_and_tiny_targets():
    transform = full_frame_transform(400, 300, 320)
    cropped = full_frame_transform(400, 300, 320, crop=Crop(40, 0, 160, 150))
    assert cropped.apply(Box(0, 0, 10, 10)) is None
    assert cropped.restore(Box(0, 0, max(1, cropped.left), 10)) is None
    assert "below_min_target_px" in target_flags(transform, Box(0, 0, 1, 1), 2)
    with pytest.raises(ValueError):
        Box(0, 0, 0, 1)
