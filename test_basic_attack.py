import random

import aion2_cleric_rotation as rotation


def test_basic_attack_repeats_while_spell_deadlines_are_in_future():
    steps = rotation.active_steps(rotation.load_profile(rotation.DEFAULT_PROFILE_FILE), 3)
    for step in steps:
        step.next_due = 0 if step.key == "BTN_LEFT" else 100
    taps = []
    for now in (1, 2, 3):
        rotation.emit_due_steps(steps, now=now, focused=True, tap=taps.append,
                                damage_allowed=True, jitter=lambda _: 0)
    assert taps == ["BTN_LEFT"] * 3
    rotation.emit_due_steps(steps, now=4, focused=True, tap=taps.append,
                            damage_allowed=False)
    rotation.emit_due_steps(steps, now=5, focused=False, tap=taps.append)
    assert len(taps) == 3


def test_click_device_has_mouse_axes_and_emits_press_release(monkeypatch):
    devices, events = [], []

    class Device:
        def __init__(self, caps, name):
            devices.append(caps)

        def write(self, *event):
            events.append(event)

        def syn(self):
            pass

    monkeypatch.setattr(rotation, "UInput", Device)
    monkeypatch.setattr(rotation.time, "sleep", lambda _: None)
    injector = rotation.Injector({"BTN_LEFT", "KEY_1"},
                                rotation.Timing(.05, .05, .2, .2), random.Random(0))
    injector.tap("BTN_LEFT")
    e = rotation.e
    assert devices[0][e.EV_REL] == [e.REL_X, e.REL_Y]
    assert events == [(e.EV_KEY, e.BTN_LEFT, 1), (e.EV_KEY, e.BTN_LEFT, 0)]
