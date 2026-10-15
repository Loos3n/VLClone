"""Design tokens, light/dark palettes, the accent colour and the application style sheet.

The tokens (``BG``, ``SURFACE``, ``TEXT``, ``ACCENT``…) are module attributes
that :func:`apply_theme` rewrites, so painted widgets that read
``theme.TEXT`` at paint time follow a theme switch by themselves; widgets
that cache colours listen to :func:`notifier`. Icons can name a token
(``icon("play", "@ACCENT")``) instead of a fixed colour.

The video stage (the overlay controls over the picture, the mini player)
is always dark, like every player's: its colours are the fixed ``VIDEO_*``
tokens and :func:`overlay_style_sheet`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from ..core.settings import DEFAULT_ACCENT


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    surface: str
    surface_2: str
    surface_3: str
    border: str
    text: str
    text_muted: str
    text_faint: str
    hover: str
    pressed: str
    scroll_hover: str
    danger: str
    success: str
    overlay_bg: str
    shadow: str


DARK = Palette("dark", bg="#0e1014", surface="#161920", surface_2="#1d2129", surface_3="#262b35",
               border="#2b303b", text="#e9ebf0", text_muted="#9aa2b1", text_faint="#6b7383",
               hover="#2f3541", pressed="#232831", scroll_hover="#3a4150", danger="#ff5d5d",
               success="#7fd88f", overlay_bg="rgba(14, 16, 20, 0.82)", shadow="rgba(0, 0, 0, 0.55)")
LIGHT = Palette("light", bg="#f4f5f8", surface="#ffffff", surface_2="#eef0f4", surface_3="#e2e5eb",
                border="#d3d7df", text="#1a1d23", text_muted="#5b6372", text_faint="#8a92a1",
                hover="#d8dce4", pressed="#cdd2db", scroll_hover="#b9bfca", danger="#d93636",
                success="#2e8b45", overlay_bg="rgba(255, 255, 255, 0.92)", shadow="rgba(20, 24, 33, 0.22)")
PALETTES = {"dark": DARK, "light": LIGHT}

ACCENT_PRESETS = [
    ("VLC orange", "#ff8a2a"), ("Blue", "#3d8bfd"), ("Violet", "#9b6bff"), ("Green", "#2fbf71"),
    ("Pink", "#ff5c9a"), ("Red", "#f2545b"), ("Teal", "#19b5b0"), ("Amber", "#f5b82e"),
]

# Fixed colours of the always-dark video stage (overlay controls, mini player).
VIDEO_TEXT = DARK.text
VIDEO_MUTED = DARK.text_muted
VIDEO_SURFACE = DARK.surface_3
VIDEO_BORDER = DARK.border
VIDEO_OVERLAY_BG = DARK.overlay_bg

# Current tokens (rewritten by apply_theme). Defaults: dark + VLC orange.
MODE = "dark"            # the resolved palette: "dark" or "light"
BG = DARK.bg
SURFACE = DARK.surface
SURFACE_2 = DARK.surface_2
SURFACE_3 = DARK.surface_3
BORDER = DARK.border
TEXT = DARK.text
TEXT_MUTED = DARK.text_muted
TEXT_FAINT = DARK.text_faint
HOVER = DARK.hover
PRESSED = DARK.pressed
SCROLL_HOVER = DARK.scroll_hover
DANGER = DARK.danger
SUCCESS = DARK.success
OVERLAY_BG = DARK.overlay_bg
SHADOW = DARK.shadow
ACCENT = DEFAULT_ACCENT
ACCENT_HOVER = "#ff9d4d"
ACCENT_SOFT = "rgba(255, 138, 42, 0.16)"
ACCENT_TEXT = "#ff9d4d"  # accent-coloured text on surfaces (contrast-adjusted)
ON_ACCENT = "#1a0f05"    # text/icons on an accent background

RADIUS = 10
SPACING = 8

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue",
                 "Cantarell", "Noto Sans", "DejaVu Sans", "Arial"]


def color(token: str) -> QColor:
    """A colour from a value (``"#fff"``) or a token reference (``"@ACCENT"``)."""
    return QColor(resolve(token))


def resolve(value: str) -> str:
    if value.startswith("@"):
        return globals()[value[1:]]
    return value


def app_font(point_size: float = 10.0) -> QFont:
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    font.setPointSizeF(point_size)
    font.setHintingPreference(QFont.PreferNoHinting)
    return font


# ------------------------------------------------------------------ accent maths
def _luminance(c: QColor) -> float:
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.redF()) + 0.7152 * lin(c.greenF()) + 0.0722 * lin(c.blueF())


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(QColor(a)), _luminance(QColor(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _shift(c: QColor, lighter: bool, amount: float) -> QColor:
    h, s, l, a = c.getHslF()
    l = min(1.0, l + amount) if lighter else max(0.0, l - amount)
    return QColor.fromHslF(max(0.0, h), s, l, a)


def derive_accent(accent: str, mode: str) -> dict[str, str]:
    """Hover, soft background, readable text and on-accent colours for an accent in a mode."""
    base = QColor(accent if QColor(accent).isValid() else DEFAULT_ACCENT)
    surface = PALETTES[mode].surface
    hover = _shift(base, mode == "dark", 0.07)
    text = QColor(base)
    for _ in range(12):  # accent-coloured text must stay readable on the panels
        if contrast(text.name(), surface) >= 3.2:
            break
        text = _shift(text, mode == "dark", 0.05)
    on = "#1a0f05" if contrast(base.name(), "#1a0f05") >= contrast(base.name(), "#ffffff") else "#ffffff"
    alpha = 0.16 if mode == "dark" else 0.14
    soft = f"rgba({base.red()}, {base.green()}, {base.blue()}, {alpha})"
    return {"ACCENT": base.name(), "ACCENT_HOVER": hover.name(), "ACCENT_SOFT": soft,
            "ACCENT_TEXT": text.name(), "ON_ACCENT": on}


# ------------------------------------------------------------------ mode
def system_prefers_dark() -> bool:
    """The OS colour scheme (Qt 6.5+), with the Windows registry as a fallback."""
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except (AttributeError, RuntimeError):
        pass
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            pass
    return True  # unknown: VLClone's own default is dark


def resolve_mode(mode: str) -> str:
    """"dark" / "light" / "system" -> the palette to use."""
    if mode == "system":
        return "dark" if system_prefers_dark() else "light"
    return mode if mode in PALETTES else "dark"


class ThemeNotifier(QObject):
    changed = Signal()


_notifier: ThemeNotifier | None = None
_requested = ("dark", DEFAULT_ACCENT)
_system_hooked = False


def notifier() -> ThemeNotifier:
    global _notifier
    if _notifier is None:
        _notifier = ThemeNotifier()
    return _notifier


def requested() -> tuple[str, str]:
    """The (mode, accent) last passed to apply_theme ("system" stays "system")."""
    return _requested


def set_tokens(mode: str, accent: str) -> None:
    """Rewrite the module tokens for a resolved mode ("dark"/"light") and accent."""
    pal = PALETTES[mode]
    g = globals()
    g.update(MODE=mode, BG=pal.bg, SURFACE=pal.surface, SURFACE_2=pal.surface_2, SURFACE_3=pal.surface_3,
             BORDER=pal.border, TEXT=pal.text, TEXT_MUTED=pal.text_muted, TEXT_FAINT=pal.text_faint,
             HOVER=pal.hover, PRESSED=pal.pressed, SCROLL_HOVER=pal.scroll_hover, DANGER=pal.danger,
             SUCCESS=pal.success, OVERLAY_BG=pal.overlay_bg, SHADOW=pal.shadow)
    g.update(derive_accent(accent, mode))
    g["STYLE_SHEET"] = style_sheet()


def style_sheet() -> str:
    return f"""
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
QLabel#Error, QLabel[error="true"] {{ color: {DANGER}; }}
QLabel#Success {{ color: {SUCCESS}; }}
QLabel#SectionTitle {{
    color: {TEXT_MUTED};
    font-weight: 600;
    letter-spacing: 0.5px;
    padding-top: 6px;
}}
QLabel#Heading {{ font-size: 15pt; font-weight: 600; }}
QLabel#Title {{ font-size: 12pt; font-weight: 600; }}
QFrame#Rule {{ background: {BORDER}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#VRule {{ background: {BORDER}; border: none; }}

