"""Convert / save dialog and the conversion queue panel.

The dialog turns sources + a preset + a few options into queue jobs
(:class:`ConvertQueue`); the panel shows them with progress, ETA and
actions. Presets this libVLC cannot produce are disabled with the reason
as tooltip (from :class:`PresetProber`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialogButtonBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..core.converter import ConvertJob, ConvertQueue, JobState, PresetProber
from ..core.media_sources import display_name, mrl_to_path
from ..core.paths import default_videos_dir
from ..core.settings import Settings
from ..core.timefmt import format_ms, parse_time
from ..core.transcode import (AUDIO_BITRATES, CONVERT_PRESETS, PRESETS, RESOLUTIONS, VIDEO_QUALITY,
                              ConvertSettings, output_path, validate)
from . import theme
from .dialogs import BaseDialog
from .icons import icon
from .quick_settings import chip, section


@dataclass
class ConvertSource:
    mrl: str
    title: str = ""
    options: list[str] = field(default_factory=list)
    duration_ms: int = -1

    def __post_init__(self) -> None:
        self.title = self.title or display_name(self.mrl)


class SourceList(QListWidget):
    """Sources; accepts files and links dropped on it."""

    dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        from .overlay import mime_to_inputs

        self.dropped.emit(mime_to_inputs(e.mimeData()))
        e.acceptProposedAction()


class ConvertDialog(BaseDialog):
    def __init__(self, queue: ConvertQueue, prober: PresetProber, settings: Settings,
                 sources: list[ConvertSource] | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Convert / save", parent)
        self.queue = queue
        self.prober = prober
        self.settings = settings
        self.jobs: list[ConvertJob] = []
        self.setMinimumWidth(620)
        self.setAcceptDrops(True)

        head = QHBoxLayout()
        head.addWidget(section("Files"))
        head.addStretch(1)
        add = chip("Add files…", checkable=False)
        add.clicked.connect(self._add_files)
        remove = chip("Remove", checkable=False)
        remove.clicked.connect(lambda: [self.sources.takeItem(self.sources.row(i))
                                        for i in self.sources.selectedItems()])
        head.addWidget(add)
        head.addWidget(remove)
        self.body.addLayout(head)
        self.sources = SourceList()
        self.sources.setMinimumHeight(110)
        self.sources.dropped.connect(self.add_inputs)
        self.body.addWidget(self.sources)
        self.drop_hint = QLabel("Drop files here, or use Add files…")
        self.drop_hint.setObjectName("Muted")
        self.body.addWidget(self.drop_hint)

        self.body.addWidget(section("Format"))
        self.preset = QComboBox()
        self.preset_model = QStandardItemModel(self.preset)
        self.preset.setModel(self.preset_model)
        for key in CONVERT_PRESETS:
            item = QStandardItem(PRESETS[key].label)
            item.setData(key, Qt.UserRole)
            self.preset_model.appendRow(item)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self.body.addWidget(self.preset)
        self.preset_note = QLabel()
        self.preset_note.setObjectName("Muted")
        self.preset_note.setWordWrap(True)
        self.body.addWidget(self.preset_note)

        self.body.addWidget(section("Save to"))
        self.next_to_source = QCheckBox("Next to each source file")
        self.next_to_source.setChecked(settings.convert_next_to_source)
        out_row = QHBoxLayout()
        self.folder = QLineEdit(settings.convert_output_dir or default_videos_dir())
        browse = chip("Browse…", checkable=False)
        browse.clicked.connect(self._pick_folder)
        out_row.addWidget(self.folder, 1)
        out_row.addWidget(browse)
        self.next_to_source.toggled.connect(lambda on: (self.folder.setEnabled(not on), browse.setEnabled(not on)))
        self.next_to_source.toggled.emit(self.next_to_source.isChecked())
        self.body.addWidget(self.next_to_source)
        self.body.addLayout(out_row)

        self.more = chip("More options ▾", checkable=True)
        self.more.toggled.connect(self._toggle_more)
        self.body.addWidget(self.more, 0, Qt.AlignLeft)
        self.options = QWidget()
        grid = QGridLayout(self.options)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        self.resolution = QComboBox()
        for height, label in RESOLUTIONS:
            self.resolution.addItem(label, height)
        self.vquality = QComboBox()
        for kbps, label in VIDEO_QUALITY:
            self.vquality.addItem(label, kbps)
        self.abitrate = QComboBox()
        for kbps in AUDIO_BITRATES:
            self.abitrate.addItem("Preset default" if not kbps else f"{kbps} kb/s", kbps)
        self.trim_start = QLineEdit()
        self.trim_start.setPlaceholderText("start")
        self.trim_end = QLineEdit()
        self.trim_end.setPlaceholderText("end")
        trim = QHBoxLayout()
        trim.addWidget(self.trim_start)
        trim.addWidget(QLabel("to"))
        trim.addWidget(self.trim_end)
        self.subtitles = QComboBox()
        self.subtitles.addItem("Don't burn in subtitles", "")
        self.subtitles.addItem("Burn in the media's subtitles", "embedded")
        self.subtitles.addItem("Burn in a subtitle file…", "file")
        self.subtitles.activated.connect(self._subtitle_choice)
        self._subtitle_file = ""
        rows = [("Resolution", self.resolution), ("Video quality", self.vquality), ("Audio bitrate", self.abitrate),
                ("Trim (h:mm:ss)", trim), ("Subtitles", self.subtitles)]
        for r, (label, widget) in enumerate(rows):
            grid.addWidget(QLabel(label), r, 0)
            if isinstance(widget, QHBoxLayout):
                grid.addLayout(widget, r, 1)
            else:
                grid.addWidget(widget, r, 1)
        grid.setColumnStretch(1, 1)
        self.options.hide()
        self.body.addWidget(self.options)

        self.error = QLabel()
        self.error.setObjectName("Error")
        self.error.setWordWrap(True)
        self.body.addWidget(self.error)
        box = QDialogButtonBox()
        cancel = box.addButton(QDialogButtonBox.Cancel)
        self.start = box.addButton("Convert", QDialogButtonBox.AcceptRole)
        self.start.setObjectName("Primary")
        self.start.setDefault(True)
        cancel.clicked.connect(self.reject)
        self.start.clicked.connect(self.accept)
        self.body.addWidget(box)

        index = self.preset.findData(settings.convert_preset, Qt.UserRole)
        self.preset.setCurrentIndex(max(0, index))
        for s in sources or []:
            self.add_source(s)
        self._update_list_hint()
        self._caps_timer = QTimer(self, interval=250)
        self._caps_timer.timeout.connect(self._apply_caps)
        self.prober.start()
        self._apply_caps()
        if not self.prober.ready:
            self._caps_timer.start()

    # ------------------------------------------------------------- sources
    def add_source(self, source: ConvertSource) -> None:
        existing = {self.sources.item(i).data(Qt.UserRole).mrl for i in range(self.sources.count())}
        if source.mrl in existing:
            return
        item = QListWidgetItem(icon("file" if mrl_to_path(source.mrl) else "link"), source.title)
        item.setData(Qt.UserRole, source)
        item.setToolTip(mrl_to_path(source.mrl) or source.mrl)
        self.sources.addItem(item)
        self._update_list_hint()

    def add_inputs(self, inputs: list[str]) -> None:
        from ..core.media_sources import expand_inputs

        for mrl in expand_inputs(inputs):
            self.add_source(ConvertSource(mrl))

    def _update_list_hint(self) -> None:
        self.drop_hint.setVisible(self.sources.count() == 0)

    def source_list(self) -> list[ConvertSource]:
        return [self.sources.item(i).data(Qt.UserRole) for i in range(self.sources.count())]

    def _add_files(self) -> None:
        from .main_window import MEDIA_FILTER

        files, _ = QFileDialog.getOpenFileNames(self, "Add files", self.settings.last_open_dir or "", MEDIA_FILTER)
        self.add_inputs(files)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        from .overlay import mime_to_inputs

        self.add_inputs(mime_to_inputs(e.mimeData()))

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Save converted files to", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _subtitle_choice(self, index: int) -> None:
        if self.subtitles.itemData(index) != "file":
            return
        from .main_window import SUBTITLE_FILTER

        path, _ = QFileDialog.getOpenFileName(self, "Subtitle file", "", SUBTITLE_FILTER)
        if path:
            self._subtitle_file = path
            self.subtitles.setItemText(index, f"Burn in {Path(path).name}")
        else:
            self.subtitles.setCurrentIndex(0)

    def _toggle_more(self, on: bool) -> None:
        self.options.setVisible(on)
        self.more.setText("More options ▴" if on else "More options ▾")
        self.adjustSize()

    # ------------------------------------------------------------- presets
    @property
    def preset_key(self) -> str:
        return self.preset.currentData(Qt.UserRole)

    def _apply_caps(self) -> None:
        ready = self.prober.ready
        for row in range(self.preset_model.rowCount()):
            item = self.preset_model.item(row)
            support = self.prober.support(item.data(Qt.UserRole))
            enabled = support is None or support.ok
            item.setEnabled(enabled)
            item.setToolTip("" if enabled else f"Not available in this VLC: {support.reason}")
        if ready:
            self._caps_timer.stop()
            current = self.preset_model.item(self.preset.currentIndex())
            if current is not None and not current.isEnabled():
                first = next((r for r in range(self.preset_model.rowCount())
                              if self.preset_model.item(r).isEnabled()), 0)
                self.preset.setCurrentIndex(first)
        self._preset_changed()

    def _preset_changed(self, *_) -> None:
        key = self.preset_key
        if key is None:
            return
        preset = PRESETS[key]
        notes = [preset.note] if preset.note else []
        if not self.prober.ready:
            notes.append("Checking which formats this VLC can write…")
        elif self.prober.error:
            notes.append(self.prober.error)
        self.preset_note.setText(" ".join(notes))
        video = preset.group == "video"
        for w in (self.resolution, self.vquality, self.subtitles):
            w.setEnabled(video)
        self.abitrate.setEnabled(not preset.remux and preset.key != "flac")

    # ---------------------------------------------------------- building
    def build_settings(self) -> ConvertSettings:
        def ms(edit: QLineEdit, name: str) -> int:
            text = edit.text().strip()
            if not text:
                return 0
            value = parse_time(text)
            if value is None:
                raise ValueError(f"Trim {name}: use seconds, m:ss or h:mm:ss")
            return value

        sub = self.subtitles.currentData()
        return ConvertSettings(
            preset=self.preset_key,
            max_height=self.resolution.currentData() or 0,
            video_kbps=self.vquality.currentData() or 0,
            audio_kbps=self.abitrate.currentData() or 0,
            start_ms=ms(self.trim_start, "start"),
            end_ms=ms(self.trim_end, "end"),
            subtitles=self._subtitle_file if sub == "file" else (sub or ""),
        )

    def build_jobs(self) -> list[tuple[ConvertSource, ConvertSettings, str]]:
        sources = self.source_list()
        if not sources:
            raise ValueError("Add at least one file to convert")
        settings = self.build_settings()
        preset = PRESETS[settings.preset]
        support = self.prober.support(preset.key)
        if support is not None and not support.ok:
            raise ValueError(f"{preset.label} is not available in this VLC: {support.reason}")
        problem = validate(preset, settings)
        if problem:
            raise ValueError(problem)
        folder = "" if self.next_to_source.isChecked() else self.folder.text().strip()
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as exc:
                raise ValueError(f"Cannot use that folder: {exc}") from exc
        taken = self.queue.taken_outputs()
        jobs = []
        for src in sources:
            local = mrl_to_path(src.mrl)
            if preset.key == "remux_mp4" and local:
                problem = validate(preset, settings, self.queue.engine.media_info(local))
                if problem:
                    raise ValueError(f"{src.title}: {problem}")
            source_dir = str(Path(local).parent) if local else None
            # "Next to the source" has no folder for streams: they go to the Videos folder.
            target = folder or ("" if source_dir else default_videos_dir())
            out = output_path(Path(local).name if local else src.title, source_dir, preset, target, taken)
            taken.add(out)
            jobs.append((src, settings, out))
        return jobs

    def accept(self) -> None:
        try:
            planned = self.build_jobs()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.settings.convert_preset = self.preset_key
        self.settings.convert_next_to_source = self.next_to_source.isChecked()
        if not self.next_to_source.isChecked():
            self.settings.convert_output_dir = self.folder.text().strip()
        for src, settings, out in planned:
            self.jobs.append(self.queue.add(src.mrl, src.title, settings, out, src.options,
                                            duration_ms=src.duration_ms))
        super().accept()


# ------------------------------------------------------------------- queue
def job_status(job: ConvertJob) -> str:
    if job.state is JobState.QUEUED:
        return "Waiting"
    if job.state is JobState.RUNNING:
        if job.live:
            return f"● Recording  {format_ms(int(job.elapsed_s * 1000))}"
        if not job.length_known:
            mb = job.part_size / 1_000_000
            return f"Converting…  {format_ms(max(0, job.media_time_ms))}" + (f"  ·  {mb:.1f} MB" if mb >= 0.1 else "")
        text = f"{int(job.progress * 100)} %"
        if job.eta_s is not None:
            text += f"  ·  {format_ms(int(job.eta_s * 1000))} left"
        return text
    if job.state is JobState.DONE:
        return f"Done  ·  saved in {Path(job.output).parent}"
    if job.state is JobState.CANCELLED:
        return "Cancelled"
    return f"Failed: {job.error}"


class JobRow(QFrame):
    def __init__(self, job: ConvertJob, queue: ConvertQueue) -> None:
        super().__init__()
        self.job = job
        self.queue = queue
        self.setObjectName("JobRow")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.title = QLabel()
        font = self.title.font()
        font.setWeight(font.Weight.DemiBold)
        self.title.setFont(font)
        top.addWidget(self.title, 1)
        self.buttons: dict[str, QPushButton] = {}
        for key, label in (("stop", "Stop"), ("cancel", "Cancel"), ("open", "Show"), ("retry", "Retry"),
                           ("remove", "Remove")):
            b = chip(label, checkable=False)
            b.clicked.connect(lambda _=False, k=key: self._action(k))
            top.addWidget(b)
            self.buttons[key] = b
        lay.addLayout(top)
        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        lay.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        lay.addWidget(self.bar)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        for label in (self.title, self.detail, self.status):  # long names wrap instead of widening the panel
            label.setMinimumWidth(1)
        self.detail.setWordWrap(True)
        self.refresh()

    def _action(self, key: str) -> None:
        q, job = self.queue, self.job
        if key == "stop":
            q.finish(job.id)
        elif key == "cancel":
            q.cancel(job.id)
        elif key == "retry":
            q.retry(job.id)
        elif key == "remove":
            q.remove(job.id)
        elif key == "open":
            from .library_view import show_in_folder

            show_in_folder(job.output)

    def refresh(self) -> None:
        job = self.job
        self.title.setText(job.title)
        self.detail.setText(f"{job.preset.label}  →  {Path(job.output).name}")
        self.detail.setToolTip(job.output)
        self.bar.setVisible(not job.finished)
        self.bar.setRange(0, 0 if (job.live or not job.length_known) and job.state is JobState.RUNNING else 1000)
        self.bar.setValue(int(job.progress * 1000))
        self.status.setText(job_status(job))
        theme.set_error(self.status, job.state is JobState.FAILED or (job.live and job.state is JobState.RUNNING))
        running = job.state is JobState.RUNNING
        self.buttons["stop"].setVisible(job.live and running)
        self.buttons["cancel"].setVisible(not job.finished)
        self.buttons["cancel"].setText("Discard" if job.live else "Cancel")
        self.buttons["open"].setVisible(job.state is JobState.DONE)
        self.buttons["retry"].setVisible(job.state in (JobState.FAILED, JobState.CANCELLED) and not job.live)
        self.buttons["remove"].setVisible(job.finished)


class ConvertQueuePanel(QWidget):
    """Floating list of conversions and recordings."""

    def __init__(self, queue: ConvertQueue, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Tool)
        self.setWindowTitle("Conversions")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setObjectName("QueuePanel")
        self.queue = queue
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        head = QHBoxLayout()
        title = QLabel("Conversions")
        title.setObjectName("Heading")
        head.addWidget(title)
        head.addStretch(1)
        clear = chip("Clear finished", checkable=False)
        clear.clicked.connect(queue.clear_finished)
        head.addWidget(clear)
        lay.addLayout(head)
        self.empty = QLabel("Nothing converting. Use Media → Convert / save (Ctrl+R).")
        self.empty.setObjectName("Muted")
        lay.addWidget(self.empty)
        self.list = QWidget()
        self.rows_layout = QVBoxLayout(self.list)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.list)
        lay.addWidget(scroll, 1)
        self.rows: dict[int, JobRow] = {}
        self._generation = -1
        queue.changed.connect(lambda *_: self.sync() if self.isVisible() else None)
        self.sync()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.sync()

    def sync(self) -> None:
        if self.queue.generation == self._generation:
            return
        self._generation = self.queue.generation
        ids = [j.id for j in self.queue.jobs]
        for job_id in list(self.rows):
            if job_id not in ids:
                self.rows.pop(job_id).deleteLater()
        for i, job in enumerate(self.queue.jobs):
            row = self.rows.get(job.id)
            if row is None:
                row = JobRow(job, self.queue)
                self.rows[job.id] = row
                self.rows_layout.insertWidget(i, row)
            row.refresh()
        self.empty.setVisible(not self.queue.jobs)
