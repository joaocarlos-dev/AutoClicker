"""Configurable global hotkeys that work across window managers.

On Linux, global key *listening* is done through evdev (reading raw
/dev/input/event* devices). This bypasses the display server entirely, so
it works identically under X11, Hyprland, Sway, GNOME, KDE, or any other
WM/compositor - the tradeoff is the user must be able to read those device
nodes (member of the `input` group).

On Windows, pynput's GlobalHotKeys (Win32 low-level hooks) is used, which
has no such caveat. It's also kept as an automatic fallback on Linux X11
sessions if evdev access isn't available.

A hotkey "spec" is a normalized string like "f6" or "ctrl+alt+p".
"""

import threading

MODIFIER_TOKENS = ("ctrl", "alt", "shift", "super")


def normalize_spec(spec: str) -> str:
    tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]

    if not tokens:
        raise ValueError("empty hotkey spec")

    mods = sorted(t for t in tokens if t in MODIFIER_TOKENS)
    main = [t for t in tokens if t not in MODIFIER_TOKENS]

    if len(main) != 1:
        raise ValueError(f"hotkey spec needs exactly one non-modifier key: {spec!r}")

    return "+".join(mods + main)


def spec_from_tokens(tokens: set[str]) -> str:
    mods = sorted(t for t in tokens if t in MODIFIER_TOKENS)
    main = [t for t in tokens if t not in MODIFIER_TOKENS]

    if len(main) != 1:
        raise ValueError("token set needs exactly one non-modifier key")

    return "+".join(mods + main)


class HotkeyBackend:
    name = "base"

    def register(self, spec: str, callback) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        """Drops all registered bindings (call before re-registering a fresh set)."""
        raise NotImplementedError

    def apply(self) -> None:
        """Activate/refresh bindings after register()/unregister() calls."""
        raise NotImplementedError

    def capture_next(self, on_captured, on_error=None) -> None:
        """Listens for the next key combo pressed and calls on_captured(spec)."""
        raise NotImplementedError

    def stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# evdev backend (Linux, any WM/compositor)
# ---------------------------------------------------------------------------

class EvdevHotkeyBackend(HotkeyBackend):
    name = "evdev"

    def __init__(self):
        import evdev
        from evdev import ecodes

        self._evdev = evdev
        self._ecodes = ecodes

        self._code_to_token = self._build_code_map()
        self._bindings: dict[str, callable] = {}
        self._held: set[str] = set()
        self._lock = threading.Lock()
        self._devices = []
        self._threads = []
        self._capture_cb = None
        self._capture_err_cb = None
        self._started = False
        self.permission_errors: list[str] = []

    def _build_code_map(self) -> dict[int, str]:
        ecodes = self._ecodes
        mapping: dict[int, str] = {
            ecodes.KEY_LEFTCTRL: "ctrl", ecodes.KEY_RIGHTCTRL: "ctrl",
            ecodes.KEY_LEFTALT: "alt", ecodes.KEY_RIGHTALT: "alt",
            ecodes.KEY_LEFTSHIFT: "shift", ecodes.KEY_RIGHTSHIFT: "shift",
            ecodes.KEY_LEFTMETA: "super", ecodes.KEY_RIGHTMETA: "super",
        }

        for name, code in ecodes.ecodes.items():
            if not name.startswith("KEY_"):
                continue
            if code in mapping:
                continue

            token = name[4:].lower()
            mapping[code] = token

        return mapping

    def _open_devices(self):
        devices = []

        for path in self._evdev.list_devices():
            try:
                dev = self._evdev.InputDevice(path)
                caps = dev.capabilities().get(self._ecodes.EV_KEY, [])

                if caps:
                    devices.append(dev)
            except (PermissionError, OSError) as e:
                self.permission_errors.append(f"{path}: {e}")

        return devices

    def start(self) -> None:
        if self._started:
            return

        self._devices = self._open_devices()

        for dev in self._devices:
            t = threading.Thread(target=self._read_loop, args=(dev,), daemon=True)
            t.start()
            self._threads.append(t)

        self._started = True

    def _read_loop(self, device) -> None:
        ecodes = self._ecodes

        try:
            for event in device.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue

                token = self._code_to_token.get(event.code)
                if token is None:
                    continue

                if event.value == 1:  # keydown
                    with self._lock:
                        self._held.add(token)
                        held_snapshot = frozenset(self._held)

                    self._handle_keydown(token, held_snapshot)

                elif event.value == 0:  # keyup
                    with self._lock:
                        self._held.discard(token)

        except OSError:
            return  # device unplugged/session ended

    def _handle_keydown(self, token: str, held: frozenset) -> None:
        if self._capture_cb is not None:
            if token in MODIFIER_TOKENS:
                return

            try:
                spec = spec_from_tokens(set(held))
            except ValueError:
                return

            cb = self._capture_cb
            self._capture_cb = None
            cb(spec)
            return

        try:
            spec = spec_from_tokens(set(held))
        except ValueError:
            return

        callback = self._bindings.get(spec)
        if callback:
            callback()

    def register(self, spec: str, callback) -> None:
        self._bindings[normalize_spec(spec)] = callback

    def clear(self) -> None:
        self._bindings.clear()

    def apply(self) -> None:
        self.start()

    def capture_next(self, on_captured, on_error=None) -> None:
        self.start()

        if not self._devices:
            if on_error:
                msg = "No readable keyboard devices."
                if self.permission_errors:
                    msg += (
                        " Your user likely lacks permission to read /dev/input/*. "
                        "Run: sudo usermod -aG input $USER, then log out and back in."
                    )
                on_error(msg)
            return

        self._capture_cb = on_captured

    def stop(self) -> None:
        for dev in self._devices:
            try:
                dev.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# pynput backend (Windows always; Linux X11 fallback)
