# -*- coding: utf-8 -*-
"""
JARVIS Android Assistant
Kivy + Buildozer compatible. Core commands work offline.
"""
from datetime import datetime
import math
import os
import sys
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.utils import get_color_from_hex
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget


def write_crash_log(exc_type, exc_value, exc_tb):
    try:
        log_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, "jarvis_crash.log"), "w", encoding="utf-8") as f:
            f.write(log_text)
        try:
            from android.storage import primary_external_storage_path
            ext = primary_external_storage_path()
            with open(os.path.join(ext, "jarvis_crash.log"), "w", encoding="utf-8") as f:
                f.write(log_text)
        except Exception:
            pass
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = write_crash_log


# ---------------------------------------------------------------------------
# 4 modern interface themes
# ---------------------------------------------------------------------------
THEMES = [
    {"name": "IRON HUD", "accent": "#38D9FF", "accent2": "#167DFF",
     "background": "#050B14", "panel": "#0B1928", "text": "#EAFBFF"},
    {"name": "NEURAL CORE", "accent": "#62F5C8", "accent2": "#26B89B",
     "background": "#04110F", "panel": "#0A211D", "text": "#E4FFF7"},
    {"name": "HOLOGRAPHIC", "accent": "#C18CFF", "accent2": "#55CFFF",
     "background": "#0B0718", "panel": "#19112D", "text": "#F5EEFF"},
    {"name": "JARVIS SPHERE", "accent": "#54B7FF", "accent2": "#2C5CFF",
     "background": "#030914", "panel": "#09182C", "text": "#E8F5FF"},
]

VOICE_PROFILES = ["JARVIS Classic", "Aura"]

TEXTS = {
    "tg": {
        "ready": "ҶАРВИС ОМОДА АСТ",
        "online": "Система фаъол аст",
        "welcome": "Салом! Ман JARVIS ҳастам. Чӣ кор кунам?",
        "input": "Фармонро нависед...",
        "settings": "Танзимоти JARVIS",
        "language": "Забон",
        "theme": "Тарҳи интерфейс",
        "voice": "Тони овозӣ",
        "close": "Пӯшидан",
        "commands": "Фармонҳо: салом, соат, сана, ҳолат, мавзӯъ, кӯмак",
        "help": "Ман ҳоло фармонҳои асосии офлайнро иҷро карда метавонам",
        "unknown": "Ин фармон ҳоло дастгирӣ намешавад. Барои рӯйхати фармонҳо «кӯмак» нависед.",
        "mic": "Шинохти овоз ҳанӯз пайваст нашудааст.",
        "response": "Ҷавоби JARVIS",
        "time": "Вақти ҳозира",
        "date": "Санаи имрӯз",
        "system": "Ҳолат: онлайн\nМуҳаррик: Kivy",
        "voice_note": "Интихоби тон ҳоло танҳо номашро нигоҳ медорад; синтези овоз ҳанӯз пайваст нашудааст.",
    },
    "ru": {
        "ready": "JARVIS ГОТОВ",
        "online": "Система активна",
        "welcome": "Здравствуйте! Я JARVIS. Чем могу помочь?",
        "input": "Введите команду...",
        "settings": "Настройки JARVIS",
        "language": "Язык",
        "theme": "Дизайн интерфейса",
        "voice": "Голосовой тон",
        "close": "Закрыть",
        "commands": "Команды: привет, время, дата, статус, тема, помощь",
        "help": "Сейчас я выполняю основные офлайн-команды",
        "unknown": "Эта команда пока не поддерживается. Напишите «помощь» для списка команд.",
        "mic": "Распознавание речи ещё не подключено.",
        "response": "Ответ JARVIS",
        "time": "Текущее время",
        "date": "Сегодняшняя дата",
        "system": "Состояние: онлайн\nДвижок: Kivy",
        "voice_note": "Сейчас тон только сохраняет имя; синтез речи ещё не подключён.",
    },
    "en": {
        "ready": "JARVIS IS READY",
        "online": "System online",
        "welcome": "Hello! I am JARVIS. How can I help?",
        "input": "Type a command...",
        "settings": "JARVIS Settings",
        "language": "Language",
        "theme": "Interface design",
        "voice": "Voice tone",
        "close": "Close",
        "commands": "Commands: hello, time, date, status, theme, help",
        "help": "I can currently run basic offline commands",
        "unknown": "This command is not supported yet. Type 'help' for available commands.",
        "mic": "Speech recognition has not been connected yet.",
        "response": "JARVIS response",
        "time": "Current time",
        "date": "Today's date",
        "system": "State: online\nEngine: Kivy",
        "voice_note": "The tone name is saved, but speech synthesis is not connected yet.",
    },
}


