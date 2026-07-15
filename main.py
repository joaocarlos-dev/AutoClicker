import threading
import time
import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.dialogs import Messagebox

import config
import hotkeys as hk
import platform_backend as pb

# =====================================
# State
# =====================================

cfg = config.load()

running = False
slots: list[tuple[int, int]] = [tuple(p) for p in cfg.get("slots", [])]

clicker = None
click_backend_name = "none"
click_backend_warning = None

capture = None
capture_backend_name = "none"
capture_backend_warning = None

hotkey_backend = None
hotkey_backend_name = "none"
hotkey_backend_warning = None

HOTKEY_ACTIONS = [
    ("start", "Start clicking"),
    ("stop", "Stop clicking"),
    ("toggle", "Toggle start/stop"),
    ("add_slot", "Add slot (capture position)"),
]


def init_backends():
    global clicker, click_backend_name, click_backend_warning
    global capture, capture_backend_name, capture_backend_warning
    global hotkey_backend, hotkey_backend_name, hotkey_backend_warning

    try:
        clicker, click_backend_name, click_backend_warning = pb.get_clicker(
            cfg.get("click_backend", "auto")
        )
    except pb.BackendError as e:
        clicker = None
        click_backend_name = "none"
        click_backend_warning = str(e)

    try:
        capture, capture_backend_name, capture_backend_warning = pb.get_position_capture(
            cfg.get("capture_backend", "auto")
        )
    except pb.BackendError as e:
        capture = None
        capture_backend_name = "none"
        capture_backend_warning = str(e)

    try:
        hotkey_backend, hotkey_backend_name, hotkey_backend_warning = hk.get_hotkey_backend(
            cfg.get("hotkey_backend", "auto")
        )
    except Exception as e:  # pragma: no cover - defensive
        hotkey_backend = None
        hotkey_backend_name = "none"
        hotkey_backend_warning = str(e)


init_backends()

# =====================================
# Slot capture ("click where you want the click to happen")
# =====================================


def capture_next_click():
    if capture is None:
        Messagebox.show_error("No position capture backend available.", "No capture backend")
        return

    status_var.set("Preparing capture...")
    root.iconify()

    def on_point(x, y):
        root.after(0, lambda: _slot_captured(x, y))

    def on_error(msg):
        root.after(0, lambda: _slot_capture_failed(msg))

    def on_status(msg):
        root.after(0, lambda: status_var.set(msg))

    # give the window manager a moment to actually iconify before the
    # capture overlay (slurp) or listener grabs input
    root.after(150, lambda: capture.capture(on_point, on_error, on_status))


def _slot_captured(x, y):
    slots.append((x, y))
    refresh_slots()
    root.deiconify()
    root.lift()
    status_var.set(f"Slot added: ({x}, {y})")


def _slot_capture_failed(msg):
    root.deiconify()
    root.lift()
    status_var.set("Capture failed")
    Messagebox.show_error(msg, "Position capture failed")


def refresh_slots():
    slot_tree.delete(*slot_tree.get_children())

    for i, (x, y) in enumerate(slots, start=1):
        slot_tree.insert("", "end", iid=str(i - 1), values=(i, x, y))

    slot_count_var.set(f"{len(slots)} slot{'s' if len(slots) != 1 else ''} configured")


def remove_selected_slot():
    selected = slot_tree.selection()

    if not selected:
        return

    for iid in sorted((int(i) for i in selected), reverse=True):
        del slots[iid]

    refresh_slots()


def clear_slots():
    slots.clear()
    refresh_slots()


# =====================================
# Auto click loop
# =====================================


def click_loop():
    global running

    try:
        cps = float(cps_var.get())
    except ValueError:
        cps = 10
    cps = max(0.1, cps)
    delay = 1.0 / cps

    mode = mode_var.get()

    try:
        amount = max(1, int(amount_var.get()))
    except ValueError:
        amount = 100

    try:
        duration = max(0.1, float(duration_var.get()))
    except ValueError:
        duration = 60.0

    start_time = time.monotonic()
    clicks_done = 0

    while running:
        for x, y in list(slots):
            if not running:
                break

            try:
                clicker.click(x, y)
            except pb.BackendError as e:
                running = False
                root.after(0, lambda e=e: Messagebox.show_error(str(e), "Click backend error"))
                break

            clicks_done += 1

            if mode == "amount" and clicks_done >= amount:
                running = False
                break

            if mode == "duration" and (time.monotonic() - start_time) >= duration:
                running = False
                break

            time.sleep(delay)

    running = False
    root.after(0, _set_stopped_ui)


