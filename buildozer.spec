[app]

title = JARVIS
package.name = jarvis
package.domain = org.jarvis

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 0.1

requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,pyjnius,requests,certifi,urllib3,idna,chardet

orientation = portrait

android.permissions = INTERNET,RECORD_AUDIO,ACCESS_NETWORK_STATE

p4a.branch = v2024.01.21
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]

log_level = 2
warn_on_root = 1
