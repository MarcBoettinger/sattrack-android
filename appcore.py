"""Logic of the phone app, free of Kivy so it can be tested on a PC."""
import time

# ---------------------------------------------------------------- network --
class HttpStatus(IOError):
    """The server answered with an error status."""
    def __init__(self, code, host):
        super().__init__(f"HTTP {code} von {host}")
        self.code, self.host = code, host


class NetGuard:
    """Wraps catalog._get. After a server error (403, 429, 5xx) no new request goes to
    that host for a while: CelesTrak asks clients to stop querying when they get one."""
    def __init__(self, get, store, save=lambda: None, cooldown=1800):
        self.get, self.store, self.save, self.cooldown = get, store, save, cooldown

    def __call__(self, url, timeout=20):
        host = url.split("/")[2]
        t = self.store.get(host)
        if t and time.time() - t < self.cooldown:
            wait = int((self.cooldown - (time.time() - t)) // 60) + 1
            raise IOError(f"{host} meldete zuletzt einen Serverfehler. Neue Anfrage erst in "
                          f"ca. {wait} min (der Dienst bittet darum, dann nicht erneut abzufragen).")
        try:
            result = self.get(url, timeout)
        except HttpStatus as e:
            if e.code in (403, 429) or e.code >= 500:
                self.store[host] = time.time()
                self.save()
            raise
        if host in self.store:
            del self.store[host]
            self.save()
        return result


# ------------------------------------------------------------ transmitters --
def usable_transmitters(txs, radio_ok, modes):
    """Those the radio can use, voice repeaters first (same order as the PC tool)."""
    usable = [t for t in txs if radio_ok(t["downlink"]) and t["mode"] in modes]
    usable.sort(key=lambda t: (0 if t["uplink"] and t["raw_mode"] == "FM" else
                               1 if t["mode"] == "FM" else 2))
    return usable


def tx_label(t):
    up = f"   Up {t['uplink']:.3f} MHz" if t["uplink"] else "   (nur Empfang)"
    return f"Down {t['downlink']:.3f} MHz{up}\n{t['raw_mode']}  {t['description'][:40]}"


def parse_mhz(text):
    text = (text or "").strip().replace(",", ".")
    return float(text) if text else 0.0


def make_entry(norad, tx, name, ctcss):
    """tx: dict with downlink/uplink/mode (a SatNOGS transmitter or typed in)."""
    return {"norad": norad, "downlink": round(float(tx["downlink"]), 5),
            "uplink": round(float(tx.get("uplink") or 0), 5),
            "mode": str(tx.get("mode", "FM")).upper() or "FM",
            "ctcss": float(ctcss or 0), "name": (name or "").strip()}


def validate_entry(cfg, entry, radio_ok, modes, tones, name_len):
    """Error text in German, or None. Same rules as 'sattrack add'."""
    if not radio_ok(entry["downlink"]) or (entry["uplink"] and not radio_ok(entry["uplink"])):
        return "Diese Frequenzen kann das Funkgerät nicht (18-1300 MHz)."
    if entry["mode"] not in modes:
        return f"Das Funkgerät kann {entry['mode']} nicht empfangen (FM, AM, USB, CW)."
    if entry["ctcss"] and entry["ctcss"] not in tones:
        return f"{entry['ctcss']:g} Hz ist kein CTCSS-Ton, den das Funkgerät senden kann."
    n = entry["name"]
    if not n or not n.isascii():
        return "Name: nur einfache Buchstaben und Ziffern."
    if len(n) > name_len:
        return f"Name: höchstens {name_len} Zeichen."
    if cfg.find(n):
        return f"Es gibt schon einen Satelliten namens {n}."
    return None


# ------------------------------------------------------------------ passes --
def countdown(aos, now):
    mins = int((aos - now).total_seconds() // 60)
    return f"{mins // 60}h{mins % 60:02d}m" if mins >= 60 else f"{max(mins, 0)}m"


def pass_label(i, p, prev, now, compass):
    """Two lines for one pass; i is 1-based."""
    t = p.aos.astimezone()
    tx = "" if p.sat.get("uplink") and p.sat.get("mode", "FM") == "FM" else "  (nur Empfang)"
    over = "  ÜBERSCHNEIDET" if prev is not None and p.aos < prev.los else ""
    return (f"{i:>2}  {p.sat['name']}   {t:%a %d.%m. %H:%M:%S}   in {countdown(p.aos, now)}\n"
            f"{p.minutes:.1f} min   max {p.max_el:.0f}°   {compass(p.aos_az)} > {compass(p.los_az)}{tx}{over}")


def next_n(count, limit):
    return list(range(min(limit, count)))


def upload(cfg, st, obs, chosen, radio, now=time.time):
    """The sending half of 'sattrack load', for passes the user ticked."""
    blobs = []
    for i, p in enumerate(chosen, 1):
        st.status(f"Bereite Überflug {i}/{len(chosen)} vor")
        samples, step = st.sample_pass(obs, p)
        blobs.append(st.build_pass(p.sat, samples, step, p.max_el, st.aos_label(p.aos),
                                   st.pass_extras(obs, p)))
    print()
    index = st.build_index(int(now()), cfg.data.get("ppm", 0), len(blobs))
    radio.upload(index, blobs, progress=st.status)
    print()
    first = chosen[0]
    print(f"Fertig. Am Funkgerät: F, 7, Sat Track. Nächster Überflug: {first.sat['name']} "
          f"{first.aos.astimezone():%a %H:%M}.")
    if "ppm_measured" not in cfg.data:
        print("Tipp: einmal 'Uhr kalibrieren', dann hält das Funkgerät die Zeit besser.")
    return len(blobs)
