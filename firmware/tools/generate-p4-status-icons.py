#!/usr/bin/env python3
from pathlib import Path

from PIL import Image, ImageDraw


OUT = Path(__file__).parents[1] / "assets/waveshare_p4_wifi6_touch_lcd_7b/sprites"
CYAN = (53, 244, 219, 255)
BLUE = (85, 184, 255, 255)
MAGENTA = (255, 74, 173, 255)
YELLOW = (255, 211, 79, 255)


def canvas():
    image = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def save(name, painter):
    image, draw = canvas()
    painter(draw)
    image.save(OUT / f"{name}.png")


def mic(draw, color=CYAN, slash=False, active=False):
    draw.rounded_rectangle((14, 5, 26, 25), radius=6, outline=color, width=3)
    draw.arc((9, 13, 31, 31), 0, 180, fill=color, width=3)
    draw.line((20, 31, 20, 35), fill=color, width=3)
    draw.line((14, 35, 26, 35), fill=color, width=3)
    if slash:
        draw.line((7, 7, 33, 33), fill=MAGENTA, width=4)
    if active:
        draw.arc((5, 9, 35, 35), 310, 50, fill=BLUE, width=2)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    save("mic_enabled", lambda d: mic(d))
    save("mic_disabled", lambda d: mic(d, slash=True))
    save("mic_active", lambda d: mic(d, active=True))
    save("mute", lambda d: (d.polygon((5, 16, 12, 16, 21, 8, 21, 32, 12, 24, 5, 24), outline=CYAN), d.line((25, 13, 35, 27), fill=MAGENTA, width=4), d.line((35, 13, 25, 27), fill=MAGENTA, width=4)))
    save("dnd", lambda d: (d.ellipse((8, 8, 32, 32), outline=BLUE, width=3), d.line((11, 29, 29, 11), fill=MAGENTA, width=4)))
    save("timer", lambda d: (d.ellipse((7, 8, 33, 34), outline=CYAN, width=3), d.line((20, 3, 20, 8), fill=CYAN, width=3), d.line((15, 3, 25, 3), fill=CYAN, width=3), d.line((20, 21, 27, 15), fill=BLUE, width=3)))
    save("alarm", lambda d: (d.ellipse((8, 9, 32, 33), outline=CYAN, width=3), d.arc((4, 3, 17, 15), 180, 340, fill=BLUE, width=3), d.arc((23, 3, 36, 15), 200, 360, fill=BLUE, width=3), d.line((20, 14, 20, 22, 26, 25), fill=CYAN, width=3)))
    save("playback", lambda d: (d.polygon((10, 7, 31, 20, 10, 33), outline=CYAN, fill=(53, 244, 219, 90)),))
    save("update_available", lambda d: (d.line((20, 5, 20, 25), fill=CYAN, width=4), d.polygon((12, 19, 20, 29, 28, 19), fill=CYAN), d.line((7, 34, 33, 34), fill=BLUE, width=3)))
    save("warning", lambda d: (d.polygon((20, 4, 36, 34, 4, 34), outline=YELLOW), d.line((20, 13, 20, 24), fill=YELLOW, width=4), d.ellipse((18, 28, 22, 32), fill=YELLOW)))
    save("privacy", lambda d: (d.polygon((20, 4, 33, 9, 31, 26, 20, 35, 9, 26, 7, 9), outline=CYAN), d.rectangle((15, 18, 25, 28), outline=BLUE, width=2), d.arc((16, 11, 24, 22), 180, 360, fill=BLUE, width=2)))
    save("cloud_offline", lambda d: (d.arc((5, 13, 25, 33), 90, 270, fill=BLUE, width=3), d.arc((13, 7, 33, 29), 180, 355, fill=BLUE, width=3), d.line((7, 8, 33, 34), fill=MAGENTA, width=4)))


if __name__ == "__main__":
    main()
