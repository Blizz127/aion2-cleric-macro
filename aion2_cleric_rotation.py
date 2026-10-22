#!/usr/bin/env python3
"""One-button personal AION 2 Cleric solo rotation for Linux/Wayland.

F8 turns combat on and repeated presses leave it on; F9 turns it off. Leaving
the AION 2 window pauses input until you return. The local JSON profile
owns a dedicated hotbar lane across the full leveling range. Recovery follows
the visible HP bar; damage runs only while the HUD confirms a hostile target.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import random
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from bisect import bisect_left
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

try:
    import evdev
    from evdev import UInput, ecodes as e
except ImportError:  # pragma: no cover - host dependency check
    sys.exit(
        "python-evdev is required.\n"
        "Install it with: python3 -m pip install --user evdev"
    )


APP_ID = "3393110"
UINPUT_NAME = "aion2-cleric-rotation"
TOGGLE_KEY = "KEY_F8"
PANIC_KEY = "KEY_F9"
DEFAULT_PROFILE_FILE = Path(__file__).with_name("cleric_personal.json")
DEFAULT_CONTROLS_FILE = Path(__file__).with_name("controls.json")
MAX_LEVEL = 50
DEFAULT_LEVEL = MAX_LEVEL
IDLE_SLEEP = 0.010


@dataclass
class Step:
    """A managed hotbar action and its minimum retry interval."""

    name: str
    key: str
    interval: float
    activation_level: int
    enabled: bool = True
    role: str = "damage"
    health_below: float | None = None
    retry_jitter: float = 0.0
    next_due: float = 0.0

    def active(self, level: int) -> bool:
        return self.enabled and level >= self.activation_level

    def ready_for_health(self, health: float | None) -> bool:
        if self.health_below is None:
            return True
        return health is not None and health <= self.health_below


@dataclass(frozen=True)
class Timing:
    hold_min: float
    hold_max: float
    action_gap_min: float
    action_gap_max: float


@dataclass(frozen=True)
class Profile:
    label: str
    character: str
    min_level: int
    max_level: int
    steps: tuple[Step, ...]
    manual: str
    timing: Timing


# One X client at a time: `import` captures and `xprop` focus probes must not
# overlap, or a slow capture stalls the probe past its timeout and the guard
# would read a focused game as unfocused.
X_LOCK = threading.Lock()


@dataclass
class RuntimeGuard:
    """Throttle expensive process/window checks while remaining fail-closed.

    `focused` may answer None when the window state could not be read right
    now (probe busy or timed out). The last known answer is kept for up to
    `unknown_grace` seconds; after that the guard fails closed to unfocused.
    """

    interval: float = 0.2
    unknown_grace: float = 2.0
    next_check: float = 0.0
    last_focused: bool | None = None
    unknown_since: float | None = None

    def reset(self) -> None:
        self.next_check = 0.0
        self.last_focused = None
        self.unknown_since = None

    def poll(
        self,
        now: float,
        process_running: Callable[[], bool],
        focused: Callable[[], bool | None],
    ) -> str:
        if now < self.next_check:
            return "cached"
        self.next_check = now + self.interval
        if not process_running():
            return "exited"
        answer = focused()
        if answer is None:
            if self.unknown_since is None:
                self.unknown_since = now
            if self.last_focused is None or now - self.unknown_since > self.unknown_grace:
                return "unfocused"
            return "ok" if self.last_focused else "unfocused"
        self.unknown_since = None
        self.last_focused = answer
        return "ok" if answer else "unfocused"


@dataclass
class KeyDebouncer:
    """Collapse duplicate hotkey events emitted by mirrored input devices."""

    interval: float = 0.4
    last_press: dict[int, float] = field(default_factory=dict)

    def accept(self, code: int, now: float) -> bool:
        previous = self.last_press.get(code, float("-inf"))
        if now - previous < self.interval:
            return False
        self.last_press[code] = now
        return True


def capture_window_crop(
    window_id: str,
    left: int,
    top: int,
    width: int,
    height: int,
    *,
    resize_percent: int = 100,
) -> bytes:
    """Capture one small 8-bit RGB crop from the verified game window."""
    resize = [] if resize_percent == 100 else ["-resize", f"{resize_percent}%"]
    with X_LOCK:
        image = subprocess.run(
            [
                "import",
                "-window",
                window_id,
                "-crop",
                f"{width}x{height}+{left}+{top}",
                *resize,
                "-depth",
                "8",
                "rgb:-",
            ],
            check=True,
            capture_output=True,
            timeout=2.5,
        ).stdout
    output_width = (width * resize_percent + 99) // 100
    output_height = (height * resize_percent + 99) // 100
    if len(image) != output_width * output_height * 3:
        raise OSError("AION 2 HUD crop returned an unexpected size")
    return image


def hostile_target_marker_present(image: bytes, width: int, height: int) -> bool:
    """Recognize AION's cyan lock brackets around a hostile red target plate."""
    if width <= 0 or height <= 0 or len(image) != width * height * 3:
        return False

    max_marker_width = max(12, round(width * 0.04))
    max_marker_height = max(20, round(height * 0.15))
    runs: list[tuple[int, int, int, int, int]] = []
    parents: list[int] = []
    previous_row: list[int] = []

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for y in range(height):
        current_row: list[int] = []
        x = 0
        while x < width:
            offset = (y * width + x) * 3
            red, green, blue = image[offset : offset + 3]
            is_cyan = (
                green > 120
                and blue > 140
                and blue * 100 > red * 135
                and green * 100 > red * 120
            )
            if not is_cyan:
                x += 1
                continue
            start = x
            x += 1
            while x < width:
                offset = (y * width + x) * 3
                red, green, blue = image[offset : offset + 3]
                if not (
                    green > 120
                    and blue > 140
                    and blue * 100 > red * 135
                    and green * 100 > red * 120
                ):
                    break
                x += 1
            stop = x - 1
            if stop - start + 1 > max_marker_width:
                continue
            index = len(runs)
            runs.append((stop - start + 1, start, y, stop, y))
            parents.append(index)
            current_row.append(index)
            for previous in previous_row:
                previous_run = runs[previous]
                if previous_run[1] <= stop + 1 and previous_run[3] >= start - 1:
                    union(index, previous)
        previous_row = current_row

    merged: dict[int, list[int]] = {}
    for index, run in enumerate(runs):
        root = find(index)
        if root not in merged:
            merged[root] = [run[0], run[1], run[2], run[3], run[4]]
        else:
            component = merged[root]
            component[0] += run[0]
            component[1] = min(component[1], run[1])
            component[2] = min(component[2], run[2])
            component[3] = max(component[3], run[3])
            component[4] = max(component[4], run[4])

    components = [tuple(component) for component in merged.values()]
    markers = [
        component
        for component in components
        if component[0] >= 3
        and component[3] - component[1] <= max_marker_width
        and component[4] - component[2] <= max_marker_height
    ]
    minimum_gap = max(20, round(width * 0.04))
    maximum_gap = round(width * 0.50)
    y_tolerance = max(10, round(height * 0.06))

    # Busy scenes can produce hundreds of bracket candidates. Classify each row
    # once and answer every pair from sorted red columns instead of rescanning
    # the pixels between each pair.
    red_rows: dict[int, list[int]] = {}

    def red_columns(y: int) -> list[int]:
        columns = red_rows.get(y)
        if columns is None:
            row = y * width * 3
            row_end = row + width * 3
            columns = [
                x
                for x, (red, green, blue) in enumerate(
                    zip(
                        image[row:row_end:3],
                        image[row + 1 : row_end : 3],
                        image[row + 2 : row_end : 3],
                    )
                )
                if red > 140 and red * 10 > green * 15 and red * 10 > blue * 13
            ]
            red_rows[y] = columns
        return columns

    for left in markers:
        left_x = (left[1] + left[3]) // 2
        left_y = (left[2] + left[4]) // 2
        for right in markers:
            right_x = (right[1] + right[3]) // 2
            right_y = (right[2] + right[4]) // 2
            gap = right_x - left_x
            if not minimum_gap <= gap <= maximum_gap:
                continue
            if abs(right_y - left_y) > y_tolerance:
                continue

            x_start = max(0, left_x)
            x_stop = min(width, right_x + 1)
            y_start = max(0, min(left[2], right[2]) - round(height * 0.08))
            y_stop = min(height, max(left[4], right[4]) + round(height * 0.05))
            red_count = 0
            first_red = x_stop
            last_red = -1
            for y in range(y_start, y_stop):
                columns = red_columns(y)
                low = bisect_left(columns, x_start)
                high = bisect_left(columns, x_stop)
                if low < high:
                    red_count += high - low
                    first_red = min(first_red, columns[low])
                    last_red = max(last_red, columns[high - 1])
            area = max(1, (x_stop - x_start) * (y_stop - y_start))
            if (
                red_count / area >= 0.02
                and last_red >= 0
                and last_red - first_red >= gap * 0.35
            ):
                return True
    return False


