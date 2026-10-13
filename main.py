# -*- coding: utf-8 -*-
"""
JARVIS Android Assistant
Kivy + Buildozer compatible. Core commands work offline.
Includes crash logger saving directly to /sdcard/Download/ for mobile debugging.
Only characters present in the default Roboto font are used (no missing-glyph boxes).
"""
from datetime import datetime
import math
import os
import random
import sys
import traceback

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.utils import get_color_from_hex
from kivy.graphics import Color, Ellipse, Line, Point, Rectangle, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget


# Системаи сабти хатогиҳо бевосита ба папкаи Download дар телефони мобилӣ
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    try:
        log_path = "/sdcard/Download/jarvis_crash_log.txt"
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(error_msg)
    except Exception as e:
        print("Error saving log:", e)

    sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = handle_exception


THEMES = [
    {"name": "IRON HUD", "accent": "#38D9FF", "accent2": "#167DFF",
     "background": "#050B14", "panel": "#0B1928", "text": "#EAFBFF"},
    {"name": "NEURAL CORE", "accent": "#62F5C8", "accent2": "#26B89B",
     "background": "#04110F", "panel": "#0A211D", "text": "#E4FFF7"},
    {"name": "HOLOGRAPHIC", "accent": "#C18CFF", "accent2": "#55CFFF",
     "background": "#0B0718", "panel": "#19112D", "text": "#F5EEFF"},
    {"name": "MINIMAL AI", "accent": "#F1F4F8", "accent2": "#8A9AAF",
     "background": "#101318", "panel": "#1B2028", "text": "#F5F7FA"},
    {"name": "JARVIS SPHERE", "accent": "#54B7FF", "accent2": "#2C5CFF",
     "background": "#030914", "panel": "#09182C", "text": "#E8F5FF"},
    {"name": "AURA ORB", "accent": "#7CC4FF", "accent2": "#2F6BFF",
     "background": "#02040A", "panel": "#0A1A33", "text": "#F2F6FF"},
    {"name": "PARTICLE CORE", "accent": "#62E6FF", "accent2": "#1A8FB0",
     "background": "#000000", "panel": "#101418", "text": "#E8FBFF"},
    {"name": "GLASS ORB", "accent": "#38C8FF", "accent2": "#1A7FB0",
     "background": "#01050A", "panel": "#08141F", "text": "#E8F8FF"},
]

VOICES = ("JARVIS Classic", "Aura")

LANGUAGES = {
    "Тоҷикӣ": "tg",
    "Русский": "ru",
    "English": "en",
}

TEXTS = {
    "tg": {
        "ready": "ҶАРВИС ОМОДА АСТ",
        "online": "Система фаъол аст",
        "welcome": "Салом! Ман JARVIS ҳастам. Чӣ кор кунам?",
        "input": "Фармонро нависед...",
        "send": "ФИРИСТОДАН",
        "settings": "Танзимоти JARVIS",
        "language": "Забон",
        "theme": "Тарҳи интерфейс",
        "voice": "Профили овоз",
        "close": "Пӯшидан",
        "status": "Ҳолати система",
        "commands": "Фармонҳо: салом, соат, сана, ҳолат, мавзӯъ, кӯмак",
        "help": "Ман ҳоло фармонҳои асосии офлайнро иҷро карда метавонам",
        "unknown": "Ин фармон ҳоло дастгирӣ намешавад. Барои рӯйхати фармонҳо «кӯмак» нависед.",
        "mic": "Шинохти овоз ҳанӯз пайваст нашудааст.",
        "response": "Ҷавоби JARVIS",
        "time": "Вақти ҳозира",
        "date": "Санаи имрӯз",
        "system": "Ҳолат: онлайн\nМуҳаррик: Kivy\nПайвасти интернет: аз ҷониби система муайян карда намешавад",
        "voice_note": "Интихоби профил ҳоло танҳо номи профилро нигоҳ медорад; синтези овоз ҳанӯз пайваст нашудааст.",
        "set": "ТАНЗИМ",
        "sec_theme": "Танзими интерфейс",
        "sec_lang": "Танзими забон",
        "sec_voice": "Тони садо",
        "title_theme": "Намудҳои интерфейс",
        "title_lang": "Забонҳо",
        "title_voice": "Мисолҳои садо",
        "back": "Бозгашт",
        "orb_hello": "Салом",
        "orb_ask": "Имрӯз чӣ тавр ёрдам расонам?",
        "p_ask": "Чӣ кор кунам?",
        "p_ready": "Омода барои гӯш кардан. Фармонро нависед",
    },
    "ru": {
        "ready": "JARVIS ГОТОВ",
        "online": "Система активна",
        "welcome": "Здравствуйте! Я JARVIS. Чем могу помочь?",
        "input": "Введите команду...",
        "send": "ОТПРАВИТЬ",
        "settings": "Настройки JARVIS",
        "language": "Язык",
        "theme": "Дизайн интерфейса",
        "voice": "Голосовой профиль",
        "close": "Закрыть",
        "status": "Состояние системы",
        "commands": "Команды: привет, время, дата, статус, тема, помощь",
        "help": "Сейчас я выполняю основные офлайн-команды",
        "unknown": "Эта команда пока не поддерживается. Напишите «помощь» для списка команд.",
        "mic": "Распознавание речи ещё не подключено.",
        "response": "Ответ JARVIS",
        "time": "Текущее время",
        "date": "Сегодняшняя дата",
        "system": "Состояние: онлайн\nДвижок: Kivy\nПодключение к интернету: не проверяется приложением",
        "voice_note": "Сейчас профиль только сохраняет выбранное имя; синтез речи ещё не подключён.",
        "set": "НАСТР.",
        "sec_theme": "Настройка интерфейса",
        "sec_lang": "Настройка языка",
        "sec_voice": "Тон голоса",
        "title_theme": "Виды интерфейса",
        "title_lang": "Языки",
        "title_voice": "Примеры голоса",
        "back": "Назад",
        "orb_hello": "Здравствуйте",
        "orb_ask": "Чем могу помочь сегодня?",
        "p_ask": "Что сделать?",
        "p_ready": "Готов слушать. Введите команду",
    },
    "en": {
        "ready": "JARVIS IS READY",
        "online": "System online",
        "welcome": "Hello! I am JARVIS. How can I help?",
        "input": "Type a command...",
        "send": "SEND",
        "settings": "JARVIS Settings",
        "language": "Language",
        "theme": "Interface design",
        "voice": "Voice profile",
        "close": "Close",
        "status": "System status",
        "commands": "Commands: hello, time, date, status, theme, help",
        "help": "I can currently run basic offline commands",
        "unknown": "This command is not supported yet. Type 'help' for available commands.",
        "mic": "Speech recognition has not been connected yet.",
        "response": "JARVIS response",
        "time": "Current time",
        "date": "Today's date",
        "system": "State: online\nEngine: Kivy\nInternet connectivity is not checked by this app",
        "voice_note": "The selected profile name is saved, but speech synthesis is not connected yet.",
        "set": "SET",
        "sec_theme": "Interface settings",
        "sec_lang": "Language settings",
        "sec_voice": "Voice tone",
        "title_theme": "Interface styles",
        "title_lang": "Languages",
        "title_voice": "Voice samples",
        "back": "Back",
        "orb_hello": "Hello",
        "orb_ask": "How can I help you today?",
        "p_ask": "What can I do?",
        "p_ready": "Ready to listen. Type a command",
    },
}


