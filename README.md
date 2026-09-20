# HDMI Manager

A Tildagon badge app for the [rp2350-hdmi-hexpansion](https://github.com/Corteil/rp2350-hdmi-hexpansion)
HDMI Mirror hexpansion (VID `0x1969`, PID `0x4544`, friendly name
`HDMI-HEX`).

Auto-discovers the hexpansion by VID/PID (same pattern as
[tildagon-gps-clock](https://github.com/Corteil/tildagon-gps-clock) /
[tildagon-gps-info](https://github.com/Corteil/tildagon-gps-info)) and
gives you three tiles, cycled with Left/Right:

- **Status** — a monitor icon showing Mirroring (green, filled with
  signal bars) / Found, idle (amber, empty) / Not found (red, crossed
  out), plus the port it's on.
- **Control** — press OK to attach or detach the mirror.
- **Info** — identity (VID/PID, friendly name, unique ID) and an
  auto-attach-on-mount toggle (OK to edit, Up/Down to pick, Left/Right
  to toggle, OK again to close).

This hexpansion's RP2350 receiver only ever correctly speaks one
protocol — a `TDHD`-header frame at 10MHz. There's deliberately no
driver picker here: attaching with a different protocol (e.g. a
GC9A01 screen's 40MHz init sequence) crashes the receiver, a bug
found and fixed during this app's development.

License: MIT
