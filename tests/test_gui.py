"""
Lightweight sanity tests for the GUI.

These don't try to test visual rendering - they confirm the module imports
cleanly and that the window can be constructed and torn down without
error, which is what actually tends to break during refactors.

Skipped automatically in headless environments that don't have a display
or don't have the optional GUI dependency installed.
"""

import os
import pytest

pytest.importorskip("customtkinter")

if not os.environ.get("DISPLAY") and os.name != "nt":
    pytest.skip("No display available for GUI tests", allow_module_level=True)


def test_gui_module_imports():
    from lanshare import gui  # noqa: F401


def test_app_constructs_and_closes():
    from lanshare.gui import LanshareApp

    app = LanshareApp()
    try:
        app.update()
        assert app.receive_tab is not None
        assert app.send_tab is not None
    finally:
        app.destroy()


def test_tab_switching_does_not_raise():
    from lanshare.gui import LanshareApp

    app = LanshareApp()
    try:
        app.update()
        app.nav.set("Send")
        app._switch_tab("Send")
        app.update()
        app.nav.set("Receive")
        app._switch_tab("Receive")
        app.update()
    finally:
        app.destroy()
