
from kivy.app import App
from kivy.clock import Clock
from kivy.animation import Animation
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.utils import get_color_from_hex
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.widget import Widget
from kivy.graphics import Color, Ellipse, Line, Rectangle
from datetime import datetime
import math


BG = "#050B14"
CYAN = "#35E6FF"
BLUE = "#167DFF"
WHITE = "#E7FAFF"
MUTED = "#7297AD"


def rgba(color, alpha=1):
    c = get_color_from_hex(color)
    return (c[0], c[1], c[2], alpha)


class Reactor(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.angle = 0
        self.pulse = 0
        self.bind(pos=self.draw, size=self.draw)
        Clock.schedule_interval(self.animate, 1 / 30)

    def animate(self, dt):
        self.angle = (self.angle + 0.8) % 360
        self.pulse += 0.06
        self.draw()

    def draw(self, *args):
        self.canvas.clear()
        cx, cy = self.center
        r = min(self.width, self.height) * 0.37
        if r <= 0:
            return

        with self.canvas:
            Color(*rgba(CYAN, 0.08))
            Ellipse(pos=(cx-r*1.45, cy-r*1.45),
                    size=(r*2.9, r*2.9))

            Color(*rgba(CYAN, 0.7))
            Line(circle=(cx, cy, r*1.25), width=1.1)

            Color(*rgba(BLUE, 0.9))
            Line(circle=(cx, cy, r*1.08), width=1.5)

            Color(*rgba(CYAN, 0.65))
            Line(circle=(cx, cy, r*0.91), width=1)

            for i in range(48):
                a = math.radians(i * 7.5 + self.angle)
                inner = r * (0.98 if i % 4 else 0.88)
                outer = r * (1.08 if i % 4 else 1.19)
                x1 = cx + math.cos(a) * inner
                y1 = cy + math.sin(a) * inner
                x2 = cx + math.cos(a) * outer
                y2 = cy + math.sin(a) * outer
                Color(*rgba(CYAN, 0.9 if i % 4 == 0 else 0.38))
                Line(points=[x1, y1, x2, y2], width=1.2)

            Color(*rgba(CYAN, 0.12))
            Ellipse(pos=(cx-r*0.77, cy-r*0.77),
                    size=(r*1.54, r*1.54))

            Color(*rgba(CYAN, 0.95))
            Line(circle=(cx, cy, r*0.68), width=1.4)

            Color(*rgba(BLUE, 0.7))
            Line(circle=(cx, cy, r*0.52), width=1)

            glow = 0.75 + 0.20 * math.sin(self.pulse)
            Color(0.1, 0.82, 1, glow)
            Ellipse(pos=(cx-r*0.36, cy-r*0.36),
                    size=(r*0.72, r*0.72))

            Color(*rgba(WHITE, 0.95))
            Ellipse(pos=(cx-r*0.18, cy-r*0.18),
                    size=(r*0.36, r*0.36))

            Color(*rgba(CYAN, 0.85))
            for i in range(3):
                a = math.radians(-self.angle * 2 + i * 120)
                x = cx + math.cos(a) * r * 1.25
                y = cy + math.sin(a) * r * 1.25
                Ellipse(pos=(x-dp(2), y-dp(2)),
                        size=(dp(4), dp(4)))


class HUDButton(Button):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.color = rgba(CYAN)
        self.font_size = dp(13)
        self.bold = True
        self.size_hint_y = None
        self.height = dp(46)
        self.background_disabled_normal = ""

        with self.canvas.before:
            self.bg_color = Color(*rgba("#0B1D2D", 0.95))
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)
            self.border_color = Color(*rgba(CYAN, 0.65))
            self.border_line = Line(rectangle=(*self.pos, *self.size),
                                    width=1)

        self.bind(pos=self.update_graphics, size=self.update_graphics)

    def update_graphics(self, *args):
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size
        self.border_line.rectangle = (*self.pos, *self.size)


