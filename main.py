
# ============================================================
# JARVIS AI — Intelligent Futuristic Android Interface
# Kivy + Buildozer | Tajik / Russian / English
# ============================================================

from datetime import datetime
import math
import os
import random

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.utils import get_color_from_hex
from kivy.animation import Animation

from kivy.uix.floatlayout import FloatLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.widget import Widget
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView


# -------------------- COLORS --------------------

BG = "#050B16"
PANEL = "#0B1728"
CYAN = "#35DDF5"
BLUE = "#168DFF"
WHITE = "#E7F7FF"
MUTED = "#7999B5"
AMBER = "#FFB547"
GREEN = "#39E6A0"
RED = "#FF657A"


# -------------------- LANGUAGES --------------------

TEXTS = {
    "tg": {
        "title": "JARVIS",
        "subtitle": "ЁРДАМЧИИ ИНТЕЛЛЕКТУАЛӢ",
        "ready": "СИСТЕМА ОМОДА АСТ",
        "thinking": "ТАҲЛИЛ КАРДА ИСТОДАМ...",
        "listen": "МИКРОФОН",
        "command": "ФАРМОН",
        "themes": "Мавзӯъ",
        "language": "Забон",
        "voice": "Овоз",
        "settings": "ТАНЗИМОТ",
        "system": "ҲОЛАТИ СИСТЕМА",
        "online": "Система фаъол аст",
        "clock": "ВАҚТИ МАҲАЛЛӢ",
        "status": "ҲОЛАТ",
        "classic": "JARVIS Classic",
        "aura": "Aura",
        "input": "Фармони худро нависед...",
        "send": "ИҶРО",
        "close": "ПӮШИДАН",
        "welcome": "Салом! Ман JARVIS ҳастам.",
        "help": "Фармонҳои дастрас",
        "unknown": "Ин фармон ҳоло дастгирӣ намешавад.",
        "saved": "Танзимот нигоҳ дошта шуд.",
        "mic_info": "Барои шинохти воқеии овоз интегратсияи Android SpeechRecognizer лозим аст.",
        "commands": "салом, соат, сана, ҳолат, мавзӯъ, кӯмак",
        "theme_names": [
            "IRON HUD", "NEURAL CORE", "HOLOGRAPHIC",
            "MINIMAL AI", "JARVIS SPHERE"
        ],
        "status_ready": "ОМОДА",
    },
    "ru": {
        "title": "JARVIS",
        "subtitle": "ИНТЕЛЛЕКТУАЛЬНЫЙ ПОМОЩНИК",
        "ready": "СИСТЕМА ГОТОВА",
        "thinking": "АНАЛИЗИРУЮ...",
        "listen": "МИКРОФОН",
        "command": "КОМАНДА",
        "themes": "Тема",
        "language": "Язык",
        "voice": "Голос",
        "settings": "НАСТРОЙКИ",
        "system": "СОСТОЯНИЕ СИСТЕМЫ",
        "online": "Система активна",
        "clock": "МЕСТНОЕ ВРЕМЯ",
        "status": "СТАТУС",
        "classic": "JARVIS Classic",
        "aura": "Aura",
        "input": "Введите команду...",
        "send": "ВЫПОЛНИТЬ",
        "close": "ЗАКРЫТЬ",
        "welcome": "Привет! Я JARVIS.",
        "help": "Доступные команды",
        "unknown": "Эта команда пока не поддерживается.",
        "saved": "Настройки сохранены.",
        "mic_info": "Для настоящего распознавания речи нужна интеграция Android SpeechRecognizer.",
        "commands": "привет, время, дата, статус, тема, помощь",
        "theme_names": [
            "IRON HUD", "NEURAL CORE", "HOLOGRAPHIC",
            "MINIMAL AI", "JARVIS SPHERE"
        ],
        "status_ready": "ГОТОВ",
    },
    "en": {
        "title": "JARVIS",
        "subtitle": "INTELLIGENT ASSISTANT",
        "ready": "SYSTEM READY",
        "thinking": "ANALYZING...",
        "listen": "MICROPHONE",
        "command": "COMMAND",
        "themes": "Theme",
        "language": "Language",
        "voice": "Voice",
        "settings": "SETTINGS",
        "system": "SYSTEM STATUS",
        "online": "System is active",
        "clock": "LOCAL TIME",
        "status": "STATUS",
        "classic": "JARVIS Classic",
        "aura": "Aura",
        "input": "Enter your command...",
        "send": "EXECUTE",
        "close": "CLOSE",
        "welcome": "Hello! I am JARVIS.",
        "help": "Available commands",
        "unknown": "This command is not supported yet.",
        "saved": "Settings saved.",
        "mic_info": "Real speech recognition requires Android SpeechRecognizer integration.",
        "commands": "hello, time, date, status, theme, help",
        "theme_names": [
            "IRON HUD", "NEURAL CORE", "HOLOGRAPHIC",
            "MINIMAL AI", "JARVIS SPHERE"
        ],
        "status_ready": "READY",
    },
}


