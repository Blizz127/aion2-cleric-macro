"""Behavioral tests for the AION 2 Cleric rotation helper."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import aion2_cleric_rotation as rotation


SCRIPT = Path(rotation.__file__)
PROFILE = SCRIPT.with_name("cleric_personal.json")


def test_personal_profile_covers_level_one_through_current_max():
    profile = rotation.load_profile(PROFILE)

    assert profile.character == "Blizz"
    assert profile.min_level == 1
    assert profile.max_level == 50
    assert rotation.active_steps(profile, level=1)
    assert rotation.active_steps(profile, level=50)


def test_live_level_three_plan_uses_observed_damage_and_potion_slots():
    profile = rotation.load_profile(PROFILE)

    assert [(step.name, step.key) for step in rotation.active_steps(profile, 3)] == [
        ("Emergency Health Potion", "KEY_X"),
        ("Courage Scroll", "KEY_F5"),
        ("Benediction Scroll", "KEY_F7"),
        ("Earth's Retribution", "KEY_1"),
        ("Judgment Thunder", "KEY_2"),
        ("Basic weapon attack", "BTN_LEFT"),
        ("Heavy attack (right click)", "BTN_RIGHT"),
    ]


def test_automatic_recovery_actions_are_health_gated():
    profile = rotation.load_profile(PROFILE)
    recovery = [step for step in profile.steps if step.role in {"heal", "potion"}]

    assert {step.name for step in recovery} == {
        "Emergency Health Potion",
        "Second potion (F1 slot)",
        "Light of Regeneration",
        "Radiant Recovery",
    }
    assert all(step.health_below is not None for step in recovery)


def test_personal_profile_owns_a_dedicated_damage_bar():
    profile = rotation.load_profile(PROFILE)

    assert {step.key for step in profile.steps} == {
        "KEY_1",
        "KEY_2",
        "KEY_3",
        "KEY_4",
        "KEY_5",
        "KEY_6",
        "KEY_7",
        "KEY_8",
        "BTN_LEFT",
        "BTN_RIGHT",
        "KEY_E",
        "KEY_Q",
        "KEY_X",
        "KEY_F1",
        "KEY_F5",
        "KEY_F7",
    }


def test_only_observed_or_reserved_default_slots_are_enabled():
    profile = rotation.load_profile(PROFILE)

    assert {step.key for step in profile.steps if step.enabled} == {
        "BTN_LEFT",
        "BTN_RIGHT",
        "KEY_1",
        "KEY_2",
        "KEY_3",
        "KEY_4",
        "KEY_E",
        "KEY_Q",
        "KEY_X",
        "KEY_F5",
        "KEY_F7",
    }


def test_return_scroll_and_game_auto_used_speed_scroll_are_never_pressed():
    profile = rotation.load_profile(PROFILE)

    assert not {"KEY_F3", "KEY_F6"} & {step.key for step in profile.steps}


def test_personal_profile_uses_blizz_hotbar_progression_and_base_cooldowns():
    profile = rotation.load_profile(PROFILE)

    configured = {
        step.name: (step.key, step.activation_level, step.interval)
        for step in profile.steps
    }
    assert configured == {
        "Basic weapon attack": ("BTN_LEFT", 1, 1.0),
        "Heavy attack (right click)": ("BTN_RIGHT", 1, 1.0),
        "Emergency Health Potion": ("KEY_X", 1, 15.0),
        "Second potion (F1 slot)": ("KEY_F1", 1, 15.0),
        "Courage Scroll": ("KEY_F5", 1, 300.0),
        "Benediction Scroll": ("KEY_F7", 1, 300.0),
        "Earth's Retribution": ("KEY_1", 1, 0.7),
        "Judgment Thunder": ("KEY_2", 1, 0.7),
        "Radiant Recovery": ("KEY_3", 12, 12.0),
        "Bolt": ("KEY_4", 14, 45.0),
        "Divine Aura": ("KEY_5", 3, 30.0),
        "Lightning Strike Scattershot": ("KEY_6", 5, 0.7),
        "Condemnation": ("KEY_7", 8, 3.0),
        "Debilitating Mark": ("KEY_8", 1, 10.0),
        "Chain of Torment": ("KEY_E", 4, 20.0),
        "Light of Regeneration": ("KEY_Q", 7, 30.0),
    }


def test_profile_starts_with_data_derived_pve_priority_order():
    profile = rotation.load_profile(PROFILE)

    assert [step.name for step in profile.steps] == [
        "Emergency Health Potion",
        "Second potion (F1 slot)",
        "Radiant Recovery",
        "Light of Regeneration",
        "Courage Scroll",
        "Benediction Scroll",
        "Earth's Retribution",
        "Judgment Thunder",
        "Basic weapon attack",
        "Heavy attack (right click)",
        "Divine Aura",
        "Chain of Torment",
        "Lightning Strike Scattershot",
        "Condemnation",
        "Debilitating Mark",
        "Bolt",
    ]


def test_process_detection_accepts_the_live_steam_game_command():
    command = (
        r"S:\steamapps\common\AION2\Aion2\Binaries\Win64\AION2.exe "
        r"-steam -saveddirsuffix=Steam"
    )

    assert rotation.is_aion_process_command(command)
    assert not rotation.is_aion_process_command("/usr/bin/steam")


def test_window_detection_accepts_only_the_aion_steam_window():
    assert rotation.is_aion_window_identity(
        "steam_app_3393110.steam_app_3393110", "AION2"
    )
    assert not rotation.is_aion_window_identity("konsole.konsole", "Terminal")


def test_xprop_window_properties_are_parsed_without_xdotool():
    properties = '''
WM_CLASS(STRING) = "steam_app_3393110", "steam_app_3393110"
_NET_WM_NAME(UTF8_STRING) = "AION2"
WM_NAME(STRING) = "AION2"
'''

    assert rotation.parse_window_properties(properties) == (
        "steam_app_3393110.steam_app_3393110",
        "AION2",
    )


def test_live_aion_window_id_is_found_in_wmctrl_output():
    windows = """\
