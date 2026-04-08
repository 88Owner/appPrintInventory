from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    png_path = assets / "app-icon.png"
    ico_path = assets / "app.ico"

    size = 512
    img = Image.new("RGBA", (size, size), (11, 18, 32, 255))  # #0b1220
    d = ImageDraw.Draw(img)

    # Rounded square border
    pad = 40
    r = 72
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=r, fill=(15, 23, 42, 255), outline=(79, 70, 229, 255), width=10)

    # Label shape
    lx0, ly0 = 110, 150
    lx1, ly1 = 402, 360
    d.rounded_rectangle([lx0, ly0, lx1, ly1], radius=28, fill=(255, 255, 255, 255))

    # Fake text lines
    d.rounded_rectangle([lx0 + 28, ly0 + 40, lx0 + 190, ly0 + 70], radius=10, fill=(17, 24, 39, 255))
    d.rounded_rectangle([lx0 + 28, ly0 + 90, lx0 + 220, ly0 + 120], radius=10, fill=(148, 163, 184, 255))

    # QR pattern block
    qx0, qy0 = lx0 + 210, ly0 + 32
    qsize = 160
    d.rounded_rectangle([qx0, qy0, qx0 + qsize, qy0 + qsize], radius=18, fill=(17, 24, 39, 255))
    cell = 14
    margin = 18
    for y in range(0, (qsize - 2 * margin) // cell):
        for x in range(0, (qsize - 2 * margin) // cell):
            if (x * 7 + y * 11) % 5 in (0, 1):
                cx0 = qx0 + margin + x * cell
                cy0 = qy0 + margin + y * cell
                d.rectangle([cx0, cy0, cx0 + cell - 3, cy0 + cell - 3], fill=(255, 255, 255, 255))

    img.save(png_path)

    # Multi-size ICO for Windows
    img_ico = img.copy()
    img_ico.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")


if __name__ == "__main__":
    main()

