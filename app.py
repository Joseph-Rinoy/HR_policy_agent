from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu

from chat_widget import ChatLauncher, ChatWidget
from paths import app_base_dir


def main() -> None:
    # Keep fractional per-monitor scale factors un-rounded so the window renders
    # consistently across mixed-DPI screens (must precede QApplication).
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    policies_dir = app_base_dir() / "policies"
    policies_dir.mkdir(parents=True, exist_ok=True)

    chat = ChatWidget(policies_dir=policies_dir)
    launcher = ChatLauncher()

    def open_chat() -> None:
        chat.show_above(launcher.frameGeometry())

    # The launcher is "attached" to the chat: it opens the chat and then hides
    # itself, reappearing whenever the chat is hidden/closed (× button, etc.).
    launcher.clicked.connect(open_chat)
    chat.opened.connect(launcher.hide)
    chat.closed.connect(launcher.show)

    menu = QMenu()
    quit_action = QAction("Quit Assistant")
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    def show_menu(pos: QPoint) -> None:
        menu.exec(pos)

    launcher.context_menu_requested.connect(show_menu)

    launcher.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
