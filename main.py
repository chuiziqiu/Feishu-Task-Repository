"""
QR Scanner - Entry Point
========================
Launch the QR Code Scanner with Built-in Browser application.
"""

import sys
import os

# Ensure the application directory is in the path (needed for PyInstaller)
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    os.chdir(os.path.dirname(sys.executable))
    sys.path.insert(0, os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from scanner_app import MainWindow


def main():
    """Application entry point."""
    # Enable high DPI scaling
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)

    # Set application-wide font
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    # Set application style
    app.setStyle("Fusion")

    # Create and show main window
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
