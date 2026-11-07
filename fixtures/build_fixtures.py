"""Generate canonical fixture metadata independently of compiler goldens."""

from __future__ import annotations

import hashlib
import io
import json
from collections import Counter

from load_canonical import ROOT
from PIL import Image

from labelloop_compiler.ingest import process_image
from labelloop_core.grouping import AssetEvidence, assign_splits, phash_grayscale

# Deliberately coarse source rectangles. Interface boxes contain app/web/tablet UI,
# not the neutral padding added by the original fixture preparation. Keyboard points
# cover photographed hardware, while switches are separate explicitly typed objects.
KEYBOARD = {
    "keyboard-01": ("keyboard", [0, 53, 160, 108]),
    "keyboard-02": ("keyboard", [0, 52, 160, 108]),
    "keyboard-03": ("keyboard", [54, 34, 139, 78]),
    "keyboard-04": ("keyboard", [0, 28, 160, 82]),
    "keyboard-05": ("keyboard", [0, 18, 160, 135]),
    "keyboard-06": ("keyboard_key_switch", [59, 36, 76, 49], [81, 40, 101, 59], [59, 57, 81, 82]),
    "keyboard-07": ("keyboard", [0, 51, 160, 107]),
    "keyboard-08": ("keyboard", [30, 59, 125, 104]),
    "keyboard-09": ("keyboard", [0, 27, 160, 133]),
    "keyboard-10": ("keyboard", [25, 70, 135, 124]),
    "keyboard-11": ("keyboard", [0, 69, 160, 91]),
    "keyboard-12": ("keyboard", [0, 52, 160, 111]),
    "keyboard-13": ("keyboard", [0, 55, 160, 107]),
    "keyboard-14": ("keyboard_key_switch", [18, 71, 70, 130], [80, 69, 137, 132]),
    "keyboard-15": ("keyboard_key_switch", [24, 59, 67, 128], [101, 64, 137, 119]),
    "keyboard-16": ("keyboard_key_switch", [25, 65, 68, 132], [82, 70, 139, 137]),
}
UI = {
    "ui-01": [0, 0, 160, 159],
    "ui-02": [0, 0, 160, 127],
    "ui-03": [0, 0, 159, 158],
    "ui-04": [0, 47, 160, 113],
    "ui-05": [19, 57, 141, 96],
    "ui-06": [0, 39, 160, 123],
    "ui-07": [0, 55, 160, 105],
    "ui-08": [0, 56, 159, 105],
    "ui-09": [24, 0, 136, 131],
    "ui-10": [0, 32, 159, 125],
    "ui-11": [0, 32, 160, 125],
    "ui-12": [1, 38, 160, 122],
    "ui-13": [0, 43, 159, 123],
    "ui-14": [29, 24, 130, 139],
    "ui-15": [0, 48, 160, 112],
    "ui-16": [0, 48, 160, 112],
}


def build(domain: str) -> None:
    if domain == "commons-office-keyboards":
        classes = [
            {
                "stable_id": "keyboard",
                "name": "keyboard",
                "definition": "Visible extent of computer keyboard hardware.",
            },
            {
                "stable_id": "keyboard_key_switch",
                "name": "keyboard key switch",
                "definition": "Visible loose switch or keypad hardware.",
            },
        ]
    else:
        classes = [
            {
                "stable_id": "application_view",
                "name": "application view",
                "definition": "Visible software application or web interface extent.",
            }
        ]
    assets = []
    for image in sorted((ROOT / "fixtures" / domain / "images").glob("*.jpg")):
        key = image.stem
        data = image.read_bytes()
        asset, normalized = process_image(data, asset_id=key, project_id=domain)
        assert asset.sha256 == hashlib.sha256(normalized).hexdigest()
        objects = []
        if domain == "commons-office-keyboards":
            spec = KEYBOARD[key]
            for index, box in enumerate(spec[1:]):
                objects.append(
                    {
                        "object_id": f"{key}-obj-{index + 1}",
                        "class_id": spec[0],
                        "box": box,
                        "truncated_at_image_edge": 0 in box[:2] or 160 in box[2:],
                    }
                )
        else:
            objects = [
                {"object_id": f"{key}-obj-1", "class_id": "application_view", "box": UI[key]}
            ]
        counts = Counter(item["class_id"] for item in objects)
        with Image.open(io.BytesIO(data)) as opened:
            phash = phash_grayscale(opened)
        assets.append(
            {
                "asset_id": key,
                "file": image.name,
                "normalized_sha256": asset.sha256,
                "width": asset.width,
                "height": asset.height,
                "sha256": asset.sha256,
                "objects": objects,
                "evidence": {
                    "asset_id": key,
                    "sha256": asset.sha256,
                    "phash": phash,
                    "embedding": (),
                    "metadata": {},
                    "class_counts": dict(counts),
                },
            }
        )
    evidence = [AssetEvidence(**asset["evidence"]) for asset in assets]
    assignment = assign_splits(evidence)
    labels = {
        "annotation_policy": (
            "Fixture-only coarse visible rectangles; not independently "
            "adjudicated gold ground truth."
        ),
        "annotation_licence": (
            "CC0-1.0 for fixture metadata only; image licences and "
            "attribution remain in manifest.json."
        ),
        "auto_accept_qualification": "fixture-compiler-policy-v1",
        "classes": classes,
        "class_map": {item["stable_id"]: index for index, item in enumerate(classes)},
        "images": assets,
        "split_assignment": {
            "assignment_version": assignment.assignment_version,
            "asset_splits": assignment.asset_splits,
            "group_splits": assignment.group_splits,
            "group_ids": assignment.group_ids,
            "seed": assignment.seed,
            "algorithm": assignment.algorithm,
            "reasons": assignment.reasons,
            "locked": True,
        },
        "evidence": [asset["evidence"] for asset in assets],
    }
    path = ROOT / "fixtures" / "golden" / f"{domain}.labels.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    build("commons-office-keyboards")
    build("cc0-interface")
