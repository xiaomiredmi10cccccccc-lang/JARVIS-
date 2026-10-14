[app]

# (str) Title of your application
title = JARVIS

# (str) Package name
package.name = jarvis

# (str) Package domain (needed for android packaging)
package.domain = org.jarvis

# (str) Source files where the relevent code lives (relative to directory of buildozer.spec)
source.include_exts = py,png,jpg,kv,atlas

# (list) Application requirements
# Add pyjnius and android for voice integration support
requirements = python3,kivy,pyjnius,android

# (str) Supported orientations
orientation = portrait

# (list) Permissions
# RECORD_AUDIO is mandatory for voice commands
android.permissions = RECORD_AUDIO

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2
