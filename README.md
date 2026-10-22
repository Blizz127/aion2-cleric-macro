# AION 2 Blizz Cleric solo helper

A one-button Linux/Wayland solo-combat rotation for the Cleric **Blizz**. It
uses a virtual `uinput` keyboard, listens to the physical keyboard for F8/F9,
and fails closed unless the AION 2 Steam process and verified XWayland window
are present.

F8 brings AION 2 forward and turns combat on; repeated F8 presses leave it on.
F9 turns it off from any window. Switching to another window pauses input, and
combat resumes automatically when AION 2 is focused again. Closing AION 2 stops
combat. Duplicate events from virtual keyboards are ignored and hotkeys are
debounced. Only one helper can run at a time, so keys are never doubled.

This behaves like the WoW one-button rotation: leave combat on while playing.
Recovery actions are driven by the visible HP bar even without a target.
Damage begins only when AION's cyan lock brackets and red hostile target plate
are visible, and pauses again when that marker disappears. The helper never
presses Tab, so nearby quest NPCs are not selected automatically.

## Automatic actions

The default hotbar was inspected live at level 3. The launcher uses the full
level-50 plan so reserved locked slots begin working automatically when the
game unlocks them. That plan also presses slots that are still locked; set the
window's rotation level to your current level to skip them.

| Key | Action | Opens | Automatic condition |
| --- | --- | ---: | --- |
| `X` | Emergency Health Potion | 1 | HP at or below 35% |
| `F5` | Courage Scroll | 1 | fighting a confirmed target; once per 300 s |
| `F7` | Benediction Scroll | 1 | fighting a confirmed target; once per 300 s |
| `1` | Earth's Retribution | 1 | damage cooldown |
| `2` | Judgment Thunder | 1 | damage cooldown |
| left click | Basic weapon attack | 1 | damage cooldown |
| right click | Heavy attack | 1 | damage cooldown |
| `E` | Chain of Torment | 4 | damage cooldown |
| `Q` | Light of Regeneration | 7 | HP at or below 75% |
| `3` | Radiant Recovery | 12 | HP at or below 55%; supplies recovery and cleanse |
| `4` | Bolt | 14 | damage cooldown |

The hotbar was re-read live at level 8 on 2026-10-22. Scrolls were identified
against the Fextralife scroll icons: `F3` holds the Return Scroll and is never
pressed, and `F6` holds the Speed Scroll, which the game's own auto-use (the
yellow tab on the slot) already keeps running, so the helper leaves it alone.
Scroll timers survive F9/F8, so a buff that is still running is not re-applied
on every combat toggle; they do restart when the helper itself is relaunched.
`F1` holds a second red potion whose effect has not been confirmed, so its
HP-25% step stays disabled in `cleric_personal.json` until it is.

Keys `5` through `8` are currently empty on Blizz and remain disabled. Their
future damage actions are prepared in `cleric_personal.json`, but they must not
be enabled until the matching skill is actually placed in the listed slot.

The health reader captures only the visible HP strip from the verified AION 2
window every 400 ms. If the HUD cannot be read, or the last good read is older
than two seconds, support actions remain idle; damage actions continue only
when the target gate is confirmed. A second small HUD crop checks AION's
visible hostile-target marker every 250 ms before damage. Both reads run on
background threads so a slow screenshot never delays the next key. Damage
keeps going for up to 0.6 s after the marker was last seen, so a spell effect
flashing over the brackets does not stutter the rotation; a lost target, an
unreadable HUD, or a read older than one second pauses damage inside that
window. It does not read or modify game memory.

The scheduler emits at most one action per pass. It varies key holds from
30–55 ms and action gaps from 100–180 ms, and adds bounded positive retry
variation. Those values live under `timing` in `cleric_personal.json`. It remains game automation and does not attempt to hide itself or
bypass technical protections.

Target selection, movement, quest choices, resurrection, and encounter
mechanics remain manual. Select a hostile target normally; the armed helper
will recognize whether AION accepts its next damage action.

## Run

1. Open **AION 2 Cleric Rotation (F8)** from the application menu or desktop.
   The Solo Assist window opens and waits for AION 2 if it is not running yet.
2. Start AION 2 normally and enter Blizz.
3. Press **F8** or **Combat ON** to bring AION 2 forward and turn combat on.
4. Press **F9** or **STOP** to turn it off.

The window shows the combat state, HP and target readings, the active
rotation, and an activity log. Its rotation level applies immediately.
`aion2_cleric_rotation.py` remains available as a console helper with the same
F8/F9 behavior. Safe checks that send no input:

```bash
python3 aion2_cleric_rotation.py --dry-run
python3 aion2_cleric_rotation.py --dry-run --level 3
python3 aion2_cleric_rotation.py --list
QT_QPA_PLATFORM=offscreen python3 aion2_cleric_gui.py --gui-smoke-test /tmp/aion2-window.png
python3 -m pytest -q
```

## Profile evidence and limits

Future skill levels and base cooldowns are provisional values from the
game-data-derived AION2Data Cleric table:
<https://aion2data.com/classes/cleric>. Its calculated PvE order informed the
disabled later-slot plan: <https://aion2data.com/macros/cleric>. Live hotbar
evidence takes precedence.

The runtime verifies the process and focused window. It cannot read the active
character name from local game state, so `character: "Blizz"` scopes the local
profile but is not an identity lock.

NC's AION 2 operation policy permits sanctions for unauthorized programs.
Timed multi-action input can be treated as unauthorized automation. The
game's built-in skill-macro and potion features are the lower-risk route where
they cover the required behavior.
