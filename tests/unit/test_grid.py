"""Unit tests for pure-Python PixelGrid 2D canvas."""

from __future__ import annotations

import pytest

from aseprite_codemode.grid import PixelGrid


def test_grid_creation_drawing_and_bounds() -> None:
    g = PixelGrid(12, 10)
    assert g.width == 12
    assert g.height == 10
    assert g.bounds() is None
    assert g.bottom_y() is None

    g.rect(2, 3, 4, 3, "B", filled=True)
    g.line(0, 0, 2, 2, "L")
    g.set(99, 99, "X")  # out-of-bounds ignored safely
    assert g.get(99, 99) == "."

    b = g.bounds()
    assert b is not None
    assert b["left_x"] == 0
    assert b["top_y"] == 0
    assert b["right_x"] == 5
    assert b["bottom_y"] == 5
    assert g.bottom_y() == 5
    assert g.count_by_char() == {"B": 12, "L": 3}


def test_grid_ellipse_flood_fill_and_outline() -> None:
    g = PixelGrid(9, 9)
    g.ellipse(4, 4, 2, 2, "G", filled=False)
    assert g.get(4, 4) == "."
    g.flood_fill(4, 4, "F")
    assert g.get(4, 4) == "F"
    assert g.get(0, 0) == "."

    g.outline("K", include_diagonals=False)
    assert g.get(1, 4) == "K"
    assert g.get(0, 0) == "."


def test_grid_stamp_transforms_and_rows() -> None:
    part = PixelGrid.from_rows(["AB", ".C"])
    canvas = PixelGrid(6, 5)
    canvas.stamp(part, 2, 1)
    assert canvas.get(2, 1) == "A"
    assert canvas.get(3, 1) == "B"
    assert canvas.get(2, 2) == "."
    assert canvas.get(3, 2) == "C"

    flipped = part.flip_x()
    assert flipped.to_rows() == ["BA", "C."]

    shifted = part.shift(1, 0)
    assert shifted.to_rows() == [".A", ".."]

    cropped = canvas.crop(2, 1, 2, 2)
    assert cropped.to_rows() == ["AB", ".C"]

    cropped.replace("C", "Z")
    assert cropped.render_ascii() == "AB\n.Z"


def test_grid_validation_errors() -> None:
    with pytest.raises(ValueError):
        PixelGrid(0, 5)
    with pytest.raises(ValueError):
        PixelGrid(4, 4, fill="ab")
    with pytest.raises(ValueError):
        PixelGrid.from_rows(["AA", "B"])
