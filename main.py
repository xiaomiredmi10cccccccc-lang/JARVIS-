from kivy.app import App
from kivy.uix.label import Label


class JarvisApp(App):
    def build(self):
        return Label(
            text="JARVIS ONLINE",
            font_size=40
        )


JarvisApp().run()
