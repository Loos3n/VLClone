"""Collapsible playlist sidebar with drag-to-reorder."""

from __future__ import annotations

import json
from typing import Callable

from PySide6.QtCore import (QAbstractListModel, QMimeData, QModelIndex, QRect, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListView,
                               QMenu, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QVBoxLayout, QWidget)

from ..core.playlist import Playlist
from ..core.timefmt import format_ms
from . import theme
from .icons import icon
from .widgets import IconButton

_MIME = "application/x-vlclone-rows"


class PlaylistModel(QAbstractListModel):
    """Qt adapter over the core :class:`Playlist` (which stays the source of truth)."""

    UidRole = Qt.UserRole + 1
    DurationRole = Qt.UserRole + 2
    CurrentRole = Qt.UserRole + 3

    def __init__(self, playlist: Playlist) -> None:
        super().__init__()
        self.playlist = playlist
        self.external_drop: Callable[[list[str], int], None] | None = None
        playlist.changed.connect(self._reset)
        playlist.current_changed.connect(lambda _: self._repaint())

    def _reset(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    def _repaint(self) -> None:
        if self.rowCount():
            self.dataChanged.emit(self.index(0), self.index(self.rowCount() - 1))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.playlist)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.playlist):
            return None
        item = self.playlist[index.row()]
        if role == Qt.DisplayRole:
            return item.title
        if role == Qt.ToolTipRole:
            return item.mrl
        if role == self.UidRole:
            return item.uid
        if role == self.DurationRole:
            return item.duration_ms
        if role == self.CurrentRole:
            return index.row() == self.playlist.current_index
        return None

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.isValid():
            return base | Qt.ItemIsDragEnabled
        return base | Qt.ItemIsDropEnabled

    def supportedDropActions(self):
        return Qt.MoveAction | Qt.CopyAction

    def mimeTypes(self):
        return [_MIME, "text/uri-list", "text/plain"]

    def mimeData(self, indexes):
        mime = QMimeData()
        mime.setData(_MIME, json.dumps(sorted({i.row() for i in indexes})).encode())
        return mime

    def canDropMimeData(self, data, action, row, column, parent):
        return data.hasFormat(_MIME) or data.hasUrls() or data.hasText()

    def dropMimeData(self, data, action, row, column, parent):
        if row == -1:
            row = parent.row() if parent.isValid() else len(self.playlist)
        if data.hasFormat(_MIME):
            rows = json.loads(bytes(data.data(_MIME)).decode())
            self.playlist.move(rows, row)
            return False  # we moved it ourselves; tell the view not to delete rows
        if self.external_drop:
            from .overlay import mime_to_inputs
            self.external_drop(mime_to_inputs(data), row)
        return False


class PlaylistDelegate(QStyledItemDelegate):
    ROW_HEIGHT = 44

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(6, 2, -6, -2)
        current = bool(index.data(PlaylistModel.CurrentRole))
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        if selected or hover or current:
            bg = QColor(theme.SURFACE_3 if selected else theme.SURFACE_2)
            if current and not selected:
                bg = QColor(theme.ACCENT)
                bg.setAlpha(30)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 8, 8)
        num_rect = QRect(r.left() + 6, r.top(), 26, r.height())
        if current:
            icon("play", "@ACCENT").paint(p, QRect(num_rect.left() + 4, r.center().y() - 7, 14, 14))
        else:
            p.setPen(QColor(theme.TEXT_FAINT))
            p.setFont(theme.app_font(9))
            p.drawText(num_rect, Qt.AlignVCenter | Qt.AlignLeft, str(index.row() + 1))
        duration = index.data(PlaylistModel.DurationRole) or -1
        dur_text = format_ms(duration) if duration > 0 else ""
        font = theme.app_font(10)
        if current:
            font.setWeight(font.Weight.DemiBold)
        p.setFont(font)
        fm = QFontMetrics(font)
        dur_w = fm.horizontalAdvance(dur_text) + 8 if dur_text else 0
        text_rect = QRect(num_rect.right() + 4, r.top(), r.width() - num_rect.width() - dur_w - 16, r.height())
        p.setPen(QColor(theme.ACCENT_TEXT if current else theme.TEXT))
        p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideMiddle, text_rect.width()))
        if dur_text:
            p.setFont(theme.app_font(9))
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(QRect(r.right() - dur_w - 4, r.top(), dur_w, r.height()),
                       Qt.AlignVCenter | Qt.AlignRight, dur_text)
        p.restore()


class PlaylistView(QListView):
    delete_pressed = Signal()
    activate_pressed = Signal()

    def keyPressEvent(self, e) -> None:
        if e.matches(QKeySequence.Delete) or e.key() == Qt.Key_Backspace:
            self.delete_pressed.emit()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.activate_pressed.emit()
            return
        super().keyPressEvent(e)


class FilterEdit(QLineEdit):
    def event(self, e) -> bool:
        # Claim Escape before the app-wide "leave fullscreen" shortcut sees it.
        if e.type() == e.Type.ShortcutOverride and e.key() == Qt.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Escape:
            self.clear()
            self.clearFocus()
            return
        super().keyPressEvent(e)