def hex_color(value):
    return get_color_from_hex(value)


def make_label(text="", size=14, color="#EAFBFF", bold=False, **kwargs):
    kwargs.setdefault("halign", "center")
    kwargs.setdefault("valign", "middle")
    label = Label(
        text=text,
        font_size=dp(size),
        color=hex_color(color) if isinstance(color, str) and color.startswith("#") else color,
        bold=bold,
        **kwargs
    )
    label.bind(size=lambda instance, value: setattr(instance, "text_size", value))
    return label


_TEX = {}


def _lerp_stops(stops, t):
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return [int(c0[j] + (c1[j] - c0[j]) * k) for j in range(4)]
    return list(stops[-1][1])


def _make_texture(size, data):
    tex = Texture.create(size=size, colorfmt="rgba")
    tex.blit_buffer(bytes(data), colorfmt="rgba", bufferfmt="ubyte")
    tex.mag_filter = "linear"
    tex.min_filter = "linear"
    return tex


def radial_texture(name, stops, size=96):
    """Pure-Python radial gradient (t=0 centre, t=1 edge). Built once, then cached."""
    if name not in _TEX:
        data = bytearray()
        half = (size - 1) / 2.0
        last = stops[-1][1]
        for yy in range(size):
            for xx in range(size):
                d = math.hypot(xx - half, yy - half) / half
                if d > 1.0:
                    data.extend((last[0], last[1], last[2], 0))
                else:
                    data.extend(_lerp_stops(stops, d))
        _TEX[name] = _make_texture((size, size), data)
    return _TEX[name]


def linear_texture(name, stops, rows=64):
    """Vertical gradient (t=0 bottom, t=1 top). Built once, then cached."""
    if name not in _TEX:
        data = bytearray()
        for i in range(rows):
            data.extend(_lerp_stops(stops, i / (rows - 1.0)))
        _TEX[name] = _make_texture((1, rows), data)
    return _TEX[name]