class JarvisApp(App):
    def build(self):
        Window.clearcolor = rgba(BG)

        self.root_ui = FloatLayout()

        with self.root_ui.canvas.before:
            Color(*rgba(BG))
            self.background = Rectangle(
                pos=self.root_ui.pos, size=self.root_ui.size
            )
        self.root_ui.bind(
            pos=self.update_background,
            size=self.update_background
        )

        main = BoxLayout(
            orientation="vertical",
            padding=[dp(22), dp(18), dp(22), dp(14)],
            spacing=dp(8),
            size_hint=(1, 1)
        )
        self.root_ui.add_widget(main)

        header = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(46)
        )
        title = Label(
            text="[b]J.A.R.V.I.S[/b]",
            markup=True,
            color=rgba(CYAN),
            font_size=dp(22),
            halign="left",
            valign="middle"
        )
        title.bind(size=lambda obj, size: setattr(
            obj, "text_size", size
        ))
        header.add_widget(title)

        self.clock_label = Label(
            text="--:--",
            color=rgba(WHITE),
            font_size=dp(15),
            size_hint_x=None,
            width=dp(75)
        )
        header.add_widget(self.clock_label)
        main.add_widget(header)

        line = Widget(size_hint_y=None, height=dp(1))
        with line.canvas:
            Color(*rgba(CYAN, 0.55))
            line.rect = Rectangle(pos=line.pos, size=line.size)
        line.bind(pos=lambda obj, p: setattr(obj.rect, "pos", p),
                  size=lambda obj, s: setattr(obj.rect, "size", s))
        main.add_widget(line)

        self.status = Label(
            text="СИСТЕМА ОМОДА АСТ",
            color=rgba(CYAN),
            font_size=dp(12),
            bold=True,
            size_hint_y=None,
            height=dp(30)
        )
        main.add_widget(self.status)

        reactor_area = FloatLayout(size_hint_y=0.9)
        self.reactor = Reactor(
            size_hint=(1, 1),
            pos_hint={"x": 0, "y": 0}
        )
        reactor_area.add_widget(self.reactor)

        center_text = BoxLayout(
            orientation="vertical",
            spacing=dp(3),
            size_hint=(0.65, None),
            height=dp(66),
            pos_hint={"center_x": 0.5, "center_y": 0.5}
        )
        center_text.add_widget(Label(
            text="[b]JARVIS[/b]",
            markup=True,
            color=rgba(WHITE),
            font_size=dp(24)
        ))
        center_text.add_widget(Label(
            text="ЗЕҲНИ СУНЪӢ",
            color=rgba(CYAN),
            font_size=dp(10)
        ))
        reactor_area.add_widget(center_text)
        main.add_widget(reactor_area)

        self.message = Label(
            text="Ман омода ҳастам.",
            color=rgba(WHITE),
            font_size=dp(15),
            size_hint_y=None,
            height=dp(32)
        )
        main.add_widget(self.message)

        self.wave = Label(
            text="·  ─  ·  ─  ·  ─  ·  ─  ·",
            color=rgba(CYAN),
            font_size=dp(20),
            size_hint_y=None,
            height=dp(34)
        )
        main.add_widget(self.wave)

        buttons = BoxLayout(
            orientation="vertical",
            spacing=dp(8),
            size_hint_y=None,
            height=dp(162)
        )

        listen = HUDButton(text="◉   ФАРМОНИ ОВОЗӢ")
        listen.bind(on_release=self.voice_info)
        buttons.add_widget(listen)

        apps = HUDButton(text="▦   БАРНОМАҲО")
        apps.bind(on_release=lambda *_: self.show_message(
            "Панели барномаҳо омода мешавад."
        ))
        buttons.add_widget(apps)

        info = HUDButton(text="⌘   ҲОЛАТИ СИСТЕМА")
        info.bind(on_release=lambda *_: self.show_message(
            "JARVIS фаъол аст. Микрофон ҳанӯз пайваст нест."
        ))
        buttons.add_widget(info)

        main.add_widget(buttons)

        footer = Label(
            text="J.A.R.V.I.S  •  SMART ASSISTANT",
            color=rgba(MUTED),
            font_size=dp(9),
            size_hint_y=None,
            height=dp(25)
        )
        main.add_widget(footer)

        Clock.schedule_interval(self.update_clock, 1)
        Clock.schedule_interval(self.animate_wave, 0.25)
        self.update_clock(0)

        return self.root_ui

    def update_background(self, *args):
        self.background.pos = self.root_ui.pos
        self.background.size = self.root_ui.size

    def update_clock(self, dt):
        self.clock_label.text = datetime.now().strftime("%H:%M")

    def animate_wave(self, dt):
        patterns = [
            "·  ─  ·  ─  ·  ─  ·  ─  ·",
            "─  ·  ─  ·  ─  ·  ─  ·  ─",
            "·  ·  ─  ─  ·  ·  ─  ─  ·",
            "─  ─  ·  ·  ─  ─  ·  ·  ─"
        ]
        index = int(datetime.now().microsecond / 250000)
        self.wave.text = patterns[index % len(patterns)]

    def show_message(self, message):
        self.message.text = message

    def voice_info(self, *args):
        self.status.text = "ИНТИЗОРИ ПАЙВАСТИ МИКРОФОН"
        self.message.text = "Интерфейс омода аст; овоз ҳанӯз фаъол нест."

    def on_pause(self):
        return True


if __name__ == "__main__":
    JarvisApp().run()