# ---------------------------------------------------------------------------

class PynputHotkeyBackend(HotkeyBackend):
    name = "pynput"

    _SPECIAL_KEYS = {
        "space", "esc", "tab", "enter", "backspace", "delete", "home", "end",
        "page_up", "page_down", "up", "down", "left", "right", "insert",
        "caps_lock", "print_screen", "pause", "menu",
    }

    def __init__(self):
        self._bindings: dict[str, callable] = {}
        self._listener = None
        self._capture_listener = None

    def _to_pynput_format(self, spec: str) -> str:
        tokens = spec.split("+")
        out = []

        mod_map = {"ctrl": "<ctrl>", "alt": "<alt>", "shift": "<shift>", "super": "<cmd>"}

        for t in tokens:
            if t in mod_map:
                out.append(mod_map[t])
            elif t.startswith("f") and t[1:].isdigit():
                out.append(f"<{t}>")
            elif t in self._SPECIAL_KEYS:
                out.append(f"<{t}>")
            else:
                out.append(t)

        return "+".join(out)

    def register(self, spec: str, callback) -> None:
        self._bindings[normalize_spec(spec)] = callback

    def clear(self) -> None:
        self._bindings.clear()

    def apply(self) -> None:
        from pynput.keyboard import GlobalHotKeys

        if self._listener:
            self._listener.stop()
            self._listener = None

        if not self._bindings:
            return

        mapping = {
            self._to_pynput_format(spec): cb
            for spec, cb in self._bindings.items()
        }

        self._listener = GlobalHotKeys(mapping)
        self._listener.start()

    def capture_next(self, on_captured, on_error=None) -> None:
        from pynput import keyboard

        held: set[str] = set()

        mod_names = {
            keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
            keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
            keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
            keyboard.Key.cmd: "super",
        }

        def key_token(key):
            if key in mod_names:
                return mod_names[key]

            if hasattr(key, "char") and key.char:
                return key.char.lower()

            if hasattr(key, "name"):
                return key.name

            return None

        def on_press(key):
            token = key_token(key)
            if token is None:
                return

            if token in MODIFIER_TOKENS:
                held.add(token)
                return

            try:
                spec = spec_from_tokens(held | {token})
            except ValueError:
                return

            on_captured(spec)
            return False  # stop listener

        def on_release(key):
            token = key_token(key)
            if token in MODIFIER_TOKENS:
                held.discard(token)

        self._capture_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._capture_listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._capture_listener:
            self._capture_listener.stop()
            self._capture_listener = None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_hotkey_backend(preference: str = "auto"):
    """Returns (backend, backend_name, warning)."""

    import platform_backend as pb

    if preference == "pynput":
        return PynputHotkeyBackend(), "pynput", None

    if preference == "evdev":
        return EvdevHotkeyBackend(), "evdev", None

    if pb.os_name() == "linux":
        try:
            backend = EvdevHotkeyBackend()
            backend.start()

            if backend._devices:
                return backend, "evdev", None

            warning = (
                "No /dev/input devices could be opened (missing permission?). "
                "Falling back to pynput, which only sees global hotkeys on X11 "
                "sessions, not native Wayland. Run: sudo usermod -aG input $USER "
                "and log back in to fix this."
            )
            return PynputHotkeyBackend(), "pynput", warning

        except ImportError:
            return (
                PynputHotkeyBackend(),
                "pynput",
                "python-evdev is not installed; falling back to pynput "
                "(X11 only, will not work on native Wayland).",
            )

    return PynputHotkeyBackend(), "pynput", None
