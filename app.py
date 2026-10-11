"""
HDMI Manager for the Tildagon badge.

Manages the rp2350-hdmi-hexpansion HDMI Mirror hexpansion (VID 0x1969,
PID 0x4544, friendly name "HDMI-HEX"): shows whether it's plugged in and
mirroring, lets you attach/detach the mirror, and shows its identity.

This hexpansion's RP2350 receiver only ever correctly speaks one
protocol -- TDHD-header framing at 10MHz (see rp2350-hdmi-hexpansion's
own eeprom_i2c.c / pio-testcard's spi_slave_rx.pio). Driving it with a
different protocol (e.g. a GC9A01 screen's 40MHz init sequence) crashed
it during development, so this app deliberately has no driver picker --
just attach/detach against the one correct protocol.

License: MIT
"""
import app
import math
import time
import display

from events.input import Buttons, BUTTON_TYPES
from tildagonos import tildagonos
from system.eventbus import eventbus
from system.hexpansion.events import HexpansionMountedEvent
from system.patterndisplay.events import PatternDisable, PatternEnable
from system.scheduler.events import RequestForegroundPushEvent, RequestForegroundPopEvent

HDMI_VID = 0x1969
HDMI_PID = 0x4544

# The one correct protocol for this hexpansion -- see module docstring.
HDMI_DRIVER = {"header": b"TDHD", "baudrate": 10_000_000}

FACES = ("status", "info")

# LS_A is wired to the RP2350's RUN pin: pulling it low resets the board.
# Same pulse as rp2350-hdmi-hexpansion's own badge_app, with the same
# settle time before attaching so the RP2350 has finished rebooting.
RESET_PULSE_MS = 10
RESET_SETTLE_MS = 1500

STATE_NOT_FOUND = 0
STATE_IDLE = 1
STATE_MIRRORING = 2

STATE_LABEL = {
    STATE_NOT_FOUND: "Not found",
    STATE_IDLE: "Found, idle",
    STATE_MIRRORING: "Mirroring",
}
STATE_COLOR = {
    STATE_NOT_FOUND: (0.93, 0.14, 0.0),
    STATE_IDLE: (1.0, 0.75, 0.0),
    STATE_MIRRORING: (0.0, 0.85, 0.3),
}


def get_app_by_vid_pid_shim(vid, pid):
    try:
        # Only available on Tildagon OS v2 or newer
        from system.hexpansion.util import get_app_by_vid_pid
        return get_app_by_vid_pid(vid, pid)
    except (ImportError, AttributeError):
        # ImportError: pre-v2 OS. AttributeError: hexpansion manager not ready.
        return None


def get_header_shim(port):
    """The hexpansion manager's own header registry -- same access
    pattern system.hexpansion.util.get_slots_by_vid_pid() itself uses
    internally, just keyed by port instead of scanned by vid/pid."""
    try:
        from system.hexpansion import app as hexpansion_app
        return hexpansion_app._hexpansion_manager.hexpansion_headers.get(port)
    except Exception:
        return None


def _get(key, default=None):
    try:
        import settings
        v = settings.get(key)
        return default if v is None else v
    except Exception:
        return default


def _set(key, value):
    try:
        import settings
        settings.set(key, value)
        settings.save()
    except Exception:
        pass


