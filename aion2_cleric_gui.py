#!/usr/bin/env python3
"""Window for the Blizz AION 2 Cleric rotation, styled like EQ Legends Solo Assist."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

import aion2_cleric_rotation as rotation


ROLES = {"damage": "Damage", "heal": "Heal", "potion": "Potion"}
BADGES = {
    "on": ("●  COMBAT ON", "#7addac"),
    "paused": ("●  PAUSED", "#f5ca72"),
    "off": ("●  STOPPED", "#a9b5c5"),
    "waiting": ("●  WAITING FOR AION 2", "#8dacc1"),
}


def condition_text(step: rotation.Step) -> str:
    if not step.enabled:
        return "Off until the skill is on this key"
    if step.health_below is not None:
        return f"HP ≤ {step.health_below:.0%}"
    return "Hostile target locked"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=int, default=rotation.DEFAULT_LEVEL)
    parser.add_argument(
        "--profile-file",
        type=Path,
        default=rotation.DEFAULT_PROFILE_FILE,
        help="character-scoped JSON rotation profile",
    )
    parser.add_argument(
        "--gui-smoke-test",
        type=Path,
        help="render the window to a PNG without opening input devices",
    )
    args = parser.parse_args()
    try:
        profile = rotation.load_profile(args.profile_file)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    if not profile.min_level <= args.level <= profile.max_level:
        parser.error(
            f"--level must be between {profile.min_level} and {profile.max_level}"
        )
    _instance_lock = None if args.gui_smoke_test else rotation.acquire_instance_lock()
    run_gui(profile, args.level, args.gui_smoke_test)


def run_gui(
    profile: rotation.Profile, level: int, smoke_path: Path | None = None
) -> None:
    from PySide6.QtCore import QThread, QTimer, Signal
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMainWindow,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    class Worker(QThread):
        state = Signal(object)
        line = Signal(str)

        def __init__(self) -> None:
            super().__init__()
            self.engine = rotation.Engine(
                profile,
                level,
                verbose=True,
                log=self.log,
                report=self.state.emit,
            )

        def log(self, line: str) -> None:
            # Also keep a console/journal record next to the Activity tab.
            print(line, flush=True)
            self.line.emit(line)

        def run(self) -> None:
            self.engine.serve()

    class Window(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.worker: Worker | None = None
            self.setWindowTitle(f"AION 2 · {profile.character} Cleric Solo")
            self.resize(920, 790)
            body = QWidget()
            self.setCentralWidget(body)
            layout = QVBoxLayout(body)
            layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(14)

            eyebrow = QLabel("AION 2  /  SOLO ASSIST")
            eyebrow.setObjectName("eyebrow")
            layout.addWidget(eyebrow)
            title = QLabel(f"{profile.character} · Cleric")
            title.setObjectName("title")
            layout.addWidget(title)
            subtitle = QLabel(
                "One combat key. HP-driven recovery. Damage only on your "
                "hostile target. Level 1–50."
            )
            subtitle.setObjectName("muted")
            layout.addWidget(subtitle)

            banner = QFrame()
            banner.setObjectName("banner")
            banner_layout = QHBoxLayout(banner)
            banner_layout.setContentsMargins(16, 14, 16, 14)
            self.badge = QLabel()
            self.badge.setObjectName("state")
            banner_layout.addWidget(self.badge)
            banner_layout.addStretch()
            banner_layout.addWidget(QLabel("F8  COMBAT ON     F9  COMBAT OFF"))
            layout.addWidget(banner)

            controls = QGridLayout()
            character = QComboBox()
            character.addItem(profile.character)
            character.setEnabled(False)
            self.level = QSpinBox()
            self.level.setRange(profile.min_level, profile.max_level)
            self.level.setValue(level)
            self.level.setToolTip(
                "50 also presses reserved slots that are still locked. "
                "Set your current level to skip them."
            )
            self.level.valueChanged.connect(self.level_changed)
            timing = profile.timing
            cadence = QLabel(
                f"{timing.action_gap_min * 1000:.0f}–{timing.action_gap_max * 1000:.0f} ms"
                f"  ·  hold {timing.hold_min * 1000:.0f}–{timing.hold_max * 1000:.0f} ms"
            )
            cadence.setObjectName("field")
            for column, (label, widget) in enumerate(
                [
                    ("Character", character),
                    ("Rotation level", self.level),
                    ("Action cadence", cadence),
                ]
            ):
                controls.addWidget(QLabel(label), 0, column)
                controls.addWidget(widget, 1, column)
            layout.addLayout(controls)

            self.tabs = QTabWidget()
            combat = QWidget()
            tab_layout = QVBoxLayout(combat)
            tab_layout.setContentsMargins(12, 16, 12, 12)
            self.status = QLabel()
            self.status.setWordWrap(True)
            self.status.setMinimumHeight(42)
            tab_layout.addWidget(self.status)
            self.detail = QLabel("Last action: none yet")
            self.detail.setObjectName("muted")
            tab_layout.addWidget(self.detail)
            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(
                ["Key", "Role", "Skill", "Opens", "Automatic when"]
            )
            self.table.horizontalHeader().setSectionResizeMode(
                2, QHeaderView.ResizeMode.Stretch
            )
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
            tab_layout.addWidget(self.table, 1)

            gauges = QHBoxLayout()
            self.hp = QProgressBar()
            self.hp.setObjectName("hp")
            self.hp.setRange(0, 100)
            self.target = QProgressBar()
            self.target.setObjectName("target")
            self.target.setRange(0, 1)
            gauges.addWidget(self.hp)
            gauges.addWidget(self.target)
            tab_layout.addLayout(gauges)

            buttons = QHBoxLayout()
            start = QPushButton("Combat ON  ·  F8")
            start.setObjectName("primary")
            start.clicked.connect(lambda: self.post("on"))
            stop = QPushButton("STOP  ·  F9")
            stop.setObjectName("stop")
            stop.clicked.connect(lambda: self.post("off"))
            buttons.addWidget(start)
            buttons.addWidget(stop)
            tab_layout.addLayout(buttons)
            self.tabs.addTab(combat, "Combat")

            self.activity = QPlainTextEdit()
            self.activity.setReadOnly(True)
            self.activity.setMaximumBlockCount(1000)
            self.tabs.addTab(self.activity, "Activity")
            guide = QPlainTextEdit()
            guide.setReadOnly(True)
            guide.setPlainText(GUIDE)
            self.tabs.addTab(guide, "Solo setup")
            layout.addWidget(self.tabs, 1)

            footer = QLabel(
                "F8 turns persistent combat on  ·  F9 turns it off  ·  "
                "Leaving AION 2 pauses input until you return  ·  Targeting stays manual"
            )
            footer.setWordWrap(True)
            footer.setObjectName("muted")
            layout.addWidget(footer)

            self.render_table(level)
            self.update_state(
                {
                    "mode": "waiting",
                    "status": "Starting. Waiting for AION 2.",
                    "health": None,
                    "targeted": False,
                    "level": level,
                    "last_action": "",
                }
            )
            if smoke_path is None:
                self.worker = Worker()
                self.worker.state.connect(self.update_state)
                self.worker.line.connect(self.append_line)
                self.worker.start()

        def post(self, command: str, value: object = None) -> None:
            if self.worker is not None:
                self.worker.engine.post(command, value)

        def level_changed(self, value: int) -> None:
            self.render_table(value)
            self.post("level", value)

        def render_table(self, current: int) -> None:
            # Active actions first in priority order; locked or empty slots last.
            steps = sorted(profile.steps, key=lambda step: not step.active(current))
            self.table.setRowCount(len(steps))
            for row, step in enumerate(steps):
                active = step.active(current)
                cells = [
                    step.key.removeprefix("KEY_"),
                    ROLES[step.role],
                    step.name,
                    str(step.activation_level),
                    condition_text(step),
                ]
                for column, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    if not active:
                        item.setForeground(QColor("#5f7183"))
                    elif column == 2:
                        item.setForeground(QColor("#f5ca72"))
                    self.table.setItem(row, column, item)
            self.table.resizeColumnsToContents()

        def update_state(self, state: dict[str, object]) -> None:
            text, color = BADGES.get(str(state["mode"]), BADGES["off"])
            self.badge.setText(text)
            self.badge.setStyleSheet(f"color: {color};")
            self.status.setText(str(state["status"]))
            health = state["health"]
            if isinstance(health, float):
                self.hp.setValue(round(health * 100))
                self.hp.setFormat(f"HP · {round(health * 100)}%")
            else:
                self.hp.setValue(0)
                self.hp.setFormat("HP · no reading yet")
            targeted = bool(state["targeted"])
            self.target.setValue(1 if targeted else 0)
            self.target.setFormat(
                "Target · hostile locked" if targeted else "Target · none, damage paused"
            )
            if state["last_action"]:
                self.detail.setText(f"Last action: {state['last_action']}")
            current = int(state["level"])
            if current != self.level.value():
                self.level.blockSignals(True)
                self.level.setValue(current)
                self.level.blockSignals(False)
                self.render_table(current)

        def append_line(self, line: str) -> None:
            text = line.strip()
            if text:
                self.activity.appendPlainText(text)

        def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
            if self.worker is not None:
                self.worker.engine.stopping = True
                if not self.worker.wait(5000):
                    event.ignore()
                    self.status.setText("Stopping input… close again in a moment.")
                    return
            event.accept()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("AION 2 Cleric Solo Assist")
    app.setStyleSheet(STYLE)
    window = Window()
    signal.signal(signal.SIGTERM, lambda *_: window.close())
    signal.signal(signal.SIGINT, lambda *_: window.close())
    # Python signal handlers only run when the interpreter gets control.
    wakeup = QTimer()
    wakeup.timeout.connect(lambda: None)
    wakeup.start(250)
    window.show()
    if smoke_path is not None:

        def render() -> None:
            smoke_path.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(smoke_path))
            app.quit()

        QTimer.singleShot(400, render)
    app.exec()


GUIDE = """QUICK START
1. Start AION 2 and enter Blizz. This window can open before or after the game; it waits for AION 2.
2. Press F8 or Combat ON. AION 2 comes to the front and combat stays on until F9 or STOP. Repeated F8 presses leave it on.
3. Select a hostile target yourself. Damage runs only while AION 2 shows its cyan lock brackets around a red hostile plate, and pauses when the target dies or is cleared. The helper never presses Tab, so nearby quest NPCs are never selected.
4. Recovery follows your HP bar even with no target selected.
5. Switching to another window pauses input. It resumes automatically when AION 2 is focused again. Closing AION 2 stops combat.

