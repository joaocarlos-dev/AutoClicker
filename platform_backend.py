"""Cross-platform input backends.

Covers three environments:
  - Windows                              -> pyautogui / pynput (Win32 hooks)
  - Linux, X11 session (i3, XFCE, KDE X11, GNOME X11, ...) -> pyautogui / pynput (Xlib)
  - Linux, Wayland session (Hyprland, Sway, GNOME/KDE Wayland, ...)
        click injection -> ydotool (uinput, works under any compositor)
        position capture -> slurp (wlroots layer-shell point picker) or
                             hyprctl cursorpos (Hyprland-only IPC fallback)

Nothing here talks to Tk. Position capture callbacks may fire from a
background thread; callers must marshal back onto the GUI thread
(e.g. via `root.after`).
"""

import json
import os
import platform
import shutil
import subprocess
import threading


class BackendError(Exception):
    """Raised when a backend cannot perform the requested action."""


# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def os_name() -> str:
    system = platform.system()

    if system == "Windows":
        return "windows"
    if system == "Linux":
        return "linux"
    if system == "Darwin":
        return "macos"

    return "other"


def session_type() -> str:
    """Best-effort detection of the Linux display session type."""

    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"

    xdg = os.environ.get("XDG_SESSION_TYPE", "").lower()

    if xdg in ("wayland", "x11"):
        return xdg

    if os.environ.get("DISPLAY"):
        return "x11"

    return "unknown"


def desktop_name() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "") or os.environ.get("DESKTOP_SESSION", "")


def describe_environment() -> str:
    osn = os_name()

    if osn == "windows":
        return "Windows"

    if osn == "linux":
        return f"Linux / {session_type()} / {desktop_name() or 'unknown WM'}"

    return osn


def has_binary(name: str) -> bool:
    return shutil.which(name) is not None


# ---------------------------------------------------------------------------
# Click injection
# ---------------------------------------------------------------------------

class Clicker:
    name = "base"

    def click(self, x: int, y: int) -> None:
        raise NotImplementedError


class PyAutoGuiClicker(Clicker):
    name = "pyautogui"

    def __init__(self):
        import pyautogui
        pyautogui.FAILSAFE = False
        self._pyautogui = pyautogui

    def click(self, x: int, y: int) -> None:
        self._pyautogui.click(x=x, y=y)