# -------------------- THEMES --------------------

THEMES = [
    {
        "name": "IRON HUD",
        "bg": "#050B16",
        "panel": "#0B1728",
        "accent": "#35DDF5",
        "secondary": "#168DFF",
    },
    {
        "name": "NEURAL CORE",
        "bg": "#080817",
        "panel": "#15132C",
        "accent": "#B68AFF",
        "secondary": "#725CFF",
    },
    {
        "name": "HOLOGRAPHIC",
        "bg": "#041513",
        "panel": "#092923",
        "accent": "#50FFD0",
        "secondary": "#15BDAA",
    },
    {
        "name": "MINIMAL AI",
        "bg": "#101318",
        "panel": "#202630",
        "accent": "#E7F7FF",
        "secondary": "#819DB9",
    },
    {
        "name": "JARVIS SPHERE",
        "bg": "#100A05",
        "panel": "#27170C",
        "accent": "#FFB547",
        "secondary": "#FF7835",
    },
]


# -------------------- HELPERS --------------------

def rgba(hex_color, alpha=1):
    color = get_color_from_hex(hex_color)
    return color[:3] + [alpha]


def make_label(text="", size=14, color=WHITE, bold=False, **kwargs):
    return Label(
        text=text,
        font_size=dp(size),
        color=rgba(color),
        bold=bold,
        halign="center",
        valign="middle",
        **kwargs
    )


# -------------------- REACTOR ANIMATION --------------------