class PlaylistPanel(QWidget):
    play_requested = Signal(int)
    files_dropped = Signal(list, int)
    convert_requested = Signal(list)  # rows

    def __init__(self, playlist: Playlist, trigger: Callable[[str], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.playlist = playlist
        self.trigger = trigger
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 0, 0)
        title = QLabel("Playlist")
        title.setObjectName("Heading")
        title.setStyleSheet("font-size: 13pt;")
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        header.addWidget(title)
        header.addStretch(1)
        add_btn = IconButton("plus", "Add files…", 30, 18)
        add_btn.clicked.connect(self._add_menu)
        self._add_btn = add_btn
        save_btn = IconButton("save", "Save playlist… (Ctrl+Y)", 30, 18)
        save_btn.clicked.connect(lambda: trigger("save_playlist"))
        clear_btn = IconButton("trash", "Clear playlist", 30, 18)
        clear_btn.clicked.connect(lambda: trigger("clear_playlist"))
        close_btn = IconButton("close", "Hide playlist (Ctrl+L)", 30, 16)
        close_btn.clicked.connect(lambda: trigger("toggle_playlist"))
        for b in (add_btn, save_btn, clear_btn, close_btn):
            header.addWidget(b)
        lay.addLayout(header)

        self.filter = FilterEdit()
        self.filter.setPlaceholderText("Filter…")
        self.filter.setFocusPolicy(Qt.ClickFocus)  # never steal player shortcuts by default
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self.filter)
        self.count_label.setContentsMargins(6, 0, 0, 0)
        lay.addWidget(self.count_label)

        self.model = PlaylistModel(playlist)
        self.model.external_drop = lambda items, row: self.files_dropped.emit(items, row)
        self.view = PlaylistView()
        self.view.setModel(self.model)
        self.view.setItemDelegate(PlaylistDelegate(self.view))
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setDragDropMode(QAbstractItemView.DragDrop)
        self.view.setDefaultDropAction(Qt.MoveAction)
        self.view.setDragEnabled(True)
        self.view.setAcceptDrops(True)
        self.view.setDropIndicatorShown(True)
        self.view.setMouseTracking(True)
        self.view.setUniformItemSizes(True)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.doubleClicked.connect(lambda idx: self.play_requested.emit(idx.row()))
        self.view.delete_pressed.connect(self.remove_selected)
        self.view.activate_pressed.connect(self._activate_current)
        lay.addWidget(self.view, 1)

        self.empty = QLabel("Nothing queued yet.\nDrop files here or press +")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty, 1)

        playlist.changed.connect(self._refresh)
        playlist.current_changed.connect(self._scroll_to_current)
        self._refresh()

    def _refresh(self) -> None:
        n = len(self.playlist)
        total = sum(it.duration_ms for it in self.playlist if it.duration_ms > 0)
        self.count_label.setText(f"{n} item{'s' if n != 1 else ''}" + (f" · {format_ms(total)}" if total else ""))
        self.empty.setVisible(n == 0)
        self.view.setVisible(n > 0)
        self._apply_filter(self.filter.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(len(self.playlist)):
            hidden = bool(needle) and needle not in self.playlist[row].title.lower()
            self.view.setRowHidden(row, hidden)

    def _scroll_to_current(self, index: int) -> None:
        if index >= 0:
            self.view.scrollTo(self.model.index(index), QAbstractItemView.EnsureVisible)

    def selected_rows(self) -> list[int]:
        return sorted(i.row() for i in self.view.selectionModel().selectedIndexes())

    def remove_selected(self) -> None:
        rows = self.selected_rows()
        if rows:
            self.playlist.remove(rows)

    def _activate_current(self) -> None:
        idx = self.view.currentIndex()
        if idx.isValid():
            self.play_requested.emit(idx.row())

    def _add_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(icon("file"), "Add files…", lambda: self.trigger("add_files"))
        menu.addAction(icon("folder"), "Add folder…", lambda: self.trigger("add_folder"))
        menu.addAction(icon("link"), "Add URL…", lambda: self.trigger("add_url"))
        menu.addSeparator()
        menu.addAction(icon("playlist"), "Load playlist…", lambda: self.trigger("load_playlist"))
        menu.exec(self._add_btn.mapToGlobal(self._add_btn.rect().bottomLeft()))

    def _context_menu(self, pos) -> None:
        self.build_context_menu(self.view.indexAt(pos)).exec(self.view.viewport().mapToGlobal(pos))

    def build_context_menu(self, index) -> QMenu:
        menu = QMenu(self)
        if index.isValid():
            menu.addAction(icon("play"), "Play", lambda: self.play_requested.emit(index.row()))
            rows = self.selected_rows() if index.row() in self.selected_rows() else [index.row()]
            menu.addAction(icon("convert"), "Convert / save…", lambda: self.convert_requested.emit(rows))
            menu.addAction(icon("trash"), "Remove", self.remove_selected)
            menu.addSeparator()
        menu.addAction(icon("file"), "Add files…", lambda: self.trigger("add_files"))
        menu.addAction(icon("folder"), "Add folder…", lambda: self.trigger("add_folder"))
        menu.addAction(icon("link"), "Add URL…", lambda: self.trigger("add_url"))
        menu.addSeparator()
        menu.addAction(icon("save"), "Save playlist…", lambda: self.trigger("save_playlist"))
        menu.addAction(icon("playlist"), "Load playlist…", lambda: self.trigger("load_playlist"))
        menu.addAction(icon("trash"), "Clear", lambda: self.trigger("clear_playlist"))
        return menu
