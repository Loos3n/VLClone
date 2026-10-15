"""Operating-system integration: Windows taskbar, media overlay, keep-awake, file types.

Each module turns the decisions made in ``core/desktop.py`` into OS calls and
degrades to a no-op where the OS (or an optional dependency) is missing, so
nothing here can stop the player from starting. The Windows pieces use
``ctypes`` (taskbar, power, registry) and pywinrt (the media overlay).
"""