AUTOMATIC ACTIONS
X  Emergency Health Potion: HP 35% or lower
1  Earth's Retribution: damage, hostile target
2  Judgment Thunder: damage, hostile target
E  Chain of Torment (level 4): damage, hostile target
Q  Light of Regeneration (level 7): HP 75% or lower
3  Radiant Recovery (level 12): HP 55% or lower; recovery and cleanse
4  Bolt (level 14): damage, hostile target
Keys 5–8 stay off until their skills are placed on Blizz's hotbar.

ROTATION LEVEL
Level 50 also presses reserved slots that are still locked, so new skills start working the moment they unlock. Set your current level to skip locked slots.

LIMITS
Targeting, movement, quests, resurrection, and encounter mechanics stay manual. The HP and target readings come from screen captures of the AION 2 window; game memory is never read. Key holds and gaps vary within fixed ranges. NC's operation policy allows sanctions for unauthorized programs; AION 2's built-in skill macro is the lower-risk route.
"""

STYLE = """
QWidget { background: #101820; color: #e4eaf2; font-family: 'Noto Sans'; font-size: 13px; }
QLabel#eyebrow { color: #8dacc1; font-size: 11px; font-weight: 700; letter-spacing: 2px; }
QLabel#title { color: #f5ca72; font-size: 32px; font-weight: 700; }
QLabel#muted { color: #93a4b7; font-size: 12px; }
QLabel#field { background: #1c2a36; border: 1px solid #3b4c5b; border-radius: 5px; padding: 7px; min-height: 22px; }
QFrame#banner { background: #1a2934; border: 1px solid #324552; border-radius: 9px; }
QFrame#banner QLabel { background: transparent; }
QLabel#state { font-size: 14px; font-weight: 700; }
QComboBox, QSpinBox { background: #1c2a36; border: 1px solid #3b4c5b; border-radius: 5px; padding: 7px; min-height: 22px; }
QPushButton { background: #223440; border: 1px solid #415668; border-radius: 5px; padding: 10px 12px; }
QPushButton:hover { background: #304959; }
QPushButton#primary { background: #e2bc70; color: #17212b; border-color: #e2bc70; font-weight: 700; }
QPushButton#stop { background: #482a32; color: #ffb9c2; border-color: #7f3b4b; }
QTabWidget::pane { border: 1px solid #304452; border-radius: 6px; }
QTabBar::tab { background: #192631; color: #9bb0c1; padding: 10px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #f5ca72; border-bottom-color: #f5ca72; }
QTableWidget { background: #131e28; border: 1px solid #2b3b48; gridline-color: #263643; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: #20313f; border: 0; border-bottom: 1px solid #344855; padding: 8px; color: #a6bfd0; font-size: 12px; }
QProgressBar { background: #1b2a37; border: 1px solid #304859; border-radius: 5px; text-align: center; min-height: 25px; font-size: 11px; }
QProgressBar::chunk { background: #284e78; border-radius: 4px; }
QProgressBar#hp::chunk { background: #783d4a; }
QProgressBar#target::chunk { background: #2f6b55; }
QPlainTextEdit { background: #131e28; border: none; padding: 12px; font-size: 13px; }
"""


if __name__ == "__main__":
    main()
