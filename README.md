# VLClone

A modern, clean desktop media player built **on VLC's own engine**. Playback,
codecs, formats, streaming protocols and subtitles are handled by libVLC (via
[python-vlc](https://pypi.org/project/python-vlc/)), so compatibility matches
VLC exactly. Only the interface is new: a dark, minimal UI where the video
fills the window and the controls fade away while you watch.

![Playback with controls revealed](docs/screenshot.png)

> VLClone is an independent frontend. It does not fork, modify or reverse
> engineer VLC; it uses the public libVLC API of an installed VLC.

## Windows download (no Python needed)

Every push builds Windows packages with GitHub Actions
(`.github/workflows/windows-build.yml`). Open the repository's **Actions** tab →
*Windows build* → the latest run → **Artifacts**, and download
`VLClone-<version>-windows`. It contains:

* `VLClone-<version>-Setup.exe`: installer (Start menu entry, optional desktop
  icon, "Open with" registration, uninstaller).
* `VLClone-<version>-win64-portable.zip`: unzip anywhere and run `VLClone.exe`.

Artifacts are kept for 7 days. If the account's Actions artifact storage is
full, run the workflow manually (*Run workflow* → tick *pre-release*) to get the
files as a GitHub pre-release instead.

Both **bundle libVLC** (from the official VLC 3.0.21 win64 build, verified by
SHA-256), so VLC does not need to be installed. Pushing a tag like `v0.1.0`
also attaches both files to a GitHub release. The exe is unsigned, so Windows
SmartScreen may ask you to confirm the first launch (*More info → Run anyway*).

To build locally on Windows:

```powershell
pip install -r requirements.txt pyinstaller pillow
python packaging\fetch_vlc.py
build\vlc\vlc-cache-gen.exe build\vlc\plugins
python packaging\make_icon.py
python packaging\make_version_info.py
pyinstaller packaging\vlclone.spec --noconfirm     # -> dist\VLClone\VLClone.exe
iscc /DAppVersion=0.1.0 packaging\installer.iss     # optional installer (Inno Setup 6)
```

Licensing note: the packages redistribute libVLC and VLC plugins, which are
licensed under the LGPL/GPL by VideoLAN; VLC's license is included as
`VLC-LICENSE.txt` and VLC's source is available from <https://www.videolan.org/>.

## Install from source on Windows

1. **Install VLC (64-bit)** from <https://www.videolan.org/vlc/>.
   On the download page pick *Windows 64-bit* (the default "Download VLC"
   button may give you the 32-bit build — use the small arrow next to it).
   VLClone needs a VLC whose bitness matches Python's; 64-bit Python +
   64-bit VLC is the normal setup. Installing to the default
   `C:\Program Files\VideoLAN\VLC` lets VLClone find `libvlc.dll`
   automatically (it also checks the registry).
2. **Install Python 3.10+ (64-bit)** from <https://www.python.org/downloads/windows/>
   and tick *Add python.exe to PATH*.
3. **Get the code and install the dependencies** (PowerShell):

   ```powershell
   git clone <this repo> VLClone
   cd VLClone
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

4. **Run it**:

   ```powershell
   python run.py                 # or: python -m vlclone
   python run.py "D:\Movies\film.mkv" "D:\Music"   # open files/folders directly
   ```

If VLClone says *libVLC could not be loaded*, VLC is missing or has the wrong
bitness. You can also point it at a specific install with
`set PYTHON_VLC_LIB_PATH=C:\path\to\VLC\libvlc.dll`.

### macOS / Linux

Install VLC from your package manager (Linux: `vlc` or `libvlc5` plus
`vlc-plugin-base`/`vlc-plugin-video-output`), then `pip install -r requirements.txt`
and `python run.py`. On Linux VLClone forces the XCB (X11/XWayland) Qt
platform because libVLC embeds video into X11 windows; a compositing window
manager is needed for the translucent controls (every modern desktop has one).

## Using it

* **Open**: drop files, folders, playlists or links anywhere on the window, or
  use the buttons on the start screen, `Ctrl+O` (file), `Ctrl+F` (folder),
  `Ctrl+N` (network stream). Folders are scanned recursively and sorted
  naturally (`ep2` before `ep10`).
* **Open Media** (`⋯` → *Media*, or `Ctrl+Shift+O`) is VLC's Media menu in one
  dialog with four tabs, each with *Play* and *Enqueue*:
  * *File*: several files/folders at once, optionally with a subtitle file.
  * *Disc* (`Ctrl+D`): DVD, Blu-ray (unencrypted) or audio CD from a drive,
    a disc folder (`VIDEO_TS` / `BDMV`) or an ISO image. Optical drives are
    detected (Windows: every CD/DVD/BD drive with its volume label; the drive
    with a disc in it is preselected), and picking a folder or ISO detects
    the disc type. *Disc menus* on/off, starting title and chapter, or the CD
    track.
  * *Network* (`Ctrl+N`): URL with completion from your recent URLs (right-click
    to remove one, *Clear history*), a live hint of what VLC will do
    (*HLS stream*, *RTSP*, *YouTube (via VLC's Lua script)*…) or why the URL
    is wrong, and the network caching in ms. `www.…`, `youtu.be/…` and
    `@:1234` (UDP) are completed for you.
  * *Capture device* (`Ctrl+C`): webcam and microphone (DirectShow on
    Windows, V4L2/ALSA on Linux) with resolution, frame rate and caching.
  * Under every tab: *Start at* and *Extra options* (`:option=value`, as in
    VLC's "Show more options").
* **Streams that fail say why** instead of hanging: a toast names the host and
  the likely cause with a *Retry* button (YouTube failures point at VLC's
  `youtube.lua`, disc failures at encryption). Streams that never start give
  up after 20 s (Settings → Playback). YouTube pages and remote `.m3u` lists
  expand into their real entries in the playlist, as in VLC.
* **Chapters and titles**: files with chapters (MKV, MP4, DVD, Blu-ray) show
  them as gaps in the timeline; hovering shows the chapter name, and a
  `Ch 2/12` chip opens Quick settings → *Chapters* (chapter list, titles,
  disc-menu pad). `Shift+N` / `Shift+P` = next / previous chapter (previous
  restarts the current chapter after 3 s), `Shift+B` / `Shift+O` = next /
  previous title, `Shift+M` = disc menu. While a DVD menu is shown, the arrow
  keys and `Enter` navigate it.
* **Media library** (`Ctrl+Shift+L`, or *Library* on the start screen) takes the
  video's place; the playlist stays beside it. Add *Video* and *Music*
  folders (*Folders…*); they are scanned in the background, rescanned when
  their contents change and at every launch (only new or changed files are
  read again). Sections:
  * *Videos*: thumbnail grid with duration and resolution; sort by title,
    recently added, last played, duration, size or folder.
  * *Music*: artist → album → tracks from the files' tags.
  * *Recently played*: everything you played, including streams and discs.
  * *Continue watching*: files with a saved resume position, with progress.

  Search filters every section. Double-click plays (videos switch back to the
  player, music keeps playing while you browse); right-click → *Add to
  playlist* or *Show in folder*.
* **Controls** appear when you move the mouse and fade out after ~2 s of
  playback. Hover the timeline to see the time under the cursor; drag to scrub.
  Scroll anywhere over the video to change volume (up to 200 %, the boost zone
  is orange). Double-click toggles fullscreen.
* **Speed, A-B loop, repeat and shuffle** live in the quick-settings popover
  (the sliders button or `Ctrl+E`); click the `1×` chip to jump straight to it.
* **Playlist** is a sidebar (`Ctrl+L`): drag rows to reorder, drop files onto it
  to insert at a position, `Delete` removes, `Enter`/double-click plays,
  type in *Filter…* to search.

* **Tracks, subtitles and video**: the quick-settings popover also has
  *Audio* (track, delay), *Subtitles* (track, load `.srt`/`.ass`/…, delay) and
  *Video* (aspect ratio, crop, zoom, rotate, deinterlace, snapshot) pages. The
  same options are in the `⋯` menu / right-click menu. Dropping a subtitle file
  on a playing video loads it.
* **Effects & filters** (`Ctrl+Shift+E`, or *Effects…* in Quick settings →
  Audio/Video): a floating panel with
  * *Equalizer*: 10 bands (31 Hz–16 kHz) plus preamp, on/off, all 18 VLC
    presets (Flat, Classical, Club, Dance, Full bass, …, Rock, Pop, Techno),
    and your own presets (*Save…*, *Delete*). Double-click a slider to zero it.
  * *Audio*: volume normalization with its maximum level, stereo mode
    (stereo, mono, left, right, reverse) and the same audio delay control as
    Quick settings. Gain above 100 % stays on the main volume slider.
  * *Image*: brightness, contrast, hue, saturation, gamma, each with its own
    reset.
  * *Reset all* puts every effect back to neutral.

  Everything in the panel is **live**. Flip and sharpen sit next to rotate,
  crop, zoom and deinterlace in Quick settings → Video and are marked
  **↻ reloads**: libVLC applies them when media loads, so VLClone reloads the
  file at the same position (keeping pause state, tracks, subtitles, delays,
  A-B points). Several changes in a row are batched into one reload about
  0.6 s after the last one.
* **Effect settings persist**, either one set for every file (default) or per
  file (Settings → Audio & video → *Effect settings apply to*). Per-file mode:
  each file remembers its own effects; new files start from the global set.
  Rotation, aspect ratio, crop, zoom and deinterlace follow the same setting.
* **Resume**: files continue where you stopped (with a *Start over* button).
* **Playlist restore**: the playlist comes back on the next launch with the
  last item selected (Settings → Playback; turn off to start empty).
* **Media information** (`Ctrl+I`) shows metadata, codecs, resolution, frame
  rate, audio format and live bitrate/frame statistics.
* **Settings** (`Ctrl+P`) is one short page: playback, audio & video, interface,
  shortcut editor.

![Quick settings](docs/quick-settings.png)

![Effects & filters: equalizer](docs/effects.png)

![Media library: videos](docs/library-videos.png)

![Chapters on the timeline and in Quick settings](docs/seek-chapters.jpg)

<p>
  <img src="docs/open-network.png" alt="Open Media: Network" width="420">
  <img src="docs/open-disc.png" alt="Open Media: Disc" width="420">
</p>

![Media library: music](docs/library-music.png)

### Keyboard shortcuts (VLC defaults)

| Action | Keys |
| --- | --- |
| Play / pause | `Space` |
| Stop | `S` |
| Previous / next | `P` / `N` |
| Next frame | `E` |
| Fullscreen / leave | `F` (or double-click) / `Esc` |
| Jump 3 s / 10 s / 1 min / 5 min | `Shift`/`Alt`/`Ctrl`/`Ctrl+Alt` + `←` `→` (plain `←` `→` = 10 s) |
| Jump to time | `Ctrl+T` |
| Faster / slower / normal | `]` or `+` / `[` or `-` / `=` |
| Volume up / down / mute | `Ctrl+↑` / `Ctrl+↓` / `M` |
| A-B loop | `Shift+L` |
| Repeat mode / shuffle | `L` / `R` |
| Cycle audio track | `B` |
| Cycle subtitle / toggle subtitles | `V` / `Shift+V` |
| Subtitle delay −/+ 50 ms | `G` / `H` |
| Audio delay −/+ 50 ms | `J` / `K` |
| Aspect ratio / crop / zoom | `A` / `C` / `Z` |
| Deinterlace mode | `D` |
| Snapshot | `Shift+S` |
| Show position | `T` |
| Playlist / quick settings | `Ctrl+L` / `Ctrl+E` |
| Effects & filters | `Ctrl+Shift+E` (not a VLC default; `Ctrl+E` is Quick settings) |
| Rotate / flip horizontally / flip vertically | `Ctrl+Alt+R` / unbound / unbound (assign in Settings → Shortcuts) |
| Next / previous chapter | `Shift+N` / `Shift+P` |
| Next / previous title, disc menu | `Shift+B` / `Shift+O`, `Shift+M` |
| Disc menu navigation | `↑` `↓` `←` `→` `Enter` (only while a menu is shown) |
| Media library | `Ctrl+Shift+L` |
| Media information | `Ctrl+I` (or `Ctrl+J`) |
| Settings | `Ctrl+P` |
| Save playlist | `Ctrl+Y` |
| Open file / folder / stream | `Ctrl+O` / `Ctrl+F` / `Ctrl+N` |
| Open disc / capture device / Open Media | `Ctrl+D` / `Ctrl+C` / `Ctrl+Shift+O` |
| Quit | `Ctrl+Q` |

## Architecture

```
vlclone/
  core/            # no Qt imports — unit-tested
    backend.py       MediaBackend interface (the only contract with the engine)
    vlc_backend.py   libVLC implementation (the only module importing `vlc`)
    fake_backend.py  deterministic in-memory backend for tests / UI work
    controller.py    PlayerController: all player behaviour + observer signals
    effects.py       effects model, EQ presets, transform composition, Debouncer,
                     preset + per-file stores, persisted geometry
    network.py       URL checks, protocol detection, recent-URL history, error text
    discs.py         DVD / Blu-ray / CD MRLs, VIDEO_TS/BDMV/ISO detection, drives
    capture.py       dshow / v4l2 / alsa MRLs, Linux device discovery
    library.py       media library store (SQLite): folders, metadata, history
    library_scanner.py  background scan, folder watching, thumbnails (LibraryService)
    png.py           tiny PNG writer for thumbnails
    session.py       playlist persistence between sessions
    playlist.py      ordering, current item, repeat, shuffle
    abloop.py, stepping.py, timefmt.py, media_sources.py
    m3u.py, resume.py, settings.py, shortcuts.py, paths.py
  ui/              # PySide6 widgets, talk only to PlayerController
    main_window.py   window, sidebar, menus, wiring
    overlay.py       translucent always-on-top controls window over the video
    quick_settings.py popover with pluggable pages (… Video, Chapters)
    effects_panel.py  Effects & filters panel (equalizer, audio, image)
    open_media.py    Open Media dialog (File, Disc, Network, Capture device)
    library_view.py  media library view, folder manager
    playlist_panel.py, widgets.py, icons.py (vector, high-DPI), theme.py …
```

* **libVLC renders into a native child window**, which Qt can't paint over, so
  the controls live in a frameless translucent tool window that tracks the
  video rectangle. Its background is 1/255 opaque, which makes it invisible but
  still able to receive clicks, wheel, drag-and-drop and cursor motion.
* **No libVLC callbacks reach the UI.** The window calls
  `PlayerController.tick()` every 100 ms on the Qt thread; the controller polls
  the backend and emits plain-Python signals. This sidesteps libVLC's event
  threads entirely.
* Per-media state (volume, speed, video adjustments, pending resume seek) is
  re-applied when playback actually starts, because libVLC drops some of it
  while a media is still opening.
* **Live vs. reload effects.** Equalizer, stereo mode, normalization and image
  adjust use libVLC calls that act on the playing media. Video filters
  (rotate/flip via `transform`, `sharpen`) are only honoured by libVLC per
  *instance*, so `VlcBackend` keeps a small LRU of libVLC instances keyed by
  `EngineFilters` and swaps the player when the media is reloaded. The
  controller batches requests with a `Debouncer` polled from `tick()` and
  reuses the reload-with-position path (`_start_current(reload=True)`).
  Rotation and flips are combined into one of the eight `transform` types.
* **Volume normalization** has no public libVLC switch (libVLC routes the
  audio filter chain through its equalizer call). `VlcBackend` sets the
  chain (`equalizer:normvol`) on the player's audio output through
  libvlccore's exported variable API, as VLC's own UI does. That relies on the
  3.0.x plugin ABI, so it only enables itself on libVLC 3.0 and the toggle is
  disabled (with a tooltip) if the hook isn't available.
* **Media sources are MRLs plus media options.** Every source ends up in
  `PlayerController.open(inputs, options=…, title=…)`: discs are
  `dvd:///D:/#title:chapter`, `dvdsimple://`, `bluray://` (+ `:no-bluray-menu`),
  `cdda://` (+ `:cdda-track=N`); capture is `dshow://` + `:dshow-vdev=…`;
  caching is `:network-caching=…`. Options live on the playlist item, so they
  survive reloads, the session file and `.m3u` (`#EXTVLCOPT`, like VLC).
* **Chapters/titles** are polled from the backend (once a second while
  playing, and after title changes) and arrive as `chapters_changed`.
  YouTube pages and remote playlists "end" in libVLC with sub-items; the
  controller replaces the playlist entry with them (reusing the sub-item media,
  so options set by VLC's Lua scripts are kept).
* **DirectShow devices** are listed the way VLC's preferences do it: libvlccore's
  `config_GetPszChoices("dshow-vdev")` runs the dshow module's own enumerator.
* **Media library**: `LibraryService` runs one worker thread at a time (scan,
  folder check, thumbnails) with its own libVLC instance (`VlcMediaProbe`).
  Like the player, nothing calls back into Qt: the view polls `status` and a
  `generation` counter twice a second. SQLite (WAL, one connection per thread)
  lives in the data folder (`%LOCALAPPDATA%\VLClone\library.db` on Windows,
  `~/.local/share/vlclone` on Linux) with thumbnails next to it. Metadata comes
  from libVLC's local preparser; thumbnails decode one frame a third of the
  way in through libVLC's `vmem` callbacks (no window needed) and are written
  with a small PNG encoder. Folder changes are noticed by comparing directory
  modification times every 30 s.

### Adding the later VLC features

The structure is meant to absorb VLC's bigger features without rewrites:

1. Extend `MediaBackend` with the engine calls (e.g. `set_equalizer(bands)`,
   `set_video_filter(...)`, `stream_out(sout_chain)`) and implement them in
   `VlcBackend` and `FakeBackend`.
2. Add behaviour + state to `PlayerController` (or a sibling controller in
   `core/`, e.g. `core/equalizer.py`, `core/library.py`), with tests.
3. Expose it in the UI: register actions in `MainWindow.register_feature_actions`,
   add a page with `QuickSettings.add_page(...)`, add menu entries in
   `MainWindow.extend_menu`, and settings fields in `core/settings.py`.

| Future feature | Where it plugs in |
| --- | --- |
| Equalizer / audio effects | ✅ phase 2 (`effects.py`, `effects_panel.py`) |
| Video effects & filters | ✅ phase 2 (adjust live; transform/sharpen via `EngineFilters`) |
| More VLC video filters (e.g. gradfun, posterize) | add a field to `VideoEffects` + `EngineFilters.vlc_args()`; they reuse the batched reload |
| Convert / stream out | a `core/transcode.py` driving a second libVLC instance with `:sout=` options, a dialog |
| DVD / Blu-ray / discs, chapters | ✅ phase 3 (`discs.py`, chapter/title/navigate in the backend) |
| Network streams, capture devices | ✅ phase 3 (`network.py`, `capture.py`, Open Media dialog) |
| Media library | ✅ phase 3 (`library.py`, `library_scanner.py`, `library_view.py`) |
| Playlists/podcasts/UPnP discovery | a backend `media_discoverer` wrapper + a library section |

## Status

All eight core-player priorities (phase 1), the effects work (phase 2) and
media sources + library (phase 3) are implemented:

| # | Feature | State |
| --- | --- | --- |
| 1 | Open files, folders (recursive), URLs/network streams, `.m3u`, drag-and-drop | ✅ |
| 2 | Play/pause, stop, seek bar with hover time, frame step, volume to 200 %, mute, fullscreen | ✅ |
| 3 | Speed (presets + fine slider), A-B loop, repeat off/all/one, shuffle | ✅ |
| 4 | Embedded + external subtitles, subtitle delay, audio track, audio delay | ✅ |
| 5 | Aspect ratio, crop, zoom, rotate, deinterlace, snapshot | ✅ |
| 6 | Playlist reorder, save/load `.m3u`, per-file resume | ✅ |
| 7 | Media info (codec, resolution, bitrate, statistics) | ✅ |
| 8 | VLC default shortcuts, editable | ✅ |
| P2 | 10-band equalizer, 18 VLC presets, preamp, on/off, custom presets | ✅ live |
| P2 | Volume normalization (max level), stereo modes | ✅ live (normalization: libVLC 3.0 only) |
| P2 | Image adjust: brightness, contrast, hue, saturation, gamma | ✅ live |
| P2 | Flip horizontal/vertical, sharpen with strength | ✅ batched reload |
| P2 | Effects persisted globally or per file; playlist restored on launch | ✅ |
| P3 | Open Media dialog: File, Disc, Network, Capture device | ✅ |
| P3 | Network: HTTP(S), HLS, RTSP, RTMP, UDP/RTP, YouTube (Lua), history, caching, clear errors + timeout | ✅ (HTTP/HLS/errors tested with real libVLC) |
| P3 | DVD (menus, titles, chapters), Blu-ray (unencrypted), audio CD; drive detection; folder/ISO | ✅ (needs a Windows test with real discs) |
| P3 | Chapter list, next/previous chapter, chapter marks on the seek bar | ✅ (MKV/MP4 tested with real libVLC) |
| P3 | Webcam / microphone via DirectShow, resolution and frame rate | ✅ (needs a Windows test with a camera) |
| P3 | Media library: watched folders, background scan, SQLite, metadata, thumbnails, four views, search/sort | ✅ |
| P3 | Rotation, aspect, crop, zoom, deinterlace persisted with the effects scope | ✅ |

Known limitations / not yet done:

* Rotate, flip and sharpen reload the media (libVLC only applies video
  filters per instance); position, pause state, tracks, external subtitles,
  delays and live effects are restored, but there is a brief flash. Live
  streams restart from the live edge.
* Turning image adjust on or off (moving away from / back to all-neutral
  values) rebuilds libVLC's video filter chain once, which can show a single
  dropped frame; moving sliders after that is smooth.
* Seek preview shows the time under the cursor, not a thumbnail.
* No single-instance mode / "open with" handoff to an existing window yet.
* Development and screenshots were done on Linux (X11 + compositor, real
  libVLC 3.0.20). The Windows path (HWND embedding, `libvlc.dll` discovery)
  follows libVLC's documented API but should be smoke-tested on a Windows box.
* DVD menus are driven with the keyboard (arrows, `Enter`) or the pad in
  Quick settings → Chapters; clicking menu buttons with the mouse is not
  supported, because the controls overlay sits above libVLC's video window.
* Blu-ray: only unencrypted discs (VLC ships no AACS/BD+ keys); interactive
  BD-J menus need Java, which the bundled VLC lacks, so turn *Disc menus*
  off if a disc shows nothing.
* YouTube relies on the `youtube.lua` that ships with VLC 3.0.21. YouTube
  changes often and that script is frequently out of date; when a link fails
  the error says so. Replacing `vlc\lua\playlist\youtube.luac` with a newer
  `youtube.lua` from VideoLAN fixes it without rebuilding.
* Capture resolutions are requested, not negotiated: DirectShow does not
  report the list of formats through libVLC, so the dialog offers common
  sizes and the camera falls back to its default if one is unsupported.
* The library only reads local metadata (tags, stream info); it does not
  download cover art or look things up online. Music without tags is grouped
  under "Unknown artist", with the folder as album only when libVLC is not
  available.
* Folder watching compares directory times every 30 s rather than using OS
  change notifications; renaming a file inside an existing folder is picked
  up on that check, edits to a file's tags on the next launch or *Rescan*.
* Later phases from the roadmap: convert/stream (sout), network discovery
  (UPnP/SMB), more of VLC's effect filters.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest            # core + offscreen UI tests
python run.py --fake-backend   # UI without libVLC
```

`tests/test_vlc_backend.py` and `tests/test_vlc_phase3.py` run against a real
libVLC when one is installed and are skipped otherwise. They serve files and an
HLS stream from a local HTTP server, check 404 and refused connections, read
chapters, tags and sizes, make thumbnails and scan a library with the real
probe; clips come from `ffmpeg` when present, and `vlclone/selftest_media.py`
writes a chaptered Matroska file and a Y4M video in pure Python otherwise.

`VLClone.exe --self-test report.txt` (run by the Windows build) plays a tone
through a filtered instance, applies the effects, reads chapters from a
generated MKV and jumps between them, plays a local HTTP stream, checks that a
404 fails, makes a library thumbnail and lists capture devices and optical
drives.
