"""
Desktop GUI for lanshare.

A small control-panel style interface over the same send/receive engine
used by the CLI - nothing about the underlying protocol changes here,
this just gives people a window instead of a terminal.

Run with:
    lanshare gui
or:
    python -m lanshare.gui
"""

from __future__ import annotations

import os
import queue
import socket
import threading
import time
import tkinter as tk
from tkinter import filedialog
from typing import Optional

try:
    import customtkinter as ctk
except ImportError as exc:  # pragma: no cover - import guard
    raise SystemExit(
        "The GUI needs an extra dependency. Install it with:\n"
        "    pip install lanshare[gui]\n"
        "or:\n"
        "    pip install customtkinter"
    ) from exc

from .crypto import generate_pin
from .discovery import Peer, discover_peers
from .transfer import DEFAULT_PORT, ReceiverServer, TransferError, send_file
from .utils import format_size, get_local_ip

# ---------------------------------------------------------------------------
# design tokens
# ---------------------------------------------------------------------------

BG = "#14171c"           # window background - graphite, not pure black
SURFACE = "#1c2027"      # panel / card surface
SURFACE_RAISED = "#242a33"
BORDER = "#2c333d"
TEXT_PRIMARY = "#e7eaee"
TEXT_SECONDARY = "#8b95a3"
ACCENT = "#3fd6c6"        # signal teal - connectivity / transfer
ACCENT_HOVER = "#33bfb0"
ACCENT_DIM = "#28504c"
WARN = "#e8a955"
ERROR = "#e5646b"
SUCCESS = "#4fd18a"

FONT_UI = "Segoe UI" if os.name == "nt" else "Helvetica"
FONT_MONO = "Consolas" if os.name == "nt" else "Menlo"


def _mono(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_MONO, size=size, weight=weight)


def _ui(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_UI, size=size, weight=weight)


# ---------------------------------------------------------------------------
# small reusable widgets
# ---------------------------------------------------------------------------

class StatusDot(ctk.CTkCanvas):
    """A small pulsing dot used to indicate an active/listening state."""

    def __init__(self, master, color=ACCENT, **kwargs):
        super().__init__(master, width=10, height=10, bg=SURFACE, highlightthickness=0, **kwargs)
        self._color = color
        self._dot = self.create_oval(1, 1, 9, 9, fill=BORDER, outline="")
        self._on = True
        self._pulsing = False

    def start(self):
        self._pulsing = True
        self._pulse()

    def stop(self):
        self._pulsing = False
        self.itemconfig(self._dot, fill=BORDER)

    def _pulse(self):
        if not self._pulsing:
            return
        self._on = not self._on
        self.itemconfig(self._dot, fill=self._color if self._on else SURFACE_RAISED)
        self.after(650, self._pulse)


class LogConsole(ctk.CTkTextbox):
    """Read-only scrolling log, styled like a small terminal readout."""

    def __init__(self, master, **kwargs):
        super().__init__(
            master,
            font=_mono(12),
            fg_color=SURFACE,
            text_color=TEXT_SECONDARY,
            wrap="word",
            **kwargs,
        )
        self.configure(state="disabled")

    def write(self, message: str, tag: str = "info"):
        colors = {"info": TEXT_SECONDARY, "ok": SUCCESS, "warn": WARN, "error": ERROR}
        self.configure(state="normal")
        timestamp = time.strftime("%H:%M:%S")
        self.insert("end", f"[{timestamp}] ", ("dim",))
        self.insert("end", f"{message}\n", (tag,))
        self.tag_config("dim", foreground=TEXT_SECONDARY)
        self.tag_config(tag, foreground=colors.get(tag, TEXT_SECONDARY))
        self.configure(state="disabled")
        self.see("end")


# ---------------------------------------------------------------------------
# Receive tab
# ---------------------------------------------------------------------------

class ReceiveTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.server: Optional[ReceiverServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self._events: "queue.Queue" = queue.Queue()

        self.grid_columnconfigure(0, weight=1)

        # -- top card: identity + control ---------------------------------
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12, border_width=1, border_color=BORDER)
        card.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 16))
        card.grid_columnconfigure(1, weight=1)

        self.dot = StatusDot(card)
        self.dot.grid(row=0, column=0, padx=(20, 8), pady=20, sticky="w")

        self.status_label = ctk.CTkLabel(
            card, text="Not listening", font=_ui(15, "bold"), text_color=TEXT_PRIMARY, anchor="w"
        )
        self.status_label.grid(row=0, column=1, sticky="w", pady=20)

        self.toggle_btn = ctk.CTkButton(
            card, text="Start Receiving", font=_ui(13, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b1210",
            corner_radius=8, width=150, height=36, command=self.toggle,
        )
        self.toggle_btn.grid(row=0, column=2, padx=20, pady=20)

        # -- PIN readout (signature element) -------------------------------
        pin_card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12, border_width=1, border_color=BORDER)
        pin_card.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        pin_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            pin_card, text="PAIRING PIN", font=_ui(11, "bold"), text_color=TEXT_SECONDARY
        ).grid(row=0, column=0, pady=(18, 0))

        self.pin_label = ctk.CTkLabel(
            pin_card, text="------", font=_mono(40, "bold"), text_color=ACCENT
        )
        self.pin_label.grid(row=1, column=0, pady=(4, 4))

        self.address_label = ctk.CTkLabel(
            pin_card, text="Not started", font=_mono(12), text_color=TEXT_SECONDARY
        )
        self.address_label.grid(row=2, column=0, pady=(0, 18))

        # -- settings row ---------------------------------------------------
        settings = ctk.CTkFrame(self, fg_color="transparent")
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        settings.grid_columnconfigure((0, 1, 2), weight=1)

        self.name_entry = self._labeled_entry(settings, "DEVICE NAME", socket.gethostname(), 0)
        self.port_entry = self._labeled_entry(settings, "PORT", str(DEFAULT_PORT), 1)
        self.dir_entry = self._labeled_entry(settings, "SAVE FOLDER", os.path.abspath("./received"), 2)

        browse = ctk.CTkButton(
            settings, text="Browse", font=_ui(11), width=70, height=28,
            fg_color=SURFACE_RAISED, hover_color=BORDER, text_color=TEXT_PRIMARY,
            command=self._browse_folder,
        )
        browse.grid(row=2, column=2, sticky="e", padx=(0, 0), pady=(4, 0))

        # -- log --------------------------------------------------------
        ctk.CTkLabel(self, text="ACTIVITY", font=_ui(11, "bold"), text_color=TEXT_SECONDARY).grid(
            row=3, column=0, sticky="w", pady=(0, 6)
        )
        self.log = LogConsole(self, height=180)
        self.log.grid(row=4, column=0, sticky="nsew")
        self.grid_rowconfigure(4, weight=1)

        self.after(200, self._poll_events)

    def _labeled_entry(self, parent, label, default, col):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 12, 0))
        ctk.CTkLabel(frame, text=label, font=_ui(11, "bold"), text_color=TEXT_SECONDARY).pack(anchor="w")
        entry = ctk.CTkEntry(
            frame, font=_mono(12), fg_color=SURFACE, border_color=BORDER,
            text_color=TEXT_PRIMARY, corner_radius=6, height=32,
        )
        entry.insert(0, default)
        entry.pack(fill="x", pady=(4, 0))
        return entry

    def _browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.dir_entry.delete(0, "end")
            self.dir_entry.insert(0, path)

    def toggle(self):
        if self.server is None:
            self.start_server()
        else:
            self.stop_server()

    def start_server(self):
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            self.log.write("Port must be a number.", "error")
            return

        pin = generate_pin()
        save_dir = self.dir_entry.get().strip() or "./received"
        name = self.name_entry.get().strip() or socket.gethostname()

        self.server = ReceiverServer(
            save_dir=save_dir,
            pin=pin,
            port=port,
            device_name=name,
            on_file_received=lambda f: self._events.put(("received", f)),
            on_progress=lambda fn, done, total: self._events.put(("progress", fn, done, total)),
        )
        try:
            self.server.start()
        except OSError as exc:
            self.log.write(f"Could not start listening: {exc}", "error")
            self.server = None
            return

        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        self.pin_label.configure(text=pin)
        self.address_label.configure(text=f"{get_local_ip()}:{port}")
        self.status_label.configure(text=f"Listening as '{name}'")
        self.toggle_btn.configure(text="Stop", fg_color=SURFACE_RAISED, hover_color=BORDER, text_color=TEXT_PRIMARY)
        self.dot.start()
        self.log.write(f"Receiver started. PIN {pin} on port {port}.", "ok")

    def stop_server(self):
        if self.server:
            self.server.stop()
            self.server = None
        self.dot.stop()
        self.pin_label.configure(text="------")
        self.address_label.configure(text="Not started")
        self.status_label.configure(text="Not listening")
        self.toggle_btn.configure(text="Start Receiving", fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b1210")
        self.log.write("Receiver stopped.", "warn")

    def _poll_events(self):
        try:
            while True:
                event = self._events.get_nowait()
                if event[0] == "received":
                    f = event[1]
                    self.log.write(f"Saved {os.path.basename(f.path)} ({format_size(f.size)}) from {f.sender}", "ok")
                elif event[0] == "progress":
                    _, fn, done, total = event
                    if done >= total:
                        self.log.write(f"Receiving {fn}: complete", "info")
        except queue.Empty:
            pass
        self.after(200, self._poll_events)


# ---------------------------------------------------------------------------
# Send tab
# ---------------------------------------------------------------------------

class SendTab(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.selected_files: list[str] = []
        self._events: "queue.Queue" = queue.Queue()

        self.grid_columnconfigure(0, weight=1)

        # -- file picker card -------------------------------------------------
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12, border_width=1, border_color=BORDER)
        card.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        card.grid_columnconfigure(0, weight=1)

        self.files_label = ctk.CTkLabel(
            card, text="No files selected", font=_ui(13), text_color=TEXT_SECONDARY, anchor="w"
        )
        self.files_label.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 4))

        pick_btn = ctk.CTkButton(
            card, text="Choose Files...", font=_ui(13, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b1210",
            corner_radius=8, height=36, command=self._pick_files,
        )
        pick_btn.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 18))

        # -- destination card --------------------------------------------
        dest = ctk.CTkFrame(self, fg_color="transparent")
        dest.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        dest.grid_columnconfigure((0, 1, 2), weight=1)

        self.host_entry = self._labeled_entry(dest, "RECEIVER IP", "", 0)
        self.port_entry = self._labeled_entry(dest, "PORT", str(DEFAULT_PORT), 1)
        self.pin_entry = self._labeled_entry(dest, "PIN", "", 2)

        find_btn = ctk.CTkButton(
            dest, text="Find Nearby Receivers", font=_ui(12, "bold"), height=32,
            fg_color=SURFACE_RAISED, hover_color=BORDER, text_color=TEXT_PRIMARY,
            corner_radius=8, command=self._discover,
        )
        find_btn.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))

        self.peer_list = ctk.CTkOptionMenu(
            dest, values=["No receivers found yet"], font=_ui(12),
            fg_color=SURFACE, button_color=SURFACE_RAISED, button_hover_color=BORDER,
            text_color=TEXT_PRIMARY, dropdown_fg_color=SURFACE_RAISED,
            command=self._select_peer,
        )
        self.peer_list.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self._peers: list[Peer] = []

        # -- send button + progress ------------------------------------
        action = ctk.CTkFrame(self, fg_color="transparent")
        action.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        action.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(
            action, fg_color=SURFACE, progress_color=ACCENT, height=10, corner_radius=5
        )
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        self.send_btn = ctk.CTkButton(
            action, text="Send", font=_ui(14, "bold"), height=40,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0b1210",
            corner_radius=8, command=self._start_send,
        )
        self.send_btn.grid(row=1, column=0, sticky="ew")

        # -- log ---------------------------------------------------------
        ctk.CTkLabel(self, text="ACTIVITY", font=_ui(11, "bold"), text_color=TEXT_SECONDARY).grid(
            row=3, column=0, sticky="w", pady=(0, 6)
        )
        self.log = LogConsole(self, height=140)
        self.log.grid(row=4, column=0, sticky="nsew")
        self.grid_rowconfigure(4, weight=1)

        self.after(150, self._poll_events)

    def _labeled_entry(self, parent, label, default, col):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 12, 0))
        ctk.CTkLabel(frame, text=label, font=_ui(11, "bold"), text_color=TEXT_SECONDARY).pack(anchor="w")
        entry = ctk.CTkEntry(
            frame, font=_mono(12), fg_color=SURFACE, border_color=BORDER,
            text_color=TEXT_PRIMARY, corner_radius=6, height=32,
        )
        entry.insert(0, default)
        entry.pack(fill="x", pady=(4, 0))
        return entry

    def _pick_files(self):
        paths = filedialog.askopenfilenames()
        if paths:
            self.selected_files = list(paths)
            names = ", ".join(os.path.basename(p) for p in self.selected_files)
            self.files_label.configure(
                text=names if len(names) < 70 else f"{len(self.selected_files)} files selected",
                text_color=TEXT_PRIMARY,
            )

    def _discover(self):
        self.log.write("Searching the network for receivers...", "info")

        def worker():
            peers = discover_peers(timeout=3.0)
            self._events.put(("peers", peers))

        threading.Thread(target=worker, daemon=True).start()

    def _select_peer(self, label: str):
        for p in self._peers:
            if str(p) == label:
                self.host_entry.delete(0, "end")
                self.host_entry.insert(0, p.ip)
                self.port_entry.delete(0, "end")
                self.port_entry.insert(0, str(p.port))

    def _start_send(self):
        if not self.selected_files:
            self.log.write("Choose at least one file first.", "error")
            return
        host = self.host_entry.get().strip()
        pin = self.pin_entry.get().strip()
        if not host or not pin:
            self.log.write("Receiver IP and PIN are both required.", "error")
            return
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            self.log.write("Port must be a number.", "error")
            return

        self.send_btn.configure(state="disabled", text="Sending...")
        files = list(self.selected_files)
        threading.Thread(target=self._send_worker, args=(files, host, port, pin), daemon=True).start()

    def _send_worker(self, files, host, port, pin):
        for path in files:
            filename = os.path.basename(path)
            self._events.put(("log", f"Sending {filename}...", "info"))
            try:
                send_file(
                    path, host=host, pin=pin, port=port,
                    on_progress=lambda done, total: self._events.put(("progress", done, total)),
                )
                self._events.put(("log", f"Sent {filename}", "ok"))
            except (TransferError, OSError) as exc:
                self._events.put(("log", f"Failed to send {filename}: {exc}", "error"))
        self._events.put(("done", None))

    def _poll_events(self):
        try:
            while True:
                event = self._events.get_nowait()
                if event[0] == "log":
                    self.log.write(event[1], event[2])
                elif event[0] == "progress":
                    _, done, total = event
                    self.progress.set(done / total if total else 0)
                elif event[0] == "done":
                    self.send_btn.configure(state="normal", text="Send")
                    self.progress.set(0)
                elif event[0] == "peers":
                    peers = event[1]
                    self._peers = peers
                    if peers:
                        labels = [str(p) for p in peers]
                        self.peer_list.configure(values=labels)
                        self.peer_list.set(labels[0])
                        self._select_peer(labels[0])
                        self.log.write(f"Found {len(peers)} receiver(s).", "ok")
                    else:
                        self.peer_list.configure(values=["No receivers found"])
                        self.log.write("No receivers found on this network.", "warn")
        except queue.Empty:
            pass
        self.after(150, self._poll_events)


