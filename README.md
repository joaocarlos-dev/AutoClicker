# Multi Slot Auto Clicker

A cross-platform auto clicker with multiple saved click positions ("slots"),
configurable global hotkeys, and three run modes: infinite, a fixed number
of clicks, or a fixed duration.

Works on:
- Windows
- Linux + X11 (i3, XFCE, GNOME/KDE on X11, ...)
- Linux + Wayland (Hyprland, HyDE, Sway, GNOME/KDE on Wayland, ...)

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate      # .venv\Scripts\activate on Windows
pip install -r requirements.txt
python main.py
```

## Usage

- **Clicker tab**: set clicks-per-second, pick a run mode (infinite / fixed
  amount / fixed duration), then click **Add Slot** and click anywhere on
  screen to record that point. Add as many slots as you want; the clicker
  cycles through all of them each pass. Select one or more entries and hit
  **Remove Selected Slot(s)**, or **Clear All Slots** to start over.
- **Hotkeys & Settings tab**: click **Set...** next to any action and press
  the key combo you want (e.g. `F6` or `Ctrl+Alt+P`), then **Save Hotkeys**.
  This tab also shows which backends are active for your system and any
  warnings about missing permissions/tools.

Settings (CPS, run mode, slots, hotkeys) are saved on close to:
- Linux: `~/.config/autoclicker/config.json`
- Windows: `%APPDATA%\AutoClicker\config.json`

## Platform notes

### Windows

Nothing extra needed — pyautogui and pynput use native Win32 APIs directly.

### Linux, X11 session

Nothing extra needed — pyautogui/pynput talk to the X server directly and
work regardless of your WM (i3, Openbox, XFCE, GNOME/KDE on Xorg, etc.).

### Linux, Wayland session (Hyprland, HyDE, Sway, GNOME/KDE Wayland, ...)

Wayland's security model doesn't let arbitrary apps inject clicks or listen
to global keys the way X11 does, so this app uses different tools for those
two jobs:

1. **Clicking** → [`ydotool`](https://github.com/ReimuNotMoe/ydotool), which
   injects input at the kernel (`/dev/uinput`) level and works under any
   compositor.

   ```bash
   sudo pacman -S ydotool          # Arch/HyDE
   sudo systemctl enable --now ydotoold
   sudo usermod -aG input $USER    # or set up a udev rule for /dev/uinput
   ```

   Log out/in (or reboot) after changing groups. Without `ydotool`, the app
   falls back to pyautogui via XWayland, which is unreliable for clicks
   outside XWayland surfaces on most compositors — install `ydotool` for
   correct behavior.

2. **Picking a click position** ("Add Slot") →
   [`slurp`](https://github.com/emersion/slurp), which shows a screen-wide
   overlay and lets you click the exact pixel, same tool used by most
   screenshot setups (`grim`+`slurp`) on Hyprland/Sway. It's already part of
   most Hyprland/HyDE installs.

   ```bash
   sudo pacman -S slurp
   ```

   If `slurp` isn't available, on Hyprland specifically the app falls back
   to a 3-second countdown that samples the live cursor position via
   `hyprctl cursorpos`.

3. **Global hotkeys** → read directly from `/dev/input/event*` (evdev), which
   works under any window manager/compositor because it bypasses the
   display server entirely. This needs your user to be able to read those
   device files:

   ```bash
   sudo usermod -aG input $USER
   ```

   Log out/in afterward. Without this, hotkeys fall back to pynput, which
   only reliably sees global key presses on X11 sessions — on Hyprland/Wayland
   it generally won't fire. The Hotkeys tab's Diagnostics section tells you
   which backend is actually active and what to fix if it's degraded.

### Checking what's active

Open the **Hotkeys & Settings** tab — the Diagnostics box shows the detected
environment and which backend was picked for clicking, position capture,
and hotkeys, plus a plain-English fix for any warning.

## Troubleshooting

- **Clicks don't land anywhere on Wayland**: install and enable `ydotool`
  (see above). Confirm it works standalone: `ydotool click 0xC0` should
  left-click at the current cursor position. If your `ydotool` version uses
  a different bitmask for left-click, you can override it by constructing
  `platform_backend.YdotoolClicker(click_arg="...")` — file an issue/PR if
  you want this exposed in the Settings UI.
- **Hotkeys don't trigger on Hyprland/Sway/etc.**: you're likely not in the
  `input` group yet, or haven't logged out/in since being added. Check the
  Diagnostics box for the exact warning.
- **"Add Slot" capture never returns**: on Wayland this spawns `slurp`,
  which takes over the whole screen until you click or press Esc. Press Esc
  to cancel and try again.

## License

MIT — see [LICENSE](LICENSE).
