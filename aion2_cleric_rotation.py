#!/usr/bin/env python3
"""One-button AION 2 Cleric damage rotation helper for Linux/Wayland.

F8 toggles the rotation and F9 immediately disarms it.  The initial profile is
deliberately limited to the two level-3 skills visibly present on the user's
current hotbar.  Healing, cleansing, resurrection, movement, defensives, and
mouse buttons are never automated.
"""

from __future__ import annotations

import argparse
import re
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    import evdev
    from evdev import UInput, ecodes as e
except ImportError:  # pragma: no cover - host dependency check
    sys.exit(
        "python-evdev is not installed.\n"
        "Install it with: python3 -m pip install --user evdev"
    )


APP_ID = "3393110"
UINPUT_NAME = "aion2-cleric-rotation"
TOGGLE_KEY = "KEY_F8"
PANIC_KEY = "KEY_F9"
DEFAULT_PROFILE = "leveling"
DEFAULT_LEVEL = 3
HOLD_TIME = 0.035
GAP_BETWEEN_SKILLS = 0.055
IDLE_SLEEP = 0.010


@dataclass
class Step:
    """A verified hotbar action and its minimum repeat interval."""

    name: str
    key: str
    interval: float
    unlock_level: int
    last: float = 0.0

    def active(self, level: int) -> bool:
        return level >= self.unlock_level


@dataclass(frozen=True)
class Profile:
    label: str
    steps: tuple[Step, ...]
    manual: str


PROFILES = {
    "leveling": Profile(
        label="Cleric early leveling (observed level-3 hotbar)",
        steps=(
            Step("Earth's Retribution", "KEY_1", 0.70, unlock_level=1),
            Step("Judgment Thunder", "KEY_2", 0.70, unlock_level=1),
        ),
        manual=(
            "All healing, cleanse, resurrection, buffs, defensives, movement, "
            "target selection, and encounter mechanics stay manual."
        ),
    ),
}


def active_steps(profile_name: str, level: int) -> list[Step]:
    return [step for step in PROFILES[profile_name].steps if step.active(level)]


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


def active_window_identity() -> tuple[str, str]:
    """Return the XWayland active window class and title, or empty strings."""
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
    except (FileNotFoundError, subprocess.SubprocessError):
        return "", ""
    return parse_window_properties(properties)


def aion_window_focused() -> bool:
    return is_aion_window_identity(*active_window_identity())


def emit_due_steps(
    steps: list[Step],
    *,
    now: float,
    focused: bool,
    tap: Callable[[str], None],
) -> str:
    """Emit one priority pass. Focus loss is fail-closed."""
    if not focused:
        return "paused"

    for step in steps:
        if now - step.last < step.interval:
            continue
        tap(step.key)
        step.last = now
        return "emitted"
    return "idle"


class Injector:
    def __init__(self, code_names: set[str]) -> None:
        self.ui = UInput(
            {e.EV_KEY: sorted(resolve(name) for name in code_names)},
            name=UINPUT_NAME,
        )

    def tap(self, code_name: str) -> None:
        if self.ui is None:
            return
        code = resolve(code_name)
        self.ui.write(e.EV_KEY, code, 1)
        self.ui.syn()
        time.sleep(HOLD_TIME)
        self.ui.write(e.EV_KEY, code, 0)
        self.ui.syn()

    def close(self) -> None:
        if self.ui is not None:
            self.ui.close()
            self.ui = None


def open_listeners(wanted: set[int]) -> selectors.BaseSelector:
    """Open toggle-capable real devices read-only without grabbing them."""
    selector = selectors.DefaultSelector()
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
            keys = device.capabilities().get(e.EV_KEY, [])
        except (PermissionError, OSError):
            continue
        if device.name == UINPUT_NAME:
            device.close()
        elif wanted & set(keys):
            selector.register(device, selectors.EVENT_READ)
        else:
            device.close()
    return selector


def print_devices() -> None:
    print("Input devices visible to this user:\n")
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except (PermissionError, OSError) as exc:
            print(f"{path}: <no access> ({exc})")
            continue
        print(f"{path}: {device.name}")
        device.close()


def print_plan(args: argparse.Namespace) -> list[Step]:
    profile = PROFILES[args.profile]
    steps = active_steps(args.profile, args.level)
    print(f"Profile  : {profile.label}")
    print(f"Level    : {args.level}")
    print(f"Toggle   : {TOGGLE_KEY}   Panic: {PANIC_KEY}")
    if args.dry_run:
        print("MODE     : dry run")
    print("\nActive damage rotation:")
    for step in steps:
        print(f"  + {step.name:<22} {step.key:<8} every {step.interval:g}s")
    print(f"\nManual   : {profile.manual}")
    return steps


def run(args: argparse.Namespace) -> None:
    steps = print_plan(args)
    if args.dry_run:
        print("\nDry run complete; no input devices were opened and no keys were sent.")
        return

    if not aion_process_running():
        raise SystemExit("AION 2 is not running; refusing to create an input device.")

    trigger = resolve(TOGGLE_KEY)
    panic = resolve(PANIC_KEY)
    injector = Injector({step.key for step in steps} | {TOGGLE_KEY, PANIC_KEY})
    selector = open_listeners({trigger, panic})
    if not selector.get_map():
        injector.close()
        selector.close()
        raise SystemExit("No readable keyboard exposes F8/F9; run --list to inspect.")

    armed = False
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print("\nReady. Focus AION 2 and press F8 to start; F9 is panic-stop.")

    try:
        while not stopping:
            for key, _ in selector.select(timeout=0 if armed else 0.05):
                device = key.fileobj
                try:
                    events = list(device.read())
                except OSError:
                    selector.unregister(device)
                    device.close()
                    continue
                for event in events:
                    if event.type != e.EV_KEY or event.value != 1:
                        continue
                    if event.code == panic:
                        armed = False
                        print("\n[OFF] panic key; rotation disarmed")
                    elif event.code == trigger:
                        if armed:
                            armed = False
                            print("\n[OFF] rotation disarmed")
                        elif not aion_window_focused():
                            print("\n[OFF] AION 2 is not focused; F8 ignored")
                        else:
                            armed = True
                            for step in steps:
                                step.last = 0.0
                            print("\n[ON] rotation armed")

            if not armed:
                continue
            if not aion_process_running():
                armed = False
                print("\n[OFF] AION 2 exited; rotation disarmed")
                continue

            result = emit_due_steps(
                steps,
                now=time.monotonic(),
                focused=aion_window_focused(),
                tap=injector.tap,
            )
            if result == "paused":
                armed = False
                print("\n[OFF] focus left AION 2; rotation disarmed")
            elif result == "emitted" and args.verbose:
                print("  damage pass")
            time.sleep(GAP_BETWEEN_SKILLS if result == "emitted" else IDLE_SLEEP)
    finally:
        injector.close()
        selector.close()
        print("\nStopped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-button damage rotation helper for an AION 2 Cleric."
    )
    parser.add_argument("--list", action="store_true", help="list input devices")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the plan without opening devices"
    )
    parser.add_argument("--verbose", action="store_true", help="log damage passes")
    parser.add_argument("--level", type=int, default=DEFAULT_LEVEL)
    parser.add_argument(
        "--profile", choices=sorted(PROFILES), default=DEFAULT_PROFILE
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list:
        print_devices()
        return
    if not 1 <= args.level <= 45:
        raise SystemExit("--level must be between 1 and 45")
    run(args)


if __name__ == "__main__":
    main()