class YdotoolClicker(Clicker):
    """Synthetic clicks via uinput, works on any Wayland compositor.

    Needs the `ydotool` binary plus either a running `ydotoold` daemon or
    direct read/write permission on /dev/uinput. The click argument
    (default left-click) is exposed so users on ydotool versions with a
    different bitmask can override it without touching the code.
    """

    name = "ydotool"

    def __init__(self, click_arg: str = "0xC0"):
        if not has_binary("ydotool"):
            raise BackendError(
                "ydotool not found. Install it (e.g. `sudo pacman -S ydotool`), "
                "enable/start the ydotoold service, and make sure your user can "
                "access /dev/uinput."
            )

        self._click_arg = click_arg

    def click(self, x: int, y: int) -> None:
        try:
            subprocess.run(
                ["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["ydotool", "click", self._click_arg],
                check=True, capture_output=True, text=True,
            )
        except FileNotFoundError as e:
            raise BackendError("ydotool binary disappeared from PATH.") from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            raise BackendError(
                "ydotool failed to click. Is ydotoold running and does your "
                f"user have access to /dev/uinput?\n{stderr}"
            ) from e


def get_clicker(preference: str = "auto") -> tuple[Clicker, str, str | None]:
    """Returns (clicker, backend_name, warning).

    warning is a human readable string when a fallback had to be used,
    otherwise None.
    """

    osn = os_name()

    if preference == "pyautogui":
        return PyAutoGuiClicker(), "pyautogui", None

    if preference == "ydotool":
        return YdotoolClicker(), "ydotool", None

    # auto
    if osn == "windows":
        return PyAutoGuiClicker(), "pyautogui", None

    if osn == "linux" and session_type() == "wayland":
        if has_binary("ydotool"):
            return YdotoolClicker(), "ydotool", None

        return (
            PyAutoGuiClicker(),
            "pyautogui",
            "Wayland session detected but ydotool is not installed. Falling back "
            "to pyautogui/XTest via XWayland, which is unreliable for global "
            "clicks on most compositors. Install ydotool for reliable clicking.",
        )

    return PyAutoGuiClicker(), "pyautogui", None


# ---------------------------------------------------------------------------
# Position capture ("click where you want the click to happen")
# ---------------------------------------------------------------------------

class PositionCapture:
    name = "base"

    def capture(self, on_point, on_error=None, on_status=None) -> None:
        """Starts an async capture. Calls on_point(x, y) exactly once on
        success, or on_error(message) on failure. May run a background
        thread; callbacks are NOT guaranteed to run on the calling thread.
        """
        raise NotImplementedError


class PynputPositionCapture(PositionCapture):
    """Global mouse-click listener via Xlib. Works on Windows and Linux X11
    (and often on Wayland compositors through XWayland, best-effort only)."""

    name = "pynput"

    def capture(self, on_point, on_error=None, on_status=None) -> None:
        from pynput import mouse

        if on_status:
            on_status("Click anywhere on screen to set the position...")

        def on_click(x, y, button, pressed):
            if pressed and button == mouse.Button.left:
                on_point(x, y)
                return False

        listener = mouse.Listener(on_click=on_click)
        listener.start()


class SlurpPositionCapture(PositionCapture):
    """Point picker for wlroots-based Wayland compositors (Hyprland, Sway,
    river, ...) using the `slurp` utility."""

    name = "slurp"

    def capture(self, on_point, on_error=None, on_status=None) -> None:
        if not has_binary("slurp"):
            if on_error:
                on_error("slurp is not installed.")
            return

        if on_status:
            on_status("Click a point on screen (slurp overlay is active)...")

        def run():
            try:
                result = subprocess.run(
                    ["slurp", "-p", "-f", "%x,%y"],
                    check=True, capture_output=True, text=True,
                )
                x_str, y_str = result.stdout.strip().split(",")
                on_point(int(float(x_str)), int(float(y_str)))
            except subprocess.CalledProcessError as e:
                if on_error:
                    stderr = (e.stderr or "").strip()
                    on_error(f"slurp was cancelled or failed: {stderr}")
            except (ValueError, FileNotFoundError) as e:
                if on_error:
                    on_error(f"Could not parse slurp output: {e}")

        threading.Thread(target=run, daemon=True).start()


class HyprctlPositionCapture(PositionCapture):
    """Hyprland-only fallback: reads the live cursor position via IPC after
    a short countdown, in case slurp is unavailable."""

    name = "hyprctl"
    countdown_seconds = 3

    def capture(self, on_point, on_error=None, on_status=None) -> None:
        if not has_binary("hyprctl"):
            if on_error:
                on_error("hyprctl is not available.")
            return

        def run():
            for remaining in range(self.countdown_seconds, 0, -1):
                if on_status:
                    on_status(
                        f"Move the mouse to the target position... capturing in {remaining}s"
                    )
                import time
                time.sleep(1)

            try:
                result = subprocess.run(
                    ["hyprctl", "cursorpos", "-j"],
                    check=True, capture_output=True, text=True,
                )
                data = json.loads(result.stdout)
                on_point(int(data["x"]), int(data["y"]))
            except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
                if on_error:
                    on_error(f"hyprctl cursorpos failed: {e}")

        threading.Thread(target=run, daemon=True).start()


def get_position_capture(preference: str = "auto") -> tuple[PositionCapture, str, str | None]:
    """Returns (capture, backend_name, warning)."""

    osn = os_name()

    if preference == "pynput":
        return PynputPositionCapture(), "pynput", None
    if preference == "slurp":
        return SlurpPositionCapture(), "slurp", None
    if preference == "hyprctl":
        return HyprctlPositionCapture(), "hyprctl", None

    # auto
    if osn == "linux" and session_type() == "wayland":
        if has_binary("slurp"):
            return SlurpPositionCapture(), "slurp", None

        if has_binary("hyprctl"):
            return (
                HyprctlPositionCapture(),
                "hyprctl",
                "slurp not installed; using a 3-second countdown + hyprctl "
                "cursorpos instead. Install slurp for click-to-pick.",
            )

        return (
            PynputPositionCapture(),
            "pynput",
            "Wayland session detected but neither slurp nor hyprctl is "
            "available. Falling back to Xlib mouse capture, which usually "
            "only sees XWayland surfaces. Install slurp for reliable "
            "click-to-pick.",
        )

    return PynputPositionCapture(), "pynput", None