0x0200000  0 org.kde.konsole.org.kde.konsole host Terminal
0x07e00003 0 steam_app_3393110.steam_app_3393110 host AION2
"""

    assert rotation.find_aion_window_id(windows) == "0x07e00003"


def test_unrelated_window_list_does_not_produce_a_focus_target():
    windows = "0x0200000 0 org.kde.konsole.org.kde.konsole host Terminal\n"

    assert rotation.find_aion_window_id(windows) is None


def test_only_physical_keyboard_devices_can_toggle_rotation():
    assert rotation.is_toggle_device("AT Translated Set 2 keyboard")
    assert not rotation.is_toggle_device("ydotoold virtual device")
    assert not rotation.is_toggle_device("aion2-cleric-rotation")


def test_toggle_debouncer_collapses_duplicate_device_events():
    debouncer = rotation.KeyDebouncer(interval=0.4)

    assert debouncer.accept(rotation.e.KEY_F8, 10.0)
    assert not debouncer.accept(rotation.e.KEY_F8, 10.1)
    assert debouncer.accept(rotation.e.KEY_F8, 10.4)


def test_dry_run_opens_no_devices_and_exits_successfully():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--level", "50"],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0
    assert "MODE     : dry run" in proc.stdout
    assert "KEY_1" in proc.stdout
    assert "KEY_2" in proc.stdout
    assert "KEY_8" not in proc.stdout
    assert "no input devices were opened" in proc.stdout.lower()


def test_default_plan_probes_observed_future_slots_for_hands_off_leveling():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0
    assert "Level    : 50" in proc.stdout
    assert "KEY_1" in proc.stdout
    assert "KEY_2" in proc.stdout
    assert "KEY_3" in proc.stdout
    assert "KEY_E" in proc.stdout
    assert "KEY_Q" in proc.stdout


def test_profile_rejects_duplicate_hotbar_keys(tmp_path):
    profile = json.loads(PROFILE.read_text())
    profile["steps"][1]["key"] = profile["steps"][0]["key"]
    invalid = tmp_path / "duplicate.json"
    invalid.write_text(json.dumps(profile))

    try:
        rotation.load_profile(invalid)
    except ValueError as exc:
        assert "duplicate" in str(exc).lower()
    else:
        raise AssertionError("duplicate hotbar keys must be rejected")


def test_profile_rejects_fractional_levels(tmp_path):
    profile = json.loads(PROFILE.read_text())
    profile["max_level"] = 50.5
    invalid = tmp_path / "fractional-level.json"
    invalid.write_text(json.dumps(profile))

    with pytest.raises(ValueError, match="whole number"):
        rotation.load_profile(invalid)


def test_profile_rejects_a_missing_enabled_gate(tmp_path):
    profile = json.loads(PROFILE.read_text())
    del profile["steps"][0]["enabled"]
    invalid = tmp_path / "missing-enabled.json"
    invalid.write_text(json.dumps(profile))

    with pytest.raises(ValueError, match="enabled must be true or false"):
        rotation.load_profile(invalid)


def test_focus_loss_pauses_before_any_key_is_sent():
    taps: list[str] = []
    profile = rotation.load_profile(PROFILE)

    result = rotation.emit_due_steps(
        rotation.active_steps(profile, level=3),
        now=10.0,
        focused=False,
        tap=taps.append,
        jitter=lambda _: 0.0,
    )

    assert result == "paused"
    assert taps == []


def test_runtime_guard_throttles_process_and_focus_probes():
    calls = {"process": 0, "focus": 0}

    def process_running():
        calls["process"] += 1
        return True

    def focused():
        calls["focus"] += 1
        return True

    guard = rotation.RuntimeGuard(interval=0.2)

    assert guard.poll(10.0, process_running, focused) == "ok"
    assert guard.poll(10.1, process_running, focused) == "cached"
    assert guard.poll(10.2, process_running, focused) == "ok"
    assert calls == {"process": 2, "focus": 2}


def test_runtime_guard_fails_closed_before_running_a_rotation_pass():
    guard = rotation.RuntimeGuard(interval=0.2)

    assert guard.poll(10.0, lambda: False, lambda: True) == "exited"


class _StopLoop(Exception):
    """Ends a scripted rotation loop once the scenario has played out."""


class _FakeKeyboard:
    def __init__(self) -> None:
        self.pending: list[SimpleNamespace] = []

    def press(self, code: int) -> None:
        self.pending.append(SimpleNamespace(type=rotation.e.EV_KEY, value=1, code=code))

    def read(self) -> list[SimpleNamespace]:
        events, self.pending = self.pending, []
        return events

    def close(self) -> None:
        pass


class _FakeSelector:
    def __init__(self, keyboard: _FakeKeyboard, max_selects: int = 60) -> None:
        self.keyboard = keyboard
        self.selects = 0
        self.max_selects = max_selects

    def get_map(self) -> dict[int, _FakeKeyboard]:
        return {0: self.keyboard}

    def select(self, timeout: float | None = None) -> list[tuple[SimpleNamespace, None]]:
        self.selects += 1
        if self.selects > self.max_selects:
            raise _StopLoop
        if self.keyboard.pending:
            return [(SimpleNamespace(fileobj=self.keyboard), None)]
        return []

    def unregister(self, _device: object) -> None:
        pass

    def close(self) -> None:
        pass


def _scene(monkeypatch, disturb) -> SimpleNamespace:
    """Patch the runtime with fakes; disturb(scene) runs in the first HUD read.

    Every step is due at that point. The read advances a fake clock by five
    seconds, like a busy-scene capture. Keys are recorded before and after the
    disturbance, and separately whenever one is sent while AION 2 is unfocused.
    """
    scene = SimpleNamespace(
        clock={"now": 100.0},
        keyboard=_FakeKeyboard(),
        focus={"aion": True, "back_at": float("inf")},
        taps={"before": [], "after": []},
        unfocused_taps=[],
        disturbed=False,
        engine=None,
    )

    def focused() -> bool:
        return scene.focus["aion"] or scene.clock["now"] >= scene.focus["back_at"]

    class FakeInjector:
        def __init__(self, *_args: object) -> None:
            pass

        def tap(self, key: str) -> None:
            scene.taps["after" if scene.disturbed else "before"].append(key)
            if not focused():
                scene.unfocused_taps.append(key)

        def close(self) -> None:
            pass

    class FakeHealthMonitor:
        window_id = "0x07e00003"
        width = 2560
        height = 1600

        def __init__(self) -> None:
            self.reads = 0

        def poll(self, _now: float) -> float:
            self.reads += 1
            if self.reads == 1:
                scene.clock["now"] += 5.0
                disturb(scene)
                scene.disturbed = True
            return 1.0

        def close(self) -> None:
            pass

    class FakeTargetMonitor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def reset(self) -> None:
            pass

        def poll(self, _now: float) -> None:
            return None

        def damage_allowed(self) -> bool:
            return True

        def close(self) -> None:
            pass

    def sleep(seconds: float) -> None:
        scene.clock["now"] += seconds

    monkeypatch.setattr(rotation.time, "monotonic", lambda: scene.clock["now"])
    monkeypatch.setattr(rotation.time, "sleep", sleep)
    monkeypatch.setattr(rotation.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(rotation, "aion_process_running", lambda: True)
    monkeypatch.setattr(rotation, "aion_window_focused", focused)
    monkeypatch.setattr(rotation, "focus_aion_window", focused)
    monkeypatch.setattr(rotation, "Injector", FakeInjector)
    monkeypatch.setattr(
        rotation, "open_listeners", lambda _wanted: _FakeSelector(scene.keyboard)
    )
    monkeypatch.setattr(rotation, "HealthMonitor", FakeHealthMonitor)
    monkeypatch.setattr(rotation, "TargetMonitor", FakeTargetMonitor)
    return scene


def _run_console(monkeypatch, disturb) -> SimpleNamespace:
    """Press F8, then run the real console loop until the script ends."""
    scene = _scene(monkeypatch, disturb)
    scene.keyboard.press(rotation.e.KEY_F8)
    args = SimpleNamespace(dry_run=False, level=3, verbose=False)
    with pytest.raises(_StopLoop):
        rotation.run(args, rotation.load_profile(PROFILE))
    return scene


def _run_window(monkeypatch, disturb) -> SimpleNamespace:
    """Click the window's Combat ON button, then run one engine session."""
    scene = _scene(monkeypatch, disturb)
    scene.engine = rotation.Engine(rotation.load_profile(PROFILE), 3)
    scene.engine.post("on")
    with pytest.raises(_StopLoop):
        scene.engine.run_session()
    return scene


