[app]
title = Sat Track 6.3
package.name = sattrack63
package.domain = org.example
source.dir = .
source.include_exts = py,app,h,c
version = 0.1
requirements = python3,kivy,sgp4,pyjnius,android,usb4a,usbserial4a,certifi
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
# usbserial4a needs the Java USB serial library. Check the exact line in the
# usbserial4a README for the version you build against:
android.gradle_dependencies = com.github.mik3y:usb-serial-for-android:3.7.0
android.enable_androidx = True
android.add_gradle_repositories = maven { url 'https://jitpack.io' }

[buildozer]
log_level = 2
warn_on_root = 1
