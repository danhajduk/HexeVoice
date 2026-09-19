#!/usr/bin/env python3
from pathlib import Path

from PIL import Image, ImageDraw


OUT = Path(__file__).parents[1] / "assets/waveshare_p4_wifi6_touch_lcd_7b/sprites"
CYAN = (53, 244, 219, 255)
BLUE = (85, 184, 255, 255)
MAGENTA = (255, 74, 173, 255)
YELLOW = (255, 211, 79, 255)
RED = (255, 48, 48, 255)


def canvas(size=40):
    dimensions = (size, size) if isinstance(size, int) else size
    image = Image.new("RGBA", dimensions, (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def save(name, painter, size=40):
    image, draw = canvas(size)
    painter(draw)
    image.save(OUT / f"{name}.png")


def activity_frame(draw):
    draw.rounded_rectangle((18, 18, 161, 161), radius=24, outline=BLUE, width=3)
    draw.line((40, 12, 140, 12), fill=CYAN, width=3)
    draw.line((40, 167, 140, 167), fill=CYAN, width=3)
    for x, y, sx, sy in ((18, 18, 1, 1), (161, 18, -1, 1), (18, 161, 1, -1), (161, 161, -1, -1)):
        draw.line((x, y, x + sx * 16, y), fill=MAGENTA, width=4)
        draw.line((x, y, x, y + sy * 16), fill=MAGENTA, width=4)


def activity_listening(draw):
    activity_frame(draw)
    draw.rounded_rectangle((75, 47, 105, 101), radius=15, outline=CYAN, width=6)
    draw.arc((59, 65, 121, 127), 0, 180, fill=BLUE, width=6)
    draw.line((90, 127, 90, 139), fill=CYAN, width=5)
    draw.line((72, 139, 108, 139), fill=CYAN, width=5)


def activity_thinking(draw):
    activity_frame(draw)
    draw.ellipse((58, 58, 122, 122), outline=BLUE, width=4)
    draw.ellipse((84, 84, 96, 96), fill=CYAN)
    for box, color in (((84, 37, 96, 49), CYAN), ((131, 84, 143, 96), MAGENTA),
                       ((84, 131, 96, 143), BLUE), ((37, 84, 49, 96), CYAN)):
        draw.ellipse(box, fill=color)
    draw.arc((47, 47, 133, 133), 210, 335, fill=CYAN, width=5)


def activity_replay(draw):
    activity_frame(draw)
    draw.arc((48, 48, 132, 132), 35, 330, fill=CYAN, width=7)
    draw.polygon((45, 52, 68, 48, 57, 70), fill=MAGENTA)
    draw.polygon((79, 67, 79, 113, 116, 90), fill=(85, 184, 255, 220))


def activity_timer(draw):
    activity_frame(draw)
    draw.ellipse((48, 50, 132, 134), outline=CYAN, width=6)
    draw.line((90, 36, 90, 51), fill=BLUE, width=6)
    draw.line((74, 36, 106, 36), fill=BLUE, width=5)
    draw.line((90, 92, 90, 65), fill=CYAN, width=5)
    draw.line((90, 92, 112, 105), fill=MAGENTA, width=5)


def activity_cross(draw):
    draw.line((72, 90, 108, 90), fill=RED, width=5)
    draw.line((90, 72, 90, 108), fill=RED, width=5)
    draw.ellipse((87, 87, 93, 93), fill=(255, 255, 255, 255))


def activity_booting(draw):
    activity_frame(draw)
    draw.arc((55, 49, 125, 119), 315, 225, fill=CYAN, width=7)
    draw.line((90, 42, 90, 84), fill=BLUE, width=7)
    draw.arc((68, 62, 112, 106), 315, 225, fill=BLUE, width=4)
    draw.ellipse((84, 126, 96, 138), fill=MAGENTA)


def activity_wifi_connecting(draw):
    activity_frame(draw)
    draw.arc((42, 46, 138, 142), 215, 325, fill=CYAN, width=7)
    draw.arc((58, 64, 122, 128), 215, 325, fill=BLUE, width=7)
    draw.arc((74, 82, 106, 114), 215, 325, fill=CYAN, width=7)
    draw.ellipse((84, 119, 96, 131), fill=MAGENTA)


def activity_backend_connecting(draw):
    activity_frame(draw)
    draw.rounded_rectangle((49, 49, 84, 78), radius=5, outline=BLUE, width=5)
    draw.rounded_rectangle((96, 102, 131, 131), radius=5, outline=CYAN, width=5)
    draw.line((82, 70, 104, 108), fill=CYAN, width=5)
    draw.polygon((96, 98, 111, 108, 97, 116), fill=MAGENTA)
    draw.ellipse((43, 58, 53, 68), fill=CYAN)
    draw.ellipse((127, 111, 137, 121), fill=BLUE)


def button_frame(draw):
    draw.rounded_rectangle((3, 3, 68, 52), radius=8, outline=BLUE, width=2)
    draw.line((11, 3, 61, 3), fill=CYAN, width=2)
    draw.line((11, 52, 61, 52), fill=CYAN, width=2)


def button_timer(draw):
    button_frame(draw)
    draw.ellipse((23, 16, 49, 42), outline=CYAN, width=3)
    draw.line((36, 10, 36, 16), fill=CYAN, width=3)
    draw.line((31, 10, 41, 10), fill=CYAN, width=3)
    draw.line((36, 29, 43, 23), fill=BLUE, width=3)


def button_weather(draw):
    button_frame(draw)
    draw.ellipse((23, 13, 37, 27), outline=YELLOW, width=3)
    draw.arc((22, 23, 46, 43), 100, 285, fill=CYAN, width=3)
    draw.arc((32, 20, 52, 42), 180, 355, fill=CYAN, width=3)
    draw.line((24, 40, 47, 40), fill=CYAN, width=3)


def button_config(draw):
    button_frame(draw)
    for x in (26, 36, 46):
        draw.line((x, 14, x, 42), fill=BLUE, width=3)
    draw.ellipse((22, 20, 30, 28), fill=CYAN)
    draw.ellipse((32, 31, 40, 39), fill=MAGENTA)
    draw.ellipse((42, 16, 50, 24), fill=CYAN)


def button_mute(draw):
    button_frame(draw)
    draw.polygon((20, 23, 27, 23, 36, 16, 36, 40, 27, 33, 20, 33), outline=CYAN)
    draw.line((42, 20, 53, 36), fill=MAGENTA, width=4)
    draw.line((53, 20, 42, 36), fill=MAGENTA, width=4)


def button_dnd(draw):
    button_frame(draw)
    draw.ellipse((23, 15, 49, 41), outline=BLUE, width=3)
    draw.line((27, 37, 45, 19), fill=MAGENTA, width=4)
    draw.line((27, 19, 45, 37), fill=CYAN, width=2)


def button_update(draw):
    button_frame(draw)
    draw.line((36, 13, 36, 34), fill=CYAN, width=4)
    draw.polygon((28, 28, 36, 38, 44, 28), fill=CYAN)
    draw.line((23, 42, 49, 42), fill=BLUE, width=3)
    draw.line((23, 42, 18, 37), fill=MAGENTA, width=2)
    draw.line((49, 42, 54, 37), fill=MAGENTA, width=2)


def volume_bar(draw):
    draw.rounded_rectangle((3, 3, 68, 136), radius=8, fill=(5, 22, 45, 105), outline=BLUE, width=2)
    draw.line((12, 3, 60, 3), fill=CYAN, width=2)
    draw.line((12, 136, 60, 136), fill=CYAN, width=2)

    # Speaker glyph above an empty track; live volume can be drawn over the segments later.
    draw.polygon((17, 20, 23, 20, 31, 14, 31, 34, 23, 28, 17, 28), outline=CYAN)
    draw.arc((27, 16, 43, 32), 300, 60, fill=BLUE, width=2)
    draw.arc((29, 12, 49, 36), 300, 60, fill=CYAN, width=2)
    draw.rounded_rectangle((19, 48, 53, 122), radius=5, outline=BLUE, width=2)
    for y in (57, 69, 81, 93, 105, 117):
        draw.line((25, y, 47, y), fill=(85, 184, 255, 170), width=2)
    draw.line((19, 48, 28, 48), fill=MAGENTA, width=3)
    draw.line((44, 122, 53, 122), fill=CYAN, width=3)


def timer_frame(draw):
    # Angular two-bay frame: primary timer on the left, upcoming timers on the right.
    draw.polygon(
        ((18, 3), (710, 3), (740, 33), (740, 117), (710, 147), (18, 147), (3, 132), (3, 18)),
        fill=(5, 22, 45, 105),
        outline=BLUE,
        width=2,
    )
    draw.line((30, 3, 238, 3), fill=CYAN, width=3)
    draw.line((506, 3, 698, 3), fill=CYAN, width=3)
    draw.line((30, 147, 238, 147), fill=CYAN, width=3)
    draw.line((506, 147, 698, 147), fill=CYAN, width=3)
    draw.line((3, 30, 3, 62), fill=CYAN, width=3)
    draw.line((3, 88, 3, 120), fill=CYAN, width=3)
    draw.line((740, 33, 740, 61), fill=CYAN, width=3)
    draw.line((740, 89, 740, 117), fill=CYAN, width=3)

    # The divider leaves a deliberate opening around the center for visual breathing room.
    draw.line((478, 16, 478, 58), fill=BLUE, width=2)
    draw.line((478, 92, 478, 134), fill=BLUE, width=2)
    draw.line((469, 67, 478, 58, 487, 67), fill=CYAN, width=2)
    draw.line((469, 83, 478, 92, 487, 83), fill=CYAN, width=2)

    for points in (
        ((3, 18), (18, 3), (32, 3)),
        ((710, 3), (740, 33), (740, 47)),
        ((740, 103), (740, 117), (710, 147)),
        ((32, 147), (18, 147), (3, 132)),
    ):
        draw.line(points, fill=CYAN, width=3)
    draw.line((11, 27, 24, 14), fill=MAGENTA, width=4)
    draw.line((719, 14, 732, 27), fill=MAGENTA, width=4)
    draw.line((719, 136, 732, 123), fill=MAGENTA, width=4)
    draw.line((11, 123, 24, 136), fill=MAGENTA, width=4)

    for x in (48, 58, 68):
        draw.line((x, 12, x + 6, 12), fill=BLUE, width=2)
    for x in (658, 668, 678):
        draw.line((x, 138, x + 6, 138), fill=BLUE, width=2)


def timer_list_frame(draw):
    draw.polygon(
        ((16, 3), (270, 3), (297, 30), (297, 120), (270, 147), (16, 147), (3, 134), (3, 16)),
        fill=(5, 22, 45, 105),
        outline=BLUE,
        width=2,
    )
    draw.line((28, 3, 120, 3), fill=CYAN, width=3)
    draw.line((180, 3, 258, 3), fill=CYAN, width=3)
    draw.line((28, 147, 120, 147), fill=CYAN, width=3)
    draw.line((180, 147, 258, 147), fill=CYAN, width=3)
    draw.line((3, 28, 3, 62), fill=CYAN, width=3)
    draw.line((3, 88, 3, 122), fill=CYAN, width=3)
    draw.line((297, 30, 297, 60), fill=CYAN, width=3)
    draw.line((297, 90, 297, 120), fill=CYAN, width=3)

    # Three list rows, with gaps at each end so the separators stay decorative.
    draw.line((24, 51, 276, 51), fill=(85, 184, 255, 135), width=1)
    draw.line((24, 99, 276, 99), fill=(85, 184, 255, 135), width=1)
    for y in (51, 99):
        draw.line((24, y, 36, y), fill=CYAN, width=2)
        draw.line((264, y, 276, y), fill=CYAN, width=2)

    draw.line((11, 25, 23, 13), fill=MAGENTA, width=4)
    draw.line((277, 13, 289, 25), fill=MAGENTA, width=4)
    draw.line((277, 137, 289, 125), fill=MAGENTA, width=4)
    draw.line((11, 125, 23, 137), fill=MAGENTA, width=4)
    for x in (132, 142, 152):
        draw.line((x, 12, x + 6, 12), fill=BLUE, width=2)


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
    save("activity_listening", activity_listening, 180)
    save("activity_thinking", activity_thinking, 180)
    save("activity_replay", activity_replay, 180)
    save("activity_timer", activity_timer, 180)
    save("activity_cross", activity_cross, 180)
    save("activity_booting", activity_booting, 180)
    save("activity_wifi_connecting", activity_wifi_connecting, 180)
    save("activity_backend_connecting", activity_backend_connecting, 180)
    save("button_timer", button_timer, (72, 56))
    save("button_weather", button_weather, (72, 56))
    save("button_config", button_config, (72, 56))
    save("button_mute", button_mute, (72, 56))
    save("button_dnd", button_dnd, (72, 56))
    save("button_update", button_update, (72, 56))
    save("volume_bar", volume_bar, (72, 140))
    save("timer_frame", timer_frame, (744, 150))
    save("timer_list_frame", timer_list_frame, (300, 150))


if __name__ == "__main__":
    main()
