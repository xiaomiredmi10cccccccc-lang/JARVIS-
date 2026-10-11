from math import sin, cos, pi
from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Line
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.widget import Widget
from kivy.core.window import Window

BG = (0.015, 0.035, 0.075, 1)
CYAN = (0.0, 0.8, 1.0, 1)
WHITE = (0.85, 0.95, 1.0, 1)
MUTED = (0.35, 0.65, 0.78, 1)

class Reactor(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.phase = 0
        self.bind(pos=self.draw, size=self.draw)
        Clock.schedule_interval(self.animate, 1 / 30)

    def animate(self, dt):
        self.phase += dt
        self.draw()

    def draw(self, *args):
        self.canvas.clear()
        with self.canvas:
            cx, cy = self.center
            r = min(self.width, self.height) * 0.37
            Color(*CYAN)
            Line(circle=(cx, cy, r), width=1.5)
            Line(circle=(cx, cy, r * 0.82), width=1)
            Line(circle=(cx, cy, r * 0.55), width=1.2)
            for i in range(48):
                a = 2 * pi * i / 48 + self.phase * 0.25
                inner = r * (0.88 if i % 4 else 0.76)
                outer = r * 0.97
                Line(points=[
                    cx + cos(a) * inner,
                    cy + sin(a) * inner,
                    cx + cos(a) * outer,
                    cy + sin(a) * outer
                ], width=1.2)
            Color(0.0, 0.55, 0.9, 0.12)
            Ellipse(pos=(cx-r*0.5, cy-r*0.5),
                    size=(r, r))
            Color(*CYAN)
            Line(circle=(cx, cy, r * (0.62 + 0.025 *
                 sin(self.phase * 2))), width=1.2)

class JarvisApp(App):
    def build(self):
        Window.clearcolor = BG
        root = BoxLayout(orientation="vertical", padding=18, spacing=10)

        title = Label(
            text="[b]J.A.R.V.I.S.[/b]",
            markup=True, font_size="30sp",
            color=CYAN, size_hint_y=0.11
        )
        root.add_widget(title)

        status = Label(
            text="СИСТЕМА ОМОДА АСТ",
            font_size="13sp", color=MUTED,
            size_hint_y=0.06
        )
        root.add_widget(status)

        reactor = Reactor(size_hint_y=0.48)
        root.add_widget(reactor)

        message = Label(
            text="Ман JARVIS ҳастам.\nСистемаи асосӣ фаъол аст.",
            font_size="17sp", color=WHITE,
            halign="center", valign="middle",
            size_hint_y=0.13
        )
        message.bind(size=lambda obj, val:
                     setattr(obj, "text_size", val))
        root.add_widget(message)

        def select_action(action):
            message.text = action
            status.text = "ИН ҚИСМ ҲАНӮЗ ПАЙВАСТ НЕСТ"

        buttons = [
            ("🎙  ФАРМОНИ ОВОЗӢ", "Фармони овозӣ ҳоло омода карда мешавад."),
            ("🧠  ЗЕҲНИ СУНЪӢ", "Пайвасти AI ҳоло илова нашудааст."),
            ("📱  БАРНОМАҲО", "Идоракунии барномаҳо ҳоло илова нашудааст."),
        ]

        for caption, result in buttons:
            btn = Button(
                text=caption, font_size="14sp",
                color=CYAN, background_normal="",
                background_color=(0.02, 0.12, 0.22, 1),
                size_hint_y=0.09
            )
            btn.bind(on_release=lambda instance, msg=result:
                     select_action(msg))
            root.add_widget(btn)

        footer = Label(
            text="JARVIS  •  ANDROID AI INTERFACE",
            font_size="10sp", color=MUTED,
            size_hint_y=0.06
        )
        root.add_widget(footer)
        return root

if __name__ == "__main__":
    JarvisApp().run()
