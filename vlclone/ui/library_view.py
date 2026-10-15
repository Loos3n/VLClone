"""Media library view: Videos grid, Music by artist/album, Recently played, Continue watching.

Shown in place of the video (the playlist sidebar stays next to it). Data
comes from :class:`LibraryService`; the view polls its ``generation`` and
reloads when the background scanner changed something.
"""

from __future__ import annotations

import os
import sys
import time
from collections import OrderedDict
from typing import Callable

from PySide6.QtCore import QAbstractListModel, QModelIndex, QProcess, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QListView, QListWidget, QListWidgetItem, QMenu, QPushButton, QStackedWidget, QStyle,
                               QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.library import FOLDER_KINDS, SORTS, LibraryItem
from ..core.library_scanner import LibraryService
from ..core.settings import Settings
from ..core.timefmt import format_ms
from . import theme
from .dialogs import BaseDialog
from .icons import icon
from .playlist_panel import FilterEdit
from .widgets import IconButton, ToggleSwitch

SECTIONS = [
    ("videos", "Videos", "film"),
    ("music", "Music", "music"),
    ("recent", "Recently played", "clock"),
    ("continue", "Continue watching", "continue"),
]
VIDEO_SORTS = ["title", "added", "played", "duration", "size", "folder"]


def ago(stamp: float | None, now: float | None = None) -> str:
    if not stamp:
        return ""
    seconds = max(0, int((now or time.time()) - stamp))
    if seconds < 60:
        return "just now"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return ""


def show_in_folder(path: str) -> None:
    """Open the file manager at ``path`` (selecting the file where the platform allows)."""
    if not path:
        return
    if sys.platform == "win32" and os.path.exists(path):
        QProcess.startDetached("explorer", ["/select,", os.path.normpath(path)])
    elif sys.platform == "darwin" and os.path.exists(path):
        QProcess.startDetached("open", ["-R", path])
    else:
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


class ThumbCache:
    """Scaled thumbnails, least recently used first out."""

    def __init__(self, size: QSize, limit: int = 400) -> None:
        self.size = size
        self.limit = limit
        self._cache: OrderedDict[str, QPixmap | None] = OrderedDict()

    def get(self, path: str | None) -> QPixmap | None:
        if not path:
            return None
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        pm = QPixmap(path)
        scaled = None if pm.isNull() else pm.scaled(self.size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        self._cache[path] = scaled
        while len(self._cache) > self.limit:
            self._cache.popitem(last=False)
        return scaled


class ItemModel(QAbstractListModel):
    ItemRole = Qt.UserRole + 1

    def __init__(self) -> None:
        super().__init__()
        self.items: list[LibraryItem] = []

    def set_items(self, items: list[LibraryItem]) -> None:
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.items):
            return None
        item = self.items[index.row()]
        if role == Qt.DisplayRole:
            return item.title
        if role == Qt.ToolTipRole:
            return item.path or item.mrl
        if role == self.ItemRole:
            return item
        return None