def test_undisturbed_slow_hud_read_still_rotates_damage(monkeypatch):
    scene = _run_console(monkeypatch, lambda _scene: None)

    assert scene.taps["before"] == []
    assert scene.taps["after"][:2] == ["KEY_F5", "KEY_F7"]


def test_focus_lost_during_a_slow_hud_read_sends_no_key(monkeypatch):
    def alt_tab(scene: SimpleNamespace) -> None:
        scene.focus["aion"] = False

    scene = _run_console(monkeypatch, alt_tab)

    assert scene.taps == {"before": [], "after": []}


def test_panic_pressed_during_a_slow_hud_read_sends_no_key(monkeypatch):
    def panic(scene: SimpleNamespace) -> None:
        scene.keyboard.press(rotation.e.KEY_F9)

    scene = _run_console(monkeypatch, panic)

    assert scene.taps == {"before": [], "after": []}


def test_pressing_f8_again_keeps_combat_on(monkeypatch):
    def press_f8_again(scene: SimpleNamespace) -> None:
        scene.keyboard.press(rotation.e.KEY_F8)

    scene = _run_console(monkeypatch, press_f8_again)

    assert scene.taps["after"][:2] == ["KEY_F5", "KEY_F7"]


def test_focus_return_resumes_combat_without_another_f8(monkeypatch):
    def alt_tab_for_one_second(scene: SimpleNamespace) -> None:
        scene.focus["aion"] = False
        scene.focus["back_at"] = scene.clock["now"] + 1.0

    scene = _run_console(monkeypatch, alt_tab_for_one_second)

    assert scene.taps["after"][:2] == ["KEY_F5", "KEY_F7"]
    assert scene.unfocused_taps == []