def hex_color(value):
    return get_color_from_hex(value)


def make_label(text="", size=14, color="#EAFBFF", bold=False, **kwargs):
    label = Label(
        text=text,
        font_size=dp(size),
        color=hex_color(color) if isinstance(color, str) and color.startswith("#") else color,
        bold=bold,
        halign="center",
        valign="middle",
        **kwargs
    )
    label.bind(size=lambda instance, value: setattr(instance, "text_size", value))
    return label


class AnimatedCore(Widget):
    """Lightweight canvas-only visual; no external images or libraries."""
    def __init__(self, theme_index=0, **kwargs):
        super().__init__(**kwargs)
        self.theme_index = theme_index
        self.phase = 0.0
        self.bind(pos=self.redraw, size=self.redraw)
        Clock.schedule_interval(self.animate, 1 / 24)
        self.redraw()

    def animate(self, dt):
        self.phase = (self.phase + dt * 0.8) % (math.pi * 2)
        self.redraw()

    def set_theme(self, index):
        self.theme_index = index
        self.redraw()

    def redraw(self, *_):
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        theme = THEMES[self.theme_index]
        accent = hex_color(theme["accent"])
        accent2 = hex_color(theme["accent2"])
        cx, cy = self.center
        radius = min(self.width, self.height) * 0.36
        with self.canvas:
            Color(*accent, 0.18)
            Line(circle=(cx, cy, radius * 1.18), width=dp(1.0))
            Color(*accent, 0.7)
            Line(circle=(cx, cy, radius), width=dp(1.5))
            Color(*accent2, 0.65)
            Line(circle=(cx, cy, radius * 0.78), width=dp(1.0))
            for i in range(12):
                angle = self.phase + i * math.pi / 6
                r1, r2 = radius * 1.02, radius * (1.12 if i % 3 == 0 else 1.07)
                x1, y1 = cx + math.cos(angle) * r1, cy + math.sin(angle) * r1
                x2, y2 = cx + math.cos(angle) * r2, cy + math.sin(angle) * r2
                Color(*accent, 0.95 if i % 3 == 0 else 0.45)
                Line(points=[x1, y1, x2, y2], width=dp(2 if i % 3 == 0 else 1))
            if self.theme_index == 0:  # IRON HUD
                Color(*accent2, 0.16)
                Ellipse(pos=(cx-radius*0.48, cy-radius*0.48), size=(radius*0.96, radius*0.96))
                Color(*accent, 0.9)
                Line(points=[cx-radius*0.62, cy, cx+radius*0.62, cy], width=dp(1))
                Line(points=[cx, cy-radius*0.62, cx, cy+radius*0.62], width=dp(1))
                Color(*accent, 0.9)
                Line(circle=(cx, cy, radius*0.36), width=dp(2))
                Color(*accent2, 0.8)
                Ellipse(pos=(cx-radius*0.16, cy-radius*0.16), size=(radius*0.32, radius*0.32))
            elif self.theme_index == 1:  # NEURAL CORE
                points = []
                for i in range(8):
                    a = self.phase * 0.35 + i * math.pi / 4
                    points.append((cx + math.cos(a) * radius * (0.35 if i % 2 else 0.68),
                                   cy + math.sin(a) * radius * (0.35 if i % 2 else 0.68)))
                Color(*accent, 0.5)
                for i in range(len(points)):
                    x1, y1 = points[i]
                    x2, y2 = points[(i + 1) % len(points)]
                    Line(points=[x1, y1, x2, y2], width=dp(1))
                    Color(*accent2, 0.9)
                    Ellipse(pos=(x1-dp(4), y1-dp(4)), size=(dp(8), dp(8)))
                    Color(*accent, 0.5)
                Color(*accent, 0.85)
                Ellipse(pos=(cx-dp(9), cy-dp(9)), size=(dp(18), dp(18)))
            elif self.theme_index == 2:  # HOLOGRAPHIC
                for j in range(3):
                    rr = radius * (0.42 + j * 0.2)
                    angle = self.phase * (1 if j % 2 == 0 else -1)
                    pts = []
                    for k in range(5):
                        a = angle + k * math.pi / 2
                        pts.extend([cx + math.cos(a)*rr, cy + math.sin(a)*rr])
                    pts.extend(pts[:2])
                    Color(*(accent if j % 2 == 0 else accent2), 0.7-j*0.12)
                    Line(points=pts, width=dp(1.3))
                Color(*accent2, 0.22)
                Ellipse(pos=(cx-radius*0.28, cy-radius*0.28), size=(radius*0.56, radius*0.56))
            else:  # JARVIS SPHERE
                Color(*accent2, 0.20)
                Ellipse(pos=(cx-radius*0.58, cy-radius*0.58), size=(radius*1.16, radius*1.16))
                Color(*accent, 0.9)
                Line(circle=(cx, cy, radius*0.58), width=dp(1.6))
                for scale in (0.28, 0.48):
                    Color(*accent, 0.45)
                    Line(ellipse=(cx-radius*0.58, cy-radius*scale, radius*1.16, radius*scale*2), width=dp(1))
                Color(*accent, 0.75)
                Line(ellipse=(cx-radius*0.26, cy-radius*0.58, radius*0.52, radius*1.16), width=dp(1))


