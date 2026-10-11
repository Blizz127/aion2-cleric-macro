"""Behavioral tests for the AION 2 Cleric rotation helper."""

import subprocess
import sys
from pathlib import Path

import aion2_cleric_rotation as rotation


SCRIPT = Path(rotation.__file__)


def test_level_three_profile_uses_only_the_two_visible_unlocked_slots():
    active = rotation.active_steps("leveling", level=3)

    assert [(step.name, step.key) for step in active] == [
        ("Earth's Retribution", "KEY_1"),
        ("Judgment Thunder", "KEY_2"),
    ]


def test_manual_recovery_actions_are_never_in_an_automatic_profile():
    forbidden = {
        "Healing Light",
        "Radiant Recovery",
        "Light of Regeneration",
        "Summon Resurrection",
        "Salvation",
        "Absolution",
        "Benevolence",
    }

    configured = {
        step.name
        for profile in rotation.PROFILES.values()
        for step in profile.steps
    }
    assert configured.isdisjoint(forbidden)


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


def test_dry_run_opens_no_devices_and_exits_successfully():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run", "--level", "3"],
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert proc.returncode == 0
    assert "MODE     : dry run" in proc.stdout
    assert "KEY_1" in proc.stdout
    assert "KEY_2" in proc.stdout
    assert "no input devices were opened" in proc.stdout.lower()


def test_future_locked_slots_are_not_guessed_into_the_profile():
    profile = rotation.PROFILES["leveling"]

    configured_keys = {step.key for step in profile.steps}
    assert "KEY_3" not in configured_keys
    assert "KEY_4" not in configured_keys


def test_focus_loss_pauses_before_any_key_is_sent():
    taps: list[str] = []

    result = rotation.emit_due_steps(
        rotation.active_steps("leveling", level=3),
        now=10.0,
        focused=False,
        tap=taps.append,
    )

    assert result == "paused"
    assert taps == []


def test_each_scheduler_pass_emits_at_most_one_skill():
    taps: list[str] = []
    steps = rotation.active_steps("leveling", level=3)

    first = rotation.emit_due_steps(steps, now=10.0, focused=True, tap=taps.append)
    second = rotation.emit_due_steps(steps, now=10.1, focused=True, tap=taps.append)

    assert first == "emitted"
    assert second == "emitted"
    assert taps == ["KEY_1", "KEY_2"]
