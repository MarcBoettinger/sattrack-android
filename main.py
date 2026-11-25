"""Sat Track for Android: calls the same code as tools/sattrack/sattrack.py,
with the radio on the phone's USB port (USB-C OTG) instead of a COM port.
UNTESTED on a device: see README.md."""
import contextlib
import os
import sys
import threading
import time
from types import SimpleNamespace

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Python on Android has no system CA list: use the one from certifi for https.
try:
    import ssl
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    ssl._create_default_https_context = lambda *a, **k: ssl.create_default_context(cafile=certifi.where())
except Exception:                                # noqa: BLE001
    pass

import traceback                              # noqa: E402

IMPORT_ERROR = None
try:
    import sattrack as st                       # noqa: E402
    from satlib import catalog                   # noqa: E402
    from satlib.config import Config             # noqa: E402
    from satlib.fmt import MODES, NAME_LEN, radio_ok  # noqa: E402
    from satlib.radio import Radio               # noqa: E402
except BaseException:                             # noqa: BLE001  incl. SystemExit; shown on screen
    IMPORT_ERROR = traceback.format_exc()


def _android_get(url, timeout=20):
    """HTTP GET through Android's own network stack (system certificates, DNS, IPv6)."""
    from jnius import autoclass, cast
    URL = autoclass("java.net.URL")
    Scanner = autoclass("java.util.Scanner")
    conn = cast("java.net.HttpURLConnection", URL(url).openConnection())
    ms = int(timeout * 1000)
    conn.setConnectTimeout(ms)
    conn.setReadTimeout(ms)
    conn.setRequestProperty("User-Agent", "sattrack (UV-K5 Sat Track)")
    try:
        code = conn.getResponseCode()
        if code >= 400:
            raise IOError(f"HTTP {code} from {url.split('/')[2]}")
        sc = Scanner(conn.getInputStream(), "UTF-8").useDelimiter("\\A")
        return sc.next() if sc.hasNext() else ""
    finally:
        conn.disconnect()


def _use_android_http(catalog_module):
    """Replace catalog._get by the Android version when running on a phone."""
    try:
        import jnius  # noqa: F401
    except ImportError:
        return
    original = catalog_module._get

    def _get(url, timeout=20):
        try:
            return _android_get(url, timeout)
        except Exception as e:                   # noqa: BLE001
            raise IOError(f"network error: {type(e).__name__}: {e}") from None
    _get.original = original
    catalog_module._get = _get


if IMPORT_ERROR is None:
    _use_android_http(catalog)


def error_screen(text):
    from kivy.uix.label import Label as L
    from kivy.uix.scrollview import ScrollView as SV
    lab = L(text="Sat Track could not start. Send a screenshot of this.\n\n" + text,
            size_hint_y=None, halign="left", valign="top")
    lab.bind(width=lambda *_: setattr(lab, "text_size", (lab.width, None)),
             texture_size=lambda *_: setattr(lab, "height", lab.texture_size[1]))
    sv = SV()
    sv.add_widget(lab)
    return sv


class NeedPermission(Exception):
    pass


def open_usb_serial():
    """The first USB serial device (the radio), as a pyserial-like object."""
    from usb4a import usb
    from usbserial4a import serial4a
    devs = usb.get_usb_device_list()
    if not devs:
        raise IOError("No USB device. Connect the radio with a USB-C cable (OTG) "
                      "and switch it on.")
    dev = devs[0]
    if not usb.has_usb_permission(dev):
        usb.request_usb_permission(dev)
        raise NeedPermission("Allow USB access in the Android dialog, then tap the button again.")
    ser = serial4a.get_serial_port(dev.getDeviceName(), 38400, 8, "N", 1, timeout=0.05)
    if ser is None or not ser.is_open:
        raise IOError("Could not open the USB serial port.")
    return ser


class LogWriter:
    """stdout of the sattrack functions -> the log; '\\r' lines go to the status line."""
    def __init__(self, app):
        self.app, self.buf = app, ""

    def write(self, s):
        self.buf += s
        while True:
            i_n, i_r = self.buf.find("\n"), self.buf.find("\r")
            hits = [i for i in (i_n, i_r) if i >= 0]
            if not hits:
                break
            i = min(hits)
            line, ch, self.buf = self.buf[:i], self.buf[i], self.buf[i + 1:]
            if ch == "\n":
                self.app.log(line)
            elif line.strip():
                self.app.set_status(line.strip())
        return len(s)

    def flush(self):
        pass


