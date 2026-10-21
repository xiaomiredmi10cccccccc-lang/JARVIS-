[app]

# (str) Title of your application
title = JARVIS

# (str) Package name
package.name = jarvis

# (str) Package domain (needed for android packaging)
package.domain = org.jarvis

# (str) Source files where the relevent code lives (relative to directory of buildozer.spec)
source.include_exts = py,png,jpg,kv,atlas

# (str) Directory where the source code is located (Ин сатр хатогиро нест мекунад)
source.dir = .

# (str) Application versioning (Версияи барнома)
version = 0.1

# (list) Application requirements
requirements = python3,kivy,pyjnius,android

# (str) Supported orientations
orientation = portrait

# (list) Permissions
android.permissions = INTERNET,RECORD_AUDIO

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug)
log_level = 2