def draw_monitor_icon(ctx, cx, cy, r, state, color):
    """A simple monitor-on-a-stand icon: filled with signal bars while
    mirroring, empty while idle, crossed out when not found."""
    w = r * 1.7
    h = r * 1.1
    rr = r * 0.18
    x0, y0 = cx - w / 2, cy - h / 2

    ctx.save()
    ctx.line_width = max(1.5, r * 0.09)
    ctx.rgb(*color)

    # Body -- rounded rect, hand-rolled (ctx has no built-in roundrect).
    ctx.begin_path()
    ctx.move_to(x0 + rr, y0)
    ctx.line_to(x0 + w - rr, y0)
    ctx.arc(x0 + w - rr, y0 + rr, rr, -math.pi / 2, 0, False)
    ctx.line_to(x0 + w, y0 + h - rr)
    ctx.arc(x0 + w - rr, y0 + h - rr, rr, 0, math.pi / 2, False)
    ctx.line_to(x0 + rr, y0 + h)
    ctx.arc(x0 + rr, y0 + h - rr, rr, math.pi / 2, math.pi, False)
    ctx.line_to(x0, y0 + rr)
    ctx.arc(x0 + rr, y0 + rr, rr, math.pi, math.pi * 1.5, False)
    ctx.close_path()
    ctx.stroke()

    # Stand.
    ctx.begin_path()
    ctx.move_to(cx, y0 + h)
    ctx.line_to(cx, y0 + h + r * 0.28)
    ctx.move_to(cx - r * 0.32, y0 + h + r * 0.28)
    ctx.line_to(cx + r * 0.32, y0 + h + r * 0.28)
    ctx.stroke()

    if state == STATE_MIRRORING:
        ctx.line_width = max(1.5, r * 0.12)
        for i, bh in enumerate((0.25, 0.45, 0.65)):
            bx = cx - w * 0.22 + i * w * 0.22
            ctx.begin_path()
            ctx.move_to(bx, cy + h * 0.3)
            ctx.line_to(bx, cy + h * 0.3 - h * bh)
            ctx.stroke()
    elif state == STATE_NOT_FOUND:
        ctx.begin_path()
        ctx.move_to(x0 - r * 0.1, y0 - r * 0.1)
        ctx.line_to(x0 + w + r * 0.1, y0 + h + r * 0.1)
        ctx.stroke()

    ctx.restore()


