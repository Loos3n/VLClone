"""Player logic that is independent of any GUI toolkit.

Nothing in this package may import PySide6. The UI layer talks to the
``PlayerController``; the controller talks to a ``MediaBackend``
(libVLC in production, a fake in tests).
"""
