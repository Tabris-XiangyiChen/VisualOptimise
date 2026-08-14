"""Image processing helpers for the visual optimization pipeline."""

from __future__ import annotations

import warnings
from math import sqrt
from pathlib import Path
from random import Random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat


PALETTE = {
    "wall": ((92, 96, 90), (28, 30, 28)),
    "floor": ((132, 134, 124), (92, 96, 88)),
    "water": ((28, 62, 82), (72, 126, 144)),
    "door": ((105, 68, 34), (52, 32, 18)),
}


def render_ascii_control(
    ascii_map: str,
    legend: dict,
    output_size: tuple[int, int],
    semantic_pattern_mode: bool = False,
) -> Image.Image:
    """Render a map-semantic control image from ASCII input."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)

    tile = max(1, min(output_size[0] // width, output_size[1] // height))
    canvas = Image.new("RGB", (width * tile, height * tile), (12, 12, 14))
    draw = ImageDraw.Draw(canvas)

    for y, row in enumerate(rows):
        for x in range(width):
            symbol = row[x] if x < len(row) else " "
            entry = legend.get(symbol)
            if entry is None:
                warnings.warn(f"Unknown ASCII symbol in map: {symbol!r}", RuntimeWarning)
                fill, line = (180, 30, 180), (60, 0, 60)
                name = "unknown"
            else:
                name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
                fill, line = PALETTE.get(name, ((150, 150, 150), (40, 40, 40)))
            box = (x * tile, y * tile, (x + 1) * tile - 1, (y + 1) * tile - 1)
            draw.rectangle(box, fill=fill, outline=line)
            if semantic_pattern_mode:
                _draw_semantic_pattern(draw, box, name, fill, line)
            else:
                _draw_symbol_pattern(draw, box, name, line)

    resized = canvas.resize(output_size, Image.Resampling.NEAREST)
    return resized


def parse_ascii_layout(ascii_map: str, legend: dict) -> dict:
    """Parse deterministic semantic regions from an ASCII map."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)
    padded = [row.ljust(width) for row in rows]
    visited: set[tuple[int, int]] = set()
    regions = []

    for y, row in enumerate(padded):
        for x, symbol in enumerate(row):
            if symbol == " " or (x, y) in visited or symbol not in legend:
                continue
            cells = _collect_region_cells(padded, x, y, symbol, visited)
            entry = legend.get(symbol, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            region = _build_region(symbol, name, cells, width, height)
            if name == "door":
                region.update(_door_adjacency(padded, cells, legend))
            regions.append(region)

    return {
        "map_size": {
            "width": width,
            "height": height,
        },
        "regions": regions,
    }


def render_simplified_scene_control(
    ascii_map: str,
    legend: dict,
    output_size: tuple[int, int],
) -> Image.Image:
    """Render a clean top-down control image without dense floor or brick grid lines."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)
    padded = [row.ljust(width) for row in rows]
    tile = 32
    canvas = Image.new("RGB", (width * tile, height * tile), (150, 152, 142))
    draw = ImageDraw.Draw(canvas)

    for y, row in enumerate(padded):
        for x, symbol in enumerate(row):
            entry = legend.get(symbol, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            box = (x * tile, y * tile, (x + 1) * tile - 1, (y + 1) * tile - 1)
            if name == "wall":
                draw.rectangle(box, fill=(34, 36, 34))
            elif name == "water":
                draw.rectangle(box, fill=(18, 70, 96))
            elif name == "door":
                draw.rectangle(box, fill=(112, 70, 32))
                inset = max(3, tile // 8)
                draw.rectangle((box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset), outline=(222, 164, 85), width=2)

    _outline_regions(draw, padded, legend, "water", (122, 205, 218), tile, width=3)
    _outline_regions(draw, padded, legend, "wall", (6, 8, 6), tile, width=2)
    _outline_regions(draw, padded, legend, "door", (240, 192, 98), tile, width=2)
    return canvas.resize(output_size, Image.Resampling.NEAREST)


def render_scene_semantic_proxy(
    ascii_map: str,
    legend: dict,
    scene_layout: dict,
    shared_style: dict | None,
    output_size: tuple[int, int],
    seed: int = 12345,
) -> Image.Image:
    """Render a muted material-semantic proxy image for scene img2img."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)
    padded = [row.ljust(width) for row in rows]
    image = Image.new("RGB", output_size, (17, 21, 20))
    draw = ImageDraw.Draw(image)
    rng = Random(seed)

    colors = _semantic_proxy_colors(shared_style)

    door_region = _first_region(scene_layout, "door")
    for y, row in enumerate(padded):
        for x, symbol in enumerate(row):
            entry = legend.get(symbol, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            box = _scaled_cell_box(x, y, width, height, output_size)
            color = colors.get(name, colors["unknown"])
            draw.rectangle(box, fill=color)
            if name in {"wall", "floor"}:
                _subtle_noise(draw, box, color, rng, amount=3)
            elif name == "water":
                _subtle_noise(draw, box, color, rng, amount=2)
            elif name == "door":
                _draw_top_down_hatch(draw, box, door_region)

    return image


def render_textured_visual_proxy(
    ascii_map: str,
    legend: dict,
    scene_layout: dict,
    shared_style: dict | None,
    output_size: tuple[int, int],
    seed: int = 12345,
) -> Image.Image:
    """Render a deterministic low-fidelity textured scene proxy with Pillow."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)
    padded = [row.ljust(width) for row in rows]
    image = Image.new("RGB", output_size, (13, 16, 15))
    draw = ImageDraw.Draw(image, "RGBA")
    rng = Random(seed)
    door_region = _first_region(scene_layout, "door")

    for y, row in enumerate(padded):
        for x, symbol in enumerate(row):
            entry = legend.get(symbol, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            box = _scaled_cell_box(x, y, width, height, output_size)
            if name == "wall":
                _draw_proxy_wall(draw, box, rng)
            elif name == "floor":
                _draw_proxy_floor(draw, box, rng)
            elif name == "water":
                _draw_proxy_water(draw, box, rng)
            elif name == "door":
                _draw_proxy_door(draw, box, door_region, rng)
            else:
                _draw_proxy_void(draw, box, rng)

    return image


def render_round3_scene_control(
    ascii_map: str,
    legend: dict,
    scene_layout: dict,
    output_size: tuple[int, int],
) -> Image.Image:
    """Render clean structural regions for Canny without dense decorative lines."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)
    padded = [row.ljust(width) for row in rows]
    image = Image.new("RGB", output_size, (8, 10, 10))
    draw = ImageDraw.Draw(image)
    values = {
        "unknown": (8, 10, 10),
        "wall": (58, 58, 58),
        "floor": (146, 146, 146),
        "water": (96, 96, 96),
        "door": (112, 112, 112),
    }

    for y, row in enumerate(padded):
        for x, symbol in enumerate(row):
            entry = legend.get(symbol, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            draw.rectangle(_scaled_cell_box(x, y, width, height, output_size), fill=values.get(name, values["unknown"]))

    _outline_regions_scaled(draw, padded, legend, "wall", (245, 245, 245), output_size, width=2)
    _outline_regions_scaled(draw, padded, legend, "water", (210, 210, 210), output_size, width=2)
    _outline_regions_scaled(draw, padded, legend, "door", (230, 230, 230), output_size, width=2)
    return image


def create_symbol_mask(
    ascii_map: str,
    symbol: str,
    output_size: tuple[int, int],
    padding_pixels: int = 0,
) -> Image.Image:
    """Create an L-mode binary mask for an ASCII symbol."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char == symbol:
                draw.point((x, y), fill=255)
    mask = mask.resize(output_size, Image.Resampling.NEAREST)
    if padding_pixels > 0 and mask.getbbox():
        kernel_size = padding_pixels * 2 + 1
        mask = mask.filter(ImageFilter.MaxFilter(kernel_size))
    return mask.point(lambda value: 255 if value >= 128 else 0, mode="L")


def create_region_mask(
    ascii_map: str,
    legend: dict,
    region_name: str,
    output_size: tuple[int, int],
    padding_pixels: int = 0,
    subtract_region_names: set[str] | None = None,
) -> Image.Image:
    """Create an L-mode binary mask for every ASCII cell matching a legend region name."""
    rows = [line.rstrip("\n") for line in ascii_map.splitlines() if line.strip()]
    if not rows:
        raise ValueError("ASCII map is empty.")
    width = max(len(row) for row in rows)
    height = len(rows)
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)

    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            entry = legend.get(char, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            if name == region_name:
                draw.point((x, y), fill=255)

    mask = mask.resize(output_size, Image.Resampling.NEAREST)
    if padding_pixels > 0 and mask.getbbox():
        mask = mask.filter(ImageFilter.MaxFilter(padding_pixels * 2 + 1))

    if subtract_region_names:
        protected = Image.new("L", (width, height), 0)
        protected_draw = ImageDraw.Draw(protected)
        for y, row in enumerate(rows):
            for x, char in enumerate(row):
                entry = legend.get(char, {})
                name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
                if name in subtract_region_names:
                    protected_draw.point((x, y), fill=255)
        protected = protected.resize(output_size, Image.Resampling.NEAREST)
        mask = ImageChops.multiply(mask, ImageChops.invert(protected))

    return mask.point(lambda value: 255 if value >= 128 else 0, mode="L")


def mask_area_percentage(mask: Image.Image) -> float:
    """Return the percentage of white pixels in a binary mask."""
    binary = mask.convert("L").point(lambda value: 255 if value >= 128 else 0, mode="L")
    total = max(1, binary.width * binary.height)
    selected = sum(1 for value in binary.getdata() if value == 255)
    return float(selected * 100.0 / total)


def compare_inpaint_stage(
    before: Image.Image,
    after: Image.Image,
    mask: Image.Image,
    heatmap_path: Path | None = None,
) -> dict:
    """Measure how much an inpainting stage changed inside and outside its mask."""
    base = before.convert("RGB")
    generated = after.convert("RGB").resize(base.size, Image.Resampling.NEAREST)
    binary_mask = mask.convert("L").resize(base.size, Image.Resampling.NEAREST)
    binary_mask = binary_mask.point(lambda value: 255 if value >= 128 else 0, mode="L")
    diff = ImageChops.difference(base, generated)

    diff_values = [sum(pixel) / 3.0 for pixel in diff.getdata()]
    mask_values = list(binary_mask.getdata())
    inside = [value for value, mask_value in zip(diff_values, mask_values) if mask_value == 255]
    outside = [value for value, mask_value in zip(diff_values, mask_values) if mask_value == 0]

    if heatmap_path is not None:
        heatmap_path.parent.mkdir(parents=True, exist_ok=True)
        max_channel = [max(pixel) for pixel in diff.getdata()]
        heatmap = Image.new("L", base.size)
        heatmap.putdata(max_channel)
        heatmap.convert("RGB").save(heatmap_path)

    return {
        "image_dimensions": [base.width, base.height],
        "mask_area_percentage": mask_area_percentage(binary_mask),
        "mean_absolute_change_inside_mask_0_255": _mean_or_zero(inside),
        "mean_absolute_change_outside_mask_0_255": _mean_or_zero(outside),
        "result_differs_from_input": diff.getbbox() is not None,
        "inside_pixel_count": len(inside),
        "outside_pixel_count": len(outside),
    }


def generate_brick_prototype(
    size: int = 64,
    brick_width: int = 16,
    brick_height: int = 8,
    line_width: int = 1,
    irregularity: bool = False,
    seed: int = 12345,
    colourize: bool = False,
) -> Image.Image:
    """Generate a small tileable staggered brick prototype with Pillow."""
    image = Image.new("RGB", (size, size), (102, 105, 98))
    draw = ImageDraw.Draw(image)
    mortar = (38, 42, 39)
    colors = [(118, 119, 110), (92, 96, 90), (132, 130, 118)]

    rng = Random(seed)
    for y in range(-brick_height, size + brick_height, brick_height):
        row_index = y // brick_height
        row_jitter = 0
        offset = ((brick_width // 2) if row_index % 2 else 0) + row_jitter
        for x in range(-brick_width - offset, size + brick_width, brick_width):
            x0 = x + offset
            height_jitter = rng.randint(-1, 1) if irregularity else 0
            x1 = x0 + brick_width
            y1 = y + brick_height
            color = colors[(row_index + x0 // brick_width) % len(colors)]
            if colourize:
                color = tuple(max(0, min(255, channel + rng.randint(-9, 9))) for channel in color)
            elif irregularity:
                delta = rng.randint(-10, 10)
                color = tuple(max(0, min(255, channel + delta)) for channel in color)
            draw.rectangle((x0, y, x1 - 1, y1 - 1), fill=color)
            draw.rectangle((x0, y, x1 - 1, y1 - 1), outline=mortar, width=line_width)
            if irregularity and rng.random() < 0.35:
                yy = y + max(1, brick_height // 2 + height_jitter)
                draw.line((x0 + 2, yy, x1 - 3, yy), fill=tuple(max(0, c - 18) for c in color))

    return image


def generate_floor_slab_prototype(
    size: int = 512,
    slab_width: int = 128,
    slab_height: int = 96,
    line_width: int = 2,
    wet: bool = False,
    seed: int = 12345,
) -> Image.Image:
    """Generate a deterministic tileable broad stone floor slab prototype."""
    rng = Random(seed)
    base = (86, 90, 84) if wet else (105, 108, 100)
    joint = (48, 55, 52) if wet else (68, 72, 67)
    image = Image.new("RGB", (size, size), base)
    draw = ImageDraw.Draw(image, "RGBA")

    for y in range(0, size, slab_height):
        row = y // slab_height
        offset = (slab_width // 2) if row % 2 else 0
        for x in range(-offset, size, slab_width):
            tone = rng.randint(-8, 6) if wet else rng.randint(-6, 7)
            color = tuple(max(0, min(255, channel + tone)) for channel in base)
            box = (x, y, x + slab_width - 1, y + slab_height - 1)
            draw.rectangle(box, fill=(*color, 255))
            draw.rectangle(box, outline=(*joint, 160), width=line_width)
            if rng.random() < 0.35:
                cx = x + rng.randint(max(2, slab_width // 6), max(3, slab_width - slab_width // 6))
                cy = y + rng.randint(max(2, slab_height // 5), max(3, slab_height - slab_height // 5))
                length = rng.randint(max(8, slab_width // 8), max(10, slab_width // 4))
                draw.line((cx, cy, cx + length, cy + rng.randint(-3, 3)), fill=(42, 46, 43, 100), width=1)

    if wet:
        for _ in range(max(5, size // 80)):
            cx = rng.randint(0, size)
            cy = rng.randint(0, size)
            rx = rng.randint(max(16, size // 18), max(24, size // 9))
            ry = rng.randint(max(10, size // 24), max(18, size // 12))
            draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(37, 58, 55, 34))
    return image


def generate_water_prototype(
    size: int = 512,
    seed: int = 12345,
) -> Image.Image:
    """Generate a deterministic tileable dark top-down water prototype."""
    rng = Random(seed)
    image = Image.new("RGB", (size, size), (18, 65, 74))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(0, size, max(32, size // 10)):
        color = (42, 105, 114, 80)
        amplitude = max(4, size // 80)
        step = max(12, size // 32)
        points = []
        for x in range(-step, size + step, step):
            phase = ((x // step) + (y // max(1, step))) % 4
            yy = y + (amplitude if phase in (1, 2) else -amplitude)
            points.append((x, yy))
        if len(points) > 1:
            draw.line(points, fill=color, width=max(1, size // 180))
    for _ in range(max(4, size // 96)):
        cx = rng.randint(0, size)
        cy = rng.randint(0, size)
        rx = rng.randint(max(24, size // 16), max(36, size // 8))
        ry = rng.randint(max(12, size // 32), max(24, size // 16))
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(10, 45, 54, 30))
    return image


def generate_irregular_masonry_structure_control(
    size: int = 512,
    seed: int = 12345,
    min_row_height: int = 44,
    max_row_height: int = 86,
    min_block_width: int = 70,
    max_block_width: int = 180,
    line_width: int = 3,
) -> Image.Image:
    """Generate a low-density irregular masonry structure guide for ControlNet."""
    rng = Random(seed)
    image = Image.new("RGB", (size, size), (172, 174, 166))
    draw = ImageDraw.Draw(image, "RGBA")
    line = (38, 40, 38, 210)

    y = 0
    row_index = 0
    while y < size:
        row_h = rng.randint(min_row_height, max_row_height)
        y_next = min(size, y + row_h)
        draw.line((0, y, size, y), fill=line, width=line_width)
        draw.line((0, y_next - 1, size, y_next - 1), fill=line, width=max(1, line_width - 1))

        x = -rng.randint(0, max(1, min_block_width // 2))
        if row_index % 2:
            x -= rng.randint(max(1, min_block_width // 3), max(2, max_block_width // 2))
        while x < size:
            block_w = rng.randint(min_block_width, max_block_width)
            x_next = x + block_w
            jitter_top = rng.randint(-4, 4)
            jitter_bottom = rng.randint(-4, 4)
            if 0 <= x < size and rng.random() > 0.12:
                draw.line(
                    (x, max(0, y + jitter_top), x + rng.randint(-2, 2), min(size, y_next + jitter_bottom)),
                    fill=line,
                    width=max(1, line_width - 1),
                )
            if rng.random() < 0.28:
                crack_y = rng.randint(y + 6, max(y + 7, y_next - 6))
                crack_x0 = max(0, x + rng.randint(8, max(9, block_w // 3)))
                crack_x1 = min(size, crack_x0 + rng.randint(18, max(20, block_w // 2)))
                draw.line((crack_x0, crack_y, crack_x1, crack_y + rng.randint(-3, 3)), fill=(70, 72, 68, 80), width=1)
            x = x_next
        y = y_next
        row_index += 1

    return image


def resize_control(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resize a control image using nearest-neighbor sampling."""
    return image.convert("RGB").resize(size, Image.Resampling.NEAREST)


def create_canny_control(
    image: Image.Image,
    size: tuple[int, int] | None = None,
    low_threshold: int = 80,
    high_threshold: int = 160,
) -> Image.Image:
    """Create a Canny-like control image, using OpenCV when available."""
    source = image.convert("RGB")
    if size is not None:
        source = source.resize(size, Image.Resampling.NEAREST)
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        arr = np.array(source.convert("L"))
        edges = cv2.Canny(arr, low_threshold, high_threshold)
        return Image.fromarray(edges).convert("RGB")
    except ImportError:
        warnings.warn(
            "OpenCV is not installed; using Pillow FIND_EDGES fallback for wall control.",
            RuntimeWarning,
        )
        return source.convert("L").filter(ImageFilter.FIND_EDGES).convert("RGB")


def create_tiled_preview(
    image: Image.Image,
    tiles: tuple[int, int] = (3, 3),
) -> Image.Image:
    """Create a repeated preview image from a tile."""
    tile = image.convert("RGB")
    preview = Image.new("RGB", (tile.width * tiles[0], tile.height * tiles[1]))
    for y in range(tiles[1]):
        for x in range(tiles[0]):
            preview.paste(tile, (x * tile.width, y * tile.height))
    return preview


def save_image(image: Image.Image, path: Path) -> str:
    """Save an image and return the normalized path string."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return str(path)


def compare_images(
    reference: Image.Image,
    generated: Image.Image,
    heatmap_path: Path | None = None,
) -> dict:
    """Compute non-perceptual pixel-difference diagnostics and optionally save a heatmap."""
    ref = reference.convert("RGB")
    gen = generated.convert("RGB").resize(reference.size, Image.Resampling.NEAREST)
    diff = ImageChops.difference(ref, gen)
    stat = ImageStat.Stat(diff)
    pixel_count = max(1, reference.size[0] * reference.size[1])
    max_diffs = [max(pixel) for pixel in diff.getdata()]
    metrics = {
        "mean_absolute_difference_0_255": float(sum(stat.mean) / len(stat.mean)),
        "root_mean_square_error": float(sqrt(sum(value * value for value in stat.rms) / len(stat.rms))),
        "percent_pixels_abs_rgb_diff_gt_5": float(sum(value > 5 for value in max_diffs) * 100.0 / pixel_count),
        "percent_pixels_abs_rgb_diff_gt_10": float(sum(value > 10 for value in max_diffs) * 100.0 / pixel_count),
        "percent_pixels_abs_rgb_diff_gt_20": float(sum(value > 20 for value in max_diffs) * 100.0 / pixel_count),
        "correlation": None,
    }
    if heatmap_path is not None:
        heatmap_path.parent.mkdir(parents=True, exist_ok=True)
        heatmap = Image.new("L", reference.size)
        heatmap.putdata(max_diffs)
        heatmap.convert("RGB").save(heatmap_path)
    return metrics


def _draw_symbol_pattern(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], name: str, color: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    if name == "wall":
        step = max(4, (x1 - x0 + 1) // 4)
        for yy in range(y0 + step, y1, step):
            draw.line((x0, yy, x1, yy), fill=color)
        for xx in range(x0 + step, x1, step * 2):
            draw.line((xx, y0, xx, y1), fill=color)
    elif name == "water":
        mid = (y0 + y1) // 2
        draw.arc((x0, mid - 4, x1, mid + 6), 0, 180, fill=color, width=1)
    elif name == "door":
        draw.line((x0 + 2, y0 + 2, x1 - 2, y1 - 2), fill=color)


def _safe_correlation(ref: object, gen: object) -> float | None:
    import numpy as np  # type: ignore

    ref_flat = np.asarray(ref, dtype=np.float32).reshape(-1)
    gen_flat = np.asarray(gen, dtype=np.float32).reshape(-1)
    if float(ref_flat.std()) == 0.0 or float(gen_flat.std()) == 0.0:
        return None
    return float(np.corrcoef(ref_flat, gen_flat)[0, 1])


def _mean_or_zero(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _draw_semantic_pattern(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    name: str,
    fill: tuple[int, int, int],
    line: tuple[int, int, int],
) -> None:
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0 + 1)
    height = max(1, y1 - y0 + 1)
    if name == "wall":
        draw.rectangle(box, fill=(58, 62, 58), outline=(8, 8, 8), width=max(2, width // 14))
        step = max(5, width // 4)
        for yy in range(y0 + step, y1, step):
            draw.line((x0, yy, x1, yy), fill=(18, 20, 18), width=1)
        for xx in range(x0 + step, x1, step * 2):
            draw.line((xx, y0, xx, y1), fill=(18, 20, 18), width=1)
    elif name == "floor":
        draw.rectangle(box, fill=(154, 156, 145), outline=(92, 96, 88), width=1)
    elif name == "water":
        draw.rectangle(box, fill=(20, 70, 96), outline=(108, 174, 190), width=max(1, width // 24))
        wave_color = (130, 202, 214)
        spacing = max(8, height // 4)
        amplitude = max(3, height // 12)
        for yy in range(y0 + spacing // 2, y1, spacing):
            points = []
            for xx in range(x0 + 2, x1 - 1, max(3, width // 12)):
                phase = ((xx - x0) // max(3, width // 12)) % 2
                points.append((xx, yy + (amplitude if phase else -amplitude)))
            if len(points) > 1:
                draw.line(points, fill=wave_color, width=max(1, width // 28))
    elif name == "door":
        draw.rectangle(box, fill=(74, 48, 24), outline=(12, 8, 4), width=max(2, width // 16))
        inset = max(4, width // 7)
        draw.rectangle((x0 + inset, y0 + 2, x1 - inset, y1 - 2), outline=(205, 144, 75), width=max(1, width // 28))
        draw.line((x0 + inset, y0 + 2, x1 - inset, y1 - 2), fill=(205, 144, 75), width=max(1, width // 30))
        knob_x = x1 - inset - max(2, width // 9)
        knob_y = (y0 + y1) // 2
        draw.ellipse((knob_x - 2, knob_y - 2, knob_x + 2, knob_y + 2), fill=(230, 190, 96))
    else:
        draw.rectangle(box, fill=fill, outline=line)


def _collect_region_cells(
    rows: list[str],
    start_x: int,
    start_y: int,
    symbol: str,
    visited: set[tuple[int, int]],
) -> list[list[int]]:
    stack = [(start_x, start_y)]
    cells: list[list[int]] = []
    while stack:
        x, y = stack.pop()
        if (x, y) in visited or y < 0 or y >= len(rows) or x < 0 or x >= len(rows[0]):
            continue
        if rows[y][x] != symbol:
            continue
        visited.add((x, y))
        cells.append([x, y])
        stack.extend([(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)])
    return sorted(cells, key=lambda cell: (cell[1], cell[0]))


def _build_region(symbol: str, name: str, cells: list[list[int]], map_width: int, map_height: int) -> dict:
    xs = [cell[0] for cell in cells]
    ys = [cell[1] for cell in cells]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    box_width = x_max - x_min + 1
    box_height = y_max - y_min + 1
    return {
        "symbol": symbol,
        "name": name,
        "cells": cells,
        "bounding_box": {
            "x_min": x_min,
            "y_min": y_min,
            "x_max": x_max,
            "y_max": y_max,
        },
        "relative_position": _relative_position((x_min + x_max) / 2, (y_min + y_max) / 2, map_width, map_height),
        "tile_size": f"{box_width} by {box_height} tiles" if len(cells) > 1 else "1 tile",
    }


def _door_adjacency(rows: list[str], cells: list[list[int]], legend: dict) -> dict:
    directions = {
        "north": (0, -1),
        "south": (0, 1),
        "west": (-1, 0),
        "east": (1, 0),
    }
    wall_dirs: list[str] = []
    for x, y in [(cell[0], cell[1]) for cell in cells]:
        for direction, (dx, dy) in directions.items():
            nx, ny = x + dx, y + dy
            if 0 <= ny < len(rows) and 0 <= nx < len(rows[0]):
                entry = legend.get(rows[ny][nx], {})
                if isinstance(entry, dict) and entry.get("name") == "wall" and direction not in wall_dirs:
                    wall_dirs.append(direction)

    if not wall_dirs:
        orientation = "freestanding_floor_tile_or_hatch"
    elif any(direction in wall_dirs for direction in ("west", "east")) and any(direction in wall_dirs for direction in ("north", "south")):
        orientation = "corner_wall_adjacent"
    elif any(direction in wall_dirs for direction in ("west", "east")):
        orientation = "vertical_wall_opening"
    else:
        orientation = "horizontal_wall_opening"

    return {
        "adjacent_to_wall": bool(wall_dirs),
        "adjacent_wall_directions": wall_dirs,
        "orientation": orientation,
    }


def _relative_position(x_center: float, y_center: float, width: int, height: int) -> str:
    horizontal = "left" if x_center < width / 3 else "right" if x_center >= width * 2 / 3 else "center"
    vertical = "top" if y_center < height / 3 else "bottom" if y_center >= height * 2 / 3 else "middle"
    if horizontal == "center" and vertical == "middle":
        return "center"
    if horizontal == "center":
        return vertical
    if vertical == "middle":
        return f"{horizontal} side"
    return f"{vertical}-{horizontal}"


def _outline_regions(
    draw: ImageDraw.ImageDraw,
    rows: list[str],
    legend: dict,
    target_name: str,
    color: tuple[int, int, int],
    tile: int,
    width: int = 2,
) -> None:
    for y, row in enumerate(rows):
        for x, symbol in enumerate(row):
            entry = legend.get(symbol, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            if name != target_name:
                continue
            x0, y0, x1, y1 = x * tile, y * tile, (x + 1) * tile - 1, (y + 1) * tile - 1
            neighbours = {
                "north": (x, y - 1, (x0, y0, x1, y0)),
                "south": (x, y + 1, (x0, y1, x1, y1)),
                "west": (x - 1, y, (x0, y0, x0, y1)),
                "east": (x + 1, y, (x1, y0, x1, y1)),
            }
            for nx, ny, line in neighbours.values():
                if ny < 0 or ny >= len(rows) or nx < 0 or nx >= len(rows[0]):
                    draw.line(line, fill=color, width=width)
                    continue
                neighbour_entry = legend.get(rows[ny][nx], {})
                neighbour_name = neighbour_entry.get("name", "unknown") if isinstance(neighbour_entry, dict) else "unknown"
                if neighbour_name != target_name:
                    draw.line(line, fill=color, width=width)


def _scaled_cell_box(
    x: int,
    y: int,
    map_width: int,
    map_height: int,
    output_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    out_w, out_h = output_size
    x0 = int(round(x * out_w / map_width))
    y0 = int(round(y * out_h / map_height))
    x1 = int(round((x + 1) * out_w / map_width)) - 1
    y1 = int(round((y + 1) * out_h / map_height)) - 1
    return x0, y0, max(x0, x1), max(y0, y1)


def _subtle_noise(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    base: tuple[int, int, int],
    rng: Random,
    amount: int,
) -> None:
    x0, y0, x1, y1 = box
    area = max(1, (x1 - x0 + 1) * (y1 - y0 + 1))
    for _ in range(max(2, area // 900)):
        px = rng.randint(x0, x1)
        py = rng.randint(y0, y1)
        delta = rng.randint(-amount, amount)
        color = tuple(max(0, min(255, channel + delta)) for channel in base)
        draw.point((px, py), fill=color)


def _draw_top_down_hatch(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], door_region: dict | None) -> None:
    x0, y0, x1, y1 = box
    pad_x = max(3, (x1 - x0 + 1) // 7)
    pad_y = max(3, (y1 - y0 + 1) // 7)
    if door_region and door_region.get("adjacent_to_wall"):
        fill = (74, 48, 30)
    else:
        fill = (82, 51, 31)
    inner = (x0 + pad_x, y0 + pad_y, x1 - pad_x, y1 - pad_y)
    draw.rectangle(inner, fill=fill)
    draw.rectangle(inner, outline=(45, 29, 18), width=max(1, min(pad_x, pad_y) // 2))
    for xx in range(inner[0] + max(4, pad_x), inner[2], max(5, pad_x)):
        draw.line((xx, inner[1] + 2, xx, inner[3] - 2), fill=(58, 36, 22), width=1)


def _draw_proxy_void(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rng: Random) -> None:
    base = _jitter_color((13, 16, 15), rng, 2)
    draw.rectangle(box, fill=(*base, 255))
    if rng.random() < 0.25:
        _draw_low_frequency_patch(draw, box, (18, 21, 20), rng, alpha=20)


def _draw_proxy_floor(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rng: Random) -> None:
    base = _jitter_color((91, 94, 87), rng, 5)
    draw.rectangle(box, fill=(*base, 255))
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0 + 1)
    height = max(1, y1 - y0 + 1)
    joint = (62, 66, 61, 90)
    if width > 24 and rng.random() < 0.75:
        xx = x0 + width // 2 + rng.randint(-max(1, width // 10), max(1, width // 10))
        draw.line((xx, y0 + 3, xx, y1 - 3), fill=joint, width=1)
    if height > 24 and rng.random() < 0.65:
        yy = y0 + height // 2 + rng.randint(-max(1, height // 10), max(1, height // 10))
        draw.line((x0 + 3, yy, x1 - 3, yy), fill=joint, width=1)
    if rng.random() < 0.35:
        _draw_low_frequency_patch(draw, box, (104, 106, 98), rng, alpha=28)
    if rng.random() < 0.22:
        _draw_short_wear_mark(draw, box, rng, (55, 58, 54, 90))


def _draw_proxy_wall(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rng: Random) -> None:
    base = _jitter_color((50, 53, 50), rng, 4)
    draw.rectangle(box, fill=(*base, 255))
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0 + 1)
    height = max(1, y1 - y0 + 1)
    mortar = (32, 35, 33, 95)
    mid_y = y0 + height // 2 + rng.randint(-max(1, height // 12), max(1, height // 12))
    draw.line((x0 + 3, mid_y, x1 - 3, mid_y), fill=mortar, width=1)
    if width > 28:
        mid_x = x0 + width // 2 + rng.randint(-max(1, width // 8), max(1, width // 8))
        draw.line((mid_x, y0 + 4, mid_x, y1 - 4), fill=mortar, width=1)
    if rng.random() < 0.3:
        _draw_low_frequency_patch(draw, box, (62, 65, 61), rng, alpha=22)


def _draw_proxy_water(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rng: Random) -> None:
    base = _jitter_color((20, 62, 72), rng, 4)
    draw.rectangle(box, fill=(*base, 255))
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0 + 1)
    height = max(1, y1 - y0 + 1)
    ripple = (54, 104, 112, 85)
    for index in range(2):
        yy = y0 + (index + 1) * height // 3 + rng.randint(-max(1, height // 12), max(1, height // 12))
        draw.arc((x0 + width // 8, yy - height // 8, x1 - width // 8, yy + height // 8), 0, 180, fill=ripple, width=1)
    if rng.random() < 0.35:
        _draw_low_frequency_patch(draw, box, (15, 50, 60), rng, alpha=32)


def _draw_proxy_door(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], door_region: dict | None, rng: Random) -> None:
    if door_region and door_region.get("adjacent_to_wall"):
        _draw_proxy_wall_doorway(draw, box, rng)
    else:
        _draw_proxy_floor_hatch(draw, box, rng)


def _draw_proxy_wall_doorway(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rng: Random) -> None:
    _draw_proxy_wall(draw, box, rng)
    x0, y0, x1, y1 = box
    pad_x = max(4, (x1 - x0 + 1) // 6)
    pad_y = max(4, (y1 - y0 + 1) // 6)
    inner = (x0 + pad_x, y0 + pad_y, x1 - pad_x, y1 - pad_y)
    draw.rectangle(inner, fill=(45, 30, 20, 210))
    draw.rectangle(inner, outline=(74, 53, 36, 180), width=1)


def _draw_proxy_floor_hatch(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rng: Random) -> None:
    _draw_proxy_floor(draw, box, rng)
    x0, y0, x1, y1 = box
    pad_x = max(5, (x1 - x0 + 1) // 7)
    pad_y = max(5, (y1 - y0 + 1) // 7)
    inner = (x0 + pad_x, y0 + pad_y, x1 - pad_x, y1 - pad_y)
    draw.rectangle(inner, fill=(74, 46, 27, 235))
    draw.rectangle(inner, outline=(39, 25, 16, 170), width=1)
    plank_step = max(5, (inner[3] - inner[1] + 1) // 4)
    for yy in range(inner[1] + plank_step, inner[3], plank_step):
        draw.line((inner[0] + 3, yy, inner[2] - 3, yy), fill=(45, 29, 18, 160), width=1)
    hinge_w = max(2, (inner[2] - inner[0] + 1) // 8)
    hinge_h = max(2, (inner[3] - inner[1] + 1) // 10)
    for hx in (inner[0] + 4, inner[2] - hinge_w - 4):
        hy = inner[1] + max(4, (inner[3] - inner[1]) // 3)
        draw.rectangle((hx, hy, hx + hinge_w, hy + hinge_h), fill=(31, 31, 29, 190))


def _jitter_color(base: tuple[int, int, int], rng: Random, amount: int) -> tuple[int, int, int]:
    delta = rng.randint(-amount, amount)
    return tuple(max(0, min(255, channel + delta)) for channel in base)


def _draw_low_frequency_patch(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    color: tuple[int, int, int],
    rng: Random,
    alpha: int,
) -> None:
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0 + 1)
    height = max(1, y1 - y0 + 1)
    cx = rng.randint(x0, x1)
    cy = rng.randint(y0, y1)
    rx = max(3, width // rng.randint(3, 5))
    ry = max(3, height // rng.randint(3, 5))
    draw.ellipse(
        (
            max(x0, cx - rx),
            max(y0, cy - ry),
            min(x1, cx + rx),
            min(y1, cy + ry),
        ),
        fill=(*color, alpha),
    )


def _draw_short_wear_mark(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    rng: Random,
    color: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = box
    if x1 - x0 < 10 or y1 - y0 < 10:
        return
    sx = rng.randint(x0 + 4, x1 - 4)
    sy = rng.randint(y0 + 4, y1 - 4)
    length = rng.randint(5, max(6, (x1 - x0 + 1) // 5))
    draw.line((sx, sy, min(x1 - 3, sx + length), sy + rng.randint(-2, 2)), fill=color, width=1)


def _first_region(scene_layout: dict, name: str) -> dict | None:
    for region in scene_layout.get("regions", []):
        if region.get("name") == name:
            return region
    return None


def _outline_regions_scaled(
    draw: ImageDraw.ImageDraw,
    rows: list[str],
    legend: dict,
    target_name: str,
    color: tuple[int, int, int],
    output_size: tuple[int, int],
    width: int = 2,
) -> None:
    map_width = len(rows[0])
    map_height = len(rows)
    for y, row in enumerate(rows):
        for x, symbol in enumerate(row):
            entry = legend.get(symbol, {})
            name = entry.get("name", "unknown") if isinstance(entry, dict) else "unknown"
            if name != target_name:
                continue
            x0, y0, x1, y1 = _scaled_cell_box(x, y, map_width, map_height, output_size)
            neighbours = {
                "north": (x, y - 1, (x0, y0, x1, y0)),
                "south": (x, y + 1, (x0, y1, x1, y1)),
                "west": (x - 1, y, (x0, y0, x0, y1)),
                "east": (x + 1, y, (x1, y0, x1, y1)),
            }
            for nx, ny, line in neighbours.values():
                if ny < 0 or ny >= len(rows) or nx < 0 or nx >= map_width:
                    draw.line(line, fill=color, width=width)
                    continue
                neighbour_entry = legend.get(rows[ny][nx], {})
                neighbour_name = neighbour_entry.get("name", "unknown") if isinstance(neighbour_entry, dict) else "unknown"
                if neighbour_name != target_name:
                    draw.line(line, fill=color, width=width)


def _semantic_proxy_colors(shared_style: dict | None) -> dict[str, tuple[int, int, int]]:
    """Return deterministic muted dungeon colours for material-semantic proxy regions."""
    colors = {
        "unknown": (17, 21, 20),
        "wall": (63, 69, 64),
        "floor": (119, 123, 116),
        "water": (23, 63, 77),
        "door": (90, 56, 34),
    }
    palette_values = " ".join(
        str(item).lower()
        for key in ("global_palette", "colour_palette")
        for item in _style_list((shared_style or {}).get(key, []))
    )
    if "moss" in palette_values:
        colors["wall"] = (59, 67, 61)
    if "charcoal" in palette_values:
        colors["unknown"] = (15, 18, 18)
    if "blue" in palette_values or "water" in palette_values:
        colors["water"] = (20, 61, 76)
    return colors


def _style_list(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return []