class CardDelegate(QStyledItemDelegate):
    """Video card: 16:9 thumbnail with duration badge and resume progress, then title + details."""

    CARD = QSize(216, 186)
    THUMB = QSize(200, 112)

    def __init__(self, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs

    def sizeHint(self, option, index) -> QSize:
        return self.CARD

    def paint(self, p: QPainter, option, index) -> None:
        item: LibraryItem = index.data(ItemModel.ItemRole)
        if item is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = option.rect
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        card = QRectF(r.left() + 4, r.top() + 4, r.width() - 8, r.height() - 8)
        if selected or hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE_3 if selected else theme.SURFACE_2))
            p.drawRoundedRect(card, 10, 10)
        thumb = QRectF(card.left() + 4, card.top() + 4, self.THUMB.width(), self.THUMB.height())
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 8, 8)
        p.setClipPath(clip)
        pm = self.thumbs.get(item.thumb)
        if pm is not None:
            sx = max(0, (pm.width() - thumb.width()) / 2)
            sy = max(0, (pm.height() - thumb.height()) / 2)
            p.drawPixmap(thumb, pm, QRectF(sx, sy, thumb.width(), thumb.height()))
        else:
            p.fillRect(thumb, QColor(theme.SURFACE_2))
            name = "music" if item.kind == "audio" else "film"
            icon(name, "@TEXT_FAINT").paint(p, QRect(int(thumb.center().x()) - 18, int(thumb.center().y()) - 18,
                                                        36, 36))
        if item.progress > 0:
            p.fillRect(QRectF(thumb.left(), thumb.bottom() - 4, thumb.width(), 4), QColor(255, 255, 255, 60))
            p.fillRect(QRectF(thumb.left(), thumb.bottom() - 4, thumb.width() * item.progress, 4),
                       QColor(theme.ACCENT))
        p.setClipping(False)
        if item.duration_ms > 0:
            text = format_ms(item.duration_ms)
            font = theme.app_font(8.5)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            w = QFontMetrics(font).horizontalAdvance(text) + 10
            badge = QRectF(thumb.right() - w - 6, thumb.bottom() - 26, w, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(badge, 5, 5)
            p.setPen(QColor("#ffffff"))
            p.drawText(badge, Qt.AlignCenter, text)
        title_font = theme.app_font(10)
        title_font.setWeight(QFont.Weight.DemiBold)
        p.setFont(title_font)
        fm = QFontMetrics(title_font)
        text_rect = QRect(int(thumb.left()) + 2, int(thumb.bottom()) + 6, int(thumb.width()) - 4, fm.height())
        p.setPen(QColor(theme.TEXT))
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(item.title, Qt.ElideRight,
                                                                            text_rect.width()))
        p.setFont(theme.app_font(8.5))
        details = self.details(item)
        p.setPen(QColor(theme.TEXT_MUTED))
        sub = QRect(text_rect.left(), text_rect.bottom() + 2, text_rect.width(), 16)
        p.drawText(sub, Qt.AlignLeft | Qt.AlignVCenter, QFontMetrics(p.font()).elidedText(details, Qt.ElideRight,
                                                                                          sub.width()))
        p.restore()

    @staticmethod
    def details(item: LibraryItem) -> str:
        if item.position_ms:
            left = item.duration_ms - item.position_ms if item.duration_ms > 0 else 0
            return f"{format_ms(left)} left" if left > 0 else f"at {format_ms(item.position_ms)}"
        parts = [item.resolution, item.year]
        if not item.in_library:
            parts.append("not in library")
        return "  ·  ".join(x for x in parts if x)


class RowDelegate(QStyledItemDelegate):
    """Recently played row: small thumbnail/icon, title, artist or folder, when and how often."""

    HEIGHT = 56

    def __init__(self, thumbs: ThumbCache, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.HEIGHT)

    def paint(self, p: QPainter, option, index) -> None:
        item: LibraryItem = index.data(ItemModel.ItemRole)
        if item is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(6, 3, -6, -3)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.SURFACE_3 if option.state & QStyle.State_Selected else theme.SURFACE_2))
            p.drawRoundedRect(r, 8, 8)
        art = QRectF(r.left() + 6, r.top() + 4, 72, r.height() - 8)
        pm = self.thumbs.get(item.thumb)
        clip = QPainterPath()
        clip.addRoundedRect(art, 5, 5)
        p.setClipPath(clip)
        if pm is not None:
            p.drawPixmap(art, pm, QRectF((pm.width() - pm.height() * 72 / art.height()) / 2, 0,
                                         pm.height() * 72 / art.height(), pm.height()))
        else:
            p.fillRect(art, QColor(theme.SURFACE_2))
            name = "music" if item.kind == "audio" else ("link" if "://" in item.mrl and not item.path else "film")
            icon(name, "@TEXT_FAINT").paint(p, QRect(int(art.center().x()) - 11, int(art.center().y()) - 11,
                                                        22, 22))
        p.setClipping(False)
        x = int(art.right()) + 12
        right_w = 150
        font = theme.app_font(10)
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        fm = QFontMetrics(font)
        top = QRect(x, r.top() + 8, r.right() - x - right_w, fm.height())
        p.setPen(QColor(theme.TEXT))
        p.drawText(top, Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(item.title, Qt.ElideRight, top.width()))
        p.setFont(theme.app_font(8.5))
        p.setPen(QColor(theme.TEXT_MUTED))
        sub_text = (" — ".join(x for x in (item.artist, item.album) if x)
                    or (os.path.dirname(item.path) if item.path else item.mrl))
        sub = QRect(x, top.bottom() + 3, top.width(), 16)
        p.drawText(sub, Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(p.font()).elidedText(sub_text, Qt.ElideMiddle, sub.width()))
        when = ago(item.last_played)
        plays = f"{item.play_count} play{'s' if item.play_count != 1 else ''}" if item.play_count else ""
        right = QRect(r.right() - right_w, r.top(), right_w - 10, r.height())
        p.drawText(right, Qt.AlignRight | Qt.AlignVCenter, "\n".join(x for x in (when, plays) if x))
        p.restore()


