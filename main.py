# -*- coding: utf-8 -*-
"""
JARVIS Android Assistant
Kivy + Buildozer compatible. Uses built-in urllib for network tasks.
"""
from datetime import datetime
import json
import math
import os
import random
import re
import sys
import time
import traceback
import uuid
import urllib.request
import urllib.error

from kivy.app import App
from kivy.base import ExceptionHandler, ExceptionManager
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.storage.jsonstore import JsonStore
from kivy.utils import get_color_from_hex
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

# ==========================================================================
# VOICE LAYER
# ==========================================================================
class VoiceUnavailable(Exception):
    pass

def microphone_permission_state():
    try:
        from android.permissions import check_permission, Permission
    except ImportError:
        return "not_android"
    try:
        return "granted" if check_permission(Permission.RECORD_AUDIO) else "denied"
    except Exception:
        return "unknown"

def request_microphone(callback):
    try:
        from android.permissions import request_permissions, Permission
    except ImportError:
        callback(True)
        return

    def done(permissions, grants):
        callback(bool(grants) and all(grants))

    try:
        request_permissions([Permission.RECORD_AUDIO], done)
    except Exception:
        callback(False)

LANG_TAGS = {"tg": "tg-TJ", "ru": "ru-RU", "en": "en-US"}

def android_available():
    try:
        import jnius  # noqa: F401
        import android  # noqa: F401
    except Exception:
        return False
    return True

class SpeechRecognizer(object):
    REQUEST_CODE = 7041

    def __init__(self):
        self._bound = False
        self._on_text = None
        self._on_error = None

    def is_supported(self):
        return android_available()

    def start(self, lang, prompt, on_text, on_error):
        if not android_available():
            raise VoiceUnavailable("Speech recognition works only on Android.")
        try:
            from jnius import autoclass
            from android import activity
            Intent = autoclass("android.content.Intent")
            RecognizerIntent = autoclass("android.speech.RecognizerIntent")
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            self._on_text, self._on_error = on_text, on_error
            intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, LANG_TAGS.get(lang, "en-US"))
            intent.putExtra(RecognizerIntent.EXTRA_PROMPT, prompt)
            intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            if not self._bound:
                activity.bind(on_activity_result=self._on_result)
                self._bound = True
            PythonActivity.mActivity.startActivityForResult(intent, self.REQUEST_CODE)
        except Exception as exc:
            raise VoiceUnavailable(str(exc))

    def _on_result(self, request_code, result_code, intent):
        if request_code != self.REQUEST_CODE:
            return
        on_text, on_error = self._on_text, self._on_error
        try:
            text = ""
            if result_code == -1 and intent is not None:
                from jnius import autoclass
                RecognizerIntent = autoclass("android.speech.RecognizerIntent")
                results = intent.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
                if results is not None and results.size() > 0:
                    text = str(results.get(0))
            if text:
                if on_text:
                    on_text(text)
            elif on_error:
                on_error("no_speech" if result_code == -1 else "cancelled")
        except Exception as exc:
            if on_error:
                on_error(str(exc))

    def stop(self):
        return None

class SpeechSynthesizer(object):
    def __init__(self):
        self._tts = None

    def is_supported(self):
        return android_available()

    def _engine(self):
        if self._tts is None:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            self._tts = TextToSpeech(PythonActivity.mActivity, None)
        return self._tts

    def warm_up(self):
        if android_available():
            try:
                self._engine()
            except Exception:
                pass

    def speak(self, text, lang="ru"):
        if not android_available():
            raise VoiceUnavailable("Speech synthesis works only on Android.")
        try:
            from jnius import autoclass
            Locale = autoclass("java.util.Locale")
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            tts = self._engine()
            result = "ok"
            code = tts.setLanguage(Locale(lang))
            if code < 0 and lang == "tg":
                code = tts.setLanguage(Locale("ru"))
                result = "fallback"
            if code < 0:
                raise VoiceUnavailable("No voice for this language is installed.")
            if tts.speak(text, TextToSpeech.QUEUE_FLUSH, None, "jarvis") != 0:
                raise VoiceUnavailable("The speech engine is still starting.")
            return result
        except VoiceUnavailable:
            raise
        except Exception as exc:
            raise VoiceUnavailable(str(exc))

    def stop(self):
        try:
            if self._tts is not None:
                self._tts.stop()
        except Exception:
            pass

    def shutdown(self):
        try:
            if self._tts is not None:
                self._tts.stop()
                self._tts.shutdown()
        except Exception:
            pass
        self._tts = None

class LocalAccountService(object):
    def is_configured(self):
        return False
    def current_user(self):
        return None

def get_service(private_dir=None):
    return LocalAccountService()

CRASH_FILE = "jarvis_crash_log.txt"

def crash_dirs():
    priv = os.environ.get("ANDROID_PRIVATE")
    return [priv if priv else os.getcwd()]

def save_crash(text):
    for d in crash_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, CRASH_FILE)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return path
        except Exception:
            pass
    return None

def read_last_crash():
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

class JarvisExceptionHandler(ExceptionHandler):
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

