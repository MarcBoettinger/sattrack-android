# Sat Track for Android (UNTESTED)

Runs the same code as `tools/sattrack/sattrack.py` on a phone. The radio is
connected by USB-C (OTG); `pyserial` is replaced by `usbserial4a`.
Nothing here has been built or run on a device.

## Contents
- `main.py`     Kivy screen: location, add satellite, passes, load, calibrate, install
- `sattrack.py`, `satlib/`, `msg.py`, `sat_format.h`, `dcs.c`  copies from tools/sattrack and tools/serialtool (7.0.0 + Sat Track patch)
- `buildozer.spec`, `.github/workflows/apk.yml`

## Build (Linux, WSL2 or GitHub Actions)
1. Optional: copy `SatTrack.app` (from `App/apps/sat`) next to `main.py` to bundle it.
2. WSL2/Ubuntu: `pip install buildozer cython` and the packages from the Buildozer docs, then `buildozer android debug`.
   GitHub: push this folder to a repo, Actions > Build APK > Run workflow, download the artifact.
3. Install `bin/sattrack-0.1-arm64-v8a-debug.apk` on the phone (allow unknown sources).

## Use
Phone to radio with a USB-C cable (OTG), radio on its main screen. Enter location,
Save, Add satellites (NORAD id), then Load next 16 passes. The first time Android
asks for USB access: allow, then tap the button again.

## Known risks
- `sgp4` builds a C extension; python-for-android may need a recipe or the pure-Python build.
- The Gradle line for the Java USB library in `buildozer.spec` is a best guess: compare it
  with the usbserial4a README.
- Which USB device is used: the first one Android lists. Unplug other USB devices.
- Add picks the first usable SatNOGS transmitter and sets no CTCSS tone.
- First test: Radio, then Load; send only to a radio you have a calibration backup for.