/* Buttons */
QPushButton {{
    background: {SURFACE_3};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 14px;
    min-height: 20px;
}}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {PRESSED}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; }}
QPushButton#Primary {{
    background: {ACCENT};
    border: none;
    color: {ON_ACCENT};
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Flat {{ background: transparent; border: none; color: {TEXT_MUTED}; }}
QPushButton#Flat:hover {{ color: {TEXT}; }}
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
    color: {ACCENT_TEXT};
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
QPushButton#ListRow:checked {{ background: {ACCENT_SOFT}; color: {ACCENT_TEXT}; }}
QPushButton#ListRow:disabled {{ color: {TEXT_FAINT}; }}
QPushButton#Swatch {{ border-radius: 13px; min-width: 26px; max-width: 26px; min-height: 26px;
    max-height: 26px; padding: 0; border: 2px solid transparent; }}
QPushButton#Swatch:checked {{ border: 2px solid {TEXT}; }}
QPushButton#ThemeCard {{
    background: {SURFACE_2}; border: 2px solid {BORDER}; border-radius: 12px; padding: 10px;
    text-align: center;
}}
QPushButton#ThemeCard:checked {{ border: 2px solid {ACCENT}; background: {ACCENT_SOFT}; }}

/* Inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: {ON_ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    selection-background-color: {SURFACE_3};
    selection-color: {TEXT};
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0px; border: none; }}
QCheckBox, QRadioButton {{ spacing: 10px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    background: {SURFACE_3}; border: 1px solid {BORDER};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT}; }}
QRadioButton::indicator {{
    width: 14px; height: 14px; border-radius: 8px;
    background: {SURFACE_3}; border: 1px solid {BORDER};
}}
QRadioButton::indicator:checked {{ background: {ACCENT}; border: 3px solid {SURFACE_3}; }}
QKeySequenceEdit QLineEdit {{ padding: 4px 8px; }}

/* Lists */
QListView, QListWidget, QTreeWidget, QTableWidget, QTreeView {{
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
QTableWidget {{ gridline-color: {BORDER}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* Scrollbars */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SURFACE_3}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SURFACE_3}; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

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

QSlider::groove:vertical {{ width: 4px; background: {SURFACE_3}; border-radius: 2px; }}
QSlider::add-page:vertical {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::sub-page:vertical {{ background: {SURFACE_3}; border-radius: 2px; }}
QSlider::handle:vertical {{
    background: {TEXT}; width: 14px; height: 14px; margin: 0 -5px; border-radius: 7px;
}}
QSlider::handle:hover {{ background: {ACCENT_HOVER}; }}
QSlider:disabled {{ }}
QSlider::sub-page:horizontal:disabled, QSlider::add-page:vertical:disabled {{ background: {TEXT_FAINT}; }}
QLabel#ReloadBadge {{
    color: {ACCENT_TEXT};
    background: {ACCENT_SOFT};
    border-radius: 8px;
    padding: 1px 7px;
    font-size: 8pt;
    font-weight: 600;
}}
QLabel#LiveBadge {{
    color: {SUCCESS};
    background: rgba(127, 216, 143, 0.14);
    border-radius: 8px;
    padding: 1px 7px;
    font-size: 8pt;
    font-weight: 600;
}}
QLabel#ValueLabel {{ color: {TEXT_MUTED}; font-size: 8.5pt; }}

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
#Banner {{
    background: {ACCENT_SOFT};
    border: 1px solid {ACCENT};
    border-radius: {RADIUS}px;
}}
QTabWidget::pane {{ border: none; }}
QSplitter::handle {{ background: transparent; }}