class HudReader:
    """Run one HUD capture at a time in the background so polling never blocks.

    `poll(now)` returns the latest completed value immediately. A new capture
    starts on the configured cadence once the previous one has finished, and a
    value older than `max_age` (or a failed capture) reads as None so the
    caller fails closed instead of acting on a stale HUD.
    """

    def __init__(
        self, read: Callable[[], object], *, interval: float, max_age: float
    ) -> None:
        self.read = read
        self.interval = interval
        self.max_age = max_age
        self.value: object | None = None
        self.read_at: float | None = None
        self.failed = False
        self.next_start = 0.0
        self._thread: threading.Thread | None = None
        self._outcome: tuple[object | None, BaseException | None] | None = None
        self._generation = 0

    def _run(self, generation: int) -> None:
        try:
            outcome: tuple[object | None, BaseException | None] = (self.read(), None)
        except Exception as exc:  # noqa: BLE001 - any capture failure fails closed
            outcome = (None, exc)
        if generation == self._generation:
            self._outcome = outcome

    def reset(self) -> None:
        """Discard the current value and any capture still in flight."""
        self._generation += 1
        self._thread = None
        self._outcome = None
        self.value = None
        self.read_at = None
        self.failed = False
        self.next_start = 0.0

    def poll(self, now: float) -> object | None:
        if self._thread is not None and not self._thread.is_alive():
            self._thread.join()
            self._thread = None
            if self._outcome is not None:
                value, error = self._outcome
                self._outcome = None
                self.failed = error is not None
                self.value = None if self.failed else value
                self.read_at = None if self.failed else now
        if self._thread is None and now >= self.next_start:
            self.next_start = now + self.interval
            self._thread = threading.Thread(
                target=self._run, args=(self._generation,), daemon=True
            )
            self._thread.start()
        if self.read_at is not None and now - self.read_at > self.max_age:
            return None
        return None if self.failed else self.value

    def close(self) -> None:
        self._generation += 1
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.5)


def _is_hp_red(red: int, green: int, blue: int) -> bool:
    return red > 90 and red > green * 2.5 and red > blue * 2