# ---------------------------------------------------------------------------
# main window
# ---------------------------------------------------------------------------

class LanshareApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")

        self.title("lanshare")
        self.geometry("620x680")
        self.minsize(560, 560)
        self.configure(fg_color=BG)

        # -- header -----------------------------------------------------
        header = ctk.CTkFrame(self, fg_color="transparent", height=64)
        header.pack(fill="x", padx=24, pady=(20, 0))

        ctk.CTkLabel(
            header, text="lanshare", font=_ui(20, "bold"), text_color=TEXT_PRIMARY
        ).pack(side="left")
        ctk.CTkLabel(
            header, text="local network file transfer", font=_ui(12), text_color=TEXT_SECONDARY
        ).pack(side="left", padx=(10, 0), pady=(4, 0))

        # -- nav segmented control ---------------------------------------
        nav_wrap = ctk.CTkFrame(self, fg_color="transparent")
        nav_wrap.pack(fill="x", padx=24, pady=16)

        self.nav = ctk.CTkSegmentedButton(
            nav_wrap,
            values=["Receive", "Send"],
            font=_ui(13, "bold"),
            fg_color=SURFACE,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=SURFACE,
            unselected_hover_color=SURFACE_RAISED,
            text_color=TEXT_PRIMARY,
            text_color_disabled=TEXT_SECONDARY,
            corner_radius=8,
            height=36,
            command=self._switch_tab,
        )
        self.nav.set("Receive")
        self.nav.pack(fill="x")

        # -- content area --------------------------------------------------
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=24, pady=(0, 20))

        self.receive_tab = ReceiveTab(self.content)
        self.send_tab = SendTab(self.content)
        self.receive_tab.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _switch_tab(self, value: str):
        self.receive_tab.pack_forget()
        self.send_tab.pack_forget()
        if value == "Receive":
            self.receive_tab.pack(fill="both", expand=True)
        else:
            self.send_tab.pack(fill="both", expand=True)

    def _on_close(self):
        if self.receive_tab.server:
            self.receive_tab.server.stop()
        self.destroy()


def main() -> None:
    ctk.set_default_color_theme("blue")  # base theme; overridden by explicit colors above
    app = LanshareApp()
    app.mainloop()


if __name__ == "__main__":
    main()
