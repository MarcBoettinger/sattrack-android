"""Sat Track for Android: the sattrack.py logic with a touch screen, radio on the phone's USB-C."""
import contextlib
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from types import SimpleNamespace

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import NoTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.gridlayout import GridLayout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Python on Android has no system CA list; used only if Android's own HTTP is unavailable.
try:
    import ssl
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    ssl._create_default_https_context = lambda *a, **k: ssl.create_default_context(cafile=certifi.where())
except Exception:                                # noqa: BLE001
    pass

FW_KW = {}                    # extra arguments for Radio(): command set of the firmware
APP_NAMES = ("SatTrack.app",)            # SatTrack.app files looked for next to main.py
FS = sp(17)

IMPORT_ERROR = None
try:
    import appcore
    import sattrack as st
    from satlib import catalog
    from satlib.config import Config
    from satlib.fmt import CTCSS_TONES, MODES, NAME_LEN, PASS_COUNT, radio_ok
    from satlib.radio import Radio
except BaseException:                             # noqa: BLE001  incl. SystemExit; shown on screen
    IMPORT_ERROR = traceback.format_exc()


class NeedPermission(Exception):
    pass


def error_screen(text):
    lab = Label(text="Sat Track konnte nicht starten. Bitte Screenshot senden.\n\n" + text,
                size_hint_y=None, halign="left", valign="top", font_size=sp(13))
    lab.bind(width=lambda *_: setattr(lab, "text_size", (lab.width, None)),
             texture_size=lambda *_: setattr(lab, "height", lab.texture_size[1]))
    sv = ScrollView()
    sv.add_widget(lab)
    return sv


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
            raise appcore.HttpStatus(code, url.split("/")[2])
        sc = Scanner(conn.getInputStream(), "UTF-8").useDelimiter("\\A")
        return sc.next() if sc.hasNext() else ""
    finally:
        conn.disconnect()


def open_usb_serial():
    """The first USB serial device (the radio), as a pyserial-like object."""
    from usb4a import usb
    from usbserial4a import serial4a
    devs = usb.get_usb_device_list()
    if not devs:
        raise IOError("Kein USB-Gerät gefunden. Funkgerät per USB-C (OTG) anschließen und einschalten.")
    dev = devs[0]
    if not usb.has_usb_permission(dev):
        usb.request_usb_permission(dev)
        raise NeedPermission("USB-Zugriff im Android-Dialog erlauben, dann die Taste noch einmal drücken.")
    ser = serial4a.get_serial_port(dev.getDeviceName(), 38400, 8, "N", 1, timeout=0.05)
    if ser is None or not ser.is_open:
        raise IOError("Der USB-Seriell-Port konnte nicht geöffnet werden.")
    return ser


class LogWriter:
    """stdout of the sattrack functions -> the log; '\\r' lines go to the status line."""
    def __init__(self, app):
        self.app, self.buf = app, ""

    def write(self, s):
        self.buf += s
        while True:
            hits = [i for i in (self.buf.find("\n"), self.buf.find("\r")) if i >= 0]
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

    def finish(self):
        if self.buf.strip():
            self.app.log(self.buf.strip())
        self.buf = ""


# ------------------------------------------------------------ small widgets --
def btn(text, cb, h=56, w=None, **kw):
    b = Button(text=text, font_size=FS, size_hint_y=None, height=dp(h), halign="center", **kw)
    if w:
        b.size_hint_x, b.width = None, dp(w)
    b.bind(size=lambda wd, s: setattr(wd, "text_size", (s[0] - dp(12), None)))
    b.bind(on_release=lambda *_: cb())
    return b


def field(hint, text="", filt=None):
    return TextInput(text=text, hint_text=hint, multiline=False, font_size=FS, size_hint_y=None,
                     height=dp(52), input_filter=filt, write_tab=False, padding=(dp(10), dp(12)))