def locate_health_bar(
    image: bytes, width: int, height: int
) -> tuple[int, int] | None:
    """Find the HP bar inside a search crop: (middle row, red run start).

    The crop covers the left end of the bar. Rows whose left region is mostly
    red belong to the bar; the middle one is returned together with the first
    red column so the fill can be measured from the bar's true left edge.
    """
    if width <= 0 or height <= 0 or len(image) != width * height * 3:
        return None
    rows: list[tuple[int, int]] = []
    for y in range(height):
        first: int | None = None
        reds = 0
        for x in range(width):
            offset = (y * width + x) * 3
            if _is_hp_red(image[offset], image[offset + 1], image[offset + 2]):
                reds += 1
                if first is None:
                    first = x
        if first is not None and reds * 2 >= width - first:
            rows.append((y, first))
    if len(rows) < 3:
        return None
    # Take the longest contiguous band so stray red pixels cannot win.
    best: list[tuple[int, int]] = []
    current: list[tuple[int, int]] = []
    for row in rows:
        if current and row[0] != current[-1][0] + 1:
            if len(current) > len(best):
                best = current
            current = []
        current.append(row)
    if len(current) > len(best):
        best = current
    if len(best) < 3:
        return None
    middle = best[len(best) // 2]
    return middle[0], min(first for _, first in best)


def health_fill_fraction(
    image: bytes, width: int, height: int, bar_width: int
) -> float | None:
    """Fraction of the bar that is filled, from the rightmost red column.

    The crop starts at the bar's left edge and spans `bar_width` columns (it
    may be wider). Each row votes with its own rightmost red column; the
    median wins so a slanted end cap or a stray pixel does not skew it. None
    means no red at all, which is treated as unreadable rather than 0% HP.
    """
    if width <= 0 or height <= 0 or len(image) != width * height * 3:
        return None
    edges: list[int] = []
    for y in range(height):
        last: int | None = None
        for x in range(width):
            offset = (y * width + x) * 3
            if _is_hp_red(image[offset], image[offset + 1], image[offset + 2]):
                last = x
        if last is not None:
            edges.append(last)
    if not edges:
        return None
    edges.sort()
    edge = edges[len(edges) // 2]
    return max(0.0, min(1.0, (edge + 1) / max(1, bar_width)))


class HealthMonitor:
    """Read Blizz's HUD health bar without touching game memory.

    The bar is located on the screen at the first read (and again whenever a
    read finds no red), so window size, aspect ratio and HUD scale do not
    matter. Only the game window is captured.
    """

    # The bar sits left of the centre emblem; these bound the search area.
    SEARCH_LEFT = 0.33
    SEARCH_RIGHT = 0.41
    SEARCH_TOP = 0.85
    SEARCH_BOTTOM = 0.94
    RIGHT_END_GAP = 0.04  # bar right end = width/2 - gap * height

    def __init__(self, interval: float = 0.4, max_age: float = 2.0) -> None:
        self.interval = interval
        self.health: float | None = None
        windows = subprocess.run(
            ["wmctrl", "-lx"],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
        self.window_id = find_aion_window_id(windows)
        if self.window_id is None:
            raise OSError("AION 2 window was not found")
        geometry = subprocess.run(
            ["xwininfo", "-id", self.window_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
        width_match = re.search(r"^\s*Width:\s*(\d+)", geometry, re.MULTILINE)
        height_match = re.search(r"^\s*Height:\s*(\d+)", geometry, re.MULTILINE)
        if width_match is None or height_match is None:
            raise OSError("AION 2 window geometry could not be read")
        self.width = int(width_match.group(1))
        self.height = int(height_match.group(1))
        self.bar_row: int | None = None
        self.bar_left: int | None = None
        self.bar_right = round(self.width / 2 - self.RIGHT_END_GAP * self.height)
        self.reader = HudReader(self.read_health, interval=interval, max_age=max_age)

    def locate(self) -> bool:
        """Find the bar's row and left edge; runs on the reader thread."""
        left = round(self.width * self.SEARCH_LEFT)
        top = round(self.height * self.SEARCH_TOP)
        width = max(20, round(self.width * (self.SEARCH_RIGHT - self.SEARCH_LEFT)))
        height = max(10, round(self.height * (self.SEARCH_BOTTOM - self.SEARCH_TOP)))
        image = capture_window_crop(self.window_id, left, top, width, height)
        found = locate_health_bar(image, width, height)
        if found is None:
            self.bar_row = None
            self.bar_left = None
            return False
        self.bar_row = top + found[0]
        self.bar_left = left + found[1]
        return True

    def read_health(self) -> float:
        """Capture the bar's middle rows and measure the fill (reader thread)."""
        if self.bar_row is None and not self.locate():
            raise OSError("AION 2 HP bar not found on screen")
        assert self.bar_row is not None and self.bar_left is not None
        bar_width = max(20, self.bar_right - self.bar_left)
        image = capture_window_crop(
            self.window_id, self.bar_left, self.bar_row - 1, bar_width, 3
        )
        fraction = health_fill_fraction(image, bar_width, 3, bar_width)
        if fraction is None:
            # The HUD moved, is hidden, or HP is empty: re-locate once and
            # otherwise report unreadable instead of 0%.
            if self.locate():
                bar_width = max(20, self.bar_right - self.bar_left)
                image = capture_window_crop(
                    self.window_id, self.bar_left, self.bar_row - 1, bar_width, 3
                )
                fraction = health_fill_fraction(image, bar_width, 3, bar_width)
            if fraction is None:
                raise OSError("AION 2 HP bar unreadable")
        return fraction

    def poll(self, now: float) -> float | None:
        value = self.reader.poll(now)
        self.health = value if isinstance(value, float) else None
        return self.health

    def close(self) -> None:
        self.reader.close()


class TargetMonitor:
    """Gate damage using the game's visible hostile-target lock marker.

    The marker is read in the background. Damage stays allowed for a short
    grace period after the marker was last seen so a spell effect flashing
    over the brackets does not stutter the rotation; a lost target, an
    unreadable HUD, or a stale read still pauses damage within that window.
    """

    def __init__(
        self,
        window_id: str,
        width: int,
        height: int,
        *,
        interval: float = 0.25,
        max_age: float = 1.0,
        grace: float = 0.6,
    ) -> None:
        self.window_id = window_id
        self.left = round(width * 0.12)
        self.top = round(height * 0.10)
        self.capture_width = max(200, round(width * 0.68))
        self.capture_height = max(120, round(height * 0.68))
        self.width = (self.capture_width + 1) // 2
        self.height = (self.capture_height + 1) // 2
        self.interval = interval
        self.grace = grace
        self.reader = HudReader(self.read_marker, interval=interval, max_age=max_age)
        self.reset()

    def read_marker(self) -> bool:
        """Capture the target area and look for the lock marker (reader thread)."""
        image = capture_window_crop(
            self.window_id,
            self.left,
            self.top,
            self.capture_width,
            self.capture_height,
            resize_percent=50,
        )
        return hostile_target_marker_present(image, self.width, self.height)

    def reset(self) -> None:
        self.reader.reset()
        self.targeted = False
        self.last_seen: float | None = None
        self.reported_state: bool | None = None

    def damage_allowed(self) -> bool:
        return self.targeted

    def poll(self, now: float) -> str | None:
        value = self.reader.poll(now)
        if value is True:
            self.last_seen = now
        self.targeted = self.last_seen is not None and now - self.last_seen <= self.grace
        if self.targeted:
            if self.reported_state is not True:
                self.reported_state = True
                return "hostile target confirmed"
            return None
        if self.reported_state is not False:
            self.reported_state = False
            if value is None and self.reader.failed:
                return "target HUD unreadable; damage paused"
            return "no hostile target; damage paused"
        return None

    def close(self) -> None:
        self.reader.close()


def _number(value: object, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if result < minimum:
        raise ValueError(f"{field} must be at least {minimum:g}")
    return result


def _level(value: object, field: str) -> int:
    result = _number(value, field, minimum=1)
    if not result.is_integer():
        raise ValueError(f"{field} must be a whole number")
    return int(result)


def load_profile(path: Path) -> Profile:
    """Load and validate the one local, character-scoped rotation profile."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Profile file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Profile is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Profile root must be a JSON object")

    label = data.get("label")
    character = data.get("character")
    manual = data.get("manual")
    strings = (label, character, manual)
    if not all(isinstance(value, str) and value.strip() for value in strings):
        raise ValueError("label, character, and manual must be non-empty strings")

    min_level = _level(data.get("min_level"), "min_level")
    max_level = _level(data.get("max_level"), "max_level")
    if min_level != 1 or max_level != MAX_LEVEL:
        raise ValueError(f"Personal profile must cover levels 1 through {MAX_LEVEL}")

    timing_data = data.get("timing")
    if not isinstance(timing_data, dict):
        raise ValueError("timing must be a JSON object")
    timing = Timing(
        hold_min=_number(timing_data.get("hold_min"), "timing.hold_min"),
        hold_max=_number(timing_data.get("hold_max"), "timing.hold_max"),
        action_gap_min=_number(
            timing_data.get("action_gap_min"), "timing.action_gap_min"
        ),
        action_gap_max=_number(
            timing_data.get("action_gap_max"), "timing.action_gap_max"
        ),
    )
    if timing.hold_min > timing.hold_max:
        raise ValueError("timing.hold_min cannot exceed timing.hold_max")
    if timing.action_gap_min > timing.action_gap_max:
        raise ValueError("timing.action_gap_min cannot exceed timing.action_gap_max")

    step_data = data.get("steps")
    if not isinstance(step_data, list) or not step_data:
        raise ValueError("steps must be a non-empty JSON array")

    steps: list[Step] = []
    keys: set[str] = set()
    for index, item in enumerate(step_data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"steps[{index}] must be a JSON object")
        name = item.get("name")
        key = item.get("key")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"steps[{index}].name must be a non-empty string")
        if not isinstance(key, str) or not re.fullmatch(
            r"(?:KEY_(?:[1-8]|E|Q|X|F[1-8])|BTN_LEFT|BTN_RIGHT)", key
        ):
            raise ValueError(
                f"steps[{index}].key must be KEY_1 through KEY_8, KEY_E, "
                "KEY_Q, KEY_X, KEY_F1 through KEY_F8, BTN_LEFT, or BTN_RIGHT"
            )
        if key in keys:
            raise ValueError(f"Duplicate hotbar key in profile: {key}")
        keys.add(key)
        activation_level = _level(
            item.get("activation_level"), f"steps[{index}].activation_level"
        )
        if activation_level > max_level:
            raise ValueError(f"steps[{index}].activation_level exceeds level cap")
        enabled = item.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError(f"steps[{index}].enabled must be true or false")
        role = item.get("role", "damage")
        if role not in {"damage", "heal", "potion", "buff"}:
            raise ValueError(
                f"steps[{index}].role must be damage, heal, potion, or buff"
            )
        health_value = item.get("health_below")
        health_below = None
        if health_value is not None:
            health_below = _number(
                health_value, f"steps[{index}].health_below", minimum=0.01
            )
            if health_below > 1:
                raise ValueError(f"steps[{index}].health_below cannot exceed 1")
        if role in {"damage", "buff"} and health_below is not None:
            raise ValueError(
                f"steps[{index}] damage and buff actions cannot use health_below"
            )
        if role in {"heal", "potion"} and health_below is None:
            raise ValueError(f"steps[{index}] recovery actions require health_below")
        steps.append(
            Step(
                name=name,
                key=key,
                interval=_number(
                    item.get("interval"), f"steps[{index}].interval", minimum=0.05
                ),
                activation_level=activation_level,
                enabled=enabled,
                role=role,
                health_below=health_below,
                retry_jitter=_number(
                    item.get("retry_jitter", 0.0),
                    f"steps[{index}].retry_jitter",
                ),
            )
        )

    return Profile(
        label=label,
        character=character,
        min_level=min_level,
        max_level=max_level,
        steps=tuple(steps),
        manual=manual,
        timing=timing,
    )


def active_steps(profile: Profile, level: int) -> list[Step]:
    """Create fresh scheduler state for every run."""
    return [replace(step, next_due=0.0) for step in profile.steps if step.active(level)]


def resolve(code_name: str) -> int:
    code = getattr(e, code_name, None)
    if code is None:
        raise ValueError(f"Unknown Linux input code: {code_name}")
    return code


def is_aion_process_command(command: str) -> bool:
    normalized = command.replace("\\", "/").lower()
    return "/aion2/binaries/win64/aion2.exe" in normalized


def aion_process_running(proc_root: Path = Path("/proc")) -> bool:
    for cmdline in proc_root.glob("[0-9]*/cmdline"):
        try:
            command = cmdline.read_bytes().replace(b"\0", b" ").decode(
                errors="replace"
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if is_aion_process_command(command):
            return True
    return False


def is_aion_window_identity(window_class: str, title: str) -> bool:
    identity = f"{window_class} {title}".lower()
    return f"steam_app_{APP_ID}" in identity and "aion2" in identity


def parse_window_properties(properties: str) -> tuple[str, str]:
    class_line = next(
        (line for line in properties.splitlines() if line.startswith("WM_CLASS")),
        "",
    )
    title_line = next(
        (
            line
            for line in properties.splitlines()
            if line.startswith("_NET_WM_NAME")
        ),
        "",
    )
    classes = re.findall(r'"([^"]*)"', class_line)
    titles = re.findall(r'"([^"]*)"', title_line)
    return ".".join(classes), titles[0] if titles else ""


def active_window_identity() -> tuple[str, str] | None:
    """Return the XWayland active window class and title.

    Empty strings mean no X window is active. None means the probe could not
    run right now (a capture holds the X server, or xprop timed out).
    """
    if not X_LOCK.acquire(timeout=0.05):
        return None
    try:
        return _active_window_identity_locked()
    finally:
        X_LOCK.release()


def _active_window_identity_locked() -> tuple[str, str] | None:
    try:
        root_properties = subprocess.run(
            ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
        match = re.search(r"#\s+(0x[0-9a-fA-F]+)", root_properties)
        if match is None:
            return "", ""
        properties = subprocess.run(
            [
                "xprop",
                "-id",
                match.group(1),
                "WM_CLASS",
                "_NET_WM_NAME",
                "WM_NAME",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
    except FileNotFoundError:
        return "", ""
    except subprocess.TimeoutExpired:
        return None
    except subprocess.SubprocessError:
        return "", ""
    return parse_window_properties(properties)


def aion_window_focused() -> bool | None:
    """True/False for the active window, None when it cannot be read now."""
    identity = active_window_identity()
    if identity is None:
        return None
    return is_aion_window_identity(*identity)


def find_aion_window_id(windows: str) -> str | None:
    """Find the AION2 XWayland window in ``wmctrl -lx`` output."""
    for line in windows.splitlines():
        fields = line.split(maxsplit=4)
        if len(fields) != 5:
            continue
        window_id, _, window_class, _, title = fields
        if is_aion_window_identity(window_class, title):
            return window_id
    return None


def focus_aion_window() -> bool:
    """Bring AION2 forward and confirm focus before allowing rotation input."""
    try:
        windows = subprocess.run(
            ["wmctrl", "-lx"],
            check=True,
            capture_output=True,
            text=True,
            timeout=1,
        ).stdout
        window_id = find_aion_window_id(windows)
        if window_id is None:
            return False
        with X_LOCK:
            subprocess.run(
                ["wmctrl", "-ia", window_id],
                check=True,
                capture_output=True,
                timeout=1,
            )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False

    for _ in range(6):
        if aion_window_focused():
            return True
        time.sleep(0.05)
    return False


def emit_due_steps(
    steps: list[Step],
    *,
    now: float,
    focused: bool,
    tap: Callable[[str], None],
    jitter: Callable[[float], float] | None = None,
    health: float | None = None,
    damage_allowed: bool = True,
    on_emit: Callable[[Step], None] | None = None,
) -> str:
    """Emit one priority pass. Focus loss is fail-closed.

    Damage and buff actions (scrolls, food) need a confirmed hostile target;
    recovery actions follow the HP bar alone.
    """
    if not focused:
        return "paused"

    if jitter is None:
        jitter = lambda upper: random.uniform(0.0, upper)
    due = [
        step
        for step in steps
        if now >= step.next_due
        and step.ready_for_health(health)
        and (step.role not in {"damage", "buff"} or damage_allowed)
    ]
    if not due:
        return "idle"

    # Oldest deadline wins. Tuple order remains the priority tie-breaker while
    # preventing short-cooldown keys at the front from starving later slots.
    step = min(due, key=lambda candidate: candidate.next_due)
    tap(step.key)
    step.next_due = now + step.interval + jitter(step.retry_jitter)
    if on_emit is not None:
        on_emit(step)
    return "emitted"


class Injector:
    def __init__(
        self,
        code_names: set[str],
        timing: Timing,
        rng: random.Random | random.SystemRandom,
    ) -> None:
        self.timing = timing
        self.rng = rng
        capabilities = {e.EV_KEY: sorted(resolve(name) for name in code_names)}
        if any(name.startswith("BTN_") for name in code_names):
            # Relative axes allow libinput to classify the click source as a mouse.
            # We advertise them but never emit movement.
            capabilities[e.EV_REL] = [e.REL_X, e.REL_Y]
        self.ui = UInput(
            capabilities,
            name=UINPUT_NAME,
        )

    def tap(self, code_name: str) -> None:
        if self.ui is None:
            return
        code = resolve(code_name)
        self.ui.write(e.EV_KEY, code, 1)
        self.ui.syn()
        time.sleep(self.rng.uniform(self.timing.hold_min, self.timing.hold_max))
        self.ui.write(e.EV_KEY, code, 0)
        self.ui.syn()

    def close(self) -> None:
        if self.ui is not None:
            self.ui.close()
            self.ui = None


def is_toggle_device(name: str) -> bool:
    """Accept physical keyboards while rejecting local virtual injectors."""
    normalized = name.casefold()
    return (
        "keyboard" in normalized
        and "virtual" not in normalized
        and "ydotool" not in normalized
        and name != UINPUT_NAME
    )


# Codes that mark a device worth listening to: keyboards (function keys) and
# game controllers (face/shoulder/stick buttons, paddles).
LISTEN_CODES = (
    set(range(e.KEY_F1, e.KEY_F10 + 1))
    | set(range(e.BTN_GAMEPAD, e.BTN_THUMBR + 1))
    | set(range(e.BTN_TRIGGER_HAPPY1, e.BTN_TRIGGER_HAPPY40 + 1))
)
PREFERRED_NAMES = ("BTN_SOUTH", "BTN_EAST", "BTN_NORTH", "BTN_WEST")


def is_listener_device(name: str, capabilities: dict[int, object]) -> bool:
    """Accept physical keyboards and game controllers; reject injectors and mice."""
    normalized = name.casefold()
    if "virtual" in normalized or "ydotool" in normalized or name == UINPUT_NAME:
        return False
    if e.EV_REL in capabilities:
        return False  # mice: constant motion events, never a trigger source
    keys = capabilities.get(e.EV_KEY, [])
    return bool(set(keys) & LISTEN_CODES)


def code_name(code: int) -> str:
    """Canonical Linux input name for a key or button code."""
    names = e.keys.get(code) or e.BTN.get(code) or e.KEY.get(code)
    if isinstance(names, (list, tuple)):
        for preferred in PREFERRED_NAMES:
            if preferred in names:
                return preferred
        return names[0]
    if isinstance(names, str):
        return names
    return f"code {code}"


def load_controls(path: Path = DEFAULT_CONTROLS_FILE) -> tuple[str, str]:
    """Return (combat on, combat off) key names, defaulting to F8/F9."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        on = str(data.get("combat_on", TOGGLE_KEY))
        off = str(data.get("combat_off", PANIC_KEY))
        resolve(on)
        resolve(off)
        if on == off:
            raise ValueError("combat_on and combat_off must differ")
        return on, off
    except (FileNotFoundError, ValueError, json.JSONDecodeError, AttributeError):
        return TOGGLE_KEY, PANIC_KEY


def load_flip_control(path: Path = DEFAULT_CONTROLS_FILE) -> str | None:
    """Optional single button that flips combat on/off (Legion Go Y1 = KEY_F7)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        name = data.get("combat_toggle")
        if not name:
            return None
        resolve(str(name))
        return str(name)
    except (FileNotFoundError, ValueError, json.JSONDecodeError, AttributeError):
        return None


def save_controls(
    on: str, off: str, path: Path = DEFAULT_CONTROLS_FILE, flip: str | None = None
) -> None:
    data: dict[str, str] = {"combat_on": on, "combat_off": off}
    if flip:
        data["combat_toggle"] = flip
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class Listeners:
    """Keyboards and controllers opened read-only, re-scanned for hotplug.

    Nothing is grabbed, so the game still sees every press. Controllers that
    connect after launch (a Legion Go 2 dock, a Bluetooth pad) are picked up
    on the next scan.
    """

    RESCAN = 3.0

    def __init__(self) -> None:
        self.selector = selectors.DefaultSelector()
        self.next_scan = 0.0
        self.rescan(0.0, force=True)

    def paths(self) -> set[str]:
        return {key.fileobj.path for key in self.selector.get_map().values()}

    def rescan(self, now: float, *, force: bool = False) -> int:
        if not force and now < self.next_scan:
            return 0
        self.next_scan = now + self.RESCAN
        known = self.paths()
        added = 0
        for path in evdev.list_devices():
            if path in known:
                continue
            try:
                device = evdev.InputDevice(path)
                capabilities = device.capabilities()
            except (PermissionError, OSError):
                continue
            if is_listener_device(device.name, capabilities):
                self.selector.register(device, selectors.EVENT_READ)
                added += 1
            else:
                device.close()
        return added

    def names(self) -> list[str]:
        return [key.fileobj.name for key in self.selector.get_map().values()]

    def get_map(self):  # noqa: ANN201 - selector-compatible
        return self.selector.get_map()

    def select(self, timeout: float | None = None):  # noqa: ANN201
        return self.selector.select(timeout=timeout)

    def unregister(self, device: object) -> None:
        self.selector.unregister(device)

    def close(self) -> None:
        for key in list(self.selector.get_map().values()):
            try:
                key.fileobj.close()
            except OSError:
                pass
        self.selector.close()


def open_listeners(_wanted: set[int] | None = None) -> Listeners:
    """Open every keyboard and controller the user can read."""
    return Listeners()


def print_devices() -> None:
    print("Input devices visible to this user:\n")
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except (PermissionError, OSError) as exc:
            print(f"{path}: <no access> ({exc})")
            continue
        listened = is_listener_device(device.name, device.capabilities())
        print(f"{path}: {device.name}{'  [trigger source]' if listened else ''}")
        device.close()


def print_plan(args: argparse.Namespace, profile: Profile) -> list[Step]:
    steps = active_steps(profile, args.level)
    print(f"Profile  : {profile.label}")
    print(f"Character: {profile.character}")
    print(f"Level    : {args.level}")
    print(
        f"Keys     : {getattr(args, 'on_key', TOGGLE_KEY)} combat on (stays on)   "
        f"{getattr(args, 'off_key', PANIC_KEY)} combat off"
    )
    if args.dry_run:
        print("MODE     : dry run")
    print("\nActive solo rotation:")
    for step in steps:
        retry_max = step.interval + step.retry_jitter
        if step.health_below:
            condition = f" below {step.health_below:.0%} HP"
        elif step.role == "buff":
            condition = " while fighting a confirmed target"
        else:
            condition = ""
        print(
            f"  + {step.name:<24} {step.key:<8} {step.role:<6} "
            f"retry {step.interval:g}-{retry_max:g}s{condition}"
        )
    print(
        "\nCadence  : key hold "
        f"{profile.timing.hold_min:g}-{profile.timing.hold_max:g}s; action gap "
        f"{profile.timing.action_gap_min:g}-{profile.timing.action_gap_max:g}s"
    )
    print(f"\nManual   : {profile.manual}")
    return steps


class SetupError(RuntimeError):
    """AION 2, its window, or a usable keyboard is not ready yet."""


def acquire_instance_lock() -> object:
    """Allow one helper per desktop session; two would double every key."""
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    handle = (runtime_dir / "aion2-cleric-rotation.lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise SystemExit("The AION 2 Cleric rotation is already running.") from None
    return handle


class Engine:
    """Run the rotation for one profile; shared by the console and the window.

    F8 turns combat on and repeated presses leave it on. F9 turns it off.
    Losing AION 2 focus pauses input and focus return resumes it.
    """

    def __init__(
        self,
        profile: Profile,
        level: int,
        *,
        verbose: bool = False,
        log: Callable[[str], None] = print,
        report: Callable[[dict[str, object]], None] | None = None,
        toggle_key: str = TOGGLE_KEY,
        panic_key: str = PANIC_KEY,
        flip_key: str | None = None,
    ) -> None:
        self.profile = profile
        self.level = level
        self.verbose = verbose
        self.log = log
        self.report = report
        self.toggle_key = toggle_key
        self.panic_key = panic_key
        self.flip_key = flip_key
        self.learning: str | None = None
        self.devices: list[str] = []
        self.steps = active_steps(profile, level)
        self.commands: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self.stopping = False
        self.mode = "waiting"
        self.status = "Waiting for AION 2."
        self.health: float | None = None
        self.targeted = False
        self.last_action = ""
        self._published: tuple[object, ...] | None = None

    def post(self, command: str, value: object = None) -> None:
        """Queue a window command: on, off, level, controls, learn, or quit."""
        self.commands.put((command, value))

    def set_controls(self, on: str, off: str) -> bool:
        """Change the combat on/off keys; names must be Linux input codes."""
        try:
            resolve(on)
            resolve(off)
        except ValueError as exc:
            self.log(f"Ignored controls: {exc}")
            return False
        if on == off:
            self.log("Ignored controls: on and off keys must differ")
            return False
        self.toggle_key = on
        self.panic_key = off
        self.log(f"Controls: {on} combat on, {off} combat off")
        self.publish()
        return True

    def snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "status": self.status,
            "health": self.health,
            "targeted": self.targeted,
            "level": self.level,
            "last_action": self.last_action,
            "toggle_key": self.toggle_key,
            "panic_key": self.panic_key,
            "flip_key": self.flip_key,
            "learning": self.learning,
            "devices": list(self.devices),
        }

    def publish(self) -> None:
        if self.report is None:
            return
        health = None if self.health is None else round(self.health * 100)
        signature = (
            self.mode,
            self.status,
            self.level,
            self.targeted,
            self.last_action,
            health,
            self.toggle_key,
            self.panic_key,
            self.flip_key,
            self.learning,
            tuple(self.devices),
        )
        if signature != self._published:
            self._published = signature
            self.report(self.snapshot())

    def set_mode(self, mode: str, status: str, line: str | None = None) -> None:
        self.mode = mode
        self.status = status
        if line is not None:
            self.log(line)
        self.publish()

    def set_level(self, level: int) -> None:
        if not self.profile.min_level <= level <= self.profile.max_level:
            return
        due = {step.key: step.next_due for step in self.steps}
        self.level = level
        self.steps = active_steps(self.profile, level)
        for step in self.steps:
            step.next_due = due.get(step.key, 0.0)
        self.log(f"Rotation level {level}: {len(self.steps)} actions")
        self.publish()

    def drain_commands(self) -> list[tuple[str, object]]:
        """Apply quit/level commands and return on/off for the session."""
        pending: list[tuple[str, object]] = []
        while True:
            try:
                command, value = self.commands.get_nowait()
            except queue.Empty:
                return pending
            if command == "quit":
                self.stopping = True
            elif command == "level" and isinstance(value, int):
                self.set_level(value)
            elif command == "controls" and isinstance(value, (tuple, list)):
                self.set_controls(str(value[0]), str(value[1]))
            elif command == "flip_key":
                try:
                    self.flip_key = str(value) if value else None
                    if self.flip_key:
                        resolve(self.flip_key)
                    self.log(f"Combat toggle button: {self.flip_key or 'none'}")
                except ValueError as exc:
                    self.flip_key = None
                    self.log(f"Ignored toggle button: {exc}")
                self.publish()
            elif command == "learn":
                self.learning = value if value in ("on", "off") else None
                if self.learning:
                    self.log(f"Press the key or button to use for combat {self.learning}")
                self.publish()
            else:
                pending.append((command, value))

    def idle(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self.stopping and time.monotonic() < deadline:
            for command, _ in self.drain_commands():
                if command == "on":
                    self.log("AION 2 is not ready; combat stays off.")
            time.sleep(0.1)

    def serve(self) -> None:
        """Window mode: wait for AION 2, run a session, then wait again."""
        while not self.stopping:
            if not aion_process_running():
                self.set_mode("waiting", "Waiting for AION 2 to start.")
                self.idle(1.0)
                continue
            try:
                self.run_session()
            except SetupError as exc:
                self.set_mode("waiting", f"{exc} Retrying.")
                self.idle(2.0)

    def run_session(self) -> None:
        """Drive one AION 2 session until the game exits or the helper stops."""
        rng = random.SystemRandom()
        keys = {step.key for step in self.profile.steps if step.enabled}
        injector = Injector(keys | {TOGGLE_KEY, PANIC_KEY}, self.profile.timing, rng)
        selector = open_listeners({resolve(self.toggle_key), resolve(self.panic_key)})
        if not selector.get_map():
            injector.close()
            selector.close()
            raise SetupError(
                "No readable keyboard or controller found; run --list to inspect."
            )
        self.devices = selector.names() if hasattr(selector, "names") else []
        try:
            health_monitor = HealthMonitor()
        except (OSError, subprocess.SubprocessError) as exc:
            injector.close()
            selector.close()
            raise SetupError(f"Cannot read the AION 2 health bar: {exc}") from exc
        target_monitor = TargetMonitor(
            health_monitor.window_id, health_monitor.width, health_monitor.height
        )
        guard = RuntimeGuard()
        debouncer = KeyDebouncer()
        guard_state = "ok"
        combat = False
        paused = False
        next_exit_check = 0.0

        def combat_on(source: str) -> None:
            nonlocal combat, paused
            # Bring AION 2 forward when possible. If it cannot be focused,
            # combat still turns on and stays paused until the game is focused.
            if not aion_window_focused():
                focus_aion_window()
            if combat:
                self.log(f"[ON] {source}: combat is already on")
                return
            combat = True
            paused = False
            guard.reset()
            target_monitor.reset()
            self.targeted = False
            for step in self.steps:
                # Buff timers (scrolls) survive F9/F8 so a buff that is still
                # running is not re-applied on every combat toggle.
                if step.role != "buff":
                    step.next_due = 0.0
            self.set_mode(
                "on",
                "Combat ON. Select a hostile target; F9 stops.",
                f"[ON] combat on ({source})",
            )

        def combat_off(reason: str) -> None:
            nonlocal combat, paused
            combat = False
            paused = False
            target_monitor.reset()
            self.targeted = False
            self.set_mode(
                "off", "Stopped. Press F8 to turn combat on.", f"[OFF] {reason}"
            )

        def read_inputs(timeout: float) -> None:
            for command, _ in self.drain_commands():
                if command == "on":
                    combat_on("window button")
                elif command == "off":
                    combat_off("window STOP button")
            rescan = getattr(selector, "rescan", None)
            if rescan is not None and rescan(time.monotonic()):
                self.devices = selector.names()
                self.log("Input devices: " + ", ".join(self.devices))
                self.publish()
            trigger = resolve(self.toggle_key)
            panic = resolve(self.panic_key)
            flip = resolve(self.flip_key) if self.flip_key else None
            for key, _ in selector.select(timeout=timeout):
                device = key.fileobj
                try:
                    events = list(device.read())
                except OSError:
                    selector.unregister(device)
                    device.close()
                    continue
                for event in events:
                    if (
                        event.type != e.EV_KEY
                        or event.value != 1
                        or not debouncer.accept(event.code, time.monotonic())
                    ):
                        continue
                    if self.learning is not None:
                        name = code_name(event.code)
                        on, off = self.toggle_key, self.panic_key
                        if self.learning == "on":
                            on = name
                        else:
                            off = name
                        self.learning = None
                        self.set_controls(on, off)
                        trigger = resolve(self.toggle_key)
                        panic = resolve(self.panic_key)
                        continue
                    if flip is not None and event.code == flip:
                        if combat:
                            combat_off(f"{self.flip_key} toggle")
                        else:
                            combat_on(f"{self.flip_key} toggle")
                    elif event.code == panic:
                        combat_off(f"{self.panic_key} stop key")
                    elif event.code == trigger:
                        combat_on(self.toggle_key)

        def poll_guard() -> str:
            nonlocal guard_state
            status = guard.poll(
                time.monotonic(), aion_process_running, aion_window_focused
            )
            if status != "cached":
                guard_state = status
            return guard_state

        self.set_mode(
            "off",
            "Ready. Press F8 to turn combat on; F9 stops.",
            "\nReady. Press F8 to turn combat on (it stays on); F9 stops.",
        )
        try:
            while not self.stopping:
                read_inputs(0 if combat else 0.05)
                if not combat:
                    now = time.monotonic()
                    if now >= next_exit_check:
                        next_exit_check = now + 1.0
                        if not aion_process_running():
                            self.set_mode("waiting", "AION 2 exited.", "[OFF] AION 2 exited")
                            return
                    continue

                state = poll_guard()
                if state == "exited":
                    combat_off("AION 2 exited")
                    self.set_mode("waiting", "AION 2 exited.")
                    return
                if state == "unfocused":
                    if not paused:
                        paused = True
                        target_monitor.reset()
                        self.targeted = False
                        self.set_mode(
                            "paused",
                            "Combat ON, paused while AION 2 is not focused. "
                            "It resumes when you return; F9 stops.",
                            "[PAUSED] AION 2 is not focused; combat resumes when you return",
                        )
                    time.sleep(0.05)
                    continue
                if paused:
                    paused = False
                    self.set_mode(
                        "on",
                        "Combat ON. Select a hostile target; F9 stops.",
                        "[ON] AION 2 focused; combat resumed",
                    )

                now = time.monotonic()
                self.health = health_monitor.poll(now)
                target_event = target_monitor.poll(now)
                self.targeted = target_monitor.damage_allowed()
                if target_event is not None and self.verbose:
                    self.log(f"  target: {target_event}")
                self.publish()

                # HUD reads run in the background, but re-read hotkeys and the
                # runtime guard right before sending so no key follows a stop
                # press or a focus change that landed during this pass.
                read_inputs(0)
                if not combat or poll_guard() != "ok":
                    continue

                emitted: list[Step] = []
                result = emit_due_steps(
                    self.steps,
                    now=time.monotonic(),
                    focused=True,
                    tap=injector.tap,
                    jitter=lambda upper: rng.uniform(0.0, upper),
                    health=self.health,
                    damage_allowed=self.targeted,
                    on_emit=emitted.append,
                )
                if result == "emitted":
                    health_text = (
                        "unknown" if self.health is None else f"{self.health:.0%}"
                    )
                    self.last_action = f"{emitted[0].name} (HP {health_text})"
                    if self.verbose:
                        self.log(f"  {self.last_action}")
                    self.publish()
                    time.sleep(
                        rng.uniform(
                            self.profile.timing.action_gap_min,
                            self.profile.timing.action_gap_max,
                        )
                    )
                else:
                    time.sleep(IDLE_SLEEP)
        finally:
            target_monitor.close()
            health_monitor.close()
            injector.close()
            selector.close()
            self.log("Input devices closed.")


def run(args: argparse.Namespace, profile: Profile) -> None:
    print_plan(args, profile)
    if args.dry_run:
        print("\nDry run complete; no input devices were opened and no keys were sent.")
        return

    if not aion_process_running():
        raise SystemExit("AION 2 is not running; refusing to create an input device.")

    engine = Engine(
        profile,
        args.level,
        verbose=args.verbose,
        toggle_key=getattr(args, "on_key", TOGGLE_KEY),
        panic_key=getattr(args, "off_key", PANIC_KEY),
        flip_key=getattr(args, "toggle_key", None),
    )

    def stop(*_: object) -> None:
        engine.stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        engine.run_session()
    except SetupError as exc:
        raise SystemExit(str(exc)) from exc
    print("\nStopped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-button solo rotation helper for an AION 2 Cleric."
    )
    parser.add_argument("--list", action="store_true", help="list input devices")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the plan without opening devices"
    )
    parser.add_argument("--verbose", action="store_true", help="log damage passes")
    parser.add_argument("--level", type=int, default=DEFAULT_LEVEL)
    default_on, default_off = load_controls()
    parser.add_argument(
        "--on-key",
        default=default_on,
        help="Linux input name for combat on, e.g. KEY_F8 or BTN_SOUTH",
    )
    parser.add_argument(
        "--off-key",
        default=default_off,
        help="Linux input name for combat off, e.g. KEY_F9 or BTN_EAST",
    )
    parser.add_argument(
        "--toggle-key",
        default=load_flip_control(),
        help="optional single button that flips combat on/off (Legion Go Y1 = KEY_F7)",
    )
    parser.add_argument(
        "--profile-file",
        type=Path,
        default=DEFAULT_PROFILE_FILE,
        help="character-scoped JSON rotation profile",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list:
        print_devices()
        return
    try:
        profile = load_profile(args.profile_file)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not profile.min_level <= args.level <= profile.max_level:
        raise SystemExit(
            f"--level must be between {profile.min_level} and {profile.max_level}"
        )
    try:
        resolve(args.on_key)
        resolve(args.off_key)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.on_key == args.off_key:
        raise SystemExit("--on-key and --off-key must differ")
    _instance_lock = None if args.dry_run else acquire_instance_lock()
    run(args, profile)


if __name__ == "__main__":
    main()
