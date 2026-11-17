"""Cross-check browser preview records against the delivered original-frame geometry."""

import json
import math
import sys
from pathlib import Path

from labelloop_core.geometry import Box, full_frame_transform


def main() -> None:
    records = json.loads(Path(sys.argv[1]).read_text())
    if {record["side"] for record in records} != {320, 640} or len(records) != 2:
        raise ValueError("browser evidence must contain both independent recipe previews")
    # These are the accepted, invented design scene's canonical source coordinates.
    sources = {
        "oat-1": Box(212, 250, 352, 520),
        "tag-1": Box(560, 530, 602, 550),
        "edge-1": Box(1846, 610, 1920, 880),
    }
    for record in records:
        transform = full_frame_transform(1920, 1080, record["side"])
        if {box["id"] for box in record["boxes"]} != set(sources):
            raise ValueError("preview evidence omitted a recorded source object")
        for box in record["boxes"]:
            mapped = transform.apply(sources[box["id"]])
            if mapped is None:
                raise ValueError("fixture object unexpectedly absent from the full frame")
            expected = (mapped.x_min, mapped.y_min, mapped.width, mapped.height)
            actual = (box["x"], box["y"], box["width"], box["height"])
            if not all(
                math.isclose(left, right, abs_tol=1e-9)
                for left, right in zip(expected, actual, strict=True)
            ):
                raise ValueError("browser rectangle disagrees with the compiler transform")
    print("Both browser previews match original-frame compiler geometry; 6 boxes verified")


if __name__ == "__main__":
    main()