/* Media library */
#Library {{ background: {BG}; }}
#Library QTreeWidget {{ background: transparent; border: none; outline: none; }}
#Library QTreeWidget::item {{ padding: 6px 2px; border-radius: 6px; }}
#Library QHeaderView {{ background: transparent; border: none; }}
#Library QTreeWidget::item:hover {{ background: {SURFACE_2}; }}
#Library QTreeWidget::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
#Library QTreeWidget::branch {{ background: transparent; }}
#Library QHeaderView::section {{ background: transparent; color: {TEXT_MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 6px 8px; font-weight: 600; }}

/* Toasts and the OSD outside the video stage (library, mini player, dialogs) */
#Toast {{ background: {OVERLAY_BG}; border: 1px solid {BORDER}; border-radius: 12px; }}
#Osd {{ background: {OVERLAY_BG}; border-radius: 12px; padding: 8px 16px; font-size: 12pt;
    font-weight: 600; }}
"""


def overlay_style_sheet() -> str:
    """The video stage stays dark in every theme; only the accent follows the user's choice."""
    return f"""
* {{ color: {VIDEO_TEXT}; }}
QLabel#Muted {{ color: {VIDEO_MUTED}; }}
QLabel#Heading {{ font-size: 15pt; font-weight: 600; color: {VIDEO_TEXT}; }}
QPushButton {{
    background: {VIDEO_SURFACE}; border: 1px solid {VIDEO_BORDER}; border-radius: 8px;
    padding: 6px 14px; min-height: 20px; color: {VIDEO_TEXT};
}}
QPushButton:hover {{ background: {DARK.hover}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: {ON_ACCENT}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Chip {{
    background: {DARK.surface_2}; border: 1px solid {VIDEO_BORDER}; border-radius: 14px;
    padding: 4px 12px; min-height: 18px;
}}
QPushButton#Chip:hover {{ background: {VIDEO_SURFACE}; }}
QPushButton#Chip:checked {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; color: {ACCENT_HOVER}; }}
QToolTip {{ background: {VIDEO_SURFACE}; color: {VIDEO_TEXT}; border: 1px solid {VIDEO_BORDER}; }}
#Toast {{ background: {VIDEO_OVERLAY_BG}; border: 1px solid {VIDEO_BORDER}; border-radius: 12px; }}
#Osd {{ background: {VIDEO_OVERLAY_BG}; border-radius: 12px; padding: 8px 16px; font-size: 12pt;
    font-weight: 600; color: {VIDEO_TEXT}; }}
#SeekPreview {{ background: {VIDEO_SURFACE}; border: 1px solid {VIDEO_BORDER}; border-radius: 8px; }}
"""