def _grid_view() -> QListView:
    view = QListView()
    view.setViewMode(QListView.IconMode)
    view.setResizeMode(QListView.Adjust)
    view.setMovement(QListView.Static)
    view.setWrapping(True)
    view.setUniformItemSizes(True)
    view.setGridSize(CardDelegate.CARD)
    view.setSpacing(0)
    view.setMouseTracking(True)
    view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    view.setContextMenuPolicy(Qt.CustomContextMenu)
    view.setFrameShape(QListView.NoFrame)
    return view


class LibraryFoldersDialog(BaseDialog):
    """Watched folders and library options."""

    def __init__(self, service: LibraryService, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__("Library folders", parent)
        self.service = service
        self.settings = settings
        self.setMinimumSize(560, 420)
        note = QLabel("VLClone indexes video files in Video folders and audio files in Music folders, "
                      "including sub-folders.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        self.body.addWidget(note)
        self.list = QListWidget()
        self.body.addWidget(self.list, 1)
        row = QHBoxLayout()
        for kind, label in FOLDER_KINDS.items():
            b = QPushButton(icon("film" if kind == "video" else "music"), f"  Add {label.lower()} folder…")
            b.clicked.connect(lambda _=False, k=kind: self.add(k))
            row.addWidget(b)
        remove = QPushButton(icon("trash"), "  Remove")
        remove.clicked.connect(self.remove_selected)
        row.addStretch(1)
        row.addWidget(remove)
        self.body.addLayout(row)
        self.error = QLabel()
        self.error.setObjectName("Error")
        self.body.addWidget(self.error)
        for attr, label in (("library_watch", "Rescan automatically when these folders change"),
                            ("library_thumbnails", "Make video thumbnails")):
            line = QHBoxLayout()
            switch = ToggleSwitch()
            switch.setChecked(getattr(settings, attr))
            switch.toggled.connect(lambda on, a=attr: self._set(a, on))
            line.addWidget(QLabel(label), 1)
            line.addWidget(switch)
            self.body.addLayout(line)
        self.add_buttons("Done")
        self.refresh()

    def _set(self, attr: str, on: bool) -> None:
        setattr(self.settings, attr, on)
        self.service.watch = self.settings.library_watch
        self.service.thumbnails = self.settings.library_thumbnails

    def refresh(self) -> None:
        self.list.clear()
        for path, kind in self.service.folders():
            item = QListWidgetItem(icon("film" if kind == "video" else "music"),
                                   f"{path}    ({FOLDER_KINDS.get(kind, kind)})")
            item.setData(Qt.UserRole, path)
            self.list.addItem(item)

    def add(self, kind: str, folder: str | None = None) -> None:
        if folder is None:
            folder = QFileDialog.getExistingDirectory(self, f"Add {FOLDER_KINDS[kind].lower()} folder")
        if not folder:
            return
        error = self.service.add_folder(folder, kind)
        self.error.setText(error or "")
        self.refresh()

    def remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.service.remove_folder(item.data(Qt.UserRole))
        self.refresh()


class LibraryView(QWidget):
    #: (mrls, play now) — play or add to the playlist
    open_requested = Signal(list, bool)
    back_requested = Signal()
    convert_requested = Signal(list)  # LibraryItems

    def __init__(self, service: LibraryService, settings: Settings,
                 resume_entries: Callable[[], dict] = dict, trigger: Callable[[str], None] = lambda a: None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Library")
        self.setAttribute(Qt.WA_StyledBackground)
        # Styled by the theme's style sheet (#Library rules), so it follows theme switches.
        self.service = service
        self.settings = settings
        self.resume_entries = resume_entries
        self.trigger = trigger
        self.section = settings.library_section if settings.library_section in dict(
            (k, k) for k, _, _ in SECTIONS) else "videos"
        self._generation = -1
        self.thumbs = ThumbCache(CardDelegate.THUMB)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ------------------------------------------------------- navigation
        nav_w = QWidget()
        nav_w.setObjectName("Sidebar")
        nav_w.setAttribute(Qt.WA_StyledBackground)
        nav_w.setFixedWidth(210)
        nav = QVBoxLayout(nav_w)
        nav.setContentsMargins(12, 16, 12, 12)
        nav.setSpacing(4)
        heading = QLabel("Library")
        heading.setObjectName("Heading")
        heading.setContentsMargins(6, 0, 0, 8)
        nav.addWidget(heading)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label, icon_name in SECTIONS:
            b = QPushButton(icon(icon_name), f"  {label}")
            b.setObjectName("ListRow")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self.show_section(k))
            self.nav_group.addButton(b)
            nav.addWidget(b)
            self.nav_buttons[key] = b
        nav.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        nav.addWidget(self.status)
        folders = QPushButton(icon("folder"), "  Folders…")
        folders.setObjectName("Chip")
        folders.setCursor(Qt.PointingHandCursor)
        folders.clicked.connect(self.manage_folders)
        nav.addWidget(folders)
        root.addWidget(nav_w)

        # ------------------------------------------------------------ main
        main = QVBoxLayout()
        main.setContentsMargins(18, 16, 18, 12)
        main.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.title = QLabel()
        self.title.setObjectName("Heading")
        self.count = QLabel()
        self.count.setObjectName("Muted")
        header.addWidget(self.title)
        header.addWidget(self.count)
        header.addStretch(1)
        self.message = QLabel()
        self.message.setObjectName("Muted")
        self._message_timer = QTimer(self, singleShot=True, interval=3500)
        self._message_timer.timeout.connect(self.message.clear)
        header.addWidget(self.message)
        self.search = FilterEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.setFocusPolicy(Qt.ClickFocus)
        self._search_timer = QTimer(self, singleShot=True, interval=200)
        self._search_timer.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        header.addWidget(self.search)
        self.sort = QComboBox()
        for key in VIDEO_SORTS:
            self.sort.addItem(SORTS[key][0], key)
        index = self.sort.findData(settings.library_sort)
        self.sort.setCurrentIndex(max(0, index))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        self.sort.setFocusPolicy(Qt.NoFocus)
        header.addWidget(self.sort)
        rescan = IconButton("refresh", "Rescan library folders", 32, 18)
        rescan.clicked.connect(self.service.rescan)
        header.addWidget(rescan)
        back = IconButton("close", "Back to the player (Ctrl+Shift+L)", 32, 16)
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        main.addLayout(header)

        self.stack = QStackedWidget()
        self.grid_model = ItemModel()
        self.grid = _grid_view()
        self.grid.setModel(self.grid_model)
        self.grid.setItemDelegate(CardDelegate(self.thumbs, self.grid))
        self.grid.doubleClicked.connect(lambda idx: self._activate([self.grid_model.items[idx.row()]]))
        self.grid.customContextMenuRequested.connect(lambda pos: self._item_menu(self.grid, self.grid_model, pos))

        self.music = QTreeWidget()
        self.music.setHeaderLabels(["Title", "Duration"])
        self.music.setColumnCount(2)
        self.music.header().setStretchLastSection(False)
        self.music.header().setSectionResizeMode(0, self.music.header().ResizeMode.Stretch)
        self.music.header().setSectionResizeMode(1, self.music.header().ResizeMode.ResizeToContents)
        self.music.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.music.setContextMenuPolicy(Qt.CustomContextMenu)
        self.music.setUniformRowHeights(True)
        self.music.setIndentation(18)
        self.music.itemDoubleClicked.connect(lambda it, _col: self._activate(self._tree_items(it)))
        self.music.customContextMenuRequested.connect(self._music_menu)

        self.list_model = ItemModel()
        self.list = QListView()
        self.list.setModel(self.list_model)
        self.list.setItemDelegate(RowDelegate(self.thumbs, self.list))
        self.list.setMouseTracking(True)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.setFrameShape(QListView.NoFrame)
        self.list.doubleClicked.connect(lambda idx: self._activate([self.list_model.items[idx.row()]]))
        self.list.customContextMenuRequested.connect(lambda pos: self._item_menu(self.list, self.list_model, pos))

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setAlignment(Qt.AlignCenter)
        el.setSpacing(12)
        self.empty_icon = QLabel()
        self.empty_icon.setAlignment(Qt.AlignCenter)
        self.empty_title = QLabel()
        self.empty_title.setObjectName("Heading")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_text = QLabel()
        self.empty_text.setObjectName("Muted")
        self.empty_text.setAlignment(Qt.AlignCenter)
        self.empty_text.setWordWrap(True)
        self.empty_buttons = QWidget()
        eb = QHBoxLayout(self.empty_buttons)
        eb.setAlignment(Qt.AlignCenter)
        for kind, label in FOLDER_KINDS.items():
            b = QPushButton(icon("film" if kind == "video" else "music", "@ON_ACCENT" if kind == "video" else None),
                            f"  Add {label.lower()} folder…")
            if kind == "video":
                b.setObjectName("Primary")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=kind: self.add_folder(k))
            eb.addWidget(b)
        for w in (self.empty_icon, self.empty_title, self.empty_text, self.empty_buttons):
            el.addWidget(w)
        for w in (self.grid, self.music, self.list, self.empty):
            self.stack.addWidget(w)
        main.addWidget(self.stack, 1)

        self.now_playing = QWidget()
        npl = QHBoxLayout(self.now_playing)
        npl.setContentsMargins(0, 4, 0, 0)
        self.np_label = QLabel()
        self.np_label.setObjectName("Muted")
        self.np_play = IconButton("pause", "Play / pause (Space)", 34, 20)
        self.np_play.clicked.connect(lambda: self.trigger("play_pause"))
        np_back = QPushButton("Back to player")
        np_back.setObjectName("Chip")
        np_back.setCursor(Qt.PointingHandCursor)
        np_back.clicked.connect(self.back_requested.emit)
        npl.addWidget(self.np_play)
        npl.addWidget(self.np_label, 1)
        npl.addWidget(np_back)
        self.now_playing.hide()
        main.addWidget(self.now_playing)
        root.addLayout(main, 1)

        self._timer = QTimer(self, interval=500)
        self._timer.timeout.connect(self.poll)
        self.show_section(self.section)

    # ------------------------------------------------------------ state
    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._timer.start()
        self.poll(force=True)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._timer.stop()

    def poll(self, force: bool = False) -> None:
        status = self.service.status
        text = status.text
        self.status.setText(text)
        self.status.setVisible(bool(text))
        gen = self.service.generation
        if force or gen != self._generation:
            self._generation = gen
            self.reload()

    def notify(self, text: str) -> None:
        """A short message in the header (e.g. "Added 3 items to the playlist")."""
        self.message.setText(text)
        self._message_timer.start()

    def set_now_playing(self, title: str, playing: bool) -> None:
        self.now_playing.setVisible(bool(title))
        self.np_label.setText(f"Now playing: {title}" if title else "")
        self.np_play.set_icon_name("pause" if playing else "play")

    def show_section(self, key: str) -> None:
        self.section = key
        self.settings.library_section = key
        self.nav_buttons[key].setChecked(True)
        self.title.setText(dict((k, label) for k, label, _ in SECTIONS)[key])
        self.sort.setVisible(key == "videos")
        self.reload()

    def _sort_changed(self) -> None:
        self.settings.library_sort = self.sort.currentData()
        self.reload()

    # ----------------------------------------------------------- loading
    def reload(self) -> None:
        # Keep the scroll position: the scanner refreshes the view while it works.
        bars = [(v.verticalScrollBar(), v.verticalScrollBar().value()) for v in (self.grid, self.list, self.music)]
        self._reload()
        for bar, value in bars:
            bar.setValue(value)

    def _reload(self) -> None:
        search = self.search.text().strip()
        key = self.section
        counts = self.service.db.counts()
        no_folders = not self.service.folders()
        if key == "videos":
            items = self.service.videos(search, self.sort.currentData() or "title")
            self.grid_model.set_items(items)
            self._show(self.grid, len(items), "video", no_folders or not counts["video"], search)
        elif key == "music":
            tree = self.service.music(search)
            self._fill_music(tree)
            n = sum(len(al.tracks) for albums in tree.values() for al in albums)
            self._show(self.music, n, "track", no_folders or not counts["audio"], search,
                       f"{len(tree)} artist{'s' if len(tree) != 1 else ''} · ")
        elif key == "recent":
            items = self.service.recent(search)
            self.list_model.set_items(items)
            self._show(self.list, len(items), "item", not items and not search, search)
        else:
            items = self.service.continue_watching(self.resume_entries(), search)
            self.grid_model.set_items(items)
            self._show(self.grid, len(items), "item", not items and not search, search)

    def _show(self, widget: QWidget, n: int, noun: str, empty: bool, search: str, prefix: str = "") -> None:
        self.count.setText(f"{prefix}{n} {noun}{'s' if n != 1 else ''}" if not empty else "")
        if empty:
            self._empty_state()
            self.stack.setCurrentWidget(self.empty)
        elif n == 0:
            self.empty_icon.setPixmap(icon("info", "@TEXT_FAINT").pixmap(48, 48))
            self.empty_title.setText("No matches")
            self.empty_text.setText(f"Nothing matches “{search}”.")
            self.empty_buttons.hide()
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(widget)

    def _empty_state(self) -> None:
        key = self.section
        busy = self.service.status.busy
        texts = {
            "videos": ("film", "No videos yet",
                       "Scanning your folders…" if busy else "Add a folder with videos to build your library."),
            "music": ("music", "No music yet",
                      "Scanning your folders…" if busy else "Add a music folder; albums and artists come from "
                                                            "the files' tags."),
            "recent": ("clock", "Nothing played yet", "Everything you play shows up here, including streams."),
            "continue": ("continue", "Nothing to continue",
                         "Videos you stop part-way through appear here, ready to resume."),
        }
        name, title, text = texts[key]
        self.empty_icon.setPixmap(icon(name, "@TEXT_FAINT").pixmap(56, 56))
        self.empty_title.setText(title)
        self.empty_text.setText(text)
        self.empty_buttons.setVisible(key in ("videos", "music") and not busy)

    def _fill_music(self, tree) -> None:
        self.music.clear()
        bold = self.music.font()
        bold.setWeight(QFont.Weight.DemiBold)
        expand = len(tree) <= 3 or bool(self.search.text().strip())
        for artist, albums in tree.items():
            a_item = QTreeWidgetItem([artist, ""])
            a_item.setFont(0, bold)
            a_item.setIcon(0, icon("music"))
            a_item.setData(0, Qt.UserRole, [t for al in albums for t in al.tracks])
            self.music.addTopLevelItem(a_item)
            for album in albums:
                label = album.name + (f"  ({album.year})" if album.year else "")
                al_item = QTreeWidgetItem([label, format_ms(album.duration_ms) if album.duration_ms else ""])
                al_item.setData(0, Qt.UserRole, list(album.tracks))
                a_item.addChild(al_item)
                for t in album.tracks:
                    title = f"{t.track:>2}.  {t.title}" if t.track else t.title
                    t_item = QTreeWidgetItem([title, format_ms(t.duration_ms) if t.duration_ms > 0 else ""])
                    t_item.setData(0, Qt.UserRole, [t])
                    t_item.setToolTip(0, t.path)
                    t_item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    al_item.addChild(t_item)
                al_item.setExpanded(True)
            a_item.setExpanded(expand)

    @staticmethod
    def _tree_items(item: QTreeWidgetItem) -> list[LibraryItem]:
        return list(item.data(0, Qt.UserRole) or [])

    # ----------------------------------------------------------- actions
    def _activate(self, items: list[LibraryItem]) -> None:
        if items:
            self.open_requested.emit([i.mrl for i in items], True)

    def _selected(self, view: QAbstractItemView, model: ItemModel) -> list[LibraryItem]:
        rows = sorted({i.row() for i in view.selectionModel().selectedIndexes()})
        return [model.items[r] for r in rows if r < len(model.items)]

    def _item_menu(self, view: QAbstractItemView, model: ItemModel, pos) -> None:
        index = view.indexAt(pos)
        if not index.isValid():
            return
        if not view.selectionModel().isSelected(index):
            view.setCurrentIndex(index)
        items = self._selected(view, model) or [model.items[index.row()]]
        self._menu(items, view.viewport().mapToGlobal(pos))

    def _music_menu(self, pos) -> None:
        item = self.music.itemAt(pos)
        if item is None:
            return
        items: list[LibraryItem] = []
        for selected in self.music.selectedItems() or [item]:
            items.extend(t for t in self._tree_items(selected) if t not in items)
        self._menu(items, self.music.viewport().mapToGlobal(pos))

    def _menu(self, items: list[LibraryItem], global_pos) -> None:
        menu = self.build_item_menu(items)
        menu.exec(global_pos)

    def build_item_menu(self, items: list[LibraryItem]) -> QMenu:
        menu = QMenu(self)
        mrls = [i.mrl for i in items]
        menu.addAction(icon("play"), "Play", lambda: self.open_requested.emit(mrls, True))
        menu.addAction(icon("plus"), "Add to playlist", lambda: self.open_requested.emit(mrls, False))
        menu.addAction(icon("convert"), "Convert / save…", lambda: self.convert_requested.emit(list(items)))
        first = items[0]
        if first.path:
            menu.addAction(icon("folder"), "Show in folder", lambda: show_in_folder(first.path))
        if self.section == "recent":
            menu.addSeparator()
            menu.addAction(icon("trash"), "Remove from history",
                           lambda: [self.service.forget_history(m) for m in mrls])
            menu.addAction("Clear history", lambda: self.service.forget_history())
        return menu

    def add_folder(self, kind: str) -> None:
        folder = QFileDialog.getExistingDirectory(self, f"Add {FOLDER_KINDS[kind].lower()} folder")
        if folder:
            error = self.service.add_folder(folder, kind)
            if error:
                self.notify(error)

    def manage_folders(self) -> None:
        LibraryFoldersDialog(self.service, self.settings, self).exec()
        self.poll(force=True)
