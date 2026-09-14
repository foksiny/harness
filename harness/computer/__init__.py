"""
Computer-use backend for Harness.

Two layers live here:

* `controller.py` — `ComputerController`: the hermetic, injectable seam the
  tools and agent use. It probes the runtime (X11 / Wayland / headless),
  captures screens (mss/PIL as the primary path, CLI fallbacks like grim /
  scrot / import), executes batched input actions (ctypes XTEST primary,
  CLI drivers xdotool/ydotool/wmctrl as fallbacks), lists/focuses windows,
  and reads/writes the clipboard. Every method is structured-output, never
  raises, and degrades to a clear `error` + install hints when a backend is
  missing — exactly the contract the agent already expects from tools.

* `xtest.py` — the zero-dependency ctypes binding to libX11/libXtst (XTEST).
  `controller.py` shells out to it when available and reports hints otherwise.

Everything here is plain-functional and import-safe: importing the module must
never crash on a headless box, only *using* a missing backend fails — and it
fails with guidance, not a traceback.
"""