STYLE_SHEET = style_sheet()


def _palette() -> QPalette:
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(SURFACE_2))
    pal.setColor(QPalette.AlternateBase, QColor(SURFACE))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(SURFACE_3))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor(ON_ACCENT))
    pal.setColor(QPalette.ToolTipBase, QColor(SURFACE_3))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_FAINT))
    pal.setColor(QPalette.Link, QColor(ACCENT_TEXT))
    pal.setColor(QPalette.Light, QColor(SURFACE_3))
    pal.setColor(QPalette.Mid, QColor(BORDER))
    pal.setColor(QPalette.Dark, QColor(BG))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_FAINT))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor(TEXT_FAINT))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_FAINT))
    return pal


def apply_theme(app: QApplication, mode: str = "dark", accent: str = DEFAULT_ACCENT) -> str:
    """Switch the whole app to ``mode`` ("dark", "light", "system") and ``accent``.

    Returns the resolved palette name. Safe to call again at any time: every
    widget is repainted and :func:`notifier` emits ``changed``.
    """
    global _requested, _system_hooked
    _requested = (mode, accent)
    hints = app.styleHints()
    if mode == "system" and hasattr(hints, "unsetColorScheme"):
        hints.unsetColorScheme()  # read the OS scheme, not an override we set earlier
    resolved = resolve_mode(mode)
    set_tokens(resolved, accent)
    # Each of these re-polishes every widget: only do what actually changes.
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    if mode != "system" and hasattr(hints, "setColorScheme"):
        # Qt 6.8+: the native title bars and dialogs follow the app's choice too.
        hints.setColorScheme(Qt.ColorScheme.Dark if resolved == "dark" else Qt.ColorScheme.Light)
    if app.styleSheet() != STYLE_SHEET:
        app.setPalette(_palette())
        app.setFont(app_font())
        app.setStyleSheet(STYLE_SHEET)
    if not _system_hooked:
        _system_hooked = True
        try:
            app.styleHints().colorSchemeChanged.connect(lambda *_: _system_changed(app))
        except AttributeError:
            pass
    for w in app.allWidgets():
        w.update()
    notifier().changed.emit()
    return resolved


def _system_changed(app: QApplication) -> None:
    mode, accent = _requested
    if mode == "system" and resolve_mode("system") != MODE:
        apply_theme(app, mode, accent)


def reapply(app: QApplication) -> None:
    apply_theme(app, *_requested)


def set_error(widget, on: bool = True) -> None:
    """Show a label in the theme's error colour (or back to normal); follows theme switches."""
    if widget.property("error") != on:
        widget.setProperty("error", on)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def popover_shadow(effect) -> None:
    """Theme a popover's drop shadow now and on every theme switch."""
    def apply() -> None:
        effect.setColor(QColor(0, 0, 0, 150 if MODE == "dark" else 70))
    apply()
    notifier().changed.connect(apply)
