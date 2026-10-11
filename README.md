# AION 2 Cleric rotation helper

A Linux/Wayland one-button damage helper for the current level-3 Cleric hotbar.
It uses a virtual `uinput` keyboard, watches physical F8/F9 key events, and
fails closed when AION 2 is not the focused XWayland window.

## Current mapping

| Hotbar | Skill | Minimum repeat interval |
| --- | --- | ---: |
| `1` | Earth's Retribution | 0.70 s |
| `2` | Judgment Thunder | 0.70 s |

This mapping comes from the visible level-3 hotbar captured on 2026-10-11.
Locked slots `3` and `4` are intentionally absent instead of guessed.

Healing, cleansing, resurrection, buffs, defensives, movement, targeting,
mouse buttons, and encounter mechanics are always manual.

## Run

1. Start AION 2 normally and enter the game.
2. Open **AION 2 Cleric Rotation (F8)** from the application menu or desktop.
3. Keep the AION 2 window focused and press **F8** to arm/disarm.
4. Press **F9** at any time for panic-stop.
5. Alt-Tabbing away automatically disarms the helper.

Safe checks that do not inject input:

```bash
python3 aion2_cleric_rotation.py --dry-run --level 3
python3 aion2_cleric_rotation.py --list
python3 -m pytest -q
```

## Policy risk

NC's AION 2 Operation Policy permits sanctions up to an integrated-account ban
for unauthorized programs. This helper does not hide itself or bypass technical
protections, but timed multi-action input can still be treated as unauthorized
automation. Use AION 2's built-in skill-macro system instead if you want the
supported low-risk route.

## Updating the rotation

Do not add a skill just because its slot unlocked. Confirm the skill name, its
hotbar key, and whether it is a damage action that is safe to repeat. Add a
tested `Step` to `PROFILES["leveling"]`; keep every reactive/support action
manual.