TEXTS = {
    "tg": {
        "ready": "ҶАРВИС ОМОДА АСТ",
        "online": "Система фаъол аст",
        "welcome": "Салом! Ман JARVIS ҳастам. Чӣ кор кунам?",
        "input": "Фармонро нависед...",
        "settings": "Танзимоти JARVIS",
        "close": "Пӯшидан",
        "commands": "Фармонҳо: салом, соат, сана, ҳолат, мавзӯъ, забон, танзимот, кӯмак",
        "menu_chats": "Чатҳои ман",
        "menu_new": "Чати нав",
        "menu_settings": "Танзимот",
        "menu_about": "Маълумот дар бораи JARVIS",
        "chat_default": "Чати нав",
        "chats_title": "Чатҳои ман",
        "chats_empty": "Ҳоло чат нест",
        "delete_ask": "Чати «{}» нест карда шавад?",
        "yes_delete": "Ҳа, нест кун",
        "no": "Не",
        "chat_created": "Чати нав сохта шуд.",
        "chats_opened": "Рӯйхати чатҳо кушода шуд.",
        "settings_opened": "Танзимот кушода шуд.",
        "about_opened": "Маълумот кушода шуд.",
        "about_title": "Дар бораи JARVIS",
        "about_body": "JARVIS — ёрдамчии шахсӣ.",
        "about_chats": "Чатҳо дар телефон: {}",
        "title_account": "Ҳисоб",
        "acct_status": "Системаи офлайн фаъол аст.",
        "mic_denied": "Иҷозати микрофон дода нашуд.",
        "lang_changed": "Забон иваз шуд: {}",
        "theme_changed": "Интерфейс: {}",
        "cmd_error": "Хатогӣ рух дод.",
        "error_hint": "Барномаро аз нав кушоед.",
        "listen_prompt": "Фармонро бигӯед",
        "stt_unavailable": "Шинохти овоз дастрас нест.",
        "stt_failed": "Овоз шинохта нашуд.",
        "tts_fallback": "Овози тоҷикӣ дар телефон нест, овози русӣ истифода мешавад.",
        "tts_unavailable": "Хондани ҷавоб ҳоло кор намекунад.",
        "speak_on": "Ҷавобҳо бо овоз: фаъол",
        "speak_off": "Ҷавобҳо бо овоз: хомӯш",
        "voice_hint": "Шинохти овоз тавассути хизматрасонии Android кор мекунад.",
        "voice_how": "Барои гуфтани фармон нишони садоро пахш кунед.",
        "voice_note": "Овоз тавассути хизматрасонии Android хонда мешавад.",
        "lang_hint": "Забонро интихоб кунед: тоҷикӣ, русӣ ё англисӣ.",
        "sec_account": "Ҳисоб",
        "help": "Ман ҳоло фармонҳои асосии офлайнро иҷро карда метавонам",
        "unknown": "Ин фармон дастгирӣ намешавад. «кӯмак» нависед.",
        "mic": "Шинохти овоз ҳанӯз пайваст нашудааст.",
        "response": "Ҷавоби JARVIS",
        "time": "Вақти ҳозира",
        "date": "Санаи имрӯз",
        "system": "Ҳолат: онлайн",
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
        "p_ready": "Омода барои гӯш кардан",
        "listening": "Гӯш мекунам...",
        "speaking": "Ҷавоб медиҳам...",
    },
    "ru": {
        "ready": "JARVIS ГОТОВ",
        "online": "Система активна",
        "welcome": "Здравствуйте! Я JARVIS. Чем могу помочь?",
        "input": "Введите команду...",
        "settings": "Настройки JARVIS",
        "close": "Закрыть",
        "commands": "Команды: привет, время, дата, статус, тема, язык, настройки, помощь",
        "menu_chats": "Мои чаты",
        "menu_new": "Новый чат",
        "menu_settings": "Настройки",
        "menu_about": "О JARVIS",
        "chat_default": "Новый чат",
        "chats_title": "Мои чаты",
        "chats_empty": "Чатов пока нет",
        "delete_ask": "Удалить чат «{}»?",
        "yes_delete": "Да, удалить",
        "no": "Нет",
        "chat_created": "Новый чат создан.",
        "chats_opened": "Список чатов открыт.",
        "settings_opened": "Настройки открыты.",
        "about_opened": "Информация открыта.",
        "about_title": "О JARVIS",
        "about_body": "JARVIS — персональный помощник.",
        "about_chats": "Чатов на телефоне: {}",
        "title_account": "Аккаунт",
        "acct_status": "Офлайн режим.",
        "mic_denied": "Доступ к микрофону не предоставлен.",
        "lang_changed": "Язык изменён: {}",
        "theme_changed": "Интерфейс: {}",
        "cmd_error": "Произошла ошибка.",
        "error_hint": "Перезапустите приложение.",
        "listen_prompt": "Скажите команду",
        "stt_unavailable": "Распознавание речи недоступно.",
        "stt_failed": "Речь не распознана.",
        "tts_fallback": "Используется русский голос.",
        "tts_unavailable": "Озвучивание не работает.",
        "speak_on": "Озвучивать ответы: вкл.",
        "speak_off": "Озвучивать ответы: выкл.",
        "voice_hint": "Распознавание речи работает через Android.",
        "voice_how": "Нажмите значок звука.",
        "voice_note": "Озвучивание выполняется средствами Android.",
        "lang_hint": "Выберите язык: таджикский, русский или английский.",
        "sec_account": "Аккаунт",
        "help": "Команды офлайн",
        "unknown": "Команда не поддерживается.",
        "mic": "Микрофон не подключен.",
        "response": "Ответ",
        "time": "Время",
        "date": "Дата",
        "system": "Состояние",
        "set": "НАСТР.",
        "sec_theme": "Интерфейс",
        "sec_lang": "Язык",
        "sec_voice": "Голос",
        "title_theme": "Виды",
        "title_lang": "Языки",
        "title_voice": "Голоса",
        "back": "Назад",
        "orb_hello": "Здравствуйте",
        "orb_ask": "Чем могу помочь?",
        "p_ask": "Что сделать?",
        "p_ready": "Готов",
        "listening": "Слушаю...",
        "speaking": "Отвечаю...",
    },
    "en": {
        "ready": "JARVIS IS READY",
        "online": "System online",
        "welcome": "Hello! I am JARVIS.",
        "input": "Type a command...",
        "settings": "JARVIS Settings",
        "close": "Close",
        "commands": "Commands: hello, time, date, status, theme, help",
        "menu_chats": "My chats",
        "menu_new": "New chat",
        "menu_settings": "Settings",
        "menu_about": "About",
        "chat_default": "New chat",
        "chats_title": "My chats",
        "chats_empty": "No chats",
        "delete_ask": "Delete chat?",
        "yes_delete": "Yes",
        "no": "No",
        "chat_created": "Created.",
        "chats_opened": "Opened.",
        "settings_opened": "Settings.",
        "about_opened": "About.",
        "about_title": "About JARVIS",
        "about_body": "JARVIS assistant.",
        "about_chats": "Chats: {}",
        "title_account": "Account",
        "acct_status": "Offline mode.",
        "mic_denied": "No mic permission.",
        "lang_changed": "Language: {}",
        "theme_changed": "Theme: {}",
        "cmd_error": "Error.",
        "error_hint": "Restart.",
        "listen_prompt": "Say command",
        "stt_unavailable": "STT unavailable.",
        "stt_failed": "Failed.",
        "tts_fallback": "Fallback voice.",
        "tts_unavailable": "TTS unavailable.",
        "speak_on": "Voice: on",
        "speak_off": "Voice: off",
        "voice_hint": "Android STT.",
        "voice_how": "Tap mic.",
        "voice_note": "Speech output uses Android text-to-speech.",
        "lang_hint": "Choose Tajik, Russian, or English.",
        "sec_account": "Account",
        "help": "Offline commands",
        "unknown": "Unknown command.",
        "mic": "Mic not connected.",
        "response": "Response",
        "time": "Time",
        "date": "Date",
        "system": "System",
        "set": "SET",
        "sec_theme": "Theme",
        "sec_lang": "Language",
        "sec_voice": "Voice",
        "title_theme": "Themes",
        "title_lang": "Languages",
        "title_voice": "Voices",
        "back": "Back",
        "orb_hello": "Hello",
        "orb_ask": "How can I help?",
        "p_ask": "What to do?",
        "p_ready": "Ready",
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
        try:
            with widget.canvas.before:
                Color(tint[0], tint[1], tint[2], fill + 0.1)
                widget._g_plain = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
            widget.bind(pos=lambda w, v: setattr(w._g_plain, "pos", w.pos),
                        size=lambda w, v: setattr(w._g_plain, "size", w.size))
        except Exception:
            pass

class AnimatedCore(Widget):
    def __init__(self, theme_index=0, **kwargs):
        super().__init__(**kwargs)
        self.theme_index = theme_index
        self.phase = 0.0
        self.level = 0.0
        self.clock_t = 0.0
        self.fib, self.dashes, self.reds = [], [], []
        if theme_index == 1:
            n = 420
            for k in range(n):
                y = 1 - 2 * (k + 0.5) / n
                rr = math.sqrt(1 - y * y)
                t = k * 2.39996
                self.fib.append((math.cos(t) * rr, y, math.sin(t) * rr))
        if theme_index == 2:
            rnd = random.Random(7)
            self.dashes = [(rnd.uniform(0, math.pi * 2), rnd.uniform(-1.5, 1.5)) for _ in range(170)]
            self.reds = [(rnd.uniform(0, math.pi * 2), rnd.uniform(-1.2, 1.2)) for _ in range(6)]
        self.bind(pos=self.redraw, size=self.redraw)
        self._event = Clock.schedule_interval(self.animate, 1 / 20 if theme_index == 1 else 1 / 24)
        self.redraw()

    def on_parent(self, widget, parent):
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
        if state == "listening":
            target = 0.35 + 0.45 * abs(math.sin(t * 3.1) * math.sin(t * 1.3 + 1.0))
            boost = 1.9
        elif state == "speaking":
            target = 0.30 + 0.50 * abs(math.sin(t * 6.2) * (0.6 + 0.4 * math.sin(t * 1.7)))
            boost = 1.4
        else:
            target, boost = 0.0, 1.0
        self.level += (target - self.level) * min(1.0, dt * 8.0)
        self.phase = (self.phase + dt * 0.8 * boost) % (math.pi * 2)
        self.redraw()

    def draw_aura(self, cx, cy, R, accent, accent2):
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
        fades = (0.35, 0.8, 0.8, 0.35)
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

    def draw_particles(self, cx, cy, R, accent, accent2):
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
        tc, ts = 0.9004, 0.4350
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
        for k in range(1, 13):
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
        for k in range(14):
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

        def equator(span):
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
        Color(1, 1, 1, 0.92)
        Line(circle=(cx, cy, R * 0.9, 303, 327), width=R * 0.035, cap="round")
        Line(circle=(cx, cy, R * 0.9, 33, 57), width=R * 0.035, cap="round")

    def redraw(self, *_):
        try:
            self._redraw_impl()
        except Exception:
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
                ca, sa = math.cos(0.5), math.sin(0.5)

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

LANG_NAMES = {"tg": "Тоҷикӣ", "ru": "Русский", "en": "English"}

def format_time(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""

class ChatStore(object):
    def __init__(self, path=None):
        self.path = path
        self.chats = []
        self.current_id = None
        self.load()

    @staticmethod
    def now():
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    def load(self):
        data = None
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                try:
                    os.replace(self.path, self.path + ".bad")
                except Exception:
                    pass
                data = None
        if isinstance(data, dict) and isinstance(data.get("chats"), list):
            for c in data["chats"]:
                if not (isinstance(c, dict) and c.get("id") and isinstance(c.get("messages", []), list)):
                    continue
                c.setdefault("title", "")
                c.setdefault("created", self.now())
                c.setdefault("updated", c["created"])
                c["messages"] = [m for m in c.get("messages", []) if isinstance(m, dict)
                                 and m.get("role") in ("user", "jarvis") and isinstance(m.get("text"), str)]
                self.chats.append(c)
            self.current_id = data.get("current")
        if self.get(self.current_id) is None:
            ordered = self.sorted_chats()
            self.current_id = ordered[0]["id"] if ordered else None
        if self.current_id is None:
            self.new_chat()

    def save(self):
        if not self.path:
            return True
        try:
            folder = os.path.dirname(self.path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "current": self.current_id, "chats": self.chats},
                          f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False

    def get(self, chat_id):
        for c in self.chats:
            if c["id"] == chat_id:
                return c
        return None

    def current(self):
        chat = self.get(self.current_id)
        return chat if chat is not None else self.new_chat()

    def sorted_chats(self):
        return sorted(self.chats, key=lambda c: c.get("updated", ""), reverse=True)

    def new_chat(self):
        cur = self.get(self.current_id)
        if cur is not None and not cur["messages"]:
            return cur
        now = self.now()
        chat = {"id": uuid.uuid4().hex[:12], "title": "", "created": now, "updated": now, "messages": []}
        self.chats.append(chat)
        self.current_id = chat["id"]
        self.save()
        return chat

    def open_chat(self, chat_id):
        if self.get(chat_id) is not None:
            self.current_id = chat_id
            self.save()

    def add_message(self, chat_id, role, text):
        chat = self.get(chat_id) or self.current()
        chat["messages"].append({"role": role, "text": text, "time": self.now()})
        chat["updated"] = self.now()
        if role == "user" and not chat["title"]:
            title = " ".join(text.split())
            chat["title"] = title[:28] + ("..." if len(title) > 28 else "")
        self.save()
        return chat

    def delete_chat(self, chat_id):
        self.chats = [c for c in self.chats if c["id"] != chat_id]
        if self.current_id == chat_id:
            ordered = self.sorted_chats()
            self.current_id = ordered[0]["id"] if ordered else None
        if self.get(self.current_id) is None:
            self.current_id = None
            self.new_chat()
        else:
            self.save()

COMMANDS = []

def command(name, *keywords, priority=10):
    def register(fn):
        COMMANDS.append((priority, name, tuple(k.lower() for k in keywords), fn))
        COMMANDS.sort(key=lambda c: -c[0])
        return fn
    return register

def keyword_match(text, keyword):
    pattern = r"(?<!\w)" + re.escape(keyword) + (r"(?!\w)" if len(keyword) <= 3 else "")
    return re.search(pattern, text) is not None

def run_command(app, text):
    low = text.lower().strip()
    for priority, name, keywords, fn in COMMANDS:
        if any(keyword_match(low, k) for k in keywords):
            try:
                return fn(app, low)
            except Exception:
                save_crash("Command '%s' failed:\n%s" % (name, traceback.format_exc()))
                return app.t("cmd_error")
    return app.t("unknown")

@command("new_chat", "чати нав", "чатӣ нав", "новый чат", "new chat", priority=90)
def cmd_new_chat(app, text):
    app.notice = app.t("chat_created")
    app.new_chat()
    return app.t("chat_created")

@command("chats", "чатҳои ман", "чатҳо", "мои чаты", "чаты", "my chats", "chats", priority=80)
def cmd_chats(app, text):
    Clock.schedule_once(lambda dt: app.show_chats(), 0.4)
    return app.t("chats_opened")

@command("language", "забон", "язык", "language", priority=70)
def cmd_language(app, text):
    targets = (("tg", ("тоҷик", "таджик", "tajik", "tg")),
               ("ru", ("русӣ", "русск", "russian", "ru")),
               ("en", ("англис", "англий", "english", "en")))
    for code, names in targets:
        if any(keyword_match(text, n) for n in names):
            app.set_language(code)
            return app.t("lang_changed").format(LANG_NAMES[code])
    return app.t("lang_hint")

@command("settings", "танзимот", "настройки", "settings", priority=60)
def cmd_settings(app, text):
    Clock.schedule_once(lambda dt: app.show_settings(), 0.4)
    return app.t("settings_opened")

@command("theme", "мавзӯъ", "мавзуъ", "тема", "theme", "design", "интерфейс",
         "aura", "particle", "glass", priority=50)
def cmd_theme(app, text):
    for i, item in enumerate(THEMES):
        if keyword_match(text, item["name"].lower().split()[0]):
            app.set_theme(i)
            break
    else:
        app.next_theme()
    return app.t("theme_changed").format(THEMES[app.theme_index]["name"])

@command("about", "маълумот", "о программе", "о jarvis", "about", priority=40)
def cmd_about(app, text):
    Clock.schedule_once(lambda dt: app.show_about(), 0.4)
    return app.t("about_opened")

@command("voice", "овоз", "голос", "voice", "микрофон", "microphone", priority=20)
def cmd_voice(app, text):
    return app.t("voice_how") + "\n" + app.t("voice_hint")

@command("time", "соат", "время", "time", "clock", priority=10)
def cmd_time(app, text):
    return app.t("time") + ": " + datetime.now().strftime("%H:%M:%S")

@command("date", "сана", "дата", "date", "today", priority=10)
def cmd_date(app, text):
    return app.t("date") + ": " + datetime.now().strftime("%d.%m.%Y")

@command("status", "ҳолат", "статус", "status", "system", priority=10)
def cmd_status(app, text):
    return app.t("system") + "\n\n" + THEMES[app.theme_index]["name"]

@command("help", "кӯмак", "кумак", "помощь", "help", "команд", priority=10)
def cmd_help(app, text):
    return app.t("help") + "\n" + app.t("commands")

@command("greeting", "салом", "привет", "здравствуй", "hello", "hi", "hey", priority=5)
def cmd_greeting(app, text):
    return app.t("welcome")

class IconGlassButton(GlassButton):
    def __init__(self, icon_kind="dots", icon_rgba=(1, 1, 1, 1), **kwargs):
        super().__init__(**kwargs)
        self.icon_kind = icon_kind
        self.icon_rgba = icon_rgba
        self.bind(pos=self.draw_icon, size=self.draw_icon)
        self.draw_icon()

    def draw_icon(self, *_):
        self.canvas.after.clear()
        cx, cy = self.center
        with self.canvas.after:
            Color(*self.icon_rgba)
            if self.icon_kind == "dots":
                for dy in (-dp(7), 0, dp(7)):
                    Ellipse(pos=(cx - dp(2.2), cy + dy - dp(2.2)), size=(dp(4.4), dp(4.4)))
            else:
                k = dp(7)
                Line(points=[cx - k, cy - k, cx + k, cy + k], width=dp(1.6), cap="round")
                Line(points=[cx - k, cy + k, cx + k, cy - k], width=dp(1.6), cap="round")

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
        self.speak_enabled = False
        self.last_response = ""
        self.animation_paused = False
        self.orb_state = "idle"
        self._orb_event = None
        self.load_settings()
        self.account_service = get_service(self.user_data_dir)
        self.speech = SpeechRecognizer()
        self.tts = SpeechSynthesizer()
        self.tts_fallback_noted = False
        self.tts_error_noted = False
        self.open_popups = set()
        self.app_paused = False
        self.notice = None
        self.chat_placeholder = False
        try:
            self.chats = ChatStore(os.path.join(self.user_data_dir, "jarvis_chats.json"))
        except Exception:
            save_crash("ChatStore failed:\n" + traceback.format_exc())
            self.chats = ChatStore(None)
        try:
            Window.softinput_mode = "below_target"
        except Exception:
            pass

        Window.clearcolor = hex_color(THEMES[self.theme_index]["background"])
        self.root_layout = FloatLayout()
        with self.root_layout.canvas.before:
            self.bg_color = Color(*hex_color(THEMES[self.theme_index]["background"]))
            self.bg_rect = Rectangle(pos=self.root_layout.pos, size=self.root_layout.size)
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
            self.theme_index = 0
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
        for i in range(rows):
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
            self.theme_index = int(data.get("theme_v2", 0)) % len(THEMES)
            self.voice_profile = data.get("voice_profile", "JARVIS Classic")
            self.speak_enabled = bool(data.get("speak", False))
        if self.language not in ("tg", "ru", "en"):
            self.language = "tg"

    def save_settings(self):
        self.store.put("settings", language=self.language,
                       theme_v2=self.theme_index,
                       voice_profile=self.voice_profile,
                       speak=self.speak_enabled)

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

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        title = make_label("J.A.R.V.I.S", 21, theme["accent"], True, halign="left")
        menu_btn = IconGlassButton(icon_kind="dots", icon_rgba=hex_color(theme["text"]),
                                   tint=tint, size_hint_x=None, width=dp(48), radius=24)
        menu_btn.bind(on_release=self.show_menu)
        header.add_widget(title)
        header.add_widget(menu_btn)
        main.add_widget(header)

        self.status_label = make_label(self.t("ready"), 12, theme["accent"], True,
                                       size_hint_y=None, height=dp(25))
        main.add_widget(self.status_label)

        self.visual_area = BoxLayout(orientation="vertical", spacing=dp(8))
        main.add_widget(self.visual_area)
        self.build_theme_layout()

        chat_h = max(dp(120), min(dp(190), Window.height * 0.21))
        chat_panel = BoxLayout(orientation="vertical", padding=dp(10),
                               spacing=dp(4), size_hint_y=None, height=chat_h)
        glass_style(chat_panel, tint, dp(18), fill=0.10, sheen=0.28, border=0.30)
        self.response_title = make_label(self.chat_title(), 10, theme["accent"], True,
                                         size_hint_y=None, height=dp(18))
        self.chat_scroll = ScrollView(do_scroll_x=False)
        self.chat_box = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        self.chat_box.bind(minimum_height=self.chat_box.setter("height"))
        self.chat_scroll.add_widget(self.chat_box)
        chat_panel.add_widget(self.response_title)
        chat_panel.add_widget(self.chat_scroll)
        main.add_widget(chat_panel)
        self.render_chat()

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
        if index == 0:
            self.visual_area.add_widget(Widget(size_hint_y=0.05))
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1.5))
            self.visual_area.add_widget(make_label(self.t("orb_hello"), 14, "#8A9AAF",
                                                   False, size_hint_y=None, height=dp(28)))
            self.visual_area.add_widget(make_label(self.t("orb_ask"), 26, theme["text"], True,
                                                   size_hint_y=None, height=dp(90)))
        elif index == 1:
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1.6))
            self.visual_area.add_widget(make_label(self.t("orb_hello"), 12, "#8A9AAF",
                                                   False, size_hint_y=None, height=dp(24)))
            self.visual_area.add_widget(make_label(self.t("p_ask"), 24, theme["text"], True,
                                                   size_hint_y=None, height=dp(48)))
            self.visual_area.add_widget(make_label(self.t("p_ready"), 10, "#9FB4C8",
                                                   False, size_hint_y=None, height=dp(30)))
        else:
            self.visual_area.add_widget(AnimatedCore(index, size_hint_y=1.7))
            self.visual_area.add_widget(make_label(self.t("orb_hello"), 12, "#8A9AAF",
                                                   False, size_hint_y=None, height=dp(24)))
            self.visual_area.add_widget(make_label(self.t("p_ask"), 24, theme["text"], True,
                                                   size_hint_y=None, height=dp(48)))

    def chat_title(self, chat=None):
        chat = chat or self.chats.current()
        return chat.get("title") or self.t("chat_default")

    def make_bubble(self, role, text):
        theme = THEMES[self.theme_index]
        user = role == "user"
        tint = (0.45, 0.55, 1.0) if user else hex_color(theme["accent"])[:3]
        bubble = BoxLayout(orientation="vertical", size_hint=(0.84, None), padding=dp(9))
        bubble.bind(minimum_height=bubble.setter("height"))
        glass_style(bubble, tint, dp(14), fill=0.26 if user else 0.12, sheen=0.3, border=0.35)
        label = Label(text=text, font_size=dp(13), color=hex_color(theme["text"]),
                      halign="left", valign="top", size_hint_y=None)
        label.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        label.bind(texture_size=lambda inst, ts: setattr(inst, "height", ts[1]))
        bubble.add_widget(label)
        row = BoxLayout(orientation="horizontal", size_hint_y=None)
        bubble.bind(height=lambda inst, h: setattr(row, "height", h))
        if user:
            row.add_widget(Widget())
            row.add_widget(bubble)
        else:
            row.add_widget(bubble)
            row.add_widget(Widget())
        return row

    def scroll_chat_down(self):
        Clock.schedule_once(lambda dt: setattr(self.chat_scroll, "scroll_y", 0), 0.15)

    def render_chat(self):
        self.chat_box.clear_widgets()
        chat = self.chats.current()
        messages = chat["messages"][-100:]
        self.chat_placeholder = not messages
        if not messages:
            self.chat_box.add_widget(self.make_bubble("jarvis", self.notice or self.t("welcome")))
            self.notice = None
        for m in messages:
            self.chat_box.add_widget(self.make_bubble(m["role"], m["text"]))
        self.response_title.text = self.chat_title(chat)
        self.scroll_chat_down()

    def append_bubble(self, role, text):
        self.chat_box.add_widget(self.make_bubble(role, text))
        self.scroll_chat_down()

    def add_chat_message(self, chat_id, role, text):
        self.chats.add_message(chat_id, role, text)
        if self.chats.current_id != chat_id:
            return
        if self.chat_placeholder:
            self.render_chat()
        else:
            self.append_bubble(role, text)
            self.response_title.text = self.chat_title()

    def show_response(self, message):
        self.add_chat_message(self.chats.current_id, "jarvis", message)

    def new_chat(self):
        chat = self.chats.new_chat()
        self.chats.open_chat(chat["id"])
        self.render_chat()

    def open_chat(self, chat_id):
        self.chats.open_chat(chat_id)
        self.render_chat()
        if getattr(self, "chats_popup", None) is not None:
            self.chats_popup.dismiss()

    def send_command(self, *_):
        if not hasattr(self, "command_input"):
            return
        command_text = self.command_input.text.strip()
        if not command_text:
            return
        self.command_input.text = ""
        chat_id = self.chats.current_id
        self.add_chat_message(chat_id, "user", command_text)
        answer = run_command(self, command_text)
        self.add_chat_message(chat_id, "jarvis", answer)
        self.speak_answer(answer)
        self.set_orb_state("speaking", min(4.0, 0.8 + len(answer) * 0.03))

    def speak_answer(self, answer):
        if not self.speak_enabled:
            return
        try:
            if self.tts.speak(answer, self.language) == "fallback" and not self.tts_fallback_noted:
                self.tts_fallback_noted = True
                self.show_response(self.t("tts_fallback"))
        except VoiceUnavailable:
            if not self.tts_error_noted:
                self.tts_error_noted = True
                self.show_response(self.t("tts_unavailable"))
        except Exception:
            save_crash("speak_answer failed:\n" + traceback.format_exc())

    def toggle_speak(self, *_):
        self.speak_enabled = not self.speak_enabled
        self.tts_error_noted = False
        self.save_settings()
        if self.speak_enabled:
            self.tts.warm_up()
        self.open_section("voice")

    def process_command(self, command_text):
        return run_command(self, command_text)

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
        state = microphone_permission_state()
        if state in ("granted", "not_android"):
            self.start_listening()
        else:
            request_microphone(
                lambda granted: Clock.schedule_once(lambda dt: self.on_mic_permission(granted), 0))

    def on_mic_permission(self, granted):
        if granted:
            self.start_listening()
        else:
            self.show_response(self.t("mic_denied"))

    def start_listening(self):
        if not self.speech.is_supported():
            self.show_response(self.t("stt_unavailable"))
            return
        try:
            self.set_orb_state("listening", 45.0)
            self.speech.start(
                self.language, self.t("listen_prompt"),
                lambda text: Clock.schedule_once(lambda dt: self.on_speech_text(text), 0),
                lambda err: Clock.schedule_once(lambda dt: self.on_speech_error(err), 0))
        except VoiceUnavailable:
            self.set_orb_state("idle")
            self.show_response(self.t("stt_unavailable"))
        except Exception:
            self.set_orb_state("idle")
            save_crash("start_listening failed:\n" + traceback.format_exc())
            self.show_response(self.t("stt_failed"))

    def on_speech_text(self, text):
        self.set_orb_state("idle")
        self.command_input.text = text
        self.send_command()

    def on_speech_error(self, err):
        self.set_orb_state("idle")
        if err != "cancelled":
            self.show_response(self.t("stt_failed"))

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

    def track_popup(self, popup):
        self.open_popups.add(popup)
        popup.bind(on_dismiss=self.popup_closed)
        self.update_pause()

    def popup_closed(self, popup, *_):
        self.open_popups.discard(popup)
        self.update_pause()

    def update_pause(self):
        self.animation_paused = bool(self.open_popups) or self.app_paused

    def make_popup(self, content, size_hint=(0.92, 0.88), **extra):
        theme = THEMES[self.theme_index]
        kwargs = dict(title="", content=content, size_hint=size_hint, auto_dismiss=True,
                      separator_height=0)
        kwargs.update(extra)
        try:
            popup = Popup(background="", background_color=(*hex_color(theme["background"])[:3], 0.96),
                          overlay_color=(0, 0, 0, 0.6), **kwargs)
        except Exception:
            popup = Popup(**kwargs)
        self.track_popup(popup)
        return popup

    def glass_option(self, content, text, active, callback, height=54, tint=None):
        theme = THEMES[self.theme_index]
        btn = GlassButton(text=text, tint=tint or hex_color(theme["accent"])[:3], active=active,
                          size_hint_y=None, height=dp(height), font_size=dp(13), bold=active,
                          color=hex_color(theme["text"]), radius=18)
        btn.bind(on_release=callback)
        content.add_widget(btn)
        return btn

    def show_menu(self, *_):
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        popup = self.make_popup(box, size_hint=(0.74, None), height=dp(4 * 50 + 3 * 8 + 24),
                                pos_hint={"right": 0.97, "top": 0.92})
        items = (("menu_chats", self.show_chats), ("menu_new", self.new_chat),
                 ("menu_settings", self.show_settings), ("menu_about", self.show_about))
        for key, action in items:
            def go(inst, f=action):
                popup.dismiss()
                Clock.schedule_once(lambda dt: f(), 0.12)
            self.glass_option(box, self.t(key), False, go, height=50)
        popup.open()

    def show_chats(self, *_):
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(9))
        content.add_widget(make_label(self.t("chats_title"), 17, theme["accent"], True,
                                      size_hint_y=None, height=dp(30)))
        chats = self.chats.sorted_chats()
        scroll = ScrollView(do_scroll_x=False)
        grid = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        for chat in chats:
            row = BoxLayout(orientation="horizontal", spacing=dp(6), size_hint_y=None, height=dp(62))
            text = self.chat_title(chat) + "\n" + format_time(chat.get("created", ""))
            open_btn = GlassButton(text=text, tint=tint, active=chat["id"] == self.chats.current_id,
                                   font_size=dp(12), color=hex_color(theme["text"]), radius=16)
            open_btn.bind(on_release=lambda inst, cid=chat["id"]: self.open_chat(cid))
            del_btn = IconGlassButton(icon_kind="cross", icon_rgba=hex_color(theme["text"]), tint=tint,
                                      size_hint_x=None, width=dp(56), radius=16)
            del_btn.bind(on_release=lambda inst, cid=chat["id"]: self.confirm_delete(cid))
            row.add_widget(open_btn)
            row.add_widget(del_btn)
            grid.add_widget(row)
        if not chats:
            grid.add_widget(make_label(self.t("chats_empty"), 13, theme["text"], False,
                                       size_hint_y=None, height=dp(50)))
        scroll.add_widget(grid)
        content.add_widget(scroll)
        content.add_widget(self.make_button(self.t("close"), lambda *_: self.chats_popup.dismiss(),
                                            theme["accent"], 44))
        self.chats_popup = self.make_popup(content)
        self.chats_popup.open()

    def confirm_delete(self, chat_id):
        chat = self.chats.get(chat_id)
        if chat is None:
            return
        theme = THEMES[self.theme_index]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        content.add_widget(make_label(self.t("delete_ask").format(self.chat_title(chat)), 14,
                                      theme["text"], False))
        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        popup = self.make_popup(content, size_hint=(0.86, None), height=dp(190))

        def do_delete(*_):
            self.chats.delete_chat(chat_id)
            popup.dismiss()
            self.render_chat()
            if getattr(self, "chats_popup", None) is not None:
                self.chats_popup.dismiss()
            Clock.schedule_once(lambda dt: self.show_chats(), 0.2)

        buttons.add_widget(self.make_button(self.t("yes_delete"), do_delete, theme["accent2"], 48))
        buttons.add_widget(self.make_button(self.t("no"), lambda *_: popup.dismiss(),
                                            theme["accent"], 48))
        content.add_widget(buttons)
        popup.open()

    def show_about(self, *_):
        theme = THEMES[self.theme_index]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        content.add_widget(make_label(self.t("about_title"), 17, theme["accent"], True,
                                      size_hint_y=None, height=dp(30)))
        content.add_widget(make_label(self.t("about_body"), 13, theme["text"], False))
        content.add_widget(make_label(self.t("about_chats").format(len(self.chats.chats)), 12,
                                      theme["accent"], False, size_hint_y=None, height=dp(28)))
        popup = self.make_popup(content, size_hint=(0.92, None), height=dp(360))
        content.add_widget(self.make_button(self.t("close"), lambda *_: popup.dismiss(),
                                            theme["accent"], 44))
        popup.open()

    def set_language(self, code):
        if code in TEXTS:
            self.language = code
            self.save_settings()
            self.build_interface()

    def set_theme(self, index):
        self.theme_index = index % len(THEMES)
        self.save_settings()
        self.update_colors()
        self.build_interface()

    def next_theme(self, *_):
        self.set_theme(self.theme_index + 1)

    def show_settings(self, *_):
        self.open_section("main")

    def open_section(self, section):
        old = getattr(self, "settings_popup", None)
        if old is not None:
            old.dismiss()
        theme = THEMES[self.theme_index]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(9))
        titles = {"main": "settings", "theme": "title_theme", "language": "title_lang",
                  "voice": "title_voice", "account": "title_account"}
        content.add_widget(make_label(self.t(titles[section]), 17, theme["accent"], True,
                                      size_hint_y=None, height=dp(30)))

        def option(text, active, callback, height=54):
            self.glass_option(content, text, active, callback, height)

        if section == "main":
            for key, target in (("sec_theme", "theme"), ("sec_lang", "language"),
                                ("sec_voice", "voice"), ("sec_account", "account")):
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
        elif section == "voice":
            option(self.t("speak_on") if self.speak_enabled else self.t("speak_off"),
                   self.speak_enabled, self.toggle_speak)
            for name in VOICES:
                option(name, self.voice_profile == name,
                       lambda inst, n=name: self.choose_voice(n, self.settings_popup))
            content.add_widget(make_label(self.t("voice_note"), 10, theme["accent2"]))
            content.add_widget(make_label(self.t("voice_hint"), 10, theme["accent2"]))
            content.add_widget(Widget())
        else:
            content.add_widget(make_label(self.t("acct_status"), 13, theme["text"], False))

        nav = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        if section != "main":
            nav.add_widget(self.make_button(self.t("back"), lambda *_: self.open_section("main"),
                                            theme["accent2"], 44))
        nav.add_widget(self.make_button(self.t("close"), lambda *_: self.settings_popup.dismiss(),
                                        theme["accent"], 44))
        content.add_widget(nav)
        self.settings_popup = self.make_popup(content)
        self.settings_popup.open()

    def choose_theme(self, index, popup):
        self.theme_index = index
        self.save_settings()
        popup.dismiss()
        self.update_colors()
        Clock.schedule_once(lambda dt: self.build_interface(), 0.1)

    def choose_language(self, code, popup):
        popup.dismiss()
        Clock.schedule_once(lambda dt: self.set_language(code), 0.1)

    def choose_voice(self, name, popup):
        self.voice_profile = name
        self.save_settings()
        popup.dismiss()
        Clock.schedule_once(lambda dt: self.show_response(self.t("voice_note")), 0.2)

    def on_start(self):
        if self.speak_enabled:
            self.tts.warm_up()
        Clock.schedule_once(lambda dt: self.show_pending_errors(), 1.0)

    def show_pending_errors(self):
        text = self.startup_error or read_last_crash()
        if not text and GLASS_ERRORS:
            text = "Glass style fallback:\n" + GLASS_ERRORS[0]
        if text:
            self.show_error_popup(text)

    def report_error(self, text):
        if text == self._last_err or self._err_count >= 3:
            return
        self._last_err = text
        self._err_count += 1
        Clock.schedule_once(lambda dt: self.show_error_popup(text), 0.2)

    def show_error_popup(self, text):
        try:
            box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(8))
            box.add_widget(TextInput(text=self.t("error_hint") + "\n\n" + text[-3500:], readonly=True, font_size=dp(10),
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
        self.app_paused = True
        self.update_pause()
        self.save_settings()
        return True

    def on_resume(self):
        self.app_paused = False
        self.update_pause()

    def on_stop(self):
        self.save_settings()
        self.tts.shutdown()

if __name__ == "__main__":
    JarvisApp().run()