def _set_stopped_ui():
    status_var.set("Stopped")
    status_label.configure(bootstyle="secondary")
    start_btn.configure(state="normal")
    stop_btn.configure(state="disabled")


def start_clicker():
    global running

    if running:
        return

    if not slots:
        Messagebox.show_warning("Add at least one slot first.", "Warning")
        return

    if clicker is None:
        Messagebox.show_error(
            click_backend_warning or "No click backend is available on this system.",
            "No click backend",
        )
        return

    running = True
    status_var.set("Running")
    status_label.configure(bootstyle="success")
    start_btn.configure(state="disabled")
    stop_btn.configure(state="normal")

    threading.Thread(target=click_loop, daemon=True).start()


def stop_clicker():
    global running
    running = False
    _set_stopped_ui()


def toggle_clicker():
    if running:
        stop_clicker()
    else:
        start_clicker()


# =====================================
# Hotkeys
# =====================================

HOTKEY_HANDLERS = {
    "start": lambda: root.after(0, start_clicker),
    "stop": lambda: root.after(0, stop_clicker),
    "toggle": lambda: root.after(0, toggle_clicker),
    "add_slot": lambda: root.after(0, capture_next_click),
}


def register_all_hotkeys():
    if hotkey_backend is None:
        return

    hotkey_backend.clear()

    for action, spec in cfg.get("hotkeys", {}).items():
        handler = HOTKEY_HANDLERS.get(action)
        if not handler or not spec:
            continue

        try:
            hotkey_backend.register(spec, handler)
        except ValueError:
            continue

    hotkey_backend.apply()


def start_hotkey_capture(action):
    if hotkey_backend is None:
        Messagebox.show_error("No hotkey backend is available.", "No hotkey backend")
        return

    hotkey_vars[action].set("press a key...")

    def on_captured(spec):
        root.after(0, lambda: hotkey_vars[action].set(spec))

    def on_error(msg):
        root.after(0, lambda: Messagebox.show_error(msg, "Hotkey capture failed"))

    hotkey_backend.capture_next(on_captured, on_error)


def save_hotkeys():
    new_specs = {}

    for action, _ in HOTKEY_ACTIONS:
        raw = hotkey_vars[action].get().strip()

        try:
            new_specs[action] = hk.normalize_spec(raw)
        except ValueError as e:
            Messagebox.show_error(f"{action}: {e}", "Invalid hotkey")
            return

    if len(set(new_specs.values())) != len(new_specs):
        Messagebox.show_error("Each action must use a different hotkey.", "Duplicate hotkeys")
        return

    cfg["hotkeys"] = new_specs

    for action, spec in new_specs.items():
        hotkey_vars[action].set(spec)

    register_all_hotkeys()
    persist_config()
    status_var.set("Hotkeys saved.")


def reset_hotkeys():
    for action, spec in config.DEFAULT_HOTKEYS.items():
        hotkey_vars[action].set(spec)


# =====================================
# Config persistence
# =====================================


def persist_config():
    cfg["cps"] = cps_var.get()
    cfg["mode"] = mode_var.get()
    cfg["amount"] = amount_var.get()
    cfg["duration_seconds"] = duration_var.get()
    cfg["slots"] = [list(p) for p in slots]

    try:
        config.save(cfg)
    except OSError as e:
        Messagebox.show_error(str(e), "Could not save settings")


def on_close():
    persist_config()
    root.destroy()


# =====================================
# UI
# =====================================

root = ttk.Window(title="Multi Slot Auto Clicker", themename="darkly")
root.geometry("700x760")
root.minsize(620, 640)

try:
    root.place_window_center()
except AttributeError:
    pass

root.protocol("WM_DELETE_WINDOW", on_close)

