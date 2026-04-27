import subprocess
from pathlib import Path

import pytest


def converter_python() -> str:
    candidate = Path.home() / ".espressif/python_env/idf6.1_py3.11_env/bin/python"
    python = str(candidate if candidate.exists() else Path(".venv/bin/python"))
    probe = subprocess.run([python, "-c", "from PIL import Image"], check=False)
    if probe.returncode != 0:
        pytest.skip("Pillow is not installed in the available converter Python environment")
    return python


def write_rgba_png(python: str, path: Path, size: tuple[int, int], pixels: list[tuple[int, int, int, int]]) -> None:
    script = (
        "from PIL import Image\n"
        "import ast, sys\n"
        "image = Image.new('RGBA', ast.literal_eval(sys.argv[2]))\n"
        "image.putdata(ast.literal_eval(sys.argv[3]))\n"
        "image.save(sys.argv[1])\n"
    )
    subprocess.run([python, "-c", script, str(path), repr(size), repr(pixels)], check=True)


def test_convert_image_writes_rgb565_and_alpha8_mask(tmp_path):
    source = tmp_path / "avatar.png"
    output = tmp_path / "avatar.rgb565"
    alpha = tmp_path / "avatar.alpha8"
    python = converter_python()
    write_rgba_png(
        python,
        source,
        (2, 2),
        [
            (255, 0, 0, 0),
            (0, 255, 0, 128),
            (0, 0, 255, 255),
            (255, 255, 255, 64),
        ],
    )

    subprocess.run(
        [
            python,
            "firmware/tools/convert_image.py",
            str(source),
            str(output),
            "--format",
            "raw-rgb565",
            "--width",
            "2",
            "--height",
            "2",
            "--fit",
            "stretch",
            "--alpha-output",
            str(alpha),
        ],
        check=True,
    )

    assert output.read_bytes() == bytes.fromhex("00f8e0071f00ffff")
    assert alpha.read_bytes() == bytes([0, 128, 255, 64])


def test_convert_image_writes_alpha1_mask(tmp_path):
    source = tmp_path / "icon.png"
    output = tmp_path / "icon.rgb565"
    alpha = tmp_path / "icon.alpha1"
    python = converter_python()
    write_rgba_png(
        python,
        source,
        (8, 1),
        [(255, 255, 255, value) for value in (0, 127, 128, 255, 1, 200, 90, 255)],
    )

    subprocess.run(
        [
            python,
            "firmware/tools/convert_image.py",
            str(source),
            str(output),
            "--format",
            "raw-rgb565",
            "--width",
            "8",
            "--height",
            "1",
            "--fit",
            "stretch",
            "--alpha-output",
            str(alpha),
            "--alpha-mask-format",
            "alpha1",
        ],
        check=True,
    )

    assert output.stat().st_size == 16
    assert alpha.read_bytes() == bytes([0b10101100])