def test_f9_stays_off_when_focus_returns(monkeypatch):
    def stop_then_alt_tab(scene: SimpleNamespace) -> None:
        scene.keyboard.press(rotation.e.KEY_F9)
        scene.focus["aion"] = False
        scene.focus["back_at"] = scene.clock["now"] + 1.0

    scene = _run_console(monkeypatch, stop_then_alt_tab)

    assert scene.taps == {"before": [], "after": []}


def test_window_combat_on_button_starts_the_rotation(monkeypatch):
    scene = _run_window(monkeypatch, lambda _scene: None)

    assert scene.taps["after"][:2] == ["KEY_F5", "KEY_F7"]
    assert scene.engine.mode == "on"


def test_window_stop_button_sends_no_further_keys(monkeypatch):
    def stop_button(scene: SimpleNamespace) -> None:
        scene.engine.post("off")

    scene = _run_window(monkeypatch, stop_button)

    assert scene.taps == {"before": [], "after": []}
    assert scene.engine.mode == "off"


def test_second_helper_instance_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    first = rotation.acquire_instance_lock()
    try:
        with pytest.raises(SystemExit, match="already running"):
            rotation.acquire_instance_lock()
    finally:
        first.close()


def test_each_scheduler_pass_emits_at_most_one_skill():
    taps: list[str] = []
    steps = rotation.active_steps(rotation.load_profile(PROFILE), level=3)

    first = rotation.emit_due_steps(
        steps, now=10.0, focused=True, tap=taps.append, jitter=lambda _: 0.0
    )
    second = rotation.emit_due_steps(
        steps, now=10.1, focused=True, tap=taps.append, jitter=lambda _: 0.0
    )

    assert first == "emitted"
    assert second == "emitted"
    assert taps == ["KEY_F5", "KEY_F7"]


