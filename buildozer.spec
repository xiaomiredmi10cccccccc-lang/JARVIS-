[app]

# (str) Title of your application
title = JARVIS

# (str) Package name
package.name = jarvis

# (str) Package domain (needed for android packaging)
package.domain = org.jarvis

# (str) Source files where the relevent code lives (relative to directory of buildozer.spec)
source.include_exts = py,png,jpg,kv,atlas

# (str) Directory where the source code is located
source.dir = .

# (str) Application versioning
version = 0.1

# (list) Application requirements
# Китобхонаҳои requests ва certifi барои пайвастшавӣ ба API илова шуданд
requirements = python3, kivy, pyjnius, requests, certifi, charset-normalizer
# (str) Supported orientations
orientation = portrait

# (list) Permissions
# Иҷозати интернет ва шабака барои кор бо API ҳатмист
android.permissions = RECORD_AUDIO, INTERNET, ACCESS_NETWORK_STATE

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug)
log_level = 2
