# HDMI Manager

A Tildagon badge app for the [rp2350-hdmi-hexpansion](https://github.com/Corteil/rp2350-hdmi-hexpansion)
HDMI Mirror hexpansion (VID `0x1969`, PID `0x4544`, friendly name
`HDMI-HEX`).

Auto-discovers the hexpansion by VID/PID (same pattern as
[tildagon-gps-clock](https://github.com/Corteil/tildagon-gps-clock) /
[tildagon-gps-info](https://github.com/Corteil/tildagon-gps-info)) and
gives you two tiles, cycled with Left/Right:

- **Status** — an "HDMI Manager" title over a monitor icon showing
  Mirroring (green, filled with signal bars) / Found, idle (amber,
  empty) / Not found (red, crossed out), plus the port it's on. OK
  detaches the mirror, or, when idle, hardware-resets the RP2350
  (pulsing `LS_A` to its `RUN` pin), waits 1.5 s for it to reboot
  (the tile shows "Resetting..." and ignores OK meanwhile), then
  attaches. Needs `LS_A` wired to `RUN`; without it OK just attaches.
- **Info** — identity (VID/PID, friendly name, unique ID) and two toggles:
  auto-attach-on-mount and the **5V boost** for the HDMI +5V pin (OK to
  edit, Up/Down to pick, Left/Right to toggle, OK again to close). The
  boost setting (`hdmi_boost`, default on) is saved like auto-attach and
  written to the RP2350 firmware over the hexpansion's EEPROM I2C
  (control byte at `0x20`, bit 0). The firmware boots with the boost off, so
  the app re-applies the saved setting after the hexpansion mounts and after
  its own reset.

This hexpansion's RP2350 receiver only ever correctly speaks one
protocol — a `TDHD`-header frame at 10MHz. There's deliberately no
driver picker here: attaching with a different protocol (e.g. a
GC9A01 screen's 40MHz init sequence) crashes the receiver, a bug
found and fixed during this app's development.

## Relationship to the hexpansion's own app

The HDMI-HEX carries its own small relay app in its emulated EEPROM, which
the badge launches on every insertion: it resets the RP2350 through `LS_A`
and attaches the mirror, with no need for this app. It reads the same
`hdmi_auto_attach` setting as this app's Info tile, and has its own
Auto-attach toggle on its screen (OK), so the setting works on a badge
that doesn't have this app installed. Both need badge firmware with the
display-mirroring patch (`display.attach_mirror` / `is_mirror_active`).
This app adds status, manual attach/detach/reset and the identity page.
The relay app has the same boost switch on its `B` key, using the same
`hdmi_boost` setting.

## Manual method

If `LS_A` isn't wired, or the automatic reset misses, the manual sequence
still works: **detach** the mirror here (OK on the Status tile while
Mirroring), press the **physical reset button** on the RP2350 board, then
**attach** again (OK). The order matters: the reset goes between detach and
attach, not after.

## Installing

Install from the Tildagon app store, or sideload the folder to
`/apps/tildagon_hdmi_manager/`. A sideloaded copy also needs a
`metadata.json` (name, category, author, description, version) next to
`app.py`: the app store generates it from `tildagon.toml`, but without it
the launcher lists the app under its folder name.

License: MIT
