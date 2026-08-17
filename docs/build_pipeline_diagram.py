"""Build the concise English VisualOptimise pipeline diagram."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "visualoptimise_pipeline_overview.png"
WIDTH, HEIGHT = 1800, 1120


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


TITLE = font(42, True)
SUBTITLE = font(22)
BOX_TITLE = font(25, True)
BOX_BODY = font(20)
FOOTER = font(18)


def centered(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fill: str, text_font: ImageFont.FreeTypeFont) -> None:
    lines = text.split("\n")
    line_heights = [draw.textbbox((0, 0), line, font=text_font)[3] for line in lines]
    total_height = sum(line_heights) + max(0, len(lines) - 1) * 6
    y = (box[1] + box[3] - total_height) // 2
    for line, line_height in zip(lines, line_heights):
        bounds = draw.textbbox((0, 0), line, font=text_font)
        x = (box[0] + box[2] - (bounds[2] - bounds[0])) // 2
        draw.text((x, y), line, fill=fill, font=text_font)
        y += line_height + 6


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], fill: str = "#536273") -> None:
    draw.line([start, end], fill=fill, width=5)
    x1, y1 = start
    x2, y2 = end
    if abs(x2 - x1) >= abs(y2 - y1):
        direction = 1 if x2 > x1 else -1
        points = [(x2, y2), (x2 - 16 * direction, y2 - 10), (x2 - 16 * direction, y2 + 10)]
    else:
        direction = 1 if y2 > y1 else -1
        points = [(x2, y2), (x2 - 10, y2 - 16 * direction), (x2 + 10, y2 - 16 * direction)]
    draw.polygon(points, fill=fill)


def rounded_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, title: str, body: str) -> None:
    draw.rounded_rectangle(box, radius=20, fill=fill, outline="#334155", width=2)
    title_box = (box[0] + 18, box[1] + 16, box[2] - 18, box[1] + 52)
    centered(draw, title_box, title, "#102033", BOX_TITLE)
    body_box = (box[0] + 18, box[1] + 62, box[2] - 18, box[3] - 16)
    centered(draw, body_box, body, "#1f2937", BOX_BODY)


def main() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#f8fafc")
    draw = ImageDraw.Draw(image)
    centered(draw, (70, 35, WIDTH - 70, 95), "VisualOptimise: From ASCII Map to Unreal RuntimeData", "#102033", TITLE)
    centered(draw, (100, 100, WIDTH - 100, 135), "Concise end-to-end pipeline overview", "#536273", SUBTITLE)

    boxes = [
        ((90, 190, 500, 370), "#dbeafe", "1. Map Package", "map.txt\nlegend.json\nstyle.txt"),
        ((695, 190, 1105, 370), "#ffedd5", "2. Python Facts", "Validate inputs\nBuild map_facts_v2"),
        ((1300, 190, 1710, 370), "#ede9fe", "3. LLM1 Planner", "Semantic symbols\nMesh and material groups"),
        ((1300, 490, 1710, 670), "#ffedd5", "4. Python Resolver", "Dynamic IDs\nTileset and material evidence"),
        ((695, 490, 1105, 670), "#ede9fe", "5. LLM2 Prompt Briefs", "SD1.5 tags\nStableMaterials phrase"),
        ((90, 490, 500, 670), "#fef3c7", "6. Validate and Compile", "Schema checks\nBackend payloads"),
        ((90, 790, 500, 970), "#dcfce7", "7. Generate Materials", "SD1.5 primary\nStableMaterials optional"),
        ((695, 790, 1105, 970), "#e0f2fe", "8. Export RuntimeData", "Select textures\nWrite manifests and tileset"),
        ((1300, 790, 1710, 970), "#e2e8f0", "9. Unreal Engine", "Load map package\nBuild the procedural map"),
    ]
    for box, fill, title, body in boxes:
        rounded_box(draw, box, fill, title, body)

    arrow(draw, (500, 280), (695, 280))
    arrow(draw, (1105, 280), (1300, 280))
    arrow(draw, (1505, 370), (1505, 490))
    arrow(draw, (1300, 580), (1105, 580))
    arrow(draw, (695, 580), (500, 580))
    arrow(draw, (295, 670), (295, 790))
    arrow(draw, (500, 880), (695, 880))
    arrow(draw, (1105, 880), (1300, 880))

    draw.text((70, 1030), "Python controls deterministic facts, IDs, validation, backend requests, texture selection, and RuntimeData integrity.", fill="#334155", font=FOOTER)
    draw.text((70, 1065), "LLMs plan semantics and prompt language. Python does not modify Unreal source code.", fill="#334155", font=FOOTER)
    image.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