def wlabel(text, bold=False, h_pad=8):
    """Left aligned label that wraps and grows in height."""
    lab = Label(text=text, font_size=FS, bold=bold, size_hint_y=None, halign="left", valign="middle")
    lab.bind(width=lambda *_: setattr(lab, "text_size", (lab.width, None)),
             texture_size=lambda *_: setattr(lab, "height", max(dp(34), lab.texture_size[1] + dp(h_pad))))
    return lab


def rowlabel(text):
    """Label that fills a row (the row sets the height)."""
    lab = Label(text=text, font_size=FS, halign="left", valign="middle")
    lab.bind(size=lambda w, s: setattr(w, "text_size", (s[0], s[1])))
    return lab


def scroll_list():
    sv = ScrollView(do_scroll_x=False)
    g = GridLayout(cols=1, size_hint_y=None, spacing=dp(6), padding=dp(4))
    g.bind(minimum_height=g.setter("height"))
    sv.add_widget(g)
    return sv, g


class SatTrackApp(App):
    title = "Sat Track"

    # ------------------------------------------------------------ building --
    def build(self):
        if IMPORT_ERROR:
            return error_screen(IMPORT_ERROR)
        try:
            return self._build()
        except BaseException:                    # noqa: BLE001
            return error_screen(traceback.format_exc())

    def _build(self):
        self._fix_timezone()
        Window.softinput_mode = "below_target"
        self.cfg = Config(os.path.join(self.user_data_dir, "config.json"))
        self.busy, self.passes, self.obs, self.toggles, self.radio = False, [], None, [], None
        self.lines = []
        self._install_net()

        root = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(4))
        self.sm = ScreenManager(transition=NoTransition())
        for scr in (self._sat_screen(), self._pass_screen(), self._radio_screen(), self._settings_screen()):
            self.sm.add_widget(scr)
        root.add_widget(self.sm)

        self.statusl = Label(text="", font_size=sp(14), size_hint_y=None, height=dp(24))
        root.add_widget(self.statusl)
        self.logl = Label(text="", font_size=sp(14), size_hint_y=None, halign="left", valign="top")
        self.logl.bind(width=lambda *_: setattr(self.logl, "text_size", (self.logl.width, None)),
                       texture_size=lambda *_: setattr(self.logl, "height", self.logl.texture_size[1]))
        self.logsv = ScrollView(size_hint_y=None, height=dp(150), do_scroll_x=False)
        self.logsv.add_widget(self.logl)
        root.add_widget(self.logsv)

        nav = BoxLayout(size_hint_y=None, height=dp(58), spacing=dp(4))
        for i, (name, text) in enumerate((("sats", "Satelliten"), ("passes", "Überflüge"),
                                          ("radio", "Funkgerät"), ("settings", "Einstellungen"))):
            tb = ToggleButton(text=text, group="nav", allow_no_selection=False, font_size=sp(15),
                              state="down" if i == 0 else "normal")
            tb.bind(on_release=lambda w, n=name: self.go(n))
            nav.add_widget(tb)
        root.add_widget(nav)
        self.log("Sat Track bereit. Standort unter Einstellungen, Satelliten hinzufügen, "
                 "dann Überflüge berechnen und laden.")
        return root

    def go(self, name):
        self.sm.current = name

    @staticmethod
    def _fix_timezone():
        """Python on Android often runs in UTC; the pass times on the radio are local time."""
        try:
            from jnius import autoclass
            tz = autoclass("java.util.TimeZone").getDefault().getID()
            if tz:
                os.environ["TZ"] = str(tz)
                time.tzset()
        except Exception:                        # noqa: BLE001
            pass

    def _install_net(self):
        try:
            import jnius  # noqa: F401
        except ImportError:
            return                                # not on Android: keep urllib

        def fetch(url, timeout=20):
            try:
                return _android_get(url, timeout)
            except appcore.HttpStatus:
                raise
            except Exception as e:               # noqa: BLE001
                raise IOError(f"Netzwerkfehler: {type(e).__name__}: {e}") from None
        catalog._get = appcore.NetGuard(fetch, self.cfg.data.setdefault("net_fail", {}), self.cfg.save)

    # -------------------------------------------------------------- screens --
    def _sat_screen(self):
        s = Screen(name="sats")
        box = BoxLayout(orientation="vertical", spacing=dp(6))
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(6))
        self.query = field("NORAD-ID oder Name, z.B. 25544")
        row.add_widget(self.query)
        row.add_widget(btn("Suchen", self.on_search, h=52, w=110))
        box.add_widget(row)
        self.sat_scroll, self.sat_grid = scroll_list()
        box.add_widget(self.sat_scroll)
        s.add_widget(box)
        self.show_saved()
        return s

    def _pass_screen(self):
        s = Screen(name="passes")
        box = BoxLayout(orientation="vertical", spacing=dp(6))
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(6))
        self.hours = field("Stunden", "48", "int")
        self.hours.size_hint_x, self.hours.width = None, dp(80)
        row.add_widget(Label(text="Stunden", font_size=FS, size_hint_x=None, width=dp(86)))
        row.add_widget(self.hours)
        row.add_widget(btn("Überflüge berechnen", self.on_passes, h=52))
        box.add_widget(row)
        self.sel_label = wlabel("Noch nichts berechnet.")
        box.add_widget(self.sel_label)
        self.pass_scroll, self.pass_grid = scroll_list()
        box.add_widget(self.pass_scroll)
        row2 = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(6))
        row2.add_widget(btn("Nächste 16", self.sel_next, h=52))
        row2.add_widget(btn("Keine", self.sel_none, h=52))
        box.add_widget(row2)
        box.add_widget(btn("Gewählte auf Funkgerät laden", self.on_load, h=60))
        s.add_widget(box)
        return s

    def _radio_screen(self):
        s = Screen(name="radio")
        sv, g = scroll_list()
        g.add_widget(wlabel("Funkgerät per USB-C (OTG-Kabel) anschließen, am Hauptbildschirm, "
                            "und beim ersten Mal den USB-Zugriff erlauben."))
        for text, cb in (("Was ist auf dem Funkgerät?", self.on_radio),
                         ("Uhr kalibrieren (2 Minuten)", self.on_cal),
                         ("SatTrack-App installieren", self.on_install),
                         ("Alle Überflüge vom Funkgerät löschen", self.on_clear)):
            g.add_widget(btn(text, cb, h=64))
        s.add_widget(sv)
        return s

    def _settings_screen(self):
        s = Screen(name="settings")
        sv, g = scroll_list()
        loc = self.cfg.data.get("location") or {}
        g.add_widget(wlabel("Dein Standort (Dezimalgrad, z.B. 50.1109 und 8.6821)", bold=True))
        self.lat = field("Breitengrad", str(loc.get("lat", "")), "float")
        self.lon = field("Längengrad", str(loc.get("lon", "")), "float")
        self.alt = field("Höhe in Metern", str(loc.get("alt", 0)), "float")
        self.minel = field("Mindest-Elevation in Grad", str(self.cfg.data.get("min_el", 15)), "float")
        for lab, w in (("Breite", self.lat), ("Länge", self.lon), ("Höhe (m)", self.alt),
                       ("Mindest-Elevation (Grad)", self.minel)):
            g.add_widget(wlabel(lab))
            g.add_widget(w)
        g.add_widget(btn("Speichern", self.on_save, h=60))
        g.add_widget(wlabel(f"Firmware-Befehlssatz: {FW_KW.get('fw', '6.3')}"))
        s.add_widget(sv)
        return s

    # ------------------------------------------------------------ ui helpers --
    def log(self, text):
        Clock.schedule_once(lambda dt: self._log(text))

    def _log(self, text):
        self.lines.append(text)
        self.lines = self.lines[-150:]
        self.logl.text = "\n".join(self.lines)
        self.logsv.scroll_y = 0

    def set_status(self, text):
        Clock.schedule_once(lambda dt: setattr(self.statusl, "text", text))

    def confirm(self, text, yes):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        lab = Label(text=text, font_size=FS, halign="center", valign="middle")
        lab.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        box.add_widget(lab)
        row = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(10))
        pop = Popup(title="Bitte bestätigen", content=box, size_hint=(0.92, 0.5), auto_dismiss=False)
        row.add_widget(btn("Abbrechen", pop.dismiss))
        row.add_widget(btn("Ja", lambda: (pop.dismiss(), yes())))
        box.add_widget(row)
        pop.open()

    # ------------------------------------------------------------ job runner --
    def run_job(self, fn, needs_radio=False, needs_loc=False, done=None):
        if self.busy:
            self.log("Bitte warten, es läuft noch ein Vorgang.")
            return
        if needs_loc and not self.cfg.data.get("location"):
            self.log("Zuerst den Standort unter Einstellungen eintragen und speichern.")
            self.go("settings")
            return
        self.busy = True

        def job():
            ser, w, result, ok = None, LogWriter(self), None, False
            try:
                if needs_radio:
                    ser = open_usb_serial()
                    radio = Radio("usb", ser=ser, **FW_KW)
                    self.radio = radio
                    self.log(f"Funkgerät gefunden, Firmware {radio.version}")
                    st.need_port = lambda cfg, args: radio
                with contextlib.redirect_stdout(w):
                    result = fn()
                ok = True
            except NeedPermission as e:
                self.log(str(e))
            except BaseException as e:           # noqa: BLE001
                self.log(f"Fehler: {type(e).__name__}: {e}")
                self.log("".join(traceback.format_exc().splitlines(True)[-6:]).rstrip())
            finally:
                w.finish()
                try:
                    if ser is not None:
                        ser.close()
                except Exception:                # noqa: BLE001
                    pass
                self.set_status("")
                self.busy = False
            if ok and done:
                Clock.schedule_once(lambda dt: done(result))
        threading.Thread(target=job, daemon=True).start()

    # ------------------------------------------------------------- satellites --
    def show_saved(self):
        g = self.sat_grid
        g.clear_widgets()
        g.add_widget(wlabel(f"Meine Satelliten ({len(self.cfg.sats)})", bold=True))
        if not self.cfg.sats:
            g.add_widget(wlabel("Noch keine. Oben die NORAD-ID eingeben (ISS: 25544) und Suchen."))
        for s in list(self.cfg.sats):
            row = BoxLayout(size_hint_y=None, height=dp(68), spacing=dp(6))
            row.add_widget(rowlabel(st.describe(s)))
            row.add_widget(btn("Entfernen", lambda s=s: self.remove_sat(s), h=68, w=120))
            g.add_widget(row)

    def remove_sat(self, s):
        self.cfg.sats.remove(s)
        self.cfg.save()
        self.log(f"Entfernt: {s['name']}")
        self.show_saved()

    def on_search(self):
        q = self.query.text.strip()
        if not q:
            self.log("Bitte NORAD-ID oder Namen eingeben.")
            return
        self.log(f"Suche '{q}' bei CelesTrak ...")
        self.run_job(lambda: catalog.search(q), done=self._after_search)

    def _after_search(self, found):
        if not found:
            self.log("Nichts gefunden.")
        elif len(found) > 1:
            g = self.sat_grid
            g.clear_widgets()
            g.add_widget(wlabel("Mehrere Treffer, bitte wählen:", bold=True))
            for item in found[:20]:
                g.add_widget(btn(f"{item[0]}   {item[1]}", lambda it=item: self.pick_satellite(it)))
            if len(found) > 20:
                g.add_widget(wlabel(f"... und {len(found) - 20} weitere. Genauer suchen oder NORAD-ID nehmen."))
            g.add_widget(btn("Abbrechen", self.show_saved))
        else:
            self.pick_satellite(found[0])

    def pick_satellite(self, item):
        norad, name, l1, l2 = item
        self.cfg.data.setdefault("tle_cache", {})[str(norad)] = {"l1": l1, "l2": l2, "fetched": time.time()}
        self.cfg.save()
        self.log(f"Gefunden: {norad} {name}. Hole Frequenzen von SatNOGS ...")

        def fetch():
            try:
                return catalog.transmitters(norad)
            except Exception as e:               # noqa: BLE001
                print(f"SatNOGS nicht erreichbar ({e}). Frequenzen bitte selbst eingeben.")
                return []
        self.run_job(fetch, done=lambda txs: self.show_transmitters(item, txs))

    def show_transmitters(self, item, txs):
        usable = appcore.usable_transmitters(txs, radio_ok, MODES)
        g = self.sat_grid
        g.clear_widgets()
        g.add_widget(wlabel(f"{item[1]} ({item[0]}): welche Frequenz?", bold=True))
        if txs and not usable:
            g.add_widget(wlabel("SatNOGS nennt keinen Sender, den das Funkgerät nutzen kann "
                                "(FM, AM, USB, CW; 18-1300 MHz)."))
        elif usable:
            g.add_widget(wlabel("Aus SatNOGS. Bitte prüfen, ob der Sender noch aktiv ist."))
        for t in usable:
            b = btn(appcore.tx_label(t), lambda t=t: self.show_form(item, t), h=76)
            g.add_widget(b)
        g.add_widget(btn("Frequenzen selbst eingeben", lambda: self.show_form(item, None), h=60))
        g.add_widget(btn("Abbrechen", self.show_saved, h=60))

    def show_form(self, item, tx):
        norad, name = item[0], item[1]
        g = self.sat_grid
        g.clear_widgets()
        g.add_widget(wlabel(f"{name} ({norad})", bold=True))
        fd = fu = fm = None
        if tx:
            up = f"   Up {tx['uplink']:.3f} MHz" if tx["uplink"] else "   (nur Empfang)"
            g.add_widget(wlabel(f"Down {tx['downlink']:.3f} MHz{up}   {tx['mode']}"))
        else:
            fd, fu, fm = field("Downlink MHz", "", "float"), field("Uplink MHz (0 = keiner)", "0", "float"), \
                field("Modus: FM, AM, USB oder CW", "FM")
            for lab, w in (("Downlink (MHz)", fd), ("Uplink (MHz)", fu), ("Modus", fm)):
                g.add_widget(wlabel(lab))
                g.add_widget(w)
        g.add_widget(wlabel(f"Name am Funkgerät (höchstens {NAME_LEN} Zeichen)"))
        fn = field("Name", st.short_name(name)[:NAME_LEN])
        g.add_widget(fn)
        g.add_widget(wlabel("CTCSS-Ton für den Uplink in Hz (0 = keiner)"))
        fc = field("CTCSS", "0", "float")
        g.add_widget(fc)

        def save():
            try:
                t = tx or {"downlink": appcore.parse_mhz(fd.text), "uplink": appcore.parse_mhz(fu.text),
                           "mode": fm.text.strip()}
                entry = appcore.make_entry(norad, t, fn.text, appcore.parse_mhz(fc.text))
            except ValueError:
                self.log("Bitte Zahlen eingeben (Punkt als Komma).")
                return
            err = appcore.validate_entry(self.cfg, entry, radio_ok, MODES, CTCSS_TONES, NAME_LEN)
            if err:
                self.log(err)
                return
            self.cfg.sats.append(entry)
            self.cfg.save()
            self.log("Hinzugefügt: " + st.describe(entry))
            self.show_saved()
        g.add_widget(btn("Speichern", save, h=60))
        g.add_widget(btn("Abbrechen", self.show_saved, h=60))

    # ----------------------------------------------------------------- passes --
    def on_passes(self):
        try:
            hours = max(1, int(self.hours.text or 48))
        except ValueError:
            hours = 48
        args = SimpleNamespace(sat=None, hours=hours, min_el=None)
        self.log(f"Berechne Überflüge der nächsten {hours} Stunden ...")
        self.run_job(lambda: st.compute_passes(self.cfg, args), needs_loc=True, done=self._after_passes)

    def _after_passes(self, result):
        self.obs, self.passes = result
        g = self.pass_grid
        g.clear_widgets()
        self.toggles = []
        if not self.passes:
            self.sel_label.text = "Keine Überflüge in diesem Zeitraum."
            return
        now = datetime.now(timezone.utc)
        for i, p in enumerate(self.passes, 1):
            prev = self.passes[i - 2] if i > 1 else None
            tb = ToggleButton(text=appcore.pass_label(i, p, prev, now, st.compass), size_hint_y=None,
                              height=dp(84), font_size=sp(15), halign="left", valign="middle")
            tb.bind(size=lambda w, s: setattr(w, "text_size", (s[0] - dp(16), s[1])))
            tb.bind(state=self.on_toggle)
            self.toggles.append(tb)
            g.add_widget(tb)
        self.sel_next()

    def _count(self):
        return sum(1 for t in self.toggles if t.state == "down")

    def on_toggle(self, widget, value):
        if value == "down" and self._count() > PASS_COUNT:
            widget.state = "normal"
            self.log(f"Das Funkgerät fasst höchstens {PASS_COUNT} Überflüge.")
        self.sel_label.text = f"{self._count()} von {PASS_COUNT} gewählt. Antippen zum Wählen."

    def sel_next(self):
        for t in self.toggles:
            t.state = "normal"
        for i in appcore.next_n(len(self.toggles), PASS_COUNT):
            self.toggles[i].state = "down"

    def sel_none(self):
        for t in self.toggles:
            t.state = "normal"

    def on_load(self):
        chosen = [p for p, t in zip(self.passes, self.toggles) if t.state == "down"]
        if not chosen:
            self.log("Erst Überflüge berechnen und einige antippen.")
            return
        self.confirm(f"{len(chosen)} Überflüge senden?\nDas ersetzt die Überflüge auf dem Funkgerät.",
                     lambda: self.run_job(lambda: appcore.upload(self.cfg, st, self.obs, chosen, self.radio),
                                          needs_radio=True))

    # ------------------------------------------------------------------ radio --
    def on_radio(self):
        self.run_job(lambda: st.cmd_radio(self.cfg, SimpleNamespace(port=None)), needs_radio=True)

    def on_cal(self):
        self.run_job(lambda: st.cmd_calibrate(self.cfg, SimpleNamespace(seconds=120, port=None)),
                     needs_radio=True)

    def on_install(self):
        def go():
            for name in APP_NAMES:
                f = os.path.join(HERE, name)
                if os.path.exists(f):
                    st.cmd_install(self.cfg, SimpleNamespace(files=[f], port=None))
                    return
            print("In dieser APK ist keine SatTrack-App enthalten (" + ", ".join(APP_NAMES) + ").")
        self.run_job(go, needs_radio=True)

    def on_clear(self):
        self.confirm("Alle Überflüge vom Funkgerät löschen?", lambda: self.run_job(
            lambda: st.cmd_clear(self.cfg, SimpleNamespace(number="all", yes=True, port=None)),
            needs_radio=True))

    # --------------------------------------------------------------- settings --
    def on_save(self):
        try:
            self.cfg.data["location"] = {"lat": float(self.lat.text), "lon": float(self.lon.text),
                                         "alt": float(self.alt.text or 0)}
            self.cfg.data["min_el"] = float(self.minel.text or 15)
        except ValueError:
            self.log("Breite, Länge, Höhe und Elevation müssen Zahlen sein.")
            return
        self.cfg.save()
        self.log("Einstellungen gespeichert.")


if __name__ == "__main__":
    SatTrackApp().run()