def test_scheduler_does_not_starve_later_damage_slots():
    taps: list[str] = []
    steps = rotation.active_steps(rotation.load_profile(PROFILE), level=50)

    for index in range(len(steps)):
        rotation.emit_due_steps(
            steps,
            now=10.0 + index * 0.1,
            focused=True,
            tap=taps.append,
            jitter=lambda _: 0.0,
            health=0.0,
        )

    assert taps == [step.key for step in steps]


def test_healthy_character_skips_recovery_actions():
    taps: list[str] = []
    steps = rotation.active_steps(rotation.load_profile(PROFILE), level=12)

    rotation.emit_due_steps(
        steps,
        now=10.0,
        focused=True,
        health=1.0,
        tap=taps.append,
        jitter=lambda _: 0.0,
    )

    assert taps == ["KEY_F5"]


def test_critical_health_prioritizes_emergency_potion():
    taps: list[str] = []
    steps = rotation.active_steps(rotation.load_profile(PROFILE), level=12)

    rotation.emit_due_steps(
        steps,
        now=10.0,
        focused=True,
        health=0.30,
        tap=taps.append,
        jitter=lambda _: 0.0,
    )

    assert taps == ["KEY_X"]


def test_damage_pauses_without_a_confirmed_enemy_target():
    taps: list[str] = []
    steps = rotation.active_steps(rotation.load_profile(PROFILE), level=50)

    result = rotation.emit_due_steps(
        steps,
        now=10.0,
        focused=True,
        health=1.0,
        damage_allowed=False,
        tap=taps.append,
        jitter=lambda _: 0.0,
    )

    assert result == "idle"
    assert taps == []


def test_recovery_actions_ignore_the_enemy_target_gate():
    taps: list[str] = []
    steps = rotation.active_steps(rotation.load_profile(PROFILE), level=50)

    rotation.emit_due_steps(
        steps,
        now=10.0,
        focused=True,
        health=0.30,
        damage_allowed=False,
        tap=taps.append,
        jitter=lambda _: 0.0,
    )

    assert taps == ["KEY_X"]


def test_hostile_target_requires_lock_brackets_around_a_red_plate():
    width = 240
    height = 120
    pixels = bytearray(bytes((0, 0, 0)) * width * height)

    def fill(left: int, top: int, right: int, bottom: int, color: bytes) -> None:
        for y in range(top, bottom):
            for x in range(left, right):
                offset = (y * width + x) * 3
                pixels[offset : offset + 3] = color

    fill(60, 50, 64, 62, bytes((30, 180, 220)))
    fill(150, 49, 154, 61, bytes((30, 180, 220)))
    fill(75, 42, 140, 48, bytes((210, 35, 35)))

    assert rotation.hostile_target_marker_present(bytes(pixels), width, height)

    fill(75, 42, 140, 48, bytes((0, 0, 0)))
    assert not rotation.hostile_target_marker_present(bytes(pixels), width, height)


def test_scheduler_adds_bounded_delay_without_shortening_retry_interval():
    step = rotation.Step(
        name="Damage",
        key="KEY_1",
        interval=0.70,
        activation_level=1,
        retry_jitter=0.12,
    )

    result = rotation.emit_due_steps(
        [step],
        now=10.0,
        focused=True,
        tap=lambda _: None,
        jitter=lambda upper: upper,
    )

    assert result == "emitted"
    assert step.next_due == pytest.approx(10.82)


def test_window_renders_without_opening_input_devices(tmp_path):
    pytest.importorskip("PySide6")
    image = tmp_path / "window.png"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT.with_name("aion2_cleric_gui.py")),
         "--gui-smoke-test", str(image)],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )

    assert proc.returncode == 0, proc.stderr
    assert image.stat().st_size > 10_000


def _settle(reader: rotation.HudReader, now: float, *, ticks: int = 200) -> float:
    """Advance the fake clock a little until the background read has landed."""
    import time as _time

    for _ in range(ticks):
        reader.poll(now)
        if reader.read_at is not None or reader.failed:
            return now
        _time.sleep(0.005)
        now += 0.005
    raise AssertionError("background HUD read never completed")


def test_hud_reader_never_blocks_the_caller_on_a_slow_capture():
    import threading
    import time as _time

    release = threading.Event()

    def slow_read() -> float:
        release.wait(5.0)
        return 0.5

    reader = rotation.HudReader(slow_read, interval=0.25, max_age=2.0)
    try:
        started = _time.perf_counter()
        assert reader.poll(10.0) is None
        assert reader.poll(10.1) is None
        assert _time.perf_counter() - started < 0.5
        release.set()
        now = _settle(reader, 10.2)
        assert reader.poll(now) == 0.5
    finally:
        release.set()
        reader.close()


