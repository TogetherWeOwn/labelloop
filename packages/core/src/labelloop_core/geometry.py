"""Half-open pixel geometry and exact affine maps derived from raster dimensions."""

import math
from dataclasses import dataclass

type Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]
VALID_CONTENT = "valid-content-rectangle-v1"


def _finite(*values: float) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError("geometry must contain only finite coordinates")


@dataclass(frozen=True, slots=True)
class Box:
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def __post_init__(self) -> None:
        _finite(self.x_min, self.y_min, self.x_max, self.y_max)
        if self.x_min < 0 or self.y_min < 0 or self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("box must be non-negative with positive width and height")

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def area(self) -> float:
        return self.width * self.height

    def within(self, width: int, height: int) -> "Box":
        if width <= 0 or height <= 0 or self.x_max > width or self.y_max > height:
            raise ValueError("box must lie inside image dimensions")
        return self

    def intersect(self, other: "Box") -> "Box | None":
        bounds = (
            max(self.x_min, other.x_min),
            max(self.y_min, other.y_min),
            min(self.x_max, other.x_max),
            min(self.y_max, other.y_max),
        )
        return Box(*bounds) if bounds[2] > bounds[0] and bounds[3] > bounds[1] else None

    def clip(self, width: int, height: int) -> "Box | None":
        return self.intersect(Box(0, 0, width, height))


@dataclass(frozen=True, slots=True)
class Crop:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        _finite(self.x, self.y, self.width, self.height)
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("crop must have a non-negative origin and positive dimensions")

    @property
    def box(self) -> Box:
        return Box(self.x, self.y, self.x + self.width, self.y + self.height)


@dataclass(frozen=True, slots=True)
class AffineTransform:
    forward: Matrix3
    inverse: Matrix3
    crop: Crop
    output_size: int
    source_width: int
    source_height: int
    resized_width: int
    resized_height: int
    left: int
    top: int
    right: int
    bottom: int
    scale_x: float
    scale_y: float
    algorithm: str

    @property
    def valid_content(self) -> Box:
        return Box(
            self.left,
            self.top,
            self.left + self.resized_width,
            self.top + self.resized_height,
        )

    def points(self, x: float, y: float, *, inverse: bool = False) -> tuple[float, float]:
        matrix = self.inverse if inverse else self.forward
        return (
            matrix[0][0] * x + matrix[0][1] * y + matrix[0][2],
            matrix[1][0] * x + matrix[1][1] * y + matrix[1][2],
        )

    def apply(self, box: Box) -> Box | None:
        """Clip before mapping and intersect again with actual raster content."""
        retained = box.intersect(self.crop.box)
        if retained is None:
            return None
        x1, y1 = self.points(retained.x_min, retained.y_min)
        x2, y2 = self.points(retained.x_max, retained.y_max)
        return Box(max(self.left, x1), max(self.top, y1), x2, y2).intersect(self.valid_content)

    def restore(self, box: Box) -> Box | None:
        """Recover only source extent supported by retained pixels, never hidden extent."""
        retained = box.intersect(self.valid_content)
        if retained is None:
            return None
        x1, y1 = self.points(retained.x_min, retained.y_min, inverse=True)
        x2, y2 = self.points(retained.x_max, retained.y_max, inverse=True)
        try:
            restored = Box(
                max(self.crop.x, x1),
                max(self.crop.y, y1),
                max(self.crop.x, x2),
                max(self.crop.y, y2),
            )
        except ValueError:
            return None
        return restored.intersect(self.crop.box)


def _matrix(scale_x: float, scale_y: float, dx: float, dy: float) -> Matrix3:
    return ((scale_x, 0.0, dx), (0.0, scale_y, dy), (0.0, 0.0, 1.0))


def full_frame_transform(
    source_width: int,
    source_height: int,
    output_size: int,
    *,
    crop: Crop | None = None,
    allow_upscale: bool = True,
) -> AffineTransform:
    """Compute gains from actual rounded raster sizes, not a nominal letterbox ratio."""
    if min(source_width, source_height, output_size) <= 0:
        raise ValueError("source and output dimensions must be positive")
    selected = crop or Crop(0, 0, source_width, source_height)
    if selected.x + selected.width > source_width or selected.y + selected.height > source_height:
        raise ValueError("crop must be contained by the source")
    ratio = min(output_size / selected.width, output_size / selected.height)
    if not allow_upscale:
        ratio = min(ratio, 1.0)
    resized_width = max(1, round(selected.width * ratio))
    resized_height = max(1, round(selected.height * ratio))
    left = (output_size - resized_width) // 2
    top = (output_size - resized_height) // 2
    scale_x, scale_y = resized_width / selected.width, resized_height / selected.height
    return AffineTransform(
        forward=_matrix(scale_x, scale_y, left - scale_x * selected.x, top - scale_y * selected.y),
        inverse=_matrix(
            1 / scale_x, 1 / scale_y, selected.x - left / scale_x, selected.y - top / scale_y
        ),
        crop=selected,
        output_size=output_size,
        source_width=source_width,
        source_height=source_height,
        resized_width=resized_width,
        resized_height=resized_height,
        left=left,
        top=top,
        right=output_size - resized_width - left,
        bottom=output_size - resized_height - top,
        scale_x=scale_x,
        scale_y=scale_y,
        algorithm=VALID_CONTENT if crop else "full-letterbox-v1",
    )


def normalize_box(box: Box, width: int, height: int) -> tuple[float, float, float, float]:
    """Five-field YOLO numerical values: center x/y and width/height."""
    if width <= 0 or height <= 0:
        raise ValueError("output image dimensions must be positive")
    if box.clip(width, height) != box:
        raise ValueError("box must be clipped to the derived image before normalization")
    values = (
        (box.x_min + box.x_max) / (2 * width),
        (box.y_min + box.y_max) / (2 * height),
        box.width / width,
        box.height / height,
    )
    if not all(0 < value <= 1 for value in values[2:]) or not all(
        0 <= value <= 1 for value in values
    ):
        raise ValueError("normalized geometry must stay in [0,1] with positive size")
    return values


def target_flags(transform: AffineTransform, box: Box, min_target_px: int) -> tuple[str, ...]:
    mapped = transform.apply(box)
    if mapped is None:
        return ("target_outside_crop",)
    if min_target_px <= 0:
        raise ValueError("min_target_px must be positive")
    flags: list[str] = []
    if box.intersect(transform.crop.box) != box:
        flags.extend(("clipped", "discarded_extent"))
    if mapped.width < min_target_px or mapped.height < min_target_px:
        flags.append("below_min_target_px")
    return tuple(flags)


def with_crop(transform: AffineTransform, crop: Crop) -> AffineTransform:
    return full_frame_transform(
        transform.source_width,
        transform.source_height,
        transform.output_size,
        crop=crop,
        allow_upscale=transform.resized_width / transform.crop.width > 1
        or transform.resized_height / transform.crop.height > 1,
    )
