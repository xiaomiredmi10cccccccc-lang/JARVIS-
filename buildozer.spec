[app]

title = JARVIS
package.name = jarvis
package.domain = org.test

source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.1

orientation = portrait
fullscreen = 0

requirements = python3,kivy

p4a.branch = develop
p4a.commit = 9a7694e

android.api = 33
android.minapi = 24
android.ndk = 28c
android.ndk_api = 24
android.archs = arm64-v8a

android.allow_backup = True
android.accept_sdk_license = True
android.skip_update = True

[buildozer]

log_level = 2
warn_on_root = 1