class AnimatedCore(Widget):
    """Lightweight canvas-only visual; no external images or libraries."""
    def __init__(self, theme_index=0, **kwargs):
        super().__init__(**kwargs)
        self.theme_index = theme_index
        self.phase = 0.0
        self.fib, self.dashes, self.reds = [], [], []
        if theme_index == 6:  # PARTICLE CORE: points on a sphere
            n = 520
            for k in range(n):
                y = 1 - 2 * (k + 0.5) / n
                rr = math.sqrt(1 - y * y)
                t = k * 2.39996
                self.fib.append((math.cos(t) * rr, y, math.sin(t) * rr))
        if theme_index == 7:  # GLASS ORB: fixed random dots on the sphere
            rnd = random.Random(7)
            self.dashes = [(rnd.uniform(0, math.pi * 2), rnd.uniform(-1.5, 1.5)) for _ in range(170)]
            self.reds = [(rnd.uniform(0, math.pi * 2), rnd.uniform(-1.2, 1.2)) for _ in range(6)]
        self.bind(pos=self.redraw, size=self.redraw)
        self._event = Clock.schedule_interval(self.animate, 1 / 20 if theme_index == 6 else 1 / 24)
        self.redraw()

    def on_parent(self, widget, parent):
        # Stop the animation timer when the widget is removed (theme change),
        # so old cores do not keep running in the background.
        if parent is None and getattr(self, "_event", None):
            self._event.cancel()
            self._event = None

    def animate(self, dt):
        app = App.get_running_app()
        if app is not None and getattr(app, "animation_paused", False):
            return
        self.phase = (self.phase + dt * 0.8) % (math.pi * 2)
        self.redraw()

    def set_theme(self, index):
        self.theme_index = index
        self.redraw()

    def draw_aura(self, cx, cy, R, accent, accent2):
        """AURA ORB: dark glass sphere, glowing violet/blue bands, soft halo."""
        halo = radial_texture("aura_halo", ((0, (50, 100, 255, 100)), (0.5, (30, 70, 220, 36)),
                                            (1, (20, 40, 160, 0))))
        Color(1, 1, 1, 1)
        Rectangle(texture=halo, pos=(cx - R * 2.1, cy - R * 2.1), size=(R * 4.2, R * 4.2))
        body = radial_texture("aura_body", ((0, (1, 3, 10, 255)), (0.75, (3, 10, 34, 255)),
                                            (1, (10, 42, 120, 255))))
        Rectangle(texture=body, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        palette = ((0.23, 0.48, 1.0), (0.54, 0.36, 1.0), (0.21, 0.82, 1.0),
                   (0.18, 0.42, 1.0), (0.69, 0.49, 1.0))
        for j in range(5):
            a_axis = R * 0.95
            b_axis = R * (0.28 + 0.14 * j + 0.04 * math.sin(self.phase * 1.3 + j))
            rot = self.phase * (0.5 + 0.12 * j) * (1 if j % 2 == 0 else -1) + j * 0.8
            cr, sr = math.cos(rot), math.sin(rot)
            pts = []
            for n in range(49):
                t = n / 48.0 * math.pi * 2
                ex, ey = a_axis * math.cos(t), b_axis * math.sin(t)
                pts.extend([cx + ex * cr - ey * sr, cy + ex * sr + ey * cr])
            r, g, b = palette[j]
            Color(r, g, b, 0.18)
            Line(points=pts, width=dp(4.5))
            Color(r, g, b, 0.55)
            Line(points=pts, width=dp(2))
            Color(0.8, 0.9, 1, 0.85)
            Line(points=pts, width=dp(0.9))
        inner = radial_texture("aura_inner", ((0, (40, 90, 255, 0)), (0.55, (40, 90, 255, 0)),
                                              (1, (60, 120, 255, 90))))
        Color(1, 1, 1, 1)
        Rectangle(texture=inner, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        Color(*accent2, 0.7)
        Line(circle=(cx, cy, R), width=dp(1.4))
        start = (self.phase * 40) % 360
        Color(0.75, 0.88, 1, 0.95)
        Line(circle=(cx, cy, R, start, start + 100), width=dp(2.6), cap="round")
        Color(0.78, 0.66, 1, 0.8)
        Line(circle=(cx, cy, R, start + 190, start + 245), width=dp(2.2), cap="round")

    def draw_particles(self, cx, cy, R, accent, accent2):
        """PARTICLE CORE: wobbling shell of glowing points inside a dark disc."""
        disc = radial_texture("p_disc", ((0, (11, 26, 32, 255)), (1, (4, 8, 11, 255))))
        Color(1, 1, 1, 1)
        Rectangle(texture=disc, pos=(cx - R * 1.3, cy - R * 1.3), size=(R * 2.6, R * 2.6))
        Color(*accent, 0.2)
        Line(circle=(cx, cy, R * 1.3), width=dp(1))
        glow = radial_texture("p_glow", ((0, (40, 200, 255, 0)), (0.55, (40, 200, 255, 0)),
                                         (0.8, (40, 200, 255, 40)), (1, (40, 200, 255, 0))))
        Color(1, 1, 1, 1)
        Rectangle(texture=glow, pos=(cx - R * 1.5, cy - R * 1.5), size=(R * 3, R * 3))
        p = self.phase
        cs, sn = math.cos(p * 0.5), math.sin(p * 0.5)
        tc, ts = 0.9004, 0.4350  # tilt of 0.45 rad
        near, mid, far = [], [], []
        for x, y, z in self.fib:
            n = (math.sin(x * 2.3 + p * 1.2) * math.sin(y * 2.9 - p * 0.9)
                 + 0.6 * math.sin(z * 3.3 + p * 1.5) + 0.4 * math.sin((x + y + z) * 5 + p * 2))
            rr = 1 + 0.13 * n
            X, Y, Z = x * rr, y * rr, z * rr
            x1 = X * cs + Z * sn
            z1 = -X * sn + Z * cs
            y1 = Y * tc - z1 * ts
            z2 = Y * ts + z1 * tc
            pt = (cx + x1 * R, cy + y1 * R)
            if z2 > 0.35:
                near.extend(pt)
            elif z2 > -0.3:
                mid.extend(pt)
            else:
                far.extend(pt)
        if far:
            Color(0.10, 0.56, 0.69, 0.55)
            Point(points=far, pointsize=dp(0.8))
        if mid:
            Color(0.38, 0.90, 1, 0.8)
            Point(points=mid, pointsize=dp(1.0))
        if near:
            Color(0.85, 0.98, 1, 1)
            Point(points=near, pointsize=dp(1.3))

    def draw_glass(self, cx, cy, R, accent, accent2):
        """GLASS ORB: dark glass sphere on a reflective floor with light pillars."""
        p = self.phase
        fy = cy - R * 1.02
        w = self.width
        floor = linear_texture("g_floor", ((0, (0, 0, 0, 0)), (1, (10, 40, 70, 140))))
        Color(1, 1, 1, 1)
        Rectangle(texture=floor, pos=(self.x, self.y), size=(w, max(fy - self.y, 1)))
        pillar = linear_texture("g_pillar", ((0, (60, 180, 255, 255)), (0.4, (50, 170, 255, 255)),
                                             (1, (40, 160, 255, 0))))
        refl = radial_texture("g_refl", ((0, (70, 190, 255, 130)), (1, (70, 190, 255, 0))))
        for fx, a in ((0.10, 0.5), (0.90, 0.5), (0.03, 0.25), (0.97, 0.25)):
            x = self.x + w * fx
            Color(1, 1, 1, a)
            Rectangle(texture=pillar, pos=(x - dp(2.5), fy), size=(dp(5), max(self.top - fy - dp(6), 1)))
            rw = w * 0.32
            Rectangle(texture=refl, pos=(x - rw / 2, fy - rw * 0.125), size=(rw, rw * 0.25))
        Color(1, 1, 1, 1)
        Rectangle(texture=refl, pos=(cx - R * 1.1, fy - R * 0.22), size=(R * 2.2, R * 0.44))
        Color(0.22, 0.78, 1, 0.2)
        for k in range(-6, 7):
            x = cx + k * R * 0.13
            Line(points=[x, fy, x + k * 2, fy - R * 0.5], width=dp(0.6))
        body = radial_texture("g_body", ((0, (10, 24, 34, 255)), (0.8, (3, 9, 15, 255)),
                                         (1, (11, 42, 60, 255))))
        Color(1, 1, 1, 1)
        Rectangle(texture=body, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        for k in range(14):  # meridian lines
            a = k / 14.0 * math.pi + p * 0.35
            rx = max(R * abs(math.cos(a)), dp(0.4))
            Color(0.22, 0.78, 1, 0.07 + 0.2 * abs(math.sin(a)))
            Line(ellipse=(cx - rx, cy - R * 0.995, 2 * rx, 2 * R * 0.995, 0, 360, 40), width=dp(0.6))
        front, back, red = [], [], []
        for lon, lat in self.dashes:
            lo = lon + p * 0.35
            x = math.cos(lat) * math.sin(lo)
            z = math.cos(lat) * math.cos(lo)
            y = math.sin(lat)
            (front if z > 0.1 else back).extend((cx + x * R * 0.97, cy + y * R * 0.97))
        for lon, lat in self.reds:
            lo = lon + p * 0.35
            if math.cos(lat) * math.cos(lo) > 0:
                red.extend((cx + math.cos(lat) * math.sin(lo) * R * 0.97, cy + math.sin(lat) * R * 0.97))
        if back:
            Color(0.37, 0.85, 1, 0.35)
            Point(points=back, pointsize=dp(0.7))
        if front:
            Color(0.45, 0.90, 1, 0.9)
            Point(points=front, pointsize=dp(0.9))
        if red:
            Color(1, 0.35, 0.29, 0.9)
            Point(points=red, pointsize=dp(1.1))
        inner = radial_texture("g_inner", ((0, (40, 160, 230, 0)), (0.7, (40, 160, 230, 0)),
                                           (1, (40, 170, 240, 70))))
        Color(1, 1, 1, 1)
        Rectangle(texture=inner, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        pl = 0.7 + 0.3 * math.sin(p * 2)
        Color(0.22, 0.78, 1, 0.25 * pl)
        Line(points=[cx - R, cy, cx + R, cy], width=dp(4))
        Color(0.55, 0.92, 1, 0.95 * pl)
        Line(points=[cx - R * 0.97, cy, cx + R * 0.97, cy], width=dp(1.4))
        Color(0.91, 0.98, 1, 0.8 * pl)
        Line(points=[cx, cy - R * 0.18, cx, cy + R * 0.18], width=dp(1))
        flare = radial_texture("g_flare", ((0, (220, 250, 255, 240)), (1, (56, 200, 255, 0))))
        Color(1, 1, 1, pl)
        Rectangle(texture=flare, pos=(cx - R * 0.22, cy - R * 0.22), size=(R * 0.44, R * 0.44))
        Color(0.35, 0.82, 1, 0.85)
        Line(circle=(cx, cy, R), width=dp(1.3))
        Color(1, 1, 1, 0.92)  # two glossy highlights (angles run clockwise from the top)
        Line(circle=(cx, cy, R * 0.9, 303, 327), width=R * 0.035, cap="round")
        Line(circle=(cx, cy, R * 0.9, 33, 57), width=R * 0.035, cap="round")

    def redraw(self, *_):
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        theme = THEMES[self.theme_index]
        accent = hex_color(theme["accent"])[:3]
        accent2 = hex_color(theme["accent2"])[:3]
        cx, cy = self.center
        radius = min(self.width, self.height) * 0.36
        with self.canvas:
            if self.theme_index == 5:
                self.draw_aura(cx, cy, min(self.width, self.height) * 0.30, accent, accent2)
                return
            if self.theme_index == 6:
                self.draw_particles(cx, cy, min(self.width, self.height) * 0.33, accent, accent2)
                return
            if self.theme_index == 7:
                self.draw_glass(self.center_x, self.y + self.height * 0.56,
                                min(self.width, self.height) * 0.34, accent, accent2)
                return
            # Outer rings
            Color(*accent, 0.18)
            Line(circle=(cx, cy, radius * 1.18), width=dp(1.0))
            Color(*accent, 0.7)
            Line(circle=(cx, cy, radius), width=dp(1.5))
            Color(*accent2, 0.65)
            Line(circle=(cx, cy, radius * 0.78), width=dp(1.0))
            # Rotating arc markers
            for i in range(12):
                angle = self.phase + i * math.pi / 6
                r1, r2 = radius * 1.02, radius * (1.12 if i % 3 == 0 else 1.07)
                x1, y1 = cx + math.cos(angle) * r1, cy + math.sin(angle) * r1
                x2, y2 = cx + math.cos(angle) * r2, cy + math.sin(angle) * r2
                Color(*accent, 0.95 if i % 3 == 0 else 0.45)
                Line(points=[x1, y1, x2, y2], width=dp(2 if i % 3 == 0 else 1))
            if self.theme_index == 0:  # IRON HUD: reactor core and crosshair
                Color(*accent2, 0.16)
                Ellipse(pos=(cx-radius*0.48, cy-radius*0.48), size=(radius*0.96, radius*0.96))
                Color(*accent, 0.9)
                Line(points=[cx-radius*0.62, cy, cx+radius*0.62, cy], width=dp(1))
                Line(points=[cx, cy-radius*0.62, cx, cy+radius*0.62], width=dp(1))
                Color(*accent, 0.9)
                Line(circle=(cx, cy, radius*0.36), width=dp(2))
                Color(*accent2, 0.8)
                Ellipse(pos=(cx-radius*0.16, cy-radius*0.16), size=(radius*0.32, radius*0.32))
            elif self.theme_index == 1:  # NEURAL CORE: node connections
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
            elif self.theme_index == 2:  # HOLOGRAPHIC: layered diamond geometry
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
            elif self.theme_index == 3:  # MINIMAL AI: clean concentric geometry
                Color(*accent, 0.9)
                Line(circle=(cx, cy, radius*0.64), width=dp(1))
                Line(circle=(cx, cy, radius*0.28), width=dp(1))
                Color(*accent, 0.95)
                Ellipse(pos=(cx-dp(3), cy-dp(3)), size=(dp(6), dp(6)))
                Color(*accent2, 0.5)
                Line(points=[cx-radius*0.9, cy-radius*0.9, cx+radius*0.9, cy+radius*0.9], width=dp(0.6))
            else:  # JARVIS SPHERE: luminous orb with latitude curves
                Color(*accent2, 0.20)
                Ellipse(pos=(cx-radius*0.58, cy-radius*0.58), size=(radius*1.16, radius*1.16))
                Color(*accent, 0.9)
                Line(circle=(cx, cy, radius*0.58), width=dp(1.6))
                for scale in (0.28, 0.48):
                    Color(*accent, 0.45)
                    Line(ellipse=(cx-radius*0.58, cy-radius*scale, radius*1.16, radius*scale*2), width=dp(1))
                Color(*accent, 0.75)
                Line(ellipse=(cx-radius*0.26, cy-radius*0.58, radius*0.52, radius*1.16), width=dp(1))


class WaveButton(Button):
    """Send button with a sound-wave icon drawn on canvas (no font glyphs needed)."""
    def __init__(self, bar_color=(1, 1, 1, 1), **kwargs):
        super().__init__(**kwargs)
        self.bar_color = bar_color
        self.text = ""
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.bind(pos=self.draw_bars, size=self.draw_bars)
        self.draw_bars()

    def draw_bars(self, *_):
        self.canvas.after.clear()
        cx, cy = self.center
        with self.canvas.after:
            Color(*self.bar_color)
            for i, h in enumerate((0.35, 0.7, 1.0, 0.6, 0.4)):
                x = cx + (i - 2) * dp(5)
                half = dp(22) * h / 2
                Line(points=[x, cy - half, x, cy + half], width=dp(1.5), cap="round")


class JarvisApp(App):
    def build(self):
        self.title = "JARVIS"
        self.store = JsonStore(os.path.join(self.user_data_dir, "jarvis_settings.json"))
        self.language = "tg"
        self.theme_index = 0
        self.voice_profile = "JARVIS Classic"
        self.last_response = ""
        self.command_history = []
        self.animation_paused = False
        self.load_settings()

        Window.clearcolor = hex_color(THEMES[self.theme_index]["background"])
        self.root_layout = FloatLayout()
        with self.root_layout.canvas.before:
            self.bg_color = Color(*hex_color(THEMES[self.theme_index]["background"]))
            self.bg_rect = Rectangle(pos=self.root_layout.pos, size=self.root_layout.size)
            # Blue glow rising from the bottom (visible only in AURA ORB)
            self.grad_color = Color(1, 1, 1, 1 if self.theme_index == 5 else 0)
            self.grad_rect = Rectangle(texture=self.make_gradient(),
                                       pos=self.root_layout.pos, size=(0, 0))
        self.root_layout.bind(pos=self.update_background, size=self.update_background)
        self.build_interface()
        return self.root_layout

    def make_gradient(self):
        rows = 64
        data = bytearray()
        for i in range(rows):  # row 0 = bottom
            t = i / (rows - 1.0)
            r = int(60 + (2 - 60) * t)
            g = int(130 + (6 - 130) * t)
            b = int(255 + (20 - 255) * t)
            a = int(235 * (1 - t) ** 1.6)
            data.extend((r, g, b, a))
        tex = Texture.create(size=(1, rows), colorfmt="rgba")
        tex.blit_buffer(bytes(data), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
        return tex

    def update_background(self, *_):
        self.bg_rect.pos = self.root_layout.pos
        self.bg_rect.size = self.root_layout.size
        self.grad_rect.pos = self.root_layout.pos
        self.grad_rect.size = (self.root_layout.width, self.root_layout.height * 0.42)

    def update_pill(self, widget, *_):
        widget.pill.pos = widget.pos
        widget.pill.size = widget.size
        widget.pill_line.rounded_rectangle = (widget.x, widget.y, widget.width, widget.height, dp(26))

    def t(self, key):
        return TEXTS.get(self.language, TEXTS["tg"]).get(key, key)

    def load_settings(self):
        if self.store.exists("settings"):
            data = self.store.get("settings")
            self.language = data.get("language", "tg")
            self.theme_index = int(data.get("theme_index", 0)) % len(THEMES)
            self.voice_profile = data.get("voice_profile", "JARVIS Classic")
        if self.language not in ("tg", "ru", "en"):
            self.language = "tg"

    def save_settings(self):
        self.store.put("settings", language=self.language,
                       theme_index=self.theme_index,
                       voice_profile=self.voice_profile)

    def update_colors(self):
        theme = THEMES[self.theme_index]
        Window.clearcolor = hex_color(theme["background"])
        self.bg_color.rgba = hex_color(theme["background"])
        self.grad_color.a = 1 if self.theme_index == 5 else 0

    def make_button(self, text, callback, color=None, height=44):
        theme = THEMES[self.theme_index]
        accent = color or theme["accent"]
        btn = Button(
            text=text,
            size_hint_y=None,
            height=dp(height),
            font_size=dp(13),
            bold=True,
            color=hex_color(theme["background"]),
            background_normal="",
            background_down="",
            background_color=hex_color(accent),
        )
        btn.bind(on_release=callback)
        return btn

    def make_panel(self, padding=12, spacing=8, orientation="vertical"):
        theme = THEMES[self.theme_index]
        panel = BoxLayout(orientation=orientation, padding=dp(padding),
                          spacing=dp(spacing), size_hint_y=None)
        panel.bind(minimum_height=panel.setter("height"))
        with panel.canvas.before:
            Color(*hex_color(theme["panel"]))
            panel.bg = Rectangle(pos=panel.pos, size=panel.size)
        panel.bind(pos=lambda w, v: setattr(w.bg, "pos", w.pos),
                   size=lambda w, v: setattr(w.bg, "size", w.size))
        return panel

    def build_interface(self):
        self.root_layout.clear_widgets()
        theme = THEMES[self.theme_index]
        main = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        self.root_layout.add_widget(main)

        # Header: title and settings button (plain text, no special glyphs)
        header = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        title = make_label("J.A.R.V.I.S", 21, theme["accent"], True, halign="left")
        title.size_hint_x = 0.6
        settings_btn = Button(text=self.t("set"), size_hint_x=None, width=dp(84),
                              font_size=dp(13), bold=True, color=hex_color(theme["accent"]),
                              background_normal="", background_down="",
                              background_color=hex_color(theme["panel"]))
        settings_btn.bind(on_release=self.show_settings)
        header.add_widget(title)
        header.add_widget(Widget())
        header.add_widget(settings_btn)
        main.add_widget(header)

        self.status_label = make_label(self.t("ready"), 12, theme["accent"], True,
                                       size_hint_y=None, height=dp(25))
        main.add_widget(self.status_label)

        # Main visual zone changes structure for each theme.
        self.visual_area = BoxLayout(orientation="vertical", spacing=dp(8))
        main.add_widget(self.visual_area)
        self.build_theme_layout()

        # Response panel
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

        # Command bar remains at the bottom in every design.
        pill = self.theme_index in (5, 6, 7)
        command_row = BoxLayout(orientation="horizontal", spacing=dp(7),
                                size_hint_y=None, height=dp(52))
        if pill:  # rounded "Ask anything..." bar
            with command_row.canvas.before:
                Color(*hex_color(theme["accent2"])[:3], 0.35)
                command_row.pill = RoundedRectangle(pos=command_row.pos, size=command_row.size,
                                                    radius=[dp(26)])
                Color(*hex_color(theme["accent"])[:3], 0.7)
                command_row.pill_line = Line(
                    rounded_rectangle=(command_row.x, command_row.y, command_row.width,
                                       command_row.height, dp(26)), width=dp(1.2))
            command_row.bind(pos=self.update_pill, size=self.update_pill)
        self.command_input = TextInput(
            hint_text=self.t("input"), multiline=False, size_hint_x=1,
            font_size=dp(15), foreground_color=hex_color(theme["text"]),
            hint_text_color=((*hex_color(theme["text"])[:3], 0.7) if pill
                             else hex_color(theme["accent2"])),
            cursor_color=hex_color(theme["accent"]),
            background_normal="", background_active="",
            background_color=((0, 0, 0, 0) if pill else hex_color(theme["panel"])),
            padding=[dp(22 if pill else 10), dp(15 if pill else 13), dp(10), dp(10)],
        )
        self.command_input.bind(on_text_validate=self.send_command)
        if pill:
            send_btn = WaveButton(bar_color=hex_color(theme["text"]),
                                  size_hint_x=None, width=dp(58))
        else:
            send_btn = Button(text=">", size_hint_x=None, width=dp(58),
                              font_size=dp(28), bold=True, color=hex_color(theme["background"]),
                              background_normal="", background_down="",
                              background_color=hex_color(theme["accent"]))
        send_btn.bind(on_release=self.send_command)
        command_row.add_widget(self.command_input)
        command_row.add_widget(send_btn)
        main.add_widget(command_row)

        footer = make_label(self.t("online") + "  \u2022  " + THEMES[self.theme_index]["name"],
                            10, theme["accent2"], False, size_hint_y=None, height=dp(20))
        main.add_widget(footer)
        self.footer_label = footer

    def build_theme_layout(self):
        self.visual_area.clear_widgets()
        theme = THEMES[self.theme_index]
        index = self.theme_index
        dot = "\u2022"  # bullet is present in Roboto

        if index == 0:  # IRON HUD: visual + technical side panels
            top = BoxLayout(orientation="horizontal", spacing=dp(5), size_hint_y=None, height=dp(45))
            top.add_widget(self.metric_card("CORE", "98.7%", theme["accent"]))
            top.add_widget(self.metric_card("LINK", "ONLINE", theme["accent2"]))
            top.add_widget(self.metric_card("MODE", "READY", theme["accent"]))
            self.visual_area.add_widget(top)
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1))
            self.visual_area.add_widget(make_label("//  TACTICAL SYSTEM  //", 11, theme["accent"], True,
                                                   size_hint_y=None, height=dp(24)))
        elif index == 1:  # NEURAL CORE: network label + node matrix + core
            self.visual_area.add_widget(make_label("NEURAL NETWORK / ACTIVE", 12, theme["accent"], True,
                                                   size_hint_y=None, height=dp(26)))
            nodes = GridLayout(cols=3, spacing=dp(6), size_hint_y=None, height=dp(92))
            for label in ("INPUT NODE", "MEMORY", "REASONING",
                          "LANGUAGE", "COMMAND", "OUTPUT"):
                nodes.add_widget(self.metric_card(dot, label, theme["accent"]))
            self.visual_area.add_widget(nodes)
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1))
        elif index == 2:  # HOLOGRAPHIC: stacked transparent-looking projection panels
            holo = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(58))
            holo.add_widget(self.metric_card("PROJECTION", "ACTIVE", theme["accent"]))
            holo.add_widget(self.metric_card("HOLO LINK", "STABLE", theme["accent2"]))
            self.visual_area.add_widget(holo)
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1))
            self.visual_area.add_widget(make_label("[ HOLOGRAPHIC INTERFACE ]", 12, theme["accent"], True,
                                                   size_hint_y=None, height=dp(28)))
        elif index == 3:  # MINIMAL AI: clean typography and short info
            self.visual_area.add_widget(Widget(size_hint_y=0.12))
            self.visual_area.add_widget(make_label("JARVIS", 32, theme["text"], True,
                                                   size_hint_y=None, height=dp(52)))
            self.visual_area.add_widget(make_label("Personal AI interface", 13, theme["accent2"],
                                                   size_hint_y=None, height=dp(28)))
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1))
            self.visual_area.add_widget(make_label("READY  /  WAITING FOR COMMAND", 10,
                                                   theme["accent"], True, size_hint_y=None, height=dp(24)))
            self.visual_area.add_widget(Widget(size_hint_y=0.1))
        elif index == 4:  # JARVIS SPHERE: large orb + orbital status chips
            self.visual_area.add_widget(make_label("SPHERICAL AI CORE", 12, theme["accent"], True,
                                                   size_hint_y=None, height=dp(24)))
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1))
            orbit = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(42))
            orbit.add_widget(self.metric_card(dot, "STABLE", theme["accent"]))
            orbit.add_widget(self.metric_card("o", "SCANNING", theme["accent2"]))
            orbit.add_widget(self.metric_card("+", "READY", theme["accent"]))
            self.visual_area.add_widget(orbit)
        elif index == 5:  # AURA ORB: glowing orb + greeting
            self.visual_area.add_widget(Widget(size_hint_y=0.05))
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1.5))
            self.visual_area.add_widget(make_label(self.t("orb_hello"), 14, "#8A9AAF",
                                                   False, size_hint_y=None, height=dp(28)))
            self.visual_area.add_widget(make_label(self.t("orb_ask"), 26, theme["text"], True,
                                                   size_hint_y=None, height=dp(90)))
        elif index == 6:  # PARTICLE CORE: point-cloud orb
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1.6))
            self.visual_area.add_widget(make_label(self.t("orb_hello"), 12, "#8A9AAF",
                                                   False, size_hint_y=None, height=dp(24)))
            self.visual_area.add_widget(make_label(self.t("p_ask"), 24, theme["text"], True,
                                                   size_hint_y=None, height=dp(48)))
            self.visual_area.add_widget(make_label(self.t("p_ready"), 10, "#9FB4C8",
                                                   False, size_hint_y=None, height=dp(30)))
        else:  # GLASS ORB: glass sphere on a reflective floor
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1.7))
            self.visual_area.add_widget(make_label(self.t("orb_hello"), 12, "#8A9AAF",
                                                   False, size_hint_y=None, height=dp(24)))
            self.visual_area.add_widget(make_label(self.t("p_ask"), 24, theme["text"], True,
                                                   size_hint_y=None, height=dp(48)))

    def metric_card(self, heading, value, color):
        theme = THEMES[self.theme_index]
        card = BoxLayout(orientation="vertical", padding=dp(4), spacing=dp(1))
        with card.canvas.before:
            Color(*hex_color(theme["panel"]))
            card.bg = Rectangle(pos=card.pos, size=card.size)
            Color(*hex_color(color)[:3], 0.7)
            card.line = Line(rectangle=(card.x, card.y, card.width, card.height), width=dp(1))
        card.bind(pos=lambda w, v: self.update_card_canvas(w),
                  size=lambda w, v: self.update_card_canvas(w))
        card.add_widget(make_label(heading, 9, color, True))
        card.add_widget(make_label(value, 9, theme["text"], False))
        return card

    def update_card_canvas(self, card):
        card.bg.pos = card.pos
        card.bg.size = card.size
        card.line.rectangle = (card.x, card.y, card.width, card.height)

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

        greetings = ("салом", "салом алейкум", "привет", "здравствуй", "hello", "hi", "hey")
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
        self.open_section("main")

    def open_section(self, section):
        """Settings are split into folders: main menu -> theme / language / voice."""
        old = getattr(self, "settings_popup", None)
        if old is not None:
            old.dismiss()
        theme = THEMES[self.theme_index]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(9))
        titles = {"main": "settings", "theme": "title_theme",
                  "language": "title_lang", "voice": "title_voice"}
        content.add_widget(make_label(self.t(titles[section]), 17, theme["accent"], True,
                                      size_hint_y=None, height=dp(30)))

        def option(text, active, callback, height=48, fg_inactive=None, parent=None):
            btn = Button(
                text=text, size_hint_y=None, height=dp(height), font_size=dp(13), bold=active,
                background_normal="", background_down="",
                background_color=hex_color(theme["accent"] if active else theme["panel"]),
                color=hex_color(theme["background"] if active else (fg_inactive or theme["text"])),
            )
            btn.bind(on_release=callback)
            (parent if parent is not None else content).add_widget(btn)

        if section == "main":
            for key, target in (("sec_theme", "theme"), ("sec_lang", "language"),
                                ("sec_voice", "voice")):
                option(self.t(key), False,
                       lambda inst, s=target: self.open_section(s),
                       height=58, fg_inactive=theme["accent"])
            content.add_widget(Widget())
        elif section == "theme":
            grid = GridLayout(cols=1, spacing=dp(9), size_hint_y=None)
            grid.bind(minimum_height=grid.setter("height"))
            for i, item in enumerate(THEMES):
                option(item["name"], i == self.theme_index,
                       lambda inst, ix=i: self.choose_theme(ix, self.settings_popup),
                       parent=grid)
            scroll = ScrollView(do_scroll_x=False)
            scroll.add_widget(grid)
            content.add_widget(scroll)
        elif section == "language":
            for label, code in (("Тоҷикӣ", "tg"), ("Русский", "ru"), ("English", "en")):
                option(label, self.language == code,
                       lambda inst, c=code: self.choose_language(c, self.settings_popup),
                       height=54)
            content.add_widget(Widget())
        else:  # voice
            for name in VOICES:
                option(name, self.voice_profile == name,
                       lambda inst, n=name: self.choose_voice(n, self.settings_popup),
                       height=54)
            content.add_widget(make_label(self.t("voice_note"), 10, theme["accent2"]))
            content.add_widget(Widget())

        nav = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        if section != "main":
            nav.add_widget(self.make_button(self.t("back"),
                                            lambda *_: self.open_section("main"),
                                            theme["accent2"], 44))
        nav.add_widget(self.make_button(self.t("close"),
                                        lambda *_: self.settings_popup.dismiss(),
                                        theme["accent"], 44))
        content.add_widget(nav)

        self.settings_popup = Popup(title="", content=content, size_hint=(0.92, 0.88),
                                    auto_dismiss=True, separator_height=0)
        self.settings_popup.bind(on_dismiss=self.settings_closed)
        self.animation_paused = True  # save battery while settings are open
        self.settings_popup.open()

    def settings_closed(self, popup, *_):
        if popup is self.settings_popup:
            self.animation_paused = False

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

    def choose_voice(self, name, popup):
        self.voice_profile = name
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
    JarvisApp().run()
