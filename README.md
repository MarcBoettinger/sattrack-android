# Sat Track for Android, firmware 6.3 only (UNTESTED)

Calls the same code as tools/sattrack of your 6.3 tree. USB-C (OTG) to the radio.
SatTrack.app (built for the 6.3 merge, API level 4) is included; "Install" puts it on the radio.

Build: push this folder to a GitHub repo (keep .github/workflows), Actions > Build APK >
Run workflow, download the artifact "sattrack-apk". Or in WSL2/Ubuntu: `buildozer android debug`.
Use: location, Save, Add satellite (NORAD id), "Load next 16 passes".