def test_hud_reader_value_goes_stale_and_failed_reads_fail_closed():
    outcome = {"value": 0.8, "fail": False}

    def read() -> float:
        if outcome["fail"]:
            raise OSError("capture failed")
        return outcome["value"]

    reader = rotation.HudReader(read, interval=0.25, max_age=1.0)
    try:
        now = _settle(reader, 10.0)
        assert reader.poll(now) == 0.8
        # Stale: no completed read within max_age reads as None.
        assert reader.poll(now + 1.5) is None
        outcome["fail"] = True
        import time as _time

        now += 2.0
        for _ in range(400):
            reader.poll(now)
            if reader.failed:
                break
            _time.sleep(0.005)
            now += 0.005
        assert reader.failed
        assert reader.poll(now) is None
    finally:
        reader.close()


def test_hud_reader_reset_discards_an_in_flight_capture():
    import threading

    release = threading.Event()

    def read() -> bool:
        release.wait(5.0)
        return True

    reader = rotation.HudReader(read, interval=0.25, max_age=1.0)
    try:
        reader.poll(10.0)
        reader.reset()
        release.set()
        import time as _time

        _time.sleep(0.05)
        assert reader.poll(10.1) is None
        assert reader.read_at is None
    finally:
        release.set()
        reader.close()


def _target_monitor_with_marker(marker: dict[str, bool]) -> rotation.TargetMonitor:
    monitor = rotation.TargetMonitor.__new__(rotation.TargetMonitor)
    monitor.window_id = "0x1"
    monitor.interval = 0.25
    monitor.grace = 0.6
    monitor.reader = rotation.HudReader(
        lambda: marker["present"], interval=0.25, max_age=1.0
    )
    monitor.reset()
    return monitor


def test_target_grace_keeps_damage_on_through_a_brief_marker_flicker():
    marker = {"present": True}
    monitor = _target_monitor_with_marker(marker)
    try:
        now = _settle(monitor.reader, 10.0)
        assert monitor.poll(now) == "hostile target confirmed"
        assert monitor.damage_allowed()
        marker["present"] = False
        monitor.reader.read_at = None
        now = _settle(monitor.reader, now + 0.3)
        assert monitor.poll(now) is None
        assert monitor.damage_allowed(), "damage must not stutter inside the grace window"
        assert monitor.poll(now + 0.7) == "no hostile target; damage paused"
        assert not monitor.damage_allowed()
    finally:
        monitor.close()


def test_target_monitor_reset_fails_closed_until_a_fresh_marker_read():
    marker = {"present": True}
    monitor = _target_monitor_with_marker(marker)
    try:
        now = _settle(monitor.reader, 10.0)
        monitor.poll(now)
        assert monitor.damage_allowed()
        monitor.reset()
        assert not monitor.damage_allowed()
        assert monitor.poll(now + 0.01) in (None, "no hostile target; damage paused")
    finally:
        monitor.close()


def test_profile_timing_is_tight_but_still_humanized():
    profile = rotation.load_profile(PROFILE)

    assert 0.02 <= profile.timing.hold_min < profile.timing.hold_max <= 0.08
    assert 0.08 <= profile.timing.action_gap_min < profile.timing.action_gap_max <= 0.2


def test_scrolls_wait_for_a_confirmed_hostile_target():
    taps: list[str] = []
    steps = rotation.active_steps(rotation.load_profile(PROFILE), level=3)

    result = rotation.emit_due_steps(
        steps,
        now=10.0,
        focused=True,
        health=1.0,
        damage_allowed=False,
        tap=taps.append,
        jitter=lambda _: 0.0,
    )

    assert result == "idle"
    assert taps == []


def test_scroll_is_not_repeated_until_its_duration_has_passed():
    taps: list[str] = []
    steps = [
        step
        for step in rotation.active_steps(rotation.load_profile(PROFILE), level=3)
        if step.role == "buff"
    ]

    for now in (10.0, 10.5, 100.0, 309.0):
        rotation.emit_due_steps(
            steps, now=now, focused=True, tap=taps.append, jitter=lambda _: 0.0
        )
    assert taps == ["KEY_F5", "KEY_F7"]

    rotation.emit_due_steps(
        steps, now=311.0, focused=True, tap=taps.append, jitter=lambda _: 0.0
    )
    assert taps == ["KEY_F5", "KEY_F7", "KEY_F5"]