class Reactor(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.angle = 0
        self.pulse = 0
        self.accent = CYAN
        self.secondary = BLUE
        self.theme_index = 0
        self.bind(pos=self.draw, size=self.draw)
        Clock.schedule_interval(self.animate, 1 / 30)

    def animate(self, dt):
        self.angle = (self.angle + 24 * dt) % 360
        self.pulse += dt * 2.2
        self.draw()

    def draw(self, *args):
        self.canvas.clear()

        if self.width <= 0 or self.height <= 0:
            return

        cx = self.x + self.width / 2
        cy = self.y + self.height / 2
        radius = min(self.width, self.height) * 0.39

        pulse = 1 + math.sin(self.pulse) * 0.025

        with self.canvas:
            from kivy.graphics import Color, Ellipse, Line

            Color(*rgba(self.accent, 0.08))
            Ellipse(
                pos=(cx - radius * pulse, cy - radius * pulse),
                size=(radius * 2 * pulse, radius * 2 * pulse)
            )

            for i in range(5):
                r = radius * (0.35 + i * 0.16)
                alpha = 0.85 - i * 0.11

                Color(*rgba(
                    self.accent if i % 2 == 0 else self.secondary,
                    alpha
                ))
                Line(
                    circle=(cx, cy, r, self.angle + i * 13,
                            self.angle + 260 + i * 13),
                    width=dp(1.1 if i % 2 == 0 else 0.65)
                )

            Color(*rgba(self.accent, 0.7))
            Line(
                circle=(cx, cy, radius * 0.29),
                width=dp(1.4)
            )

            Color(*rgba(self.secondary, 0.85))
            Line(
                circle=(cx, cy, radius * 0.17,
                        -self.angle * 1.8,
                        270 - self.angle * 1.8),
                width=dp(1.4)
            )

            Color(*rgba(self.accent, 0.95))
            Ellipse(
                pos=(cx - radius * 0.075, cy - radius * 0.075),
                size=(radius * 0.15, radius * 0.15)
            )

            for i in range(16):
                a = math.radians(self.angle + i * 22.5)
                r1 = radius * 0.84
                r2 = radius * (0.91 if i % 4 == 0 else 0.87)

                x1 = cx + math.cos(a) * r1
                y1 = cy + math.sin(a) * r1
                x2 = cx + math.cos(a) * r2
                y2 = cy + math.sin(a) * r2

                Color(*rgba(self.accent, 0.9))
                Line(points=[x1, y1, x2, y2], width=dp(1.3))


# -------------------- MAIN APPLICATION --------------------

class JarvisApp(App):

    def build(self):
        self.title = "JARVIS AI"

        self.store = JsonStore(
            os.path.join(self.user_data_dir, "jarvis_settings.json")
        )

        self.language = "tg"
        self.theme_index = 0
        self.voice_index = 0
        self.status_text = ""
        self._load_settings()

        self.root_ui = FloatLayout()
        self.main_box = BoxLayout(
            orientation="vertical",
            padding=[dp(15), dp(12), dp(15), dp(12)],
            spacing=dp(8)
        )

        self.root_ui.add_widget(self.main_box)

        self._build_header()
        self._build_reactor()
        self._build_status()
        self._build_controls()
        self._build_footer()

        self.apply_theme()
        self.refresh_text()

        Clock.schedule_interval(self.update_clock, 1)

        return self.root_ui

    # -------------------- SETTINGS --------------------

    def _load_settings(self):
        if self.store.exists("settings"):
            data = self.store.get("settings")
            self.language = data.get("language", "tg")
            self.theme_index = data.get("theme_index", 0)
            self.voice_index = data.get("voice_index", 0)

        if self.language not in TEXTS:
            self.language = "tg"

        self.theme_index = max(0, min(4, int(self.theme_index)))
        self.voice_index = max(0, min(1, int(self.voice_index)))

    def save_settings(self):
        self.store.put(
            "settings",
            language=self.language,
            theme_index=self.theme_index,
            voice_index=self.voice_index
        )

    def tr(self, key):
        return TEXTS[self.language].get(key, key)

    # -------------------- HEADER --------------------

    def _build_header(self):
        self.header = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            height=dp(75),
            spacing=dp(2)
        )

        self.title_label = make_label(
            "JARVIS", 29, CYAN, True
        )

        self.subtitle_label = make_label(
            "", 10, MUTED
        )

        self.header.add_widget(self.title_label)
        self.header.add_widget(self.subtitle_label)
        self.main_box.add_widget(self.header)

    # -------------------- REACTOR --------------------

    def _build_reactor(self):
        self.reactor_area = BoxLayout(
            orientation="vertical",
            size_hint_y=0.42,
            spacing=dp(2)
        )

        self.reactor = Reactor()
        self.reactor_area.add_widget(self.reactor)

        self.reactor_caption = make_label(
            "J.A.R.V.I.S. CORE", 11, CYAN, True
        )

        self.reactor_area.add_widget(self.reactor_caption)
        self.main_box.add_widget(self.reactor_area)

    # -------------------- STATUS --------------------

    def _build_status(self):
        self.status_panel = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            height=dp(75),
            padding=dp(8),
            spacing=dp(4)
        )

        self.status_label = make_label("", 13, GREEN, True)
        self.clock_label = make_label("", 12, MUTED)
        self.message_label = make_label(
            "", 11, WHITE
        )

        self.status_panel.add_widget(self.status_label)
        self.status_panel.add_widget(self.clock_label)
        self.status_panel.add_widget(self.message_label)

        self.main_box.add_widget(self.status_panel)

    # -------------------- BUTTONS --------------------

    def _make_button(self, text, callback, color=None):
        color = color or CYAN

        button = Button(
            text=text,
            size_hint_y=None,
            height=dp(46),
            background_normal="",
            background_down="",
            background_color=rgba(PANEL),
            color=rgba(color),
            font_size=dp(12),
            bold=True
        )

        with button.canvas.before:
            from kivy.graphics import Color, Line, RoundedRectangle

            button._bg_color = Color(*rgba(PANEL))
            button._bg_rect = RoundedRectangle(
                pos=button.pos,
                size=button.size,
                radius=[dp(8)]
            )

            button._border_color = Color(*rgba(color, 0.75))
            button._border = Line(
                rounded_rectangle=(
                    button.x, button.y, button.width,
                    button.height, dp(8)
                ),
                width=dp(1)
            )

        def update_shape(instance, value):
            instance._bg_rect.pos = instance.pos
            instance._bg_rect.size = instance.size
            instance._border.rounded_rectangle = (
                instance.x, instance.y, instance.width,
                instance.height, dp(8)
            )

        button.bind(pos=update_shape, size=update_shape)
        button.bind(on_release=callback)

        return button

    def _build_controls(self):
        self.controls = BoxLayout(
            orientation="vertical",
            spacing=dp(7),
            size_hint_y=None,
            height=dp(158)
        )

        row1 = BoxLayout(spacing=dp(7))
        self.command_button = self._make_button(
            "", self.open_command, CYAN
        )
        self.mic_button = self._make_button(
            "", self.show_microphone_info, GREEN
        )

        row1.add_widget(self.command_button)
        row1.add_widget(self.mic_button)

        row2 = BoxLayout(spacing=dp(7))
        self.theme_button = self._make_button(
            "", self.next_theme, AMBER
        )
        self.language_button = self._make_button(
            "", self.next_language, CYAN
        )

        row2.add_widget(self.theme_button)
        row2.add_widget(self.language_button)

        row3 = BoxLayout(spacing=dp(7))
        self.voice_button = self._make_button(
            "", self.next_voice, GREEN
        )
        self.settings_button = self._make_button(
            "", self.show_settings, WHITE
        )

        row3.add_widget(self.voice_button)
        row3.add_widget(self.settings_button)

        self.controls.add_widget(row1)
        self.controls.add_widget(row2)
        self.controls.add_widget(row3)

        self.main_box.add_widget(self.controls)

    # -------------------- FOOTER --------------------

    def _build_footer(self):
        self.footer = make_label(
            "JARVIS AI  •  ANDROID INTERFACE",
            9, MUTED
        )
        self.footer.size_hint_y = None
        self.footer.height = dp(22)

        self.main_box.add_widget(self.footer)

    # -------------------- UPDATE UI --------------------

    def refresh_text(self):
        self.title_label.text = self.tr("title")
        self.subtitle_label.text = self.tr("subtitle")
        self.status_label.text = (
            "●  " + self.tr("ready")
        )
        self.command_button.text = (
            "⌘  " + self.tr("command")
        )
        self.mic_button.text = (
            "◉  " + self.tr("listen")
        )
        self.theme_button.text = (
            "◈  " + self.tr("themes")
        )
        self.language_button.text = (
            "文  " + self.tr("language")
        )
        self.voice_button.text = (
            "♫  " + self.tr("voice")
        )
        self.settings_button.text = (
            "⚙  " + self.tr("settings")
        )

        self.reactor_caption.text = (
            "J.A.R.V.I.S. CORE  /  " +
            self.tr("status_ready")
        )

        voices = ["classic", "aura"]
        self.voice_button.text = (
            "♫  " + self.tr(voices[self.voice_index])
        )

        self.update_clock(0)

    def update_clock(self, dt):
        now = datetime.now()

        if hasattr(self, "clock_label"):
            self.clock_label.text = (
                self.tr("clock") + "   " +
                now.strftime("%H:%M:%S") + "   •   " +
                now.strftime("%d.%m.%Y")
            )

    # -------------------- APPLY THEME --------------------

    def apply_theme(self):
        theme = THEMES[self.theme_index]

        Window.clearcolor = rgba(theme["bg"])

        self.reactor.accent = theme["accent"]
        self.reactor.secondary = theme["secondary"]

        self.title_label.color = rgba(theme["accent"])
        self.reactor_caption.color = rgba(theme["accent"])

        for button in (
            self.command_button,
            self.mic_button,
            self.theme_button,
            self.language_button,
            self.voice_button,
            self.settings_button
        ):
            button.background_color = rgba(theme["panel"])

        self.save_settings()

    # -------------------- THEME CONTROL --------------------

    def next_theme(self, *args):
        self.theme_index = (self.theme_index + 1) % len(THEMES)
        self.apply_theme()
        self.refresh_text()

        self.show_message(
            THEMES[self.theme_index]["name"]
        )

    # -------------------- LANGUAGE CONTROL --------------------

    def next_language(self, *args):
        languages = ["tg", "ru", "en"]
        index = languages.index(self.language)
        self.language = languages[(index + 1) % len(languages)]

        self.save_settings()
        self.refresh_text()
        self.show_message(self.tr("saved"))

    # -------------------- VOICE PROFILE --------------------

    def next_voice(self, *args):
        self.voice_index = (self.voice_index + 1) % 2
        self.save_settings()
        self.refresh_text()

        self.show_message(
            self.tr(["classic", "aura"][self.voice_index])
        )

    # -------------------- STATUS MESSAGE --------------------

    def show_message(self, message):
        self.message_label.text = str(message)

        Animation.cancel_all(self.message_label)
        self.message_label.opacity = 0
        Animation(opacity=1, duration=0.3).start(
            self.message_label
        )

    # -------------------- COMMAND INPUT --------------------

    def open_command(self, *args):
        content = BoxLayout(
            orientation="vertical",
            spacing=dp(10),
            padding=dp(12)
        )

        input_box = TextInput(
            hint_text=self.tr("input"),
            multiline=False,
            size_hint_y=None,
            height=dp(48),
            font_size=dp(16),
            foreground_color=rgba(WHITE),
            background_color=rgba(PANEL),
            cursor_color=rgba(CYAN)
        )

        result_label = make_label(
            self.tr("welcome"), 12, CYAN
        )

        buttons = BoxLayout(
            size_hint_y=None,
            height=dp(45),
            spacing=dp(8)
        )

        popup = Popup(
            title="JARVIS AI",
            content=content,
            size_hint=(0.92, 0.48),
            auto_dismiss=True,
            separator_color=rgba(CYAN)
        )

        def execute(*_):
            command = input_box.text.strip()

            if not command:
                return

            answer = self.process_command(command)
            result_label.text = answer
            self.show_message(answer)

        execute_button = self._make_button(
            self.tr("send"), execute, GREEN
        )
        close_button = self._make_button(
            self.tr("close"), popup.dismiss, AMBER
        )

      
        buttons.add_widget(execute_button)
        buttons.add_widget(close_button)

        content.add_widget(input_box)
        content.add_widget(result_label)
        content.add_widget(buttons)

        input_box.bind(on_text_validate=execute)

        popup.open()
        input_box.focus = True

    # -------------------- COMMAND PROCESSOR --------------------

    def process_command(self, command):
        cmd = command.lower().strip()
        now = datetime.now()

        if any(x in cmd for x in ["салом", "привет", "hello", "hi"]):
            return self.tr("welcome")

        if any(x in cmd for x in ["соат", "время", "time"]):
            return now.strftime("%H:%M:%S")

        if any(x in cmd for x in ["сана", "дата", "date"]):
            return now.strftime("%d.%m.%Y")

        if any(x in cmd for x in ["ҳолат", "статус", "status"]):
            return (
                self.tr("online") + "\n"
                + self.tr("ready") + "\n"
                + THEMES[self.theme_index]["name"]
            )

        if any(x in cmd for x in ["мавзӯъ", "тема", "theme"]):
            self.next_theme()
            return THEMES[self.theme_index]["name"]

        if any(x in cmd for x in ["кӯмак", "помощь", "help"]):
            return self.tr("help") + ":\n" + self.tr("commands")

        return self.tr("unknown")

    # -------------------- MICROPHONE --------------------

    def show_microphone_info(self, *args):
        self.show_message(self.tr("mic_info"))

        popup = Popup(
            title=self.tr("listen"),
            content=make_label(self.tr("mic_info"), 14, WHITE),
            size_hint=(0.88, 0.34)
        )
        popup.open()

    # -------------------- SETTINGS --------------------

    def show_settings(self, *args):
        content = BoxLayout(
            orientation="vertical",
            spacing=dp(12),
            padding=dp(16)
        )

        info = make_label(
            self.tr("system") + "\n\n"
            + self.tr("online") + "\n\n"
            + "Theme: " + THEMES[self.theme_index]["name"]
            + "\nLanguage: " + self.language.upper(),
            13,
            CYAN
        )

        content.add_widget(info)

        popup = Popup(
            title=self.tr("settings"),
            content=content,
            size_hint=(0.88, 0.46)
        )

        close_button = self._make_button(
            self.tr("close"),
            lambda *_: popup.dismiss(),
            AMBER
        )
        content.add_widget(close_button)
        popup.open()

    # -------------------- ANDROID LIFECYCLE --------------------

    def on_pause(self):
        self.save_settings()
        return True

    def on_resume(self):
        self.refresh_text()

    def on_stop(self):
        self.save_settings()


if __name__ == "__main__":
    JarvisApp().run()