class HDMIManager(app.App):

    def __init__(self):
        self.button_states = Buttons(self)
        self.face = 0

        self.hexp = None
        self.port = None
        self.header = None
        self._find_hexp()

        self._editing = False
        self._sel = 0

        self._led_phase = 0
        self._led_state = None

        # Non-blocking reset settle: ms left before the RP2350 is ready,
        # and whether to attach the mirror once it is.
        self._wait_ms = 0
        self._then_attach = False

        eventbus.on_async(RequestForegroundPushEvent, self._resume, self)
        eventbus.on_async(RequestForegroundPopEvent, self._pause, self)
        eventbus.on_async(HexpansionMountedEvent, self._mounted, self)
        try:
            from system.hexpansion.events import HexpansionUnmountedEvent
            eventbus.on_async(HexpansionUnmountedEvent, self._unmounted, self)
        except ImportError:
            from system.hexpansion.events import HexpansionRemovalEvent
            eventbus.on_async(HexpansionRemovalEvent, self._unmounted, self)

        eventbus.emit(PatternDisable())

    def _find_hexp(self):
        self.hexp = get_app_by_vid_pid_shim(HDMI_VID, HDMI_PID)
        if self.hexp is not None:
            config = getattr(self.hexp, "config", None)
            self.port = getattr(config, "port", None) if config else None
            self.header = get_header_shim(self.port) if self.port else None
        else:
            self.port = None
            self.header = None

    # --- lifecycle ----------------------------------------------------

    async def _resume(self, _):
        eventbus.emit(PatternDisable())
        self._led_state = None

    async def _pause(self, _):
        self._leds_off()
        eventbus.emit(PatternEnable())

    async def _mounted(self, e):
        if self.hexp is None:
            self._find_hexp()
            if self.hexp is not None and self.port == getattr(e, "port", None):
                self.header = getattr(e, "header", None) or self.header
                if _get("hdmi_auto_attach", True):
                    self._attach()

    async def _unmounted(self, e):
        if self.port is not None and getattr(e, "port", None) == self.port:
            self.hexp = None
            self.port = None
            self.header = None
            self._wait_ms = 0
            self._then_attach = False

    # --- mirror state ---------------------------------------------------

    def _state(self):
        if self.hexp is None:
            return STATE_NOT_FOUND
        try:
            if display.is_mirror_active(self.port):
                return STATE_MIRRORING
        except Exception:
            pass
        return STATE_IDLE

    def _attach(self):
        if self.port is None:
            return
        try:
            display.detach_mirror(0)
            display.attach_mirror(
                port=self.port, baudrate=HDMI_DRIVER["baudrate"], driver=HDMI_DRIVER
            )
        except Exception as e:
            print("HDMI Manager: attach failed:", e)

    def _reset_rp2350(self):
        """Detach, then pulse LS_A low to hardware-reset the RP2350, then
        wait RESET_SETTLE_MS (in update(), not blocking) before returning
        to idle. Returns False if the pin isn't available."""
        try:
            ls_a = self.hexp.config.ls_pin[0]
        except Exception as e:
            print("HDMI Manager: no LS_A pin:", e)
            return False
        self._detach()
        try:
            ls_a.init(ls_a.OUT)
            ls_a.value(0)
            time.sleep_ms(RESET_PULSE_MS)
            ls_a.init(ls_a.IN)  # release: RUN's own pull-up takes over
        except Exception as e:
            print("HDMI Manager: LS_A reset failed:", e)
            return False
        self._wait_ms = RESET_SETTLE_MS
        return True

    def _attach_with_reset(self):
        """Reset the RP2350, then attach once it has settled. If LS_A
        can't be pulsed, fall back to a plain attach."""
        if self._reset_rp2350():
            self._then_attach = True
        else:
            self._attach()

    def _detach(self):
        try:
            display.detach_mirror(0)
        except Exception as e:
            print("HDMI Manager: detach failed:", e)

    # --- update -----------------------------------------------------------

    def update(self, delta):
        b = self.button_states
        if self._wait_ms > 0:
            self._wait_ms -= delta
            if self._wait_ms <= 0:
                self._wait_ms = 0
                if self._then_attach:
                    self._then_attach = False
                    self._attach()
        if b.get(BUTTON_TYPES["CANCEL"]):
            b.clear()
            if self._editing:
                self._editing = False
            else:
                self._leds_off()
                self.minimise()
                return

        if self._editing:
            n = len(self._menu_items())
            if b.get(BUTTON_TYPES["UP"]):
                self._sel = (self._sel - 1) % n
                b.clear()
            if b.get(BUTTON_TYPES["DOWN"]):
                self._sel = (self._sel + 1) % n
                b.clear()
            if b.get(BUTTON_TYPES["LEFT"]) or b.get(BUTTON_TYPES["RIGHT"]):
                self._menu_toggle()
                b.clear()
            if b.get(BUTTON_TYPES["CONFIRM"]):
                self._editing = False
                b.clear()
        else:
            # Latched reads: one tile step per physical press, never
            # repeating while a button is held or an event is stuck.
            step = 0
            if b.pressed(BUTTON_TYPES["RIGHT"]):
                step += 1
            if b.pressed(BUTTON_TYPES["LEFT"]):
                step -= 1
            if step:
                self.face = (self.face + step) % len(FACES)
            reset_req = b.pressed(BUTTON_TYPES["UP"])
            if b.pressed(BUTTON_TYPES["CONFIRM"]):
                if FACES[self.face] == "status":
                    if self._wait_ms > 0:
                        pass  # RP2350 still rebooting
                    elif self._state() == STATE_MIRRORING:
                        self._detach()
                    elif self.hexp is not None:
                        self._attach_with_reset()
                elif FACES[self.face] == "info":
                    self._editing = True
                    self._sel = 0
            if (reset_req and FACES[self.face] == "status"
                    and self.hexp is not None and self._wait_ms == 0):
                self._reset_rp2350()

        self._update_leds(delta)

    def _menu_items(self):
        return [("hdmi_auto_attach", "Auto-attach", _get("hdmi_auto_attach", True))]

    def _menu_toggle(self):
        key, label, cur = self._menu_items()[self._sel]
        _set(key, not cur)

    # --- LEDs ---------------------------------------------------------

    def _update_leds(self, delta):
        state = self._state()
        if state == STATE_MIRRORING:
            col = (0, 70, 20)
        elif state == STATE_IDLE:
            self._led_phase = (self._led_phase + delta) % 1200
            b_ = 0.15 + 0.85 * (0.5 * (1 + math.sin(2 * math.pi * self._led_phase / 1200)))
            col = (int(80 * b_), int(50 * b_), 0)
        else:
            col = (0, 0, 0)
        if col == self._led_state and state != STATE_IDLE:
            return
        self._led_state = col
        try:
            for i in range(12):
                tildagonos.leds[i + 1] = col
            tildagonos.leds.write()
        except Exception:
            pass

    def _leds_off(self):
        try:
            for i in range(12):
                tildagonos.leds[i + 1] = (0, 0, 0)
            tildagonos.leds.write()
        except Exception:
            pass
        self._led_state = None

    # --- draw -------------------------------------------------------------

    def draw(self, ctx):
        ctx.rgb(0.05, 0.06, 0.10).rectangle(-120, -120, 240, 240).fill()
        f = FACES[self.face]
        if f == "status":
            self._draw_status(ctx)
        else:
            self._draw_info(ctx)

    def _draw_status(self, ctx):
        state = self._state()
        ctx.save()
        draw_monitor_icon(ctx, 0, -20, 46, state, STATE_COLOR[state])
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.font_size = 20
        if self._wait_ms > 0:
            ctx.rgb(*STATE_COLOR[STATE_IDLE]).move_to(0, 50).text("Resetting...")
        else:
            ctx.rgb(*STATE_COLOR[state]).move_to(0, 50).text(STATE_LABEL[state])
        if self.port is not None:
            ctx.font_size = 14
            ctx.rgb(0.7, 0.7, 0.8).move_to(0, 70).text("Port %d" % self.port)
        ctx.font_size = 14
        if self.hexp is None:
            ctx.rgb(0.6, 0.6, 0.6).move_to(0, 90).text("Plug in HDMI-HEX")
        else:
            ctx.font_size = 12
            ctx.rgb(0.9, 0.9, 0.9).move_to(0, 90).text(
                "OK: Detach   Up: Reset"
                if state == STATE_MIRRORING
                else "OK: Attach   Up: Reset"
            )
        ctx.restore()

    def _draw_info(self, ctx):
        ctx.save()
        ctx.text_align = ctx.CENTER
        ctx.text_baseline = ctx.MIDDLE
        ctx.rgb(0.8, 0.85, 1.0)
        ctx.font_size = 18
        ctx.move_to(0, -92).text("HDMI-HEX")

        h = self.header
        name = getattr(h, "friendly_name", None) if h else None
        unique = getattr(h, "unique_id", None) if h else None
        rows = [
            ("VID", "0x%04X" % HDMI_VID),
            ("PID", "0x%04X" % HDMI_PID),
            ("Name", name or "--"),
            ("Unique", str(unique) if unique is not None else "--"),
        ]
        ctx.font_size = 14
        y = -60
        for label, val in rows:
            ctx.text_align = ctx.LEFT
            ctx.rgb(0.5, 0.6, 0.8).move_to(-82, y).text(label)
            ctx.text_align = ctx.RIGHT
            ctx.rgb(0.9, 0.9, 0.9).move_to(82, y).text(val)
            y += 20

        items = self._menu_items()
        y = 30
        for idx, (key, label, cur) in enumerate(items):
            sel = self._editing and idx == self._sel
            if sel:
                ctx.rgb(0.16, 0.26, 0.42)
                ctx.rectangle(-94, y - 13, 188, 26).fill()
            ctx.text_align = ctx.LEFT
            ctx.rgb(0.65, 0.8, 1.0).move_to(-78, y).text(label)
            ctx.text_align = ctx.RIGHT
            ctx.rgb(*((1.0, 0.95, 0.4) if sel else (0.92, 0.92, 0.92)))
            ctx.move_to(78, y).text("on" if cur else "off")
            y += 26

        ctx.font_size = 11
        ctx.rgb(0.55, 0.6, 0.7)
        ctx.text_align = ctx.CENTER
        if self._editing:
            ctx.move_to(0, 94).text("Up/Dn pick   L/R toggle   OK done")
        else:
            ctx.move_to(0, 94).text("OK to edit")
        ctx.restore()


__app_export__ = HDMIManager  # pylint: disable=invalid-name