class SatTrackApp(App):
    title = "Sat Track 6.3"

    def build(self):
        if IMPORT_ERROR:
            return error_screen(IMPORT_ERROR)
        try:
            return self._build()
        except Exception:                        # noqa: BLE001
            return error_screen(traceback.format_exc())

    def _build(self):
        self._fix_timezone()
        Window.softinput_mode = "below_target"
        self.cfg = Config(os.path.join(self.user_data_dir, "config.json"))
        self.busy = False
        root = BoxLayout(orientation="vertical", padding=6, spacing=6)

        loc = self.cfg.data.get("location") or {}
        row = GridLayout(cols=4, size_hint_y=None, height=96, spacing=4)
        self.lat = TextInput(text=str(loc.get("lat", "")), hint_text="Lat", multiline=False, input_filter="float")
        self.lon = TextInput(text=str(loc.get("lon", "")), hint_text="Lon", multiline=False, input_filter="float")
        self.alt = TextInput(text=str(loc.get("alt", 0)), hint_text="Alt m", multiline=False, input_filter="float")
        self.minel = TextInput(text=str(self.cfg.data.get("min_el", 15)), hint_text="Min el", multiline=False, input_filter="float")
        for w in (Label(text="Lat", size_hint_y=None, height=32), Label(text="Lon", size_hint_y=None, height=32),
                  Label(text="Alt m", size_hint_y=None, height=32), Label(text="Min el", size_hint_y=None, height=32),
                  self.lat, self.lon, self.alt, self.minel):
            row.add_widget(w)
        root.add_widget(row)

        add = BoxLayout(size_hint_y=None, height=56, spacing=4)
        self.query = TextInput(hint_text="NORAD id or name, e.g. 25544", multiline=False)
        add.add_widget(self.query)
        add.add_widget(self._btn("Add", self.on_add))
        root.add_widget(add)

        grid = GridLayout(cols=2, size_hint_y=None, height=3 * 64, spacing=4)
        for text, fn in (("Save settings", self.on_save), ("Satellites", self.on_sats),
                         ("Passes", self.on_passes), ("Load next 16 passes", self.on_load),
                         ("On the radio", self.on_radio), ("Calibrate clock", self.on_cal)):
            grid.add_widget(self._btn(text, fn))
        root.add_widget(grid)
        root.add_widget(self._btn("Install SatTrack.app (bundled)", self.on_install, h=56))

        self.statusl = Label(text="", size_hint_y=None, height=28)
        root.add_widget(self.statusl)
        self.logl = Label(text="", size_hint_y=None, halign="left", valign="top", markup=False)
        self.logl.bind(width=lambda *_: setattr(self.logl, "text_size", (self.logl.width, None)),
                       texture_size=lambda *_: setattr(self.logl, "height", self.logl.texture_size[1]))
        sv = ScrollView()
        sv.add_widget(self.logl)
        self.sv = sv
        root.add_widget(sv)
        self.log("Sat Track. Set location, add satellites, connect the radio by USB-C.")
        return root

    @staticmethod
    def _fix_timezone():
        """Python on Android often runs in UTC; the pass times on the radio are local time."""
        try:
            from jnius import autoclass
            tz = autoclass("java.util.TimeZone").getDefault().getID()
            if tz:
                os.environ["TZ"] = str(tz)
                time.tzset()
        except Exception:                        # noqa: BLE001  not on Android, or no jnius
            pass

    def _btn(self, text, fn, h=None):
        b = Button(text=text, size_hint_y=None, height=h or 56)
        b.bind(on_release=lambda *_: fn())
        return b

    # ---- ui helpers (any thread) ----
    def log(self, text):
        Clock.schedule_once(lambda dt: self._log(text))

    def _log(self, text):
        self.logl.text += text + "\n"
        self.sv.scroll_y = 0

    def set_status(self, text):
        Clock.schedule_once(lambda dt: setattr(self.statusl, "text", text))

    # ---- settings ----
    def on_save(self):
        try:
            self.cfg.data["location"] = {"lat": float(self.lat.text), "lon": float(self.lon.text),
                                         "alt": float(self.alt.text or 0)}
            self.cfg.data["min_el"] = float(self.minel.text or 15)
            self.cfg.save()
            self.log("Saved.")
        except ValueError:
            self.log("Latitude, longitude, altitude and min elevation must be numbers.")

    # ---- worker ----
    def run_job(self, fn, needs_radio=False, needs_loc=True):
        if self.busy:
            self.log("Busy, wait for the current job.")
            return
        if needs_loc and not self.cfg.data.get("location"):
            self.on_save()
            if not self.cfg.data.get("location"):
                return
        self.busy = True

        def job():
            ser = None
            w = LogWriter(self)
            try:
                if needs_radio:
                    ser = open_usb_serial()
                    radio = Radio("usb", ser=ser)
                    self.log(f"Radio: firmware {radio.version}")
                    st.need_port = lambda cfg, args: radio
                with contextlib.redirect_stdout(w):
                    fn()
                w.write("\n")
            except NeedPermission as e:
                self.log(str(e))
            except Exception as e:  # noqa: BLE001
                self.log(f"Error: {type(e).__name__}: {e}")
                self.log("".join(traceback.format_exc().splitlines(True)[-8:]))
            finally:
                try:
                    ser.close()
                except Exception:  # noqa: BLE001
                    pass
                self.set_status("")
                self.busy = False
        threading.Thread(target=job, daemon=True).start()

    # ---- actions ----
    def on_sats(self):
        self.run_job(lambda: st.cmd_sats(self.cfg), needs_loc=False)

    def on_passes(self):
        self.run_job(lambda: st.cmd_passes(self.cfg, SimpleNamespace(sat=None, hours=48, min_el=None)))

    def on_load(self):
        self.run_job(lambda: st.cmd_load(self.cfg, SimpleNamespace(
            sat=None, hours=48, min_el=None, pick="next", yes=True, out=None, port=None)), needs_radio=True)

    def on_radio(self):
        self.run_job(lambda: st.cmd_radio(self.cfg, SimpleNamespace(port=None)), needs_radio=True)

    def on_cal(self):
        self.run_job(lambda: st.cmd_calibrate(self.cfg, SimpleNamespace(seconds=120, port=None)), needs_radio=True)

    def on_install(self):
        f = os.path.join(HERE, "SatTrack.app")
        self.run_job(lambda: st.cmd_install(self.cfg, SimpleNamespace(files=[f], port=None)), needs_radio=True)

    def on_add(self):
        q = self.query.text.strip()
        if q:
            self.run_job(lambda: self._add(q), needs_loc=False)

    def _add(self, query):
        """Like 'sattrack add', without questions: first transmitter the radio can use."""
        print("Searching CelesTrak ...")
        found = catalog.search(query)
        if not found:
            print(f"Nothing found for '{query}'.")
            return
        if len(found) > 1:
            for norad, name, _, _ in found[:10]:
                print(f"  {norad:>6}  {name}")
            print("Several matches: enter the NORAD id of one of them.")
            return
        norad, name, l1, l2 = found[0]
        self.cfg.data.setdefault("tle_cache", {})[str(norad)] = {"l1": l1, "l2": l2, "fetched": time.time()}
        try:
            txs = catalog.transmitters(norad)
        except Exception as e:  # noqa: BLE001
            print(f"Could not reach SatNOGS ({e}).")
            return
        usable = [t for t in txs if radio_ok(t["downlink"]) and t["mode"] in MODES]
        if not usable:
            print("SatNOGS lists no transmitter this radio can use. Add it on the PC with frequencies.")
            return
        usable.sort(key=lambda t: (0 if t["uplink"] and t["raw_mode"] == "FM" else 1 if t["mode"] == "FM" else 2))
        t = usable[0]
        entry = {"norad": norad, "downlink": round(t["downlink"], 5), "uplink": round(t["uplink"], 5),
                 "mode": t["mode"], "ctcss": 0}
        n = st.short_name(name)[:NAME_LEN]
        if self.cfg.find(n):
            print(f"You already have a satellite called {n}.")
            return
        entry["name"] = n
        self.cfg.sats.append(entry)
        self.cfg.save()
        print("Added " + st.describe(entry))
        print("(Uplink CTCSS tone is set to none; change it on the PC if the satellite needs one.)")


if __name__ == "__main__":
    SatTrackApp().run()