class JarvisApp(App):
    def build(self):
        try:
            self.title = "JARVIS"
            self.store = JsonStore(os.path.join(self.user_data_dir, "jarvis_settings.json"))
            self.language = "tg"
            self.theme_index = 0
            self.voice_index = 0
            self.last_response = ""
            self.load_settings()

            Window.clearcolor = hex_color(THEMES[self.theme_index]["background"])
            self.root_layout = FloatLayout()
            with self.root_layout.canvas.before:
                self.bg_color = Color(*hex_color(THEMES[self.theme_index]["background"]))
                self.bg_rect = Rectangle(pos=self.root_layout.pos, size=self.root_layout.size)
            self.root_layout.bind(pos=self.update_background, size=self.update_background)
            self.build_interface()
            return self.root_layout
        except Exception:
            import traceback as tb
            error_text = tb.format_exc()
            scroll = ScrollView()
            label = Label(text=error_text, font_size=dp(12), color=(1, 1, 1, 1),
                          size_hint_y=None, halign="left", valign="top")
            label.bind(texture_size=lambda inst, val: setattr(inst, "size", val))
            label.bind(width=lambda inst, val: setattr(inst, "text_size", (val, None)))
            scroll.add_widget(label)
            return scroll

    def update_background(self, *_):
        self.bg_rect.pos = self.root_layout.pos
        self.bg_rect.size = self.root_layout.size

    def t(self, key):
        return TEXTS.get(self.language, TEXTS["tg"]).get(key, key)

    def load_settings(self):
        if self.store.exists("settings"):
            data = self.store.get("settings")
            self.language = data.get("language", "tg")
            self.theme_index = int(data.get("theme_index", 0)) % len(THEMES)
            self.voice_index = int(data.get("voice_index", 0)) % len(VOICE_PROFILES)
        if self.language not in ("tg", "ru", "en"):
            self.language = "tg"

    def save_settings(self):
        self.store.put("settings", language=self.language,
                       theme_index=self.theme_index,
                       voice_index=self.voice_index)

    def update_colors(self):
        theme = THEMES[self.theme_index]
        Window.clearcolor = hex_color(theme["background"])
        self.bg_color.rgba = hex_color(theme["background"])

    def build_interface(self):
        self.root_layout.clear_widgets()
        theme = THEMES[self.theme_index]
        main = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        self.root_layout.add_widget(main)

        # Header: title + single hamburger (☰) settings icon in the corner
        header = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        title = make_label("J.A.R.V.I.S", 21, theme["accent"], True, halign="left")
        title.size_hint_x = 0.72
        menu_btn = Button(text="☰", size_hint_x=None, width=dp(54),
                          font_size=dp(26), color=hex_color(theme["accent"]),
                          background_normal="", background_color=(0, 0, 0, 0))
        menu_btn.bind(on_release=self.show_settings)
        header.add_widget(title)
        header.add_widget(Widget())
        header.add_widget(menu_btn)
        main.add_widget(header)

        self.status_label = make_label(self.t("ready"), 12, theme["accent"], True,
                                       size_hint_y=None, height=dp(25))
        main.add_widget(self.status_label)

        main.add_widget(AnimatedCore(self.theme_index, size_hint_y=1))

        response_panel = BoxLayout(orientation="vertical", padding=dp(10),
                                   spacing=dp(4), size_hint_y=None, height=dp(94))
        with response_panel.canvas.before:
            Color(*hex_color(theme["panel"]))
            response_panel.bg = Rectangle(pos=response_panel.pos, size=response_panel.size)
        response_panel.bind(pos=lambda w, v: setattr(w.bg, "pos", w.pos),
                            size=lambda w, v: setattr(w.bg, "size", w.size))
        self.response_title = make_label(self.t("response"), 10, theme["accent"], True,
                                          size_hint_y=None, height=dp(18))
        self.response_label = make_label(self.last_response or self.t("welcome"), 13,
                                         theme["text"], False)
        response_scroll = ScrollView(do_scroll_x=False)
        response_scroll.add_widget(self.response_label)
        response_panel.add_widget(self.response_title)
        response_panel.add_widget(response_scroll)
        main.add_widget(response_panel)

        command_row = BoxLayout(orientation="horizontal", spacing=dp(8),
                                size_hint_y=None, height=dp(54))

        input_wrap = BoxLayout(size_hint_x=1, padding=(dp(4), dp(4)))
        with input_wrap.canvas.before:
            Color(*hex_color(theme["panel"]))
            input_wrap.bg = RoundedRectangle(pos=input_wrap.pos, size=input_wrap.size,
                                             radius=[dp(24)])
        input_wrap.bind(pos=lambda w, v: setattr(w.bg, "pos", w.pos),
                        size=lambda w, v: setattr(w.bg, "size", w.size))
        self.command_input = TextInput(
            hint_text=self.t("input"), multiline=False, size_hint_x=1,
            font_size=dp(15), foreground_color=hex_color(theme["text"]),
            hint_text_color=hex_color(theme["accent2"]),
            cursor_color=hex_color(theme["accent"]),
            background_normal="", background_active="", background_color=(0, 0, 0, 0),
            padding=[dp(14), dp(14), dp(14), dp(10)],
        )
        self.command_input.bind(on_text_validate=self.send_command)
        input_wrap.add_widget(self.command_input)

        send_btn = Button(text="➤", size_hint_x=None, width=dp(54),
                          font_size=dp(21), color=hex_color(theme["background"]),
                          background_normal="", background_color=(0, 0, 0, 0))
        with send_btn.canvas.before:
            Color(*hex_color(theme["accent"]))
            send_btn.bg = Ellipse(pos=send_btn.pos, size=send_btn.size)
        send_btn.bind(pos=lambda w, v: setattr(w.bg, "pos", w.pos),
                      size=lambda w, v: setattr(w.bg, "size", w.size))
        send_btn.bind(on_release=self.send_command)

        command_row.add_widget(input_wrap)
        command_row.add_widget(send_btn)
        main.add_widget(command_row)

        footer = make_label(self.t("online") + "  •  " + theme["name"],
                            10, theme["accent2"], False, size_hint_y=None, height=dp(20))
        main.add_widget(footer)

    def show_response(self, message):
        self.last_response = message
        if hasattr(self, "response_label"):
            self.response_label.text = message

    def send_command(self, *_):
        if not hasattr(self, "command_input"):
            return
        command = self.command_input.text.strip()
        if not command:
            return
        self.command_input.text = ""
        answer = self.process_command(command)
        self.show_response(answer)
        self.status_label.text = self.t("ready")

    def process_command(self, command):
        cmd = command.lower().strip()
        now = datetime.now()

        greetings = ("салом", "привет", "здравствуй", "hello", "hi", "hey")
        if any(word in cmd for word in greetings):
            return self.t("welcome")
        if any(word in cmd for word in ("соат", "время", "time", "clock")):
            return self.t("time") + ": " + now.strftime("%H:%M:%S")
        if any(word in cmd for word in ("сана", "дата", "date", "today")):
            return self.t("date") + ": " + now.strftime("%d.%m.%Y")
        if any(word in cmd for word in ("ҳолат", "статус", "status", "system")):
            return self.t("system") + "\n\n" + THEMES[self.theme_index]["name"]
        if any(word in cmd for word in ("мавзӯъ", "тема", "theme", "design")):
            self.next_theme()
            return "Interface: " + THEMES[self.theme_index]["name"]
        if any(word in cmd for word in ("кӯмак", "помощь", "help", "команд")):
            return self.t("help") + "\n" + self.t("commands")
        if any(word in cmd for word in ("овоз", "голос", "voice", "microphone", "микрофон")):
            return self.t("mic") + "\n" + self.t("voice_note")
        return self.t("unknown")

    def next_theme(self, *_):
        self.theme_index = (self.theme_index + 1) % len(THEMES)
        self.save_settings()
        self.update_colors()
        self.build_interface()

    def show_settings(self, *_):
        theme = THEMES[self.theme_index]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(9))
        title = make_label(self.t("settings"), 17, theme["accent"], True,
                           size_hint_y=None, height=dp(30))
        content.add_widget(title)

        content.add_widget(make_label(self.t("theme"), 12, theme["text"], True,
                                      size_hint_y=None, height=dp(24)))
        for i, item in enumerate(THEMES):
            btn = Button(
                text=("✓  " if i == self.theme_index else "") + item["name"],
                size_hint_y=None, height=dp(39), font_size=dp(12),
                color=hex_color(item["text"]), background_normal="",
                background_color=hex_color(item["panel"]),
            )
            btn.bind(on_release=lambda instance, ix=i: self.choose_theme(ix, popup))
            content.add_widget(btn)

        content.add_widget(make_label(self.t("language"), 12, theme["text"], True,
                                      size_hint_y=None, height=dp(24)))
        language_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        for label, code in (("Тоҷикӣ", "tg"), ("Русский", "ru"), ("English", "en")):
            btn = Button(text=label, font_size=dp(11), background_normal="",
                         background_color=hex_color(theme["accent"] if self.language == code else theme["panel"]),
                         color=hex_color(theme["background"] if self.language == code else theme["text"]))
            btn.bind(on_release=lambda instance, c=code: self.choose_language(c, popup))
            language_row.add_widget(btn)
        content.add_widget(language_row)

        content.add_widget(make_label(self.t("voice"), 12, theme["text"], True,
                                      size_hint_y=None, height=dp(24)))
        voice_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        for i, name in enumerate(VOICE_PROFILES):
            btn = Button(text=name, font_size=dp(11), background_normal="",
                         background_color=hex_color(theme["accent"] if self.voice_index == i else theme["panel"]),
                         color=hex_color(theme["background"] if self.voice_index == i else theme["accent"]))
            btn.bind(on_release=lambda instance, ix=i: self.choose_voice(ix, popup))
            voice_row.add_widget(btn)
        content.add_widget(voice_row)

        content.add_widget(make_label(self.t("voice_note"), 10, theme["accent2"]))
        close_btn = Button(text=self.t("close"), size_hint_y=None, height=dp(40),
                           font_size=dp(13), bold=True, color=hex_color(theme["background"]),
                           background_normal="", background_color=hex_color(theme["accent"]))
        close_btn.bind(on_release=lambda *_: popup.dismiss())
        content.add_widget(close_btn)
        popup = Popup(title="", content=content, size_hint=(0.92, 0.88),
                      auto_dismiss=True, separator_height=0)
        popup.open()

    def choose_theme(self, index, popup):
        self.theme_index = index
        self.save_settings()
        popup.dismiss()
        self.update_colors()
        Clock.schedule_once(lambda dt: self.build_interface(), 0.1)

    def choose_language(self, code, popup):
        self.language = code
        self.save_settings()
        popup.dismiss()
        Clock.schedule_once(lambda dt: self.build_interface(), 0.1)

    def choose_voice(self, index, popup):
        self.voice_index = index
        self.save_settings()
        popup.dismiss()
        Clock.schedule_once(lambda dt: self.build_interface(), 0.1)
        Clock.schedule_once(lambda dt: self.show_response(self.t("voice_note")), 0.2)

    def on_pause(self):
        self.save_settings()
        return True

    def on_resume(self):
        self.save_settings()

    def on_stop(self):
        self.save_settings()


if __name__ == "__main__":
    try:
        JarvisApp().run()
    except Exception:
        write_crash_log(*sys.exc_info())