def test_combat_on_keeps_a_running_scroll_timer(monkeypatch):
    scene = _scene(monkeypatch, lambda _scene: None)
    scene.engine = rotation.Engine(rotation.load_profile(PROFILE), 3)
    for step in scene.engine.steps:
        step.next_due = 10_000.0 if step.role == "buff" else 0.0
    scene.engine.post("on")
    with pytest.raises(_StopLoop):
        scene.engine.run_session()

    assert "KEY_F5" not in scene.taps["after"]
    assert "KEY_F7" not in scene.taps["after"]
    assert scene.taps["after"][:2] == ["KEY_1", "KEY_2"]


def _bar_image(width: int, height: int, *, rows: range, fill_to: int, left: int = 0) -> bytes:
    pixels = bytearray(bytes((20, 20, 24)) * width * height)
    for y in rows:
        for x in range(left, fill_to):
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes((200, 40, 40))
    return bytes(pixels)


def test_health_bar_is_located_by_its_red_band_not_a_fixed_row():
    width, height = 150, 90
    image = _bar_image(width, height, rows=range(40, 52), fill_to=150, left=7)

    row, left = rotation.locate_health_bar(image, width, height)

    assert 44 <= row <= 47
    assert left == 7


def test_health_bar_locator_ignores_stray_red_pixels_and_needs_a_band():
    width, height = 150, 90
    image = bytearray(_bar_image(width, height, rows=range(0, 0), fill_to=0))
    for y in (10, 30, 70):
        for x in range(0, 150):
            image[(y * width + x) * 3 : (y * width + x) * 3 + 3] = bytes((200, 40, 40))

    assert rotation.locate_health_bar(bytes(image), width, height) is None


def test_health_fill_fraction_uses_the_fill_edge_and_never_reads_empty_as_zero():
    width = 200
    full = _bar_image(width, 3, rows=range(3), fill_to=200)
    half = _bar_image(width, 3, rows=range(3), fill_to=100)
    empty = _bar_image(width, 3, rows=range(3), fill_to=0)

    assert rotation.health_fill_fraction(full, width, 3, 200) == pytest.approx(1.0)
    assert rotation.health_fill_fraction(half, width, 3, 200) == pytest.approx(0.5)
    assert rotation.health_fill_fraction(empty, width, 3, 200) is None


def test_health_monitor_relocates_and_fails_closed_when_the_bar_moves(monkeypatch):
    width, height = 1920, 1080
    monitor = rotation.HealthMonitor.__new__(rotation.HealthMonitor)
    monitor.window_id = "0x1"
    monitor.width = width
    monitor.height = height
    monitor.bar_row = None
    monitor.bar_left = None
    monitor.bar_right = round(width / 2 - rotation.HealthMonitor.RIGHT_END_GAP * height)
    screen = {"row": 965, "left": 702, "fill": 0.8}

    def capture(window_id, left, top, w, h, *, resize_percent=100):
        pixels = bytearray(bytes((20, 20, 24)) * w * h)
        fill_end = screen["left"] + round((monitor.bar_right - screen["left"]) * screen["fill"])
        for y in range(h):
            if not (screen["row"] - 6 <= top + y <= screen["row"] + 5):
                continue
            for x in range(w):
                if screen["left"] <= left + x < fill_end:
                    offset = (y * w + x) * 3
                    pixels[offset : offset + 3] = bytes((200, 40, 40))
        return bytes(pixels)

    monkeypatch.setattr(rotation, "capture_window_crop", capture)

    assert monitor.read_health() == pytest.approx(0.8, abs=0.02)
    assert monitor.bar_row == 965 or abs(monitor.bar_row - 965) <= 2
    assert monitor.bar_left == 702

    screen["row"] = 940  # HUD scale changed: the bar moved
    screen["fill"] = 0.5
    assert monitor.read_health() == pytest.approx(0.5, abs=0.02)

    screen["fill"] = 0.0  # no red anywhere: unreadable, not 0% HP
    with pytest.raises(OSError):
        monitor.read_health()


def test_runtime_guard_keeps_last_focus_through_a_brief_unknown_then_fails_closed():
    guard = rotation.RuntimeGuard(interval=0.2, unknown_grace=2.0)
    answers = iter([True, None, None, None, None, None, None, None, None, None, None, None])

    def focused():
        return next(answers)

    assert guard.poll(10.0, lambda: True, focused) == "ok"
    # Probe busy (a capture holds X): keep the last known answer.
    assert guard.poll(10.2, lambda: True, focused) == "ok"
    assert guard.poll(11.0, lambda: True, focused) == "ok"
    assert guard.poll(12.0, lambda: True, focused) == "ok"
    # Unknown for longer than the grace window: fail closed.
    assert guard.poll(12.4, lambda: True, focused) == "unfocused"