style = ttk.Style()
style.configure("TLabel", font=("TkDefaultFont", 10))
style.configure("Heading.TLabel", font=("TkDefaultFont", 13, "bold"))
style.configure("SubHeading.TLabel", font=("TkDefaultFont", 10, "bold"))
style.configure("Status.TLabel", font=("TkDefaultFont", 12, "bold"))

notebook = ttk.Notebook(root)
notebook.pack(fill="both", expand=True, padx=14, pady=14)

clicker_tab = ttk.Frame(notebook, padding=20)
hotkeys_tab = ttk.Frame(notebook, padding=20)

notebook.add(clicker_tab, text="  Clicker  ")
notebook.add(hotkeys_tab, text="  Hotkeys & Settings  ")

# ---------- Clicker tab ----------

ttk.Label(clicker_tab, text="Click Speed", style="Heading.TLabel").pack(anchor="w")

speed_row = ttk.Frame(clicker_tab)
speed_row.pack(fill="x", pady=(8, 18))

ttk.Label(speed_row, text="Clicks per second").pack(side="left")

cps_var = tk.StringVar(value=str(cfg.get("cps", 10)))
ttk.Entry(speed_row, textvariable=cps_var, width=10, bootstyle="primary").pack(
    side="right"
)

ttk.Label(clicker_tab, text="Run Mode", style="Heading.TLabel").pack(anchor="w")

mode_var = tk.StringVar(value=cfg.get("mode", "infinite"))
amount_var = tk.StringVar(value=str(cfg.get("amount", 100)))
duration_var = tk.StringVar(value=str(cfg.get("duration_seconds", 60)))

mode_frame = ttk.Frame(clicker_tab)
mode_frame.pack(fill="x", pady=(8, 18))


def _update_mode_widgets(*_):
    mode = mode_var.get()
    amount_entry.configure(state="normal" if mode == "amount" else "disabled")
    duration_entry.configure(state="normal" if mode == "duration" else "disabled")


ttk.Radiobutton(
    mode_frame, text="Infinite (until stopped)", value="infinite",
    variable=mode_var, command=_update_mode_widgets, bootstyle="primary",
).pack(anchor="w", pady=3)

amount_row = ttk.Frame(mode_frame)
amount_row.pack(fill="x", pady=3)
ttk.Radiobutton(
    amount_row, text="Fixed amount of clicks", value="amount",
    variable=mode_var, command=_update_mode_widgets, bootstyle="primary",
).pack(side="left")
amount_entry = ttk.Entry(amount_row, textvariable=amount_var, width=10)
amount_entry.pack(side="right")

duration_row = ttk.Frame(mode_frame)
duration_row.pack(fill="x", pady=3)
ttk.Radiobutton(
    duration_row, text="Run for duration (seconds)", value="duration",
    variable=mode_var, command=_update_mode_widgets, bootstyle="primary",
).pack(side="left")
duration_entry = ttk.Entry(duration_row, textvariable=duration_var, width=10)
duration_entry.pack(side="right")

_update_mode_widgets()

ttk.Separator(clicker_tab).pack(fill="x", pady=(0, 14))

slots_header = ttk.Frame(clicker_tab)
slots_header.pack(fill="x")
ttk.Label(slots_header, text="Configured Slots", style="Heading.TLabel").pack(side="left")

slot_count_var = tk.StringVar(value="0 slots configured")
ttk.Label(slots_header, textvariable=slot_count_var, bootstyle="secondary").pack(side="right")

slot_tree = ttk.Treeview(
    clicker_tab,
    columns=("index", "x", "y"),
    show="headings",
    height=9,
    selectmode="extended",
    bootstyle="primary",
)
slot_tree.heading("index", text="#")
slot_tree.heading("x", text="X")
slot_tree.heading("y", text="Y")
slot_tree.column("index", width=50, anchor="center")
slot_tree.column("x", width=100, anchor="center")
slot_tree.column("y", width=100, anchor="center")
slot_tree.pack(fill="both", expand=True, pady=(8, 14))
refresh_slots()

slot_buttons = ttk.Frame(clicker_tab)
slot_buttons.pack(fill="x", pady=(0, 18))
slot_buttons.columnconfigure((0, 1, 2), weight=1)

