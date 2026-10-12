"""Draw the app icon (purple disc, white speaker) and save it as a multi-size .ico. Usage: make_icon.py <out.ico>"""
import sys

from PIL import Image, ImageDraw


def draw(size: int) -> Image.Image:
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4 * s, 4 * s, 60 * s, 60 * s), fill=(100, 65, 165, 255))
    d.polygon([(20 * s, 26 * s), (30 * s, 26 * s), (42 * s, 16 * s), (42 * s, 48 * s), (30 * s, 38 * s), (20 * s, 38 * s)], fill="white")
    return img


if __name__ == "__main__":
    draw(256).save(sys.argv[1], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
