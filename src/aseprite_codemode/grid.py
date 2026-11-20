"""Pure-Python 2D character canvas (`PixelGrid`) for pre-render composition and QA."""

from __future__ import annotations

from collections import deque


class PixelGrid:
    """In-memory 2D character grid compatible with `draw_rows` / `ase.batch.draw_rows`."""

    def __init__(self, width: int, height: int, fill: str = ".") -> None:
        if int(width) <= 0 or int(height) <= 0:
            raise ValueError("Grid width and height must be > 0")
        if not isinstance(fill, str) or len(fill) != 1:
            raise ValueError("fill must be a single character")
        self._width = int(width)
        self._height = int(height)
        self._cells: list[list[str]] = [[fill] * self._width for _ in range(self._height)]

    @classmethod
    def from_rows(cls, rows: list[str]) -> "PixelGrid":
        """Construct a `PixelGrid` from equal-width row strings."""
        if not rows:
            raise ValueError("rows cannot be empty")
        width = len(rows[0])
        if width <= 0:
            raise ValueError("rows cannot have zero width")
        for idx, row in enumerate(rows):
            if len(row) != width:
                raise ValueError(f"Row {idx + 1} width {len(row)} does not match row 1 width {width}")
        grid = cls(width, len(rows))
        grid._cells = [list(row) for row in rows]
        return grid

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @staticmethod
    def _validate_char(ch: str, name: str = "ch") -> str:
        if not isinstance(ch, str) or len(ch) != 1:
            raise ValueError(f"{name} must be a single character")
        return ch

    def set(self, x: int, y: int, ch: str) -> "PixelGrid":
        """Set pixel `(x, y)` if inside bounds; silently ignores out-of-bounds coordinates."""
        self._validate_char(ch)
        ix, iy = int(x), int(y)
        if 0 <= ix < self._width and 0 <= iy < self._height:
            self._cells[iy][ix] = ch
        return self

    def get(self, x: int, y: int, default: str = ".") -> str:
        """Return character at `(x, y)` or `default` when out of bounds."""
        ix, iy = int(x), int(y)
        if 0 <= ix < self._width and 0 <= iy < self._height:
            return self._cells[iy][ix]
        return default

    def fill(self, ch: str = ".") -> "PixelGrid":
        """Fill the entire grid with `ch`."""
        self._validate_char(ch)
        for row in self._cells:
            for x in range(self._width):
                row[x] = ch
        return self

    def rect(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        ch: str,
        filled: bool = True,
    ) -> "PixelGrid":
        """Draw a filled or 1px stroked rectangle."""
        self._validate_char(ch)
        ix, iy, iw, ih = int(x), int(y), int(w), int(h)
        if iw <= 0 or ih <= 0:
            raise ValueError("Rectangle width and height must be > 0")
        for dy in range(ih):
            for dx in range(iw):
                if filled or dx == 0 or dy == 0 or dx == iw - 1 or dy == ih - 1:
                    self.set(ix + dx, iy + dy, ch)
        return self

    def line(self, x1: int, y1: int, x2: int, y2: int, ch: str) -> "PixelGrid":
        """Draw a 1px integer Bresenham line from `(x1, y1)` to `(x2, y2)`."""
        self._validate_char(ch)
        cx, cy = int(x1), int(y1)
        tx, ty = int(x2), int(y2)
        dx = abs(tx - cx)
        sx = 1 if cx < tx else -1
        dy = -abs(ty - cy)
        sy = 1 if cy < ty else -1
        err = dx + dy
        while True:
            self.set(cx, cy, ch)
            if cx == tx and cy == ty:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                cx += sx
            if e2 <= dx:
                err += dx
                cy += sy
        return self

    def ellipse(
        self,
        cx: int,
        cy: int,
        rx: int,
        ry: int,
        ch: str,
        filled: bool = True,
    ) -> "PixelGrid":
        """Draw a filled or 1px stroked ellipse centered at `(cx, cy)`."""
        self._validate_char(ch)
        icx, icy, irx, iry = int(cx), int(cy), int(rx), int(ry)
        if irx <= 0 or iry <= 0:
            raise ValueError("Ellipse rx and ry must be > 0")

        def _in_ell(x: int, y: int) -> bool:
            return (x * x) / (irx * irx) + (y * y) / (iry * iry) <= 1.0

        for dy in range(-iry, iry + 1):
            for dx in range(-irx, irx + 1):
                if not _in_ell(dx, dy):
                    continue
                if filled or not (
                    _in_ell(dx - 1, dy)
                    and _in_ell(dx + 1, dy)
                    and _in_ell(dx, dy - 1)
                    and _in_ell(dx, dy + 1)
                ):
                    self.set(icx + dx, icy + dy, ch)
        return self

    def flood_fill(self, x: int, y: int, ch: str) -> "PixelGrid":
        """4-connected iterative flood fill starting at `(x, y)`."""
        self._validate_char(ch)
        ix, iy = int(x), int(y)
        if not (0 <= ix < self._width and 0 <= iy < self._height):
            return self
        target = self._cells[iy][ix]
        if target == ch:
            return self
        queue: deque[tuple[int, int]] = deque([(ix, iy)])
        self._cells[iy][ix] = ch
        while queue:
            px, py = queue.popleft()
            for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                if 0 <= nx < self._width and 0 <= ny < self._height and self._cells[ny][nx] == target:
                    self._cells[ny][nx] = ch
                    queue.append((nx, ny))
        return self

    def draw_rows(
        self,
        x: int,
        y: int,
        rows: list[str],
        transparent: str = ".",
    ) -> "PixelGrid":
        """Blit ASCII row strings at `(x, y)`, skipping `transparent` characters."""
        self._validate_char(transparent, "transparent")
        if not rows:
            raise ValueError("rows cannot be empty")
        ix, iy = int(x), int(y)
        for dy, row in enumerate(rows):
            for dx, ch in enumerate(row):
                if ch != transparent:
                    self.set(ix + dx, iy + dy, ch)
        return self

    def stamp(
        self,
        other: "PixelGrid",
        x: int = 0,
        y: int = 0,
        transparent: str = ".",
    ) -> "PixelGrid":
        """Composite `other` onto this grid at `(x, y)`, skipping `transparent` pixels."""
        self._validate_char(transparent, "transparent")
        ix, iy = int(x), int(y)
        for sy in range(other._height):
            for sx in range(other._width):
                ch = other._cells[sy][sx]
                if ch != transparent:
                    self.set(ix + sx, iy + sy, ch)
        return self

    def replace(self, old_ch: str, new_ch: str) -> "PixelGrid":
        """Replace every occurrence of `old_ch` with `new_ch`."""
        self._validate_char(old_ch, "old_ch")
        self._validate_char(new_ch, "new_ch")
        for row in self._cells:
            for x in range(self._width):
                if row[x] == old_ch:
                    row[x] = new_ch
        return self

    def outline(
        self,
        ch: str,
        transparent: str = ".",
        include_diagonals: bool = False,
    ) -> "PixelGrid":
        """Add a 1px outline around all non-transparent pixels using a snapshot copy."""
        self._validate_char(ch)
        self._validate_char(transparent, "transparent")
        snap = [row[:] for row in self._cells]
        dirs = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        if include_diagonals:
            dirs.extend([(-1, -1), (1, -1), (-1, 1), (1, 1)])
        for y in range(self._height):
            for x in range(self._width):
                if snap[y][x] != transparent:
                    continue
                for dx, dy in dirs:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < self._width and 0 <= ny < self._height and snap[ny][nx] != transparent:
                        self._cells[y][x] = ch
                        break
        return self

    def copy(self) -> "PixelGrid":
        """Return an independent deep copy of this grid."""
        out = PixelGrid(self._width, self._height)
        out._cells = [row[:] for row in self._cells]
        return out

    def shift(self, dx: int, dy: int, fill: str = ".") -> "PixelGrid":
        """Return a new grid shifted by `(dx, dy)`, filling exposed edges with `fill`."""
        self._validate_char(fill, "fill")
        idx, idy = int(dx), int(dy)
        out = PixelGrid(self._width, self._height, fill=fill)
        for y in range(self._height):
            for x in range(self._width):
                ch = self._cells[y][x]
                if ch != fill:
                    out.set(x + idx, y + idy, ch)
        return out

    def flip_x(self) -> "PixelGrid":
        """Return a horizontally mirrored copy of this grid."""
        out = PixelGrid(self._width, self._height)
        out._cells = [list(reversed(row)) for row in self._cells]
        return out

    def flip_y(self) -> "PixelGrid":
        """Return a vertically mirrored copy of this grid."""
        out = PixelGrid(self._width, self._height)
        out._cells = [row[:] for row in reversed(self._cells)]
        return out

    def crop(self, x: int, y: int, w: int, h: int) -> "PixelGrid":
        """Extract a `w x h` sub-grid starting at `(x, y)` (out-of-bounds padded with `.`)."""
        ix, iy, iw, ih = int(x), int(y), int(w), int(h)
        if iw <= 0 or ih <= 0:
            raise ValueError("Crop width and height must be > 0")
        out = PixelGrid(iw, ih)
        for dy in range(ih):
            for dx in range(iw):
                out._cells[dy][dx] = self.get(ix + dx, iy + dy, ".")
        return out

    def bounds(self, transparent: str = ".") -> dict[str, int] | None:
        """Return bounding box and count of non-transparent pixels, or None if empty."""
        self._validate_char(transparent, "transparent")
        min_x = self._width
        min_y = self._height
        max_x = -1
        max_y = -1
        count = 0
        for y in range(self._height):
            row = self._cells[y]
            for x in range(self._width):
                if row[x] != transparent:
                    count += 1
                    if x < min_x:
                        min_x = x
                    if x > max_x:
                        max_x = x
                    if y < min_y:
                        min_y = y
                    if y > max_y:
                        max_y = y
        if count == 0:
            return None
        return {
            "x": min_x,
            "y": min_y,
            "w": max_x - min_x + 1,
            "h": max_y - min_y + 1,
            "left_x": min_x,
            "right_x": max_x,
            "top_y": min_y,
            "bottom_y": max_y,
            "count": count,
        }

    def bottom_y(self, transparent: str = ".") -> int | None:
        """Return the bottom-most non-transparent Y coordinate (ground contact check)."""
        b = self.bounds(transparent=transparent)
        return b["bottom_y"] if b is not None else None

    def count_by_char(self, transparent: str = ".") -> dict[str, int]:
        """Count non-transparent characters across the grid."""
        self._validate_char(transparent, "transparent")
        counts: dict[str, int] = {}
        for row in self._cells:
            for ch in row:
                if ch != transparent:
                    counts[ch] = counts.get(ch, 0) + 1
        return counts

    def to_rows(self) -> list[str]:
        """Return grid rows as strings suitable for `draw_rows`."""
        return ["".join(row) for row in self._cells]

    def render_ascii(self) -> str:
        """Return newline-joined grid representation."""
        return "\n".join(self.to_rows())
