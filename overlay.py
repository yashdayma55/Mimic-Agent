"""
Visual approval overlay: draws a red box around the target element on screen,
shows the action text, and waits for a global hotkey (Enter=approve, Esc=reject).

The box is a frameless, transparent, always-on-top window drawn at the element's
screen rectangle. It does NOT steal focus from the target app.
"""

import sys
from PyQt6 import QtWidgets, QtCore, QtGui
from pynput import keyboard as kb


class _Overlay(QtWidgets.QWidget):
    def __init__(self, rect, label):
        super().__init__()
        self._rect = rect          # (left, top, right, bottom) in screen pixels
        self._label = label

        # frameless, transparent, always-on-top, click-through, no focus steal
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # cover the full virtual desktop so we can draw anywhere
        screen = QtWidgets.QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(screen)
        self.showFullScreen()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        L, T, R, B = self._rect
        w, h = R - L, B - T

        # red box around the target
        pen = QtGui.QPen(QtGui.QColor(230, 60, 90), 3)
        p.setPen(pen)
        p.drawRect(L, T, w, h)

        # label just above the box
        p.setPen(QtGui.QColor(230, 60, 90))
        font = QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.Bold)
        p.setFont(font)
        text = f"{self._label}   (ENTER=approve  ESC=reject)"
        ty = T - 10 if T > 30 else B + 22
        p.drawText(L, ty, text)


def approve_with_overlay(rect, label):
    """Show the box at rect=(L,T,R,B), wait for Enter/Esc, return 'approve'/'reject'."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    overlay = _Overlay(rect, label)

    decision = {"answer": None}

    # global hotkey listener (works even though overlay is showing)
    def on_press(key):
        if key == kb.Key.enter:
            decision["answer"] = "approve"
            return False
        elif key == kb.Key.esc:
            decision["answer"] = "reject"
            return False

    listener = kb.Listener(on_press=on_press)
    listener.start()

    # pump the Qt event loop until a decision is made
    while decision["answer"] is None:
        app.processEvents()
        QtCore.QThread.msleep(20)

    listener.stop()
    overlay.close()
    app.processEvents()
    return decision["answer"]


if __name__ == "__main__":
    # standalone test: draw a box in the middle of the screen
    print("Showing overlay box - press ENTER or ESC...")
    result = approve_with_overlay((400, 300, 900, 500), "TEST: about to click something")
    print("You chose:", result)