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
import time
import traceback

from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.utils import get_color_from_hex
from kivy.utils import platform

if platform == "android":
    from jnius import autoclass, PythonJavaClass, java_method
  
from kivy.graphics import (Color, Ellipse, Line, Point, Rectangle,
                           RoundedRectangle, Triangle)
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


# ---- Crash log: saved in the app's private folder (always writable) and in Download ----
CRASH_FILE = "jarvis_crash_log.txt"


def crash_dirs():
    dirs = []
    priv = os.environ.get("ANDROID_PRIVATE")
    dirs.append(priv if priv else os.getcwd())
    dirs.append("/sdcard/Download")
    return dirs


def save_crash(text):
    saved = None
    for d in crash_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, CRASH_FILE)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            saved = saved or path
        except Exception:
            pass
    return saved


def read_last_crash():
    """Returns the crash text saved by the previous run (shown once inside the app)."""
    path = os.path.join(crash_dirs()[0], CRASH_FILE)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            os.replace(path, path + ".old")
            return text
    except Exception:
        pass
    return None


def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    save_crash("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


sys.excepthook = handle_exception


class JarvisExceptionHandler(ExceptionHandler):
    """Keeps the app alive when a callback fails; the error is saved and shown on screen."""
    def handle_exception(self, inst):
        try:
            text = "".join(traceback.format_exception(type(inst), inst, inst.__traceback__))
            save_crash(text)
            app = App.get_running_app()
            if app is not None:
                app.report_error(text)
        except Exception:
            pass
        return ExceptionManager.PASS


THEMES = [
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
        "listening": "Гӯш мекунам...",
        "speaking": "Ҷавоб медиҳам...",
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
        "listening": "Слушаю...",
        "speaking": "Отвечаю...",
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
        "listening": "Listening...",
        "speaking": "Speaking...",
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


GLASS_ERRORS = []


def _move_glass(w, *_):
    x, y = w.pos
    wd, ht = w.size
    inset = max(dp(2), w._g_r * 0.8)
    w._g_fill.pos = (x, y)
    w._g_fill.size = (wd, ht)
    w._g_sheen.pos = (x + inset, y + ht * 0.52)
    w._g_sheen.size = (max(wd - 2 * inset, 1), max(ht * 0.46 - dp(1.5), 1))
    w._g_glow.pos = (x + inset, y + dp(1.5))
    w._g_glow.size = (max(wd - 2 * inset, 1), max(ht * 0.26, 1))
    w._g_line.rounded_rectangle = (x, y, wd, ht, w._g_r)


def glass_style(widget, tint, radius, fill=0.18, sheen=0.5, border=0.4):
    """Liquid-glass look behind a widget: translucent tint, bright top sheen, soft bottom
    glow and a light edge. The canvas instructions are created once and only moved later."""
    try:
        sheen_tex = linear_texture("glass_sheen", ((0, (255, 255, 255, 0)), (1, (255, 255, 255, 120))))
        glow_tex = linear_texture("glass_glow", ((0, (255, 255, 255, 80)), (1, (255, 255, 255, 0))))
        with widget.canvas.before:
            widget._g_fillc = Color(tint[0], tint[1], tint[2], fill)
            widget._g_fill = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
            Color(1, 1, 1, sheen)
            widget._g_sheen = Rectangle(texture=sheen_tex, pos=widget.pos, size=(1, 1))
            Color(tint[0] * 0.5 + 0.5, tint[1] * 0.5 + 0.5, tint[2] * 0.5 + 0.5, 0.45)
            widget._g_glow = Rectangle(texture=glow_tex, pos=widget.pos, size=(1, 1))
            Color(1, 1, 1, border)
            widget._g_line = Line(rounded_rectangle=(widget.x, widget.y, widget.width,
                                                     widget.height, radius), width=dp(1))
        widget._g_r = radius
        widget.bind(pos=_move_glass, size=_move_glass)
        _move_glass(widget)
    except Exception:
        if not GLASS_ERRORS:
            GLASS_ERRORS.append(traceback.format_exc())
        try:  # plain translucent fallback
            with widget.canvas.before:
                Color(tint[0], tint[1], tint[2], fill + 0.1)
                widget._g_plain = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
            widget.bind(pos=lambda w, v: setattr(w._g_plain, "pos", w.pos),
                        size=lambda w, v: setattr(w._g_plain, "size", w.size))
        except Exception:
            pass


class AnimatedCore(Widget):
    """Canvas-only animated orb (AURA ORB, PARTICLE CORE, GLASS ORB); no external images."""
    def __init__(self, theme_index=0, **kwargs):
        super().__init__(**kwargs)
        self.theme_index = theme_index
        self.phase = 0.0
        self.level = 0.0     # 0..1 voice level (simulated until the microphone is connected)
        self.clock_t = 0.0
        self.fib, self.dashes, self.reds = [], [], []
        if theme_index == 1:  # PARTICLE CORE: points on a sphere
            n = 420
            for k in range(n):
                y = 1 - 2 * (k + 0.5) / n
                rr = math.sqrt(1 - y * y)
                t = k * 2.39996
                self.fib.append((math.cos(t) * rr, y, math.sin(t) * rr))
        if theme_index == 2:  # GLASS ORB: fixed random dots on the sphere
            rnd = random.Random(7)
            self.dashes = [(rnd.uniform(0, math.pi * 2), rnd.uniform(-1.5, 1.5)) for _ in range(170)]
            self.reds = [(rnd.uniform(0, math.pi * 2), rnd.uniform(-1.2, 1.2)) for _ in range(6)]
        self.bind(pos=self.redraw, size=self.redraw)
        self._event = Clock.schedule_interval(self.animate, 1 / 20 if theme_index == 1 else 1 / 24)
        self.redraw()

    def on_parent(self, widget, parent):
        # Stop the animation timer when the widget is removed (theme change).
        if parent is None and getattr(self, "_event", None):
            self._event.cancel()
            self._event = None

    def animate(self, dt):
        app = App.get_running_app()
        if app is not None and getattr(app, "animation_paused", False):
            return
        state = getattr(app, "orb_state", "idle") if app is not None else "idle"
        self.clock_t += dt
        t = self.clock_t
        if state == "listening":    # orb reacts to incoming sound
            target = 0.35 + 0.45 * abs(math.sin(t * 3.1) * math.sin(t * 1.3 + 1.0))
            boost = 1.9
        elif state == "speaking":   # orb pulses with the spoken answer
            target = 0.30 + 0.50 * abs(math.sin(t * 6.2) * (0.6 + 0.4 * math.sin(t * 1.7)))
            boost = 1.4
        else:
            target, boost = 0.0, 1.0
        self.level += (target - self.level) * min(1.0, dt * 8.0)
        self.phase = (self.phase + dt * 0.8 * boost) % (math.pi * 2)
        self.redraw()

    def draw_aura(self, cx, cy, R, accent, accent2):
        """AURA ORB: dark glass sphere with soft luminous ribbons, blue/violet glow, bright rim."""
        lv = self.level
        R = R * (1 + 0.10 * lv)
        p = self.phase
        halo = radial_texture("aura_halo", ((0, (50, 100, 255, 115)), (0.5, (30, 70, 220, 44)),
                                            (1, (20, 40, 160, 0))))
        hs = R * (4.4 + 1.4 * lv)
        Color(1, 1, 1, 0.8 + 0.2 * lv)
        Rectangle(texture=halo, pos=(cx - hs / 2, cy - hs / 2), size=(hs, hs))
        body = radial_texture("aura_body", ((0, (1, 3, 10, 255)), (0.7, (3, 9, 30, 255)),
                                            (1, (8, 34, 100, 255))))
        Color(1, 1, 1, 1)
        Rectangle(texture=body, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        core = radial_texture("aura_core", ((0, (40, 110, 255, 95)), (1, (40, 110, 255, 0))))
        cs = R * 1.5
        Color(1, 1, 1, 0.7 + 0.3 * lv)
        Rectangle(texture=core, pos=(cx + R * 0.28 - cs / 2, cy - R * 0.32 - cs / 2), size=(cs, cs))
        colors = ((0.30, 0.55, 1.0), (0.55, 0.38, 1.0), (0.21, 0.82, 1.0))
        fades = (0.35, 0.8, 0.8, 0.35)  # ribbons fade out at both ends
        for j in range(3):
            b_axis = R * (0.30 + 0.22 * j + 0.05 * math.sin(p * 1.1 + j * 2)
                          + 0.05 * lv * math.sin(p * 4 + j))
            rot = p * (0.35 + 0.10 * j) * (1 if j % 2 == 0 else -1) + j * 1.9
            t0 = p * (0.8 + 0.2 * j) + j * 2.1
            cr, sr = math.cos(rot), math.sin(rot)
            pts = []
            for n in range(61):
                t = t0 + n / 60.0 * 4.2
                ex, ey = R * 0.96 * math.cos(t), b_axis * math.sin(t)
                pts.append((cx + ex * cr - ey * sr, cy + ex * sr + ey * cr))
            r, g, b = colors[j]
            for chunk in range(4):
                flat = []
                for px, py in pts[chunk * 15: chunk * 15 + 16]:
                    flat.extend((px, py))
                f = fades[chunk]
                Color(r, g, b, (0.10 + 0.10 * lv) * f)
                Line(points=flat, width=R * 0.16)
                Color(r, g, b, 0.22 * f)
                Line(points=flat, width=R * 0.08)
                Color(0.8, 0.92, 1, 0.95 * f)
                Line(points=flat, width=R * 0.03)
        inner = radial_texture("aura_inner", ((0, (40, 90, 255, 0)), (0.55, (40, 90, 255, 0)),
                                              (1, (60, 120, 255, 90))))
        Color(1, 1, 1, 1)
        Rectangle(texture=inner, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        Color(*accent2, 0.10)
        Line(circle=(cx, cy, R), width=R * 0.12)
        Color(*accent2, 0.30)
        Line(circle=(cx, cy, R), width=R * 0.05)
        Color(*accent2, 0.9)
        Line(circle=(cx, cy, R), width=dp(1.4))
        start = (p * 40) % 360
        Color(0.75, 0.88, 1, 0.18)
        Line(circle=(cx, cy, R, start, start + 100), width=R * 0.08)
        Color(0.85, 0.94, 1, 0.95)
        Line(circle=(cx, cy, R, start, start + 100), width=dp(2.6) * (1 + lv), cap="round")
        Color(0.78, 0.5, 1, 0.16)
        Line(circle=(cx, cy, R, start + 190, start + 245), width=R * 0.07)
        Color(0.8, 0.66, 1, 0.85)
        Line(circle=(cx, cy, R, start + 190, start + 245), width=dp(2.2) * (1 + lv), cap="round")

    def draw_particles(self, cx, cy, R, accent, accent2):
        """PARTICLE CORE: wobbling shell of glowing dots and rings inside a dark disc."""
        lv = self.level
        R = R * (1 + 0.10 * lv)
        disc = radial_texture("p_disc", ((0, (11, 26, 32, 255)), (1, (4, 8, 11, 255))))
        Color(1, 1, 1, 1)
        Rectangle(texture=disc, pos=(cx - R * 1.3, cy - R * 1.3), size=(R * 2.6, R * 2.6))
        Color(*accent, 0.2 + 0.3 * lv)
        Line(circle=(cx, cy, R * 1.3), width=dp(1))
        glow = radial_texture("p_glow", ((0, (40, 200, 255, 0)), (0.55, (40, 200, 255, 0)),
                                         (0.8, (40, 200, 255, 40)), (1, (40, 200, 255, 0))))
        gs = R * 3 * (1 + 0.3 * lv)
        Color(1, 1, 1, 1)
        Rectangle(texture=glow, pos=(cx - gs / 2, cy - gs / 2), size=(gs, gs))
        p = self.phase
        cs, sn = math.cos(p * 0.5), math.sin(p * 0.5)
        tc, ts = 0.9004, 0.4350  # tilt of 0.45 rad
        amp = 0.13 * (1 + 2.4 * lv)

        def shell(x, y, z):
            n = (math.sin(x * 2.3 + p * 1.2) * math.sin(y * 2.9 - p * 0.9)
                 + 0.6 * math.sin(z * 3.3 + p * 1.5) + 0.4 * math.sin((x + y + z) * 5 + p * 2))
            rr = 1 + amp * n
            X, Y, Z = x * rr, y * rr, z * rr
            x1 = X * cs + Z * sn
            z1 = -X * sn + Z * cs
            y1 = Y * tc - z1 * ts
            z2 = Y * ts + z1 * tc
            return cx + x1 * R, cy + y1 * R, z2

        Color(*accent, 0.20 + 0.15 * lv)
        for k in range(1, 13):  # latitude rings
            y = -1 + 2 * k / 13.0
            r0 = math.sqrt(1 - y * y)
            pts = []
            for n in range(37):
                t = n / 36.0 * math.pi * 2
                px, py, _z = shell(math.cos(t) * r0, y, math.sin(t) * r0)
                pts.extend((px, py))
            Line(points=pts, width=dp(0.6))
        near, mid, far = [], [], []
        for x, y, z in self.fib:
            px, py, z2 = shell(x, y, z)
            if z2 > 0.35:
                near.extend((px, py))
            elif z2 > -0.3:
                mid.extend((px, py))
            else:
                far.extend((px, py))
        if far:
            Color(0.10, 0.56, 0.69, 0.55)
            Point(points=far, pointsize=dp(0.6))
        if mid:
            Color(0.38, 0.90, 1, 0.8)
            Point(points=mid, pointsize=dp(0.8))
        if near:
            Color(0.85, 0.98, 1, 1)
            Point(points=near, pointsize=dp(1.0))

    def draw_glass(self, cx, cy, R, accent, accent2):
        """GLASS ORB: dark glass sphere on a reflective floor with light pillars."""
        p = self.phase
        lv = self.level
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
            Color(1, 1, 1, min(1.0, a * (1 + 0.6 * lv)))
            Rectangle(texture=pillar, pos=(x - dp(2.5), fy), size=(dp(5), max(self.top - fy - dp(6), 1)))
            rw = w * 0.32
            Rectangle(texture=refl, pos=(x - rw / 2, fy - rw * 0.125), size=(rw, rw * 0.25))
        Color(1, 1, 1, min(1.0, 0.8 + 0.5 * lv))
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
        pl = min(1.0, 0.7 + 0.3 * math.sin(p * 2) + 0.5 * lv)

        def equator(span):  # the equator line ripples when the orb hears or speaks
            pts = []
            for i in range(41):
                u = i / 40.0 * 2 - 1
                yy = cy + math.sin(u * 9 + p * 7) * lv * R * 0.07 * (1 - u * u)
                pts.extend((cx + u * R * span, yy))
            return pts

        Color(0.22, 0.78, 1, 0.25 * pl)
        Line(points=equator(1.0), width=dp(4))
        Color(0.55, 0.92, 1, 0.95 * pl)
        Line(points=equator(0.97), width=dp(1.4) * (1 + lv))
        Color(0.91, 0.98, 1, 0.8 * pl)
        Line(points=[cx, cy - R * 0.18, cx, cy + R * 0.18], width=dp(1))
        flare = radial_texture("g_flare", ((0, (220, 250, 255, 240)), (1, (56, 200, 255, 0))))
        fs = R * 0.22 * (1 + 0.8 * lv)
        Color(1, 1, 1, pl)
        Rectangle(texture=flare, pos=(cx - fs, cy - fs), size=(fs * 2, fs * 2))
        Color(0.35, 0.82, 1, 0.85)
        Line(circle=(cx, cy, R), width=dp(1.3))
        Color(1, 1, 1, 0.92)  # two glossy highlights (angles run clockwise from the top)
        Line(circle=(cx, cy, R * 0.9, 303, 327), width=R * 0.035, cap="round")
        Line(circle=(cx, cy, R * 0.9, 33, 57), width=R * 0.035, cap="round")

    def redraw(self, *_):
        try:
            self._redraw_impl()
        except Exception:
            # a drawing error must never close the app: stop animating and report it
            if getattr(self, "_event", None):
                self._event.cancel()
                self._event = None
            try:
                self.canvas.clear()
            except Exception:
                pass
            app = App.get_running_app()
            if app is not None:
                app.report_error(traceback.format_exc())

    def _redraw_impl(self):
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        theme = THEMES[self.theme_index]
        accent = hex_color(theme["accent"])[:3]
        accent2 = hex_color(theme["accent2"])[:3]
        cx, cy = self.center
        size = min(self.width, self.height)
        with self.canvas:
            if self.theme_index == 0:
                self.draw_aura(cx, cy, size * 0.30, accent, accent2)
            elif self.theme_index == 1:
                self.draw_particles(cx, cy, size * 0.33, accent, accent2)
            else:
                self.draw_glass(self.center_x, self.y + self.height * 0.56,
                                size * 0.34, accent, accent2)


class GlassButton(Button):
    """Liquid-glass button."""
    def __init__(self, tint=(0.3, 0.6, 1.0), active=False, radius=16, **kwargs):
        super().__init__(**kwargs)
        self.tint_rgb = tint
        self.is_active = active
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.fill_base = 0.42 if active else 0.18
        glass_style(self, tint, dp(radius), fill=self.fill_base, sheen=0.55,
                    border=0.55 if active else 0.40)
        self.bind(state=self.state_changed)

    def state_changed(self, *_):
        fc = getattr(self, "_g_fillc", None)
        if fc is not None:
            t = self.tint_rgb
            fc.rgba = (t[0], t[1], t[2], self.fill_base + (0.18 if self.state == "down" else 0.0))


class SendWaveButton(Button):
    """Command-bar button: sound-wave icon when empty, paper plane once text is typed."""
    def __init__(self, tint_rgb=(0.3, 0.6, 1.0), icon_rgba=(1, 1, 1, 1), **kwargs):
        super().__init__(**kwargs)
        self.tint_rgb = tint_rgb
        self.icon_rgba = icon_rgba
        self.icon_mode = "wave"
        self.text = ""
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.bind(pos=self.draw_icon, size=self.draw_icon, state=self.draw_icon)
        self.draw_icon()

    def set_mode(self, mode):
        if mode != self.icon_mode:
            self.icon_mode = mode
            self.draw_icon()

    def draw_icon(self, *_):
        self.canvas.after.clear()
        cx, cy = self.center
        down = self.state == "down"
        ic = self.icon_rgba
        t = self.tint_rgb
        with self.canvas.after:
            if self.icon_mode == "plane":
                d = max(min(self.width, self.height) - dp(10), 1)
                Color(t[0], t[1], t[2], 0.70 if down else 0.55)
                Ellipse(pos=(cx - d / 2, cy - d / 2), size=(d, d))
                Color(1, 1, 1, 0.22)
                Ellipse(pos=(cx - d / 2 + dp(2), cy), size=(d - dp(4), d / 2 - dp(1)))
                Color(1, 1, 1, 0.55)
                Line(circle=(cx, cy, d / 2), width=dp(1))
                s = dp(8.5) * (0.92 if down else 1.0)
                ca, sa = math.cos(0.5), math.sin(0.5)  # tilted up and to the right

                def rot(px, py):
                    return cx + (px * ca - py * sa) * s, cy + (px * sa + py * ca) * s

                a1 = rot(-1.0, -0.85)
                tip = rot(1.15, 0.0)
                b1 = rot(-1.0, 0.85)
                notch = rot(-0.35, 0.0)
                Color(ic[0], ic[1], ic[2], 0.75)
                Triangle(points=[a1[0], a1[1], tip[0], tip[1], notch[0], notch[1]])
                Color(ic[0], ic[1], ic[2], 1)
                Triangle(points=[b1[0], b1[1], tip[0], tip[1], notch[0], notch[1]])
            else:
                Color(*ic)
                for i, h in enumerate((0.35, 0.7, 1.0, 0.6, 0.4)):
                    x = cx + (i - 2) * dp(5)
                    half = dp(22) * h / 2
                    Line(points=[x, cy - half, x, cy + half], width=dp(1.5), cap="round")


class JarvisApp(App):
    def build(self):
        self.title = "JARVIS"
        ExceptionManager.add_handler(JarvisExceptionHandler())
        self.startup_error = None
        self._last_err = None
        self._err_count = 0
        self.store = JsonStore(os.path.join(self.user_data_dir, "jarvis_settings.json"))
        self.language = "tg"
        self.theme_index = 0
        self.voice_profile = "JARVIS Classic"
        self.last_response = ""
        self.animation_paused = False
        self.orb_state = "idle"      # idle / listening / speaking
        self._orb_event = None
        self.load_settings()
        try:  # keep the text field visible above the on-screen keyboard
            Window.softinput_mode = "below_target"
        except Exception:
            pass

        Window.clearcolor = hex_color(THEMES[self.theme_index]["background"])
        self.root_layout = FloatLayout()
        with self.root_layout.canvas.before:
            self.bg_color = Color(*hex_color(THEMES[self.theme_index]["background"]))
            self.bg_rect = Rectangle(pos=self.root_layout.pos, size=self.root_layout.size)
            # blue glow rising from the bottom (AURA ORB only)
            self.grad_color = Color(1, 1, 1, 1 if self.theme_index == 0 else 0)
            self.grad_rect = Rectangle(texture=self.make_gradient(),
                                       pos=self.root_layout.pos, size=(0, 0))
        self.root_layout.bind(pos=self.update_background, size=self.update_background)
        try:
            self.build_interface()
        except Exception:
            err = traceback.format_exc()
            save_crash(err)
            self.startup_error = err
            self.theme_index = 0  # fall back to the first design
            try:
                self.save_settings()
                self.update_colors()
                self.build_interface()
            except Exception:
                self.root_layout.clear_widgets()
                self.root_layout.add_widget(make_label(err[-1500:], 10, "#FF8080"))
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

    def t(self, key):
        return TEXTS.get(self.language, TEXTS["tg"]).get(key, key)

    def load_settings(self):
        if self.store.exists("settings"):
            data = self.store.get("settings")
            self.language = data.get("language", "tg")
            # "theme_v2" is a new key: designs from older versions are ignored
            self.theme_index = int(data.get("theme_v2", 0)) % len(THEMES)
            self.voice_profile = data.get("voice_profile", "JARVIS Classic")
        if self.language not in ("tg", "ru", "en"):
            self.language = "tg"

    def save_settings(self):
        self.store.put("settings", language=self.language,
                       theme_v2=self.theme_index,
                       voice_profile=self.voice_profile)

    def update_colors(self):
        theme = THEMES[self.theme_index]
        Window.clearcolor = hex_color(theme["background"])
        self.bg_color.rgba = hex_color(theme["background"])
        self.grad_color.rgba = (1, 1, 1, 1 if self.theme_index == 0 else 0)

    def make_button(self, text, callback, color=None, height=44):
        theme = THEMES[self.theme_index]
        accent = color or theme["accent"]
        btn = GlassButton(text=text, tint=hex_color(accent)[:3], active=True,
                          size_hint_y=None, height=dp(height), font_size=dp(13),
                          bold=True, color=hex_color(theme["text"]))
        btn.bind(on_release=callback)
        return btn

    def build_interface(self):
        self.root_layout.clear_widgets()
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        main = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        self.root_layout.add_widget(main)

        # Header: title (takes all free width, never wraps) and the glass settings button
        header = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        title = make_label("J.A.R.V.I.S", 21, theme["accent"], True, halign="left")
        settings_btn = GlassButton(text=self.t("set"), tint=tint, size_hint_x=None, width=dp(96),
                                   font_size=dp(13), bold=True, color=hex_color(theme["text"]),
                                   radius=18)
        settings_btn.bind(on_release=self.show_settings)
        header.add_widget(title)
        header.add_widget(settings_btn)
        main.add_widget(header)

        self.status_label = make_label(self.t("ready"), 12, theme["accent"], True,
                                       size_hint_y=None, height=dp(25))
        main.add_widget(self.status_label)

        # Main visual zone: one layout per design
        self.visual_area = BoxLayout(orientation="vertical", spacing=dp(8))
        main.add_widget(self.visual_area)
        self.build_theme_layout()

        # Response panel (glass)
        response_panel = BoxLayout(orientation="vertical", padding=dp(10),
                                   spacing=dp(4), size_hint_y=None, height=dp(94))
        glass_style(response_panel, tint, dp(18), fill=0.10, sheen=0.28, border=0.30)
        self.response_title = make_label(self.t("response"), 10, theme["accent"], True,
                                          size_hint_y=None, height=dp(18))
        self.response_label = make_label(self.last_response or self.t("welcome"), 13,
                                         theme["text"], False)
        response_scroll = ScrollView(do_scroll_x=False)
        response_scroll.add_widget(self.response_label)
        response_panel.add_widget(self.response_title)
        response_panel.add_widget(response_scroll)
        main.add_widget(response_panel)

        # Command bar: glass pill; sound-wave icon when empty, paper plane after typing
        command_row = BoxLayout(orientation="horizontal", spacing=dp(7),
                                size_hint_y=None, height=dp(52))
        glass_style(command_row, tint, dp(26), fill=0.16, sheen=0.50, border=0.50)
        self.command_input = TextInput(
            hint_text=self.t("input"), multiline=False, size_hint_x=1,
            font_size=dp(15), foreground_color=hex_color(theme["text"]),
            hint_text_color=(*hex_color(theme["text"])[:3], 0.7),
            cursor_color=hex_color(theme["accent"]),
            background_normal="", background_active="",
            background_color=(0, 0, 0, 0),
            padding=[dp(22), dp(15), dp(10), dp(10)],
        )
        self.command_input.bind(on_text_validate=self.send_command)
        self.command_input.bind(text=self.on_text_change)
        self.action_btn = SendWaveButton(tint_rgb=tint, icon_rgba=hex_color(theme["text"]),
                                       size_hint_x=None, width=dp(58))
        self.action_btn.bind(on_release=self.on_action)
        command_row.add_widget(self.command_input)
        command_row.add_widget(self.action_btn)
        main.add_widget(command_row)

        footer = make_label(self.t("online") + "  \u2022  " + THEMES[self.theme_index]["name"],
                            10, (*hex_color(theme["text"])[:3], 0.7), False,
                            size_hint_y=None, height=dp(20))
        main.add_widget(footer)
        self.footer_label = footer

    def build_theme_layout(self):
        self.visual_area.clear_widgets()
        theme = THEMES[self.theme_index]
        index = self.theme_index
        if index == 0:  # AURA ORB: glowing orb + greeting
            self.visual_area.add_widget(Widget(size_hint_y=0.05))
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1.5))
            self.visual_area.add_widget(make_label(self.t("orb_hello"), 14, "#8A9AAF",
                                                   False, size_hint_y=None, height=dp(28)))
            self.visual_area.add_widget(make_label(self.t("orb_ask"), 26, theme["text"], True,
                                                   size_hint_y=None, height=dp(90)))
        elif index == 1:  # PARTICLE CORE: point-cloud orb
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
        # the orb "speaks" the answer (simulated until speech synthesis is connected)
        self.set_orb_state("speaking", min(4.0, 0.8 + len(answer) * 0.03))

    def on_text_change(self, instance, value):
        if hasattr(self, "action_btn"):
            self.action_btn.set_mode("plane" if value.strip() else "wave")

    def on_action(self, *_):
        if self.command_input.text.strip():
            self.send_command()
        else:
            self.toggle_listening()

    def toggle_listening(self):
        if self.orb_state == "listening":
            self.set_orb_state("idle")
            return
        self.show_response(self.t("mic") + "\n" + self.t("voice_note"))
        self.set_orb_state("listening", 4.0)

    def set_orb_state(self, state, seconds=None):
        self.orb_state = state
        if self._orb_event is not None:
            self._orb_event.cancel()
            self._orb_event = None
        label = getattr(self, "status_label", None)
        if label is not None:
            label.text = {"listening": self.t("listening"),
                          "speaking": self.t("speaking")}.get(state, self.t("ready"))
        if state != "idle" and seconds:
            self._orb_event = Clock.schedule_once(lambda dt: self.set_orb_state("idle"), seconds)

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
        """Settings are split into folders: main menu -> design / language / voice."""
        old = getattr(self, "settings_popup", None)
        if old is not None:
            old.dismiss()
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(9))
        titles = {"main": "settings", "theme": "title_theme",
                  "language": "title_lang", "voice": "title_voice"}
        content.add_widget(make_label(self.t(titles[section]), 17, theme["accent"], True,
                                      size_hint_y=None, height=dp(30)))

        def option(text, active, callback, height=54):
            btn = GlassButton(text=text, tint=tint, active=active, size_hint_y=None,
                              height=dp(height), font_size=dp(13), bold=active,
                              color=hex_color(theme["text"]), radius=18)
            btn.bind(on_release=callback)
            content.add_widget(btn)

        if section == "main":
            for key, target in (("sec_theme", "theme"), ("sec_lang", "language"),
                                ("sec_voice", "voice")):
                option(self.t(key), False, lambda inst, s=target: self.open_section(s), height=58)
            content.add_widget(Widget())
        elif section == "theme":
            for i, item in enumerate(THEMES):
                option(item["name"], i == self.theme_index,
                       lambda inst, ix=i: self.choose_theme(ix, self.settings_popup))
            content.add_widget(Widget())
        elif section == "language":
            for label, code in (("Тоҷикӣ", "tg"), ("Русский", "ru"), ("English", "en")):
                option(label, self.language == code,
                       lambda inst, c=code: self.choose_language(c, self.settings_popup))
            content.add_widget(Widget())
        else:  # voice
            for name in VOICES:
                option(name, self.voice_profile == name,
                       lambda inst, n=name: self.choose_voice(n, self.settings_popup))
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

        try:
            self.settings_popup = Popup(
                title="", content=content, size_hint=(0.92, 0.88), auto_dismiss=True,
                separator_height=0, background="",
                background_color=(*hex_color(theme["background"])[:3], 0.96),
                overlay_color=(0, 0, 0, 0.6))
        except Exception:
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

    def on_start(self):
        Clock.schedule_once(lambda dt: self.show_pending_errors(), 1.0)

    def show_pending_errors(self):
        text = self.startup_error or read_last_crash()
        if not text and GLASS_ERRORS:
            text = "Glass style fallback:\n" + GLASS_ERRORS[0]
        if text:
            self.show_error_popup(text)

    def report_error(self, text):
        """Called when a callback or drawing fails: show the error once instead of crashing."""
        if text == self._last_err or self._err_count >= 3:
            return
        self._last_err = text
        self._err_count += 1
        Clock.schedule_once(lambda dt: self.show_error_popup(text), 0.2)

    def show_error_popup(self, text):
        try:
            box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
            box.add_widget(TextInput(text=text[-3500:], readonly=True, font_size=dp(10),
                                     background_color=(0.05, 0.05, 0.08, 1),
                                     foreground_color=(1, 0.85, 0.85, 1)))
            ok = Button(text="OK", size_hint_y=None, height=dp(44))
            box.add_widget(ok)
            pop = Popup(title="JARVIS error log", content=box, size_hint=(0.96, 0.9))
            ok.bind(on_release=pop.dismiss)
            pop.open()
        except Exception:
            pass

    def on_pause(self):
        self.save_settings()
        return True

    def on_resume(self):
        self.save_settings()

    def on_stop(self):
        self.save_settings()


if __name__ == "__main__":
    JarvisApp().run()
