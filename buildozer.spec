[app]

# Номи барнома
title = JARVIS

# Маълумоти баста
package.name = jarvis
package.domain = org.jarvis

# Ҷузвдони асосии Python
source.dir = .

# Файлҳое, ки ба APK дохил мешаванд
source.include_exts = py,png,jpg,jpeg,kv,atlas,json,txt,ttf,otf,wav,mp3

# Версияи барнома
version = 0.1.0

# Китобхонаҳои зарурӣ
requirements = python3,kivy,pyjnius,requests,certifi

# Меъмории Android
android.archs = arm64-v8a, armeabi-v7a

# Версияи Android
android.minapi = 24
android.api = 33
android.ndk = 25b

# Самти экран
orientation = portrait

# Иҷозатҳо
android.permissions = INTERNET,RECORD_AUDIO,ACCESS_NETWORK_STATE

# AndroidX
android.enable_androidx = True

# Python-for-Android
p4a.branch = develop

# Номи файли асосии барнома
# Файли main.py бояд дар source.dir бошад

# Нусхабардории файлҳои пинҳон
source.include_patterns = assets/*,data/*

# Танзимоти Buildozer
[buildozer]
log_level = 2
warn_on_root = 1
