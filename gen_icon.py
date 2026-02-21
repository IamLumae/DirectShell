"""Generate DirectShell icon — double chevron (>>) in cyan on transparent background."""
from PIL import Image, ImageDraw

CYAN = (0, 229, 255, 255)  # #00E5FF

def draw_chevrons(size: int) -> Image.Image:
    """Draw two nested chevrons at the given size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Padding and geometry
    pad = max(1, size // 8)
    w = size - 2 * pad
    h = size - 2 * pad
    cx = size // 2
    cy = size // 2

    # Stroke width scales with icon size
    stroke = max(1, size // 10)

    # Two chevrons: left one and right one
    # Each chevron: top-left → center-right → bottom-left  (a > shape)
    gap = w // 4  # horizontal gap between the two chevrons
    chev_w = w * 3 // 8  # width of each chevron

    # Left chevron
    x0 = pad
    draw.line([
        (x0, pad),
        (x0 + chev_w, cy),
        (x0, pad + h)
    ], fill=CYAN, width=stroke, joint="curve")

    # Right chevron
    x1 = pad + gap
    draw.line([
        (x1, pad),
        (x1 + chev_w, cy),
        (x1, pad + h)
    ], fill=CYAN, width=stroke, joint="curve")

    return img


def draw_chevrons_filled(size: int) -> Image.Image:
    """Draw two filled chevron arrows — bolder, reads better at small sizes."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 7)
    h = size - 2 * pad
    cy = size // 2

    # Thickness of chevron arms
    t = max(2, size // 5)

    # Left chevron (filled polygon)
    gap = size * 3 // 10

    for offset in [0, gap]:
        x0 = pad + offset
        tip_x = x0 + size * 3 // 8

        # Outer shape of chevron
        points = [
            (x0, pad),                    # top-left outer
            (tip_x, cy),                  # right tip
            (x0, pad + h),                # bottom-left outer
            (x0 + t, pad + h),            # bottom-left inner
            (tip_x, cy),                  # right tip (same)
            (x0 + t, pad),                # top-left inner
        ]
        draw.polygon(points, fill=CYAN)

    return img


# Generate at multiple sizes
sizes = [16, 32, 48, 256]
images = []

for s in sizes:
    if s <= 32:
        # Small sizes: use filled chevrons for better visibility
        img = draw_chevrons_filled(s)
    else:
        # Larger sizes: also filled, looks cleaner
        img = draw_chevrons_filled(s)
    images.append(img)

# Save as .ico (multi-resolution)
out_path = r"C:\Users\hacka\Desktop\Neuer-Main-Server\Workspace\Projekte\Project DirectShell\directshell.ico"
images[0].save(out_path, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])

# Also save 256x256 as PNG for preview
preview = r"C:\Users\hacka\Desktop\Neuer-Main-Server\Workspace\Projekte\Project DirectShell\icon_preview.png"
images[-1].save(preview)

print(f"Icon saved: {out_path}")
print(f"Preview saved: {preview}")