ttk.Button(
    slot_buttons, text="+ Add Slot", command=capture_next_click, bootstyle="primary",
).grid(row=0, column=0, sticky="ew", padx=(0, 5))
ttk.Button(
    slot_buttons, text="Remove Selected", command=remove_selected_slot, bootstyle="warning-outline",
).grid(row=0, column=1, sticky="ew", padx=5)
ttk.Button(
    slot_buttons, text="Clear All", command=clear_slots, bootstyle="danger-outline",
).grid(row=0, column=2, sticky="ew", padx=(5, 0))

ttk.Separator(clicker_tab).pack(fill="x", pady=(0, 14))

run_buttons = ttk.Frame(clicker_tab)
run_buttons.pack(fill="x")
run_buttons.columnconfigure((0, 1), weight=1)

start_btn = ttk.Button(
    run_buttons, text="▶  Start", command=start_clicker, bootstyle="success",
)
start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 5), ipady=6)

stop_btn = ttk.Button(
    run_buttons, text="■  Stop", command=stop_clicker, bootstyle="danger", state="disabled",
)
stop_btn.grid(row=0, column=1, sticky="ew", padx=(5, 0), ipady=6)

status_var = tk.StringVar(value="Stopped")
status_label = ttk.Label(
    clicker_tab, textvariable=status_var, style="Status.TLabel", bootstyle="secondary", anchor="center",
)
status_label.pack(fill="x", pady=(14, 0))

# ---------- Hotkeys tab ----------

ttk.Label(hotkeys_tab, text="Global Hotkeys", style="Heading.TLabel").pack(anchor="w", pady=(0, 12))

hotkey_vars = {
    action: tk.StringVar(value=cfg.get("hotkeys", {}).get(action, default))
    for action, default in config.DEFAULT_HOTKEYS.items()
}

for action, description in HOTKEY_ACTIONS:
    row = ttk.Frame(hotkeys_tab)
    row.pack(fill="x", pady=4)

    ttk.Label(row, text=description).pack(side="left")
    ttk.Entry(
        row, textvariable=hotkey_vars[action], width=18, state="readonly", justify="center",
    ).pack(side="right", padx=(8, 0))
    ttk.Button(
        row, text="Set...", command=lambda a=action: start_hotkey_capture(a), bootstyle="info-outline",
    ).pack(side="right")

button_row = ttk.Frame(hotkeys_tab)
button_row.pack(fill="x", pady=(14, 0))
ttk.Button(button_row, text="Save Hotkeys", command=save_hotkeys, bootstyle="success").pack(
    side="left"
)
ttk.Button(
    button_row, text="Reset to Defaults", command=reset_hotkeys, bootstyle="secondary-outline",
).pack(side="left", padx=8)

ttk.Separator(hotkeys_tab).pack(fill="x", pady=20)

diagnostics_frame = ttk.Labelframe(hotkeys_tab, text="Diagnostics", padding=14, bootstyle="info")
diagnostics_frame.pack(fill="x")

diagnostics_text = (
    f"Environment:  {pb.describe_environment()}\n"
    f"Click backend:  {click_backend_name}\n"
    f"Position capture backend:  {capture_backend_name}\n"
    f"Hotkey backend:  {hotkey_backend_name}"
)

ttk.Label(diagnostics_frame, text=diagnostics_text, justify="left").pack(anchor="w")

warnings_text = "\n".join(
    f"• {w}" for w in [click_backend_warning, capture_backend_warning, hotkey_backend_warning] if w
)

if warnings_text:
    ttk.Label(
        diagnostics_frame, text=warnings_text, justify="left", bootstyle="warning", wraplength=580,
    ).pack(anchor="w", pady=(10, 0))

ttk.Label(
    hotkeys_tab,
    text=(
        "Add Slot: click 'Set...', then click anywhere on screen (or press the "
        "bound hotkey) to record that position as a slot.\n\n"
        "On Linux, hotkeys are captured via evdev (/dev/input) so they work under "
        "any window manager or compositor, including Wayland compositors like "
        "Hyprland. This requires your user to be in the 'input' group: "
        "sudo usermod -aG input $USER, then log out/in."
    ),
    justify="left",
    bootstyle="secondary",
    wraplength=600,
).pack(anchor="w", pady=(16, 0))

# =====================================
# Startup
# =====================================

register_all_hotkeys()

root.mainloop()