def test_runtime_guard_with_no_known_focus_treats_unknown_as_unfocused():
    guard = rotation.RuntimeGuard(interval=0.2)

    assert guard.poll(10.0, lambda: True, lambda: None) == "unfocused"


def test_focus_probe_yields_unknown_while_a_capture_holds_the_x_lock():
    with rotation.X_LOCK:
        assert rotation.aion_window_focused() is None


def test_listener_devices_are_keyboards_and_controllers_but_not_mice_or_injectors():
    e = rotation.e
    keyboard = {e.EV_KEY: [e.KEY_A, e.KEY_F8, e.KEY_F9]}
    pad = {e.EV_KEY: [e.BTN_SOUTH, e.BTN_EAST, e.BTN_TR], e.EV_ABS: [(0, None)]}
    mouse = {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT], e.EV_REL: [e.REL_X, e.REL_Y]}
    paddles = {e.EV_KEY: [e.BTN_TRIGGER_HAPPY1, e.BTN_TRIGGER_HAPPY2]}

    assert rotation.is_listener_device("AT Translated Set 2 keyboard", keyboard)
    assert rotation.is_listener_device("Legion Go 2 Controller", pad)
    assert rotation.is_listener_device("Legion Go 2 Paddles", paddles)
    assert not rotation.is_listener_device("Logitech G502", mouse)
    assert not rotation.is_listener_device("Microsoft X-Box 360 pad virtual", pad)
    assert not rotation.is_listener_device(rotation.UINPUT_NAME, keyboard)
    assert not rotation.is_listener_device("ydotoold virtual device", keyboard)


def test_code_names_prefer_the_controller_face_button_aliases():
    e = rotation.e

    assert rotation.code_name(e.KEY_F8) == "KEY_F8"
    assert rotation.code_name(e.BTN_SOUTH) == "BTN_SOUTH"
    assert rotation.resolve(rotation.code_name(e.BTN_TRIGGER_HAPPY3)) == e.BTN_TRIGGER_HAPPY3


def test_controls_file_round_trip_and_fallback(tmp_path):
    path = tmp_path / "controls.json"

    assert rotation.load_controls(path) == ("KEY_F8", "KEY_F9")
    rotation.save_controls("BTN_TR2", "BTN_TL2", path)
    assert rotation.load_controls(path) == ("BTN_TR2", "BTN_TL2")
    path.write_text('{"combat_on": "KEY_F8", "combat_off": "KEY_F8"}')
    assert rotation.load_controls(path) == ("KEY_F8", "KEY_F9")
    path.write_text('{"combat_on": "NOT_A_KEY", "combat_off": "KEY_F9"}')
    assert rotation.load_controls(path) == ("KEY_F8", "KEY_F9")


def test_controller_button_turns_combat_on_and_off(monkeypatch):
    scene = _scene(monkeypatch, lambda _scene: None)
    scene.engine = rotation.Engine(
        rotation.load_profile(PROFILE), 3, toggle_key="BTN_SOUTH", panic_key="BTN_EAST"
    )
    scene.keyboard.press(rotation.e.KEY_F8)  # old key: ignored now
    scene.keyboard.press(rotation.e.BTN_SOUTH)
    with pytest.raises(_StopLoop):
        scene.engine.run_session()

    assert scene.engine.mode == "on"
    assert scene.taps["after"][:2] == ["KEY_F5", "KEY_F7"]


def test_learn_mode_captures_the_next_press_without_starting_combat(monkeypatch):
    scene = _scene(monkeypatch, lambda _scene: None)
    scene.engine = rotation.Engine(rotation.load_profile(PROFILE), 3)
    scene.engine.post("learn", "on")
    scene.keyboard.press(rotation.e.BTN_TR2)
    with pytest.raises(_StopLoop):
        scene.engine.run_session()

    assert scene.engine.toggle_key == "BTN_TR2"
    assert scene.engine.panic_key == "KEY_F9"
    assert scene.engine.learning is None
    assert scene.engine.mode == "off"
    assert scene.taps == {"before": [], "after": []}


def test_controls_command_rejects_identical_or_unknown_keys(monkeypatch):
    lines: list[str] = []
    engine = rotation.Engine(rotation.load_profile(PROFILE), 3, log=lines.append)

    assert not engine.set_controls("KEY_F8", "KEY_F8")
    assert not engine.set_controls("KEY_NOPE", "KEY_F9")
    assert engine.set_controls("BTN_START", "BTN_SELECT")
    assert (engine.toggle_key, engine.panic_key) == ("BTN_START", "BTN_SELECT")
