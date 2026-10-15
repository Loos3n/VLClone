"""Design tokens and the application style sheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0e1014"
SURFACE = "#161920"
SURFACE_2 = "#1d2129"
SURFACE_3 = "#262b35"
BORDER = "#2b303b"
TEXT = "#e9ebf0"
TEXT_MUTED = "#9aa2b1"
TEXT_FAINT = "#6b7383"
ACCENT = "#ff8a2a"       # a nod to VLC's cone
ACCENT_HOVER = "#ff9d4d"
ACCENT_SOFT = "rgba(255, 138, 42, 0.16)"
DANGER = "#ff5d5d"
OVERLAY_BG = "rgba(14, 16, 20, 0.82)"

RADIUS = 10
SPACING = 8

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue",
                 "Cantarell", "Noto Sans", "DejaVu Sans", "Arial"]


def color(token: str) -> QColor:
    return QColor(token)


def app_font(point_size: float = 10.0) -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(point_size)
    font.setHintingPreference(QFont.PreferNoHinting)
    return font


STYLE_SHEET = f"""
* {{
    color: {TEXT};
    outline: none;
}}
QMainWindow, QDialog, #Root {{
    background: {BG};
}}
QToolTip {{
    background: {SURFACE_3};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
}}
QLabel#Muted, QLabel[muted="true"] {{ color: {TEXT_MUTED}; }}
QLabel#SectionTitle {{
    color: {TEXT_MUTED};
    font-weight: 600;
    letter-spacing: 0.5px;
    padding-top: 6px;
}}
QLabel#Heading {{ font-size: 15pt; font-weight: 600; }}

/* Buttons */
QPushButton {{
    background: {SURFACE_3};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    min-height: 20px;
}}
QPushButton:hover {{ background: #2f3541; }}
QPushButton:pressed {{ background: #232831; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; }}
QPushButton#Primary {{
    background: {ACCENT};
    border: none;
    color: #1a0f05;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Chip {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 14px;
    padding: 4px 12px;
    min-height: 18px;
}}
QPushButton#Chip:hover {{ background: {SURFACE_3}; }}
QPushButton#Chip:checked {{
    background: {ACCENT_SOFT};
    border: 1px solid {ACCENT};
    color: {ACCENT_HOVER};
}}
QPushButton#Tab {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px 12px;
    color: {TEXT_MUTED};
    font-weight: 600;
}}
QPushButton#Tab:hover {{ color: {TEXT}; background: {SURFACE_2}; }}
QPushButton#Tab:checked {{ color: {TEXT}; background: {SURFACE_3}; }}
QPushButton#ListRow {{
    text-align: left;
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 7px 10px;
}}
QPushButton#ListRow:hover {{ background: {SURFACE_2}; }}
QPushButton#ListRow:checked {{ background: {ACCENT_SOFT}; color: {ACCENT_HOVER}; }}

/* Inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: #1a0f05;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    selection-background-color: {SURFACE_3};
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
QCheckBox {{ spacing: 10px; }}
QCheckBox::indicator {{
    width: 34px; height: 18px; border-radius: 9px;
    background: {SURFACE_3}; border: 1px solid {BORDER};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}

/* Lists */
QListView, QListWidget, QTreeWidget, QTableWidget {{
    background: transparent;
    border: none;
}}
QListView::item, QListWidget::item {{
    border-radius: 8px;
    padding: 6px 8px;
}}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QHeaderView::section {{
    background: transparent; color: {TEXT_MUTED}; border: none; padding: 4px 6px;
}}

/* Scrollbars */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4150; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Menus */
QMenu {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 28px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::item:disabled {{ color: {TEXT_FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 6px; }}

/* Sliders (plain QSlider, used in dialogs) */
QSlider::groove:horizontal {{ height: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}

/* Panels */
#Sidebar {{
    background: {SURFACE};
    border-left: 1px solid {BORDER};
}}
#Popover {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
#Card {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
QTabWidget::pane {{ border: none; }}
QSplitter::handle {{ background: transparent; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(SURFACE_2))
    pal.setColor(QPalette.AlternateBase, QColor(SURFACE))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(SURFACE_3))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#1a0f05"))
    pal.setColor(QPalette.ToolTipBase, QColor(SURFACE_3))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_FAINT))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_FAINT))
    app.setPalette(pal)
    app.setFont(app_font())
    app.setStyleSheet(STYLE_SHEET)
