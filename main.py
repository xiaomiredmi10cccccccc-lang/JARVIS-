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
import threading
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
                           RoundedRectangle, Triangle, PushMatrix, PopMatrix, Rotate)
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
    """Records speech inside JARVIS and sends it to the protected STT endpoint."""
    MAX_AUDIO_BYTES = 20 * 1024 * 1024

    def __init__(self, app):
        self.app = app
        self._recorder = None
        self._audio_path = None
        self._on_text = None
        self._on_error = None
        self._language = "tg"
        self._lock = threading.RLock()

    def is_supported(self):
        return android_available()

    def start(self, lang, prompt, on_text, on_error):
        if not android_available():
            raise VoiceUnavailable("Microphone recording works only on Android.")
        if not getattr(self.app, "server_token", "").strip():
            raise VoiceUnavailable("server_token_missing")

        with self._lock:
            if self._recorder is not None:
                raise VoiceUnavailable("Recording is already active.")

            recorder = None
            path = os.path.join(
                self.app.user_data_dir,
                "jarvis_voice_" + uuid.uuid4().hex + ".m4a",
            )
            try:
                from jnius import autoclass
                MediaRecorder = autoclass("android.media.MediaRecorder")
                recorder = MediaRecorder()
                # Android MediaRecorder constants: MIC=1, MPEG_4=2, AAC=3.
                recorder.setAudioSource(1)
                recorder.setOutputFormat(2)
                recorder.setAudioEncoder(3)
                recorder.setAudioSamplingRate(16000)
                recorder.setAudioEncodingBitRate(64000)
                recorder.setMaxDuration(30000)
                recorder.setOutputFile(path)
                recorder.prepare()
                recorder.start()
            except Exception as exc:
                if recorder is not None:
                    try:
                        recorder.release()
                    except Exception:
                        pass
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
                raise VoiceUnavailable(str(exc))

            self._recorder = recorder
            self._audio_path = path
            self._on_text = on_text
            self._on_error = on_error
            self._language = lang if lang in ("tg", "ru", "en") else "tg"

    def stop(self):
        """Stop recording, then transcribe asynchronously without leaving JARVIS."""
        with self._lock:
            recorder = self._recorder
            path = self._audio_path
            on_text = self._on_text
            on_error = self._on_error
            language = self._language
            self._recorder = None
            self._audio_path = None
            self._on_text = None
            self._on_error = None

        if recorder is None or not path:
            return False

        stopped = False
        try:
            recorder.stop()
            stopped = True
        except Exception:
            # Android can reject stop() if the clip is too short to encode.
            if on_error:
                on_error("no_speech")
        finally:
            try:
                recorder.release()
            except Exception:
                pass

        if not stopped:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            return False

        threading.Thread(
            target=self._transcribe,
            args=(path, language, on_text, on_error),
            daemon=True,
        ).start()
        return True

    def _transcribe(self, path, language, on_text, on_error):
        import ssl
        try:
            if not os.path.isfile(path):
                raise RuntimeError("audio_file_missing")
            size = os.path.getsize(path)
            if size <= 1024:
                raise RuntimeError("no_speech")
            if size > self.MAX_AUDIO_BYTES:
                raise RuntimeError("audio_too_large")

            with open(path, "rb") as audio_file:
                audio = audio_file.read()

            boundary = "----JARVISBoundary" + uuid.uuid4().hex
            parts = []
            parts.append(
                ("--" + boundary + "\r\n"
                 'Content-Disposition: form-data; name="file"; filename="voice.m4a"\r\n'
                 "Content-Type: audio/mp4\r\n\r\n").encode("ascii")
                + audio + b"\r\n"
            )
            parts.append(
                ("--" + boundary + "\r\n"
                 'Content-Disposition: form-data; name="language"\r\n\r\n'
                 + language + "\r\n").encode("ascii")
            )
            parts.append(("--" + boundary + "--\r\n").encode("ascii"))
            body = b"".join(parts)
            url = "https://jarvis-ai-server-3vuo.onrender.com/transcribe"
            headers = {
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "X-Jarvis-Token": self.app.server_token,
                "Accept": "application/json",
            }
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                context = ssl.create_default_context()

            with urllib.request.urlopen(request, timeout=90, context=context) as response:
                result = json.loads(response.read().decode("utf-8"))
            text = result.get("text", "") if isinstance(result, dict) else ""
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                raise RuntimeError("empty_transcription")
            if on_text:
                on_text(text)
        except urllib.error.HTTPError as exc:
            code = getattr(exc, "code", 0)
            if on_error:
                on_error("unauthorized" if code in (401, 403) else "transcription_http_%s" % code)
        except Exception as exc:
            detail = str(exc)
            if detail in ("no_speech", "audio_too_large", "audio_file_missing", "empty_transcription"):
                error = detail
            else:
                error = "network_error"
            if on_error:
                on_error(error)
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

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

    def speak(self, text, lang="ru", pitch=1.0):
        if not android_available():
            raise VoiceUnavailable("Speech synthesis works only on Android.")
        try:
            from jnius import autoclass
            Locale = autoclass("java.util.Locale")
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            tts = self._engine()
            result = "ok"
            try:
                tts.setPitch(float(pitch))
            except Exception:
                pass
            code = tts.setLanguage(Locale(lang))
            if code < 0 and lang == "tg":
                code = tts.setLanguage(Locale("ru"))
                result = "fallback"
            if code < 0:
                raise VoiceUnavailable("No voice for this language is installed.")
            speak_code = tts.speak(text, TextToSpeech.QUEUE_FLUSH, None, "jarvis")
            if speak_code != 0:
                return "starting"
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

def visible_chars(words, position_ms):
    """How many characters of the spoken text should be visible at this playback position.
    `words` is [[start_ms, end_char], ...] from the server, in order."""
    shown = 0
    for start_ms, end_char in words:
        if start_ms > position_ms:
            break
        shown = end_char
    return shown


# Two Edge TTS voices: male and female. The stored value stays "JARVIS Classic"/"Aura"
# so saved settings keep working; the labels below are what the user sees.
VOICES = ("JARVIS Classic", "Aura")
VOICE_LABELS = {"JARVIS Classic": "Мардона / Мужской / Male", "Aura": "Занона / Женский / Female"}
# Server voice profile and Android TTS pitch (only used if the server voice fails).
VOICE_PROFILE_IDS = {"JARVIS Classic": "classic", "Aura": "aura"}
VOICE_LOCAL_PITCH = {"JARVIS Classic": 0.8, "Aura": 1.1}

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
        "acct_status": "Сервери JARVIS: Render. Барои истифода токенро ворид кунед.",
        "server_token_btn": "Танзими токени сервер",
        "server_token_hint": "Токен дар танзимоти телефони шумо нигоҳ дошта мешавад. Онро бо каси дигар мубодила накунед.",
        "server_token_title": "Токени сервери JARVIS",
        "server_token_placeholder": "JARVIS_SERVER_TOKEN",
        "server_save": "Сабт кардан",
        "server_saved": "Токен сабт шуд. Паёмро санҷед.",
        "server_missing": "Аввал дар Танзимот → Ҳисоб токени серверро ворид кунед.",
        "server_thinking": "JARVIS фикр мекунад...",
        "server_error": "Ба сервер пайваст нашуд. Интернет ва танзимоти Render-ро санҷед.",
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
        "voice_hint": "Овоз дар дохили JARVIS сабт шуда, бо ёрии зеҳни сунъӣ ба матн табдил меёбад.",
        "voice_how": "Микрофонро пахш кунед, сухан гӯед ва барои анҷом боз пахш кунед.",
        "voice_note": "Ҷавобҳо бо овози Edge (мардона ё занона) хонда мешаванд; агар сервер кор накунад, овози телефон истифода мешавад.",
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
        "acct_status": "Сервер JARVIS: Render. Для работы введите токен.",
        "server_token_btn": "Настроить токен сервера",
        "server_token_hint": "Токен хранится в настройках телефона. Не передавайте его другим.",
        "server_token_title": "Токен сервера JARVIS",
        "server_token_placeholder": "JARVIS_SERVER_TOKEN",
        "server_save": "Сохранить",
        "server_saved": "Токен сохранён. Проверьте отправку сообщения.",
        "server_missing": "Сначала в Настройки → Аккаунт введите токен сервера.",
        "server_thinking": "JARVIS думает...",
        "server_error": "Не удалось подключиться к серверу. Проверьте интернет и настройки Render.",
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
        "voice_hint": "Речь записывается внутри JARVIS и преобразуется в текст через ИИ-сервис.",
        "voice_how": "Нажмите микрофон, говорите, затем нажмите снова для завершения.",
        "voice_note": "Ответы озвучиваются голосом Edge (мужской или женский); если сервер недоступен, используется голос телефона.",
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
        "acct_status": "JARVIS server: Render. Enter the token to use it.",
        "server_token_btn": "Configure server token",
        "server_token_hint": "The token is stored in your phone settings. Do not share it.",
        "server_token_title": "JARVIS server token",
        "server_token_placeholder": "JARVIS_SERVER_TOKEN",
        "server_save": "Save",
        "server_saved": "Token saved. Try sending a message.",
        "server_missing": "First enter the server token in Settings → Account.",
        "server_thinking": "JARVIS is thinking...",
        "server_error": "Could not connect to the server. Check internet and Render settings.",
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
        "voice_hint": "Speech is recorded inside JARVIS and transcribed by the AI service.",
        "voice_how": "Tap the mic, speak, then tap it again to finish.",
        "voice_note": "Replies are read with an Edge voice (male or female); the phone voice is used only if the server fails.",
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

# Animation state shared by all orbs: when the screen is rebuilt (language, theme,
# settings) the new orb continues from the same moment instead of starting from zero.
ANIM = {"phase": 0.0, "clock_t": 0.0, "level": 0.0}

def _move_glass(w, *_):
    x, y = round(w.x), round(w.y)
    wd, ht = round(w.width), round(w.height)
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

def flat_style(widget, rgba, radius):
    """Plain rounded fill (no glass): used everywhere except the command input."""
    with widget.canvas.before:
        widget._flat_c = Color(*rgba)
        widget._flat = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[radius])
    widget.bind(pos=lambda w, v: setattr(w._flat, "pos", w.pos),
                size=lambda w, v: setattr(w._flat, "size", w.size))


ORB_FILES = {0: "orb_aura.png", 1: "orb_particle.png", 2: "orb_glass.png"}
_ORB_TEXTURES = {}
_ORB_PROBLEM = {}


def orb_texture(index):
    """Realistic orb image for a theme, or None (then the drawn orb is used)."""
    if index in _ORB_TEXTURES:
        return _ORB_TEXTURES[index]
    tex = None
    name = ORB_FILES[index]
    folders = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    try:
        app = App.get_running_app()
        if app is not None and getattr(app, "directory", None):
            folders.append(app.directory)
    except Exception:
        pass
    # Folders the user can reach with a file manager (Pydroid keeps main.py in a private folder).
    for shared in ("/storage/emulated/0/Download", "/storage/emulated/0/JARVIS", "/storage/emulated/0",
                   "/storage/emulated/0/Documents", "/storage/emulated/0/Pydroid3", "/sdcard/Download"):
        folders.append(shared)
    tried = []
    candidates = []
    for folder in folders:
        candidates.append(os.path.join(folder, "assets", name))
        candidates.append(os.path.join(folder, name))
    for path in candidates:
        if path in tried:
            continue
        tried.append(path)
        try:
            if os.path.exists(path):
                from kivy.core.image import Image as CoreImage
                tex = CoreImage(path).texture
                if tex is not None:
                    break
                _ORB_PROBLEM[index] = "файл хонда нашуд: " + path
        except Exception as exc:
            _ORB_PROBLEM[index] = "хатои хондан (%s): %s" % (type(exc).__name__, path)
    # PNG orb artwork is optional: AnimatedCore draws a procedural orb when
    # no image is found. Missing files are therefore not an application error.
    # Keep genuine image-read errors in _ORB_PROBLEM for diagnostics.
    _ORB_TEXTURES[index] = tex
    return tex


class AnimatedCore(Widget):
    def __init__(self, theme_index=0, **kwargs):
        super().__init__(**kwargs)
        self.theme_index = theme_index
        self.phase = ANIM["phase"]
        self.level = ANIM["level"]
        self.clock_t = ANIM["clock_t"]
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
        dt = min(dt, 0.1)
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
        self.phase += dt * 0.8 * boost
        ANIM["phase"], ANIM["clock_t"], ANIM["level"] = self.phase, self.clock_t, self.level
        self.redraw()

    def draw_aura(self, cx, cy, R, accent, accent2):
        lv = self.level
        R = R * (1 + 0.10 * lv)
        p = self.phase
        halo = radial_texture("aura_halo", ((0, (50, 100, 255, 115)), (0.5, (30, 70, 220, 44)),
                                            (1, (20, 40, 160, 0))))
        hs = min(R * (4.4 + 1.4 * lv), min(self.width, self.height))
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
        gs = min(R * 3 * (1 + 0.3 * lv), min(self.width, self.height))
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

    def _note_missing_orb(self):
        """PNG orb images are optional; the procedural orb is the normal fallback."""
        return

    def draw_photo(self, tex, size):
        """Draw the realistic orb image with a slow breathing motion that follows the voice level."""
        lv, t = self.level, self.clock_t
        breathe = 1 + 0.025 * math.sin(t * 0.9) + 0.07 * lv
        if self.theme_index == 2:
            w = size * 0.95 * breathe
            h = w * tex.height / tex.width
            sphere_y = self.y + self.height * 0.56 + 0.012 * size * math.sin(t * 0.8)
            Color(1, 1, 1, 0.92 + 0.08 * lv)
            # the sphere centre sits 500/1280 of the way down the image
            Rectangle(texture=tex, pos=(self.center_x - w / 2, sphere_y - 0.609 * h), size=(w, h))
            return
        side = size * 1.3 * breathe
        cx, cy = self.center
        angle = 7 * math.sin(t * 0.35) if self.theme_index == 0 else (t * 8) % 360
        PushMatrix()
        Rotate(angle=angle, origin=(cx, cy))
        Color(1, 1, 1, 0.9 + 0.1 * lv)
        Rectangle(texture=tex, pos=(cx - side / 2, cy - side / 2), size=(side, side))
        PopMatrix()

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
            photo = orb_texture(self.theme_index)
            if photo is not None:
                self.draw_photo(photo, size)
                return
            self._note_missing_orb()
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
        # Flat style: plain tinted fill, no glass sheen/border (glass stays only on the command input).
        self.fill_base = 0.26 if active else 0.12
        flat_style(self, (tint[0], tint[1], tint[2], self.fill_base), dp(radius))
        self.bind(state=self.state_changed)

    def state_changed(self, *_):
        fc = getattr(self, "_flat_c", None)
        if fc is not None:
            t = self.tint_rgb
            fc.rgba = (t[0], t[1], t[2], self.fill_base + (0.16 if self.state == "down" else 0.0))

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
    # Mentioning a language as the target of a translation is not a UI-language command.
    if any(phrase in text for phrase in ("тарҷума", "переведи", "перевести", "translate", "translation")):
        return app.t("unknown")
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

# Pre-rendered icon masks (alpha only, zlib + base64): drawn as plain textures, which is safe
# inside a ScrollView (line-based canvas icons left stray dots outside the chat area).
ICON_SIZE = 96
ICON_DATA = {
    "dots": "eNrtmLENhDAMRRkgA7AAA9CmYAEWoE3BLjTZgwUyAVukYQI2iJQT3HFJfeI1p/8GeALHtmw3jRBCCCEEijGgvPV7zrtvIf145ItjRPR9yh9ST/hD/hIAfZcruuf9U+2fnvfPtX8Gsqf2AxlkUtEnosp88XukALZbv0G9Z33rV6wHDT4EP6jLix+xzlnO7uKZ/tFB+uWu34X5+tJ/kD+IxR+Jp637v0XDgwSI9tPxod8Xz0+6vvD+gPc38eew8xU9H8LzLTyf0/sFvR/R+x29n9L7NX0fwO8b9H0Gvy9dVUbex4QQQgghTl7ixYEk",
    "copy": "eNrt2MEJgzAUgOFMUcFBdIWcPBVcwEuHcA4XUGdwAzfoIDl5KFikhxYq8fESQcL/3wKP75Qc8owhIiJKs+pxXJOr+WGV5AolX66yeqVvhf50ZX8evYX73pEaHz8lv3PLt/h++/f44/oT/rFfZ5/sWf559xMfHx8fHx9f5Ge+wv24/7uk/NtL5rdK39yfy07byM/RdUbr7zZvI1b5ZcHHv7zv3yiF+4Ku6vdCv1T6hRPxg3plmzeClXBliIiIEu0NWAHXCQ==",
    "check": "eNrtmD9EhGEcx78N0ZAaoqHkRENpOG44Go6GuCEaoiGSdBEncUdqaIgikmiIhmiIhmiIhmhoSA2RpIZoSA1xw3HD8Q7Rrz/3e/SHnnjvvrX8Psv7vr/n8fnyeofv8wKGYRiGYRjG/9BcxbQP30uwwNOPyRtzXL3IEFcvz11UvchtI1UvcsDVi6yx9EW9Zjj6q35310fRRzClt4VOih5Y14fzGooeONTHHY4eTXc6WKTogYQbpSh6YMQNuyl6YF6nDy0UPbCt8yOOHtVnurJB0QPteV2boeiBXrc6EFLf4dUDk7ocxML5s349sKobruvL8f+oB/Y1YK+M9+PRo+FGA1ZCBaQCkZOIb0f8SQPSoQLqkr8VhUH3DSRJlWhW/bk2UsCmBpyyeumxBmyR/K2PGsCqpT3CraUYp9bSV5aItfSdXVotLVF7Saqljiinln5AqaWfIdTSr1S8ln7H1dJlkt/V0gvW8TVRicrlo1RLR3nn+4mcFKa5vz/sD5BhGIZhGMZf8AIu1A4D",
    "speaker": "eNrtmE0oRUEUx49IWFAKCUkiFj6Tj8hHWUhYSCyUCLGxkGRFSjYKKykrFmJBkiILJUWxodiIQj42iBRFMpxzDe+963rcN0ep+e/+55z7qzt35tyZAdDS0tLS0tLSMqm5O0MRKba+wBxcFEJUKcHXvpGGXIP5b0HxUqQA34YkEeUSLaToZbLH+B4CiVTX+CqF9yM8xA8LC37oLsU3/DzCjwsrPiReUGLBA7rvvLDmQ94jZRps48PWxHd8qKDMiY9NfMKegT6y4kMD5fvs4XPODXynM7+1N/uzZokq4u3gSx8MfCM48ZFY81GURSWTNhctrtBKcOLnUbT4o2yEfInNRSuusQE48qktiJs0WRdyh37K5qI9JKrT+KwYiQBZ2YH23tsdMGXl1lEGfisaTPzgHUoNyAf9n9FWuuNvC7OWA8HMh7hTSiZJP4tuwg0+/Av8tEy6zP9sys5IW0cf5Pf8UbDgwxDl899dELmy3/L7wZIfdIWBQWkX0I39gP8Q/alAsOZDO00h6ZrQrf+Eb5E08WPpDWWbyERzrJIPG44jGInmSSm/y7HreNHbhKnkV2NkVbozdOkq+bkYOZBuE125Sn4MNR3p5tC1/Cc+9/hwf1/u+cm8vuz2B+b+xt2f//T/ovb/yP1/Z9+fcO+v/nh/yL6/Zd+fs58v2M9H7Oc79vMp+/ma/36A/X6D/X6G/X6J/X6M/X5PS0tLS0tL61/oFd0WXc0=",
}
_ICON_TEXTURES = {}


def icon_texture(kind):
    if kind in _ICON_TEXTURES:
        return _ICON_TEXTURES[kind]
    import base64
    import zlib
    tex = None
    try:
        alpha = zlib.decompress(base64.b64decode(ICON_DATA[kind]))
        buf = bytearray(b"\xff\xff\xff\xff" * (ICON_SIZE * ICON_SIZE))
        buf[3::4] = alpha
        tex = Texture.create(size=(ICON_SIZE, ICON_SIZE), colorfmt="rgba")
        tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
    except Exception:
        save_crash("Icon texture failed:\n" + traceback.format_exc())
        tex = None
    _ICON_TEXTURES[kind] = tex
    return tex


class FlatIconButton(Button):
    """Icon-only flat button without any text. Kinds: dots, copy, check, speaker."""
    def __init__(self, icon_kind="copy", icon_rgba=(1, 1, 1, 1), fill=0.0, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self.icon_kind = icon_kind
        self.icon_rgba = icon_rgba
        with self.canvas:
            self._fill_color = Color(1, 1, 1, fill)
            self._fill_shape = Ellipse(pos=self.pos, size=self.size)
            self._icon_color = Color(*icon_rgba)
            self._icon_rect = Rectangle(texture=icon_texture(icon_kind), pos=self.pos, size=self.size)
        self.bind(pos=self._place, size=self._place, state=self._tint)
        self._place()

    def _place(self, *_):
        u = min(self.width, self.height)
        pos = (self.center_x - u / 2, self.center_y - u / 2)
        self._fill_shape.pos = pos
        self._fill_shape.size = (u, u)
        self._icon_rect.pos = pos
        self._icon_rect.size = (u, u)

    def _tint(self, *_):
        r, g, b, a = self.icon_rgba
        self._icon_color.rgba = (r, g, b, a * (0.55 if self.state == "down" else 1.0))

    def set_kind(self, kind):
        self.icon_kind = kind
        self._icon_rect.texture = icon_texture(kind)


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
        self.speak_enabled = True
        self.server_token = ""
        self.last_response = ""
        self.animation_paused = False
        self.orb_state = "idle"
        self._orb_event = None
        self.load_settings()
        self.account_service = get_service(self.user_data_dir)
        self.speech = SpeechRecognizer(self)
        self.tts = SpeechSynthesizer()
        try:
            from android import activity
            activity.bind(on_activity_result=self.on_activity_result)
        except Exception:
            pass
        self._attachment_pending = False
        self.tts_fallback_noted = False
        self.tts_error_noted = False
        self.open_popups = set()
        self.app_paused = False
        self._mic_timeout = None
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
            self.grad_color = Color(1, 1, 1, 1)
            self.grad_rect = Rectangle(texture=self.make_gradient(THEMES[self.theme_index]),
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

    def make_gradient(self, theme=None):
        # Match the glow to each theme's actual orb engine, not one generic blue.
        # The glow rises from the bottom and fades smoothly toward the top.
        theme = theme or THEMES[self.theme_index]
        engine_colors = {
            "AURA ORB": (0.24, 0.18, 0.82),       # blue-violet aura
            "PARTICLE CORE": (0.08, 0.58, 0.68),   # cyan/teal particles
            "GLASS ORB": (0.10, 0.36, 0.82),       # deep blue glass glow
        }
        rgb = engine_colors.get(theme.get("name"), hex_color(theme["accent"])[:3])
        rows = 96
        data = bytearray()
        for i in range(rows):
            t = i / (rows - 1.0)  # bottom -> top
            strength = (1.0 - t) ** 1.65
            r = int(max(0, min(255, rgb[0] * 255 * strength)))
            g = int(max(0, min(255, rgb[1] * 255 * strength)))
            b = int(max(0, min(255, rgb[2] * 255 * strength)))
            a = int(220 * strength)
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
        self.grad_rect.size = (self.root_layout.width, self.root_layout.height * 0.44)

    def t(self, key):
        return TEXTS.get(self.language, TEXTS["tg"]).get(key, key)

    def load_settings(self):
        if self.store.exists("settings"):
            data = self.store.get("settings")
            self.language = data.get("language", "tg")
            self.theme_index = int(data.get("theme_v2", 0)) % len(THEMES)
            self.voice_profile = data.get("voice_profile", "JARVIS Classic")
            if self.voice_profile not in VOICES:
                self.voice_profile = "JARVIS Classic"
            # Enable speech by default after the voice update, while preserving
            # the user's choice on later launches via the versioned preference.
            self.speak_enabled = bool(data.get("speak_v2", True))
            self.server_token = str(data.get("server_token", ""))
        if self.language not in ("tg", "ru", "en"):
            self.language = "tg"

    def save_settings(self):
        self.store.put("settings", language=self.language,
                       theme_v2=self.theme_index,
                       voice_profile=self.voice_profile,
                       speak=self.speak_enabled,
                       speak_v2=self.speak_enabled,
                       server_token=getattr(self, "server_token", ""))

    def update_colors(self):
        theme = THEMES[self.theme_index]
        Window.clearcolor = hex_color(theme["background"])
        self.bg_color.rgba = hex_color(theme["background"])
        self.grad_color.rgba = (1, 1, 1, 1)
        self.grad_rect.texture = self.make_gradient(theme)

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
        menu_btn = FlatIconButton(icon_kind="dots", icon_rgba=hex_color(theme["text"]), fill=0.10,
                                  size_hint_x=None, width=dp(48))
        menu_btn.bind(on_release=self.show_menu)
        header.add_widget(title)
        header.add_widget(menu_btn)
        main.add_widget(header)

        self.status_label = make_label(self.t("ready"), 12, theme["accent"], True,
                                       size_hint_y=None, height=dp(25))
        main.add_widget(self.status_label)

        # Keep the animated engine visible, but reserve most of the screen for
        # the conversation so long AI replies can be read like a normal chat.
        self.visual_area = BoxLayout(orientation="vertical", spacing=dp(8),
                                     size_hint_y=None,
                                     height=min(dp(330), max(dp(205), Window.height * 0.33)))
        main.add_widget(self.visual_area)
        self.build_theme_layout()

        chat_panel = BoxLayout(orientation="vertical", padding=dp(10),
                               spacing=dp(4), size_hint_y=1)
        self.response_title = make_label(self.chat_title(), 10, theme["accent"], True,
                                         size_hint_y=None, height=dp(18))
        self.chat_scroll = ScrollView(do_scroll_x=False)
        self.chat_box = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
        self.chat_box.bind(minimum_height=self.chat_box.setter("height"))
        self.chat_scroll.add_widget(self.chat_box)
        # A narrow background-colored overlay creates a real visual fade at
        # the viewport edges. Because it is drawn above the ScrollView content,
        # it affects every part of a message frame that crosses the edge, while
        # content away from the edge and the command row remain untouched.
        self._init_chat_fade_overlay(theme)
        self.chat_scroll.bind(scroll_y=self.update_chat_fades,
                              pos=self.update_chat_fades,
                              size=self.update_chat_fades)
        chat_panel.add_widget(self.response_title)
        chat_panel.add_widget(self.chat_scroll)
        main.add_widget(chat_panel)
        self.render_chat()

        command_row = BoxLayout(orientation="horizontal", spacing=dp(7),
                                size_hint_y=None, height=dp(52))
        glass_style(command_row, tint, dp(26), fill=0.025, sheen=0.025, border=0.10)
        self.command_input = TextInput(
            hint_text=self.t("input"), multiline=False, size_hint_x=1,
            font_size=dp(15), foreground_color=hex_color(theme["text"]),
            hint_text_color=(*hex_color(theme["accent"])[:3], 0.52),
            cursor_color=hex_color(theme["accent"]),
            background_normal="", background_active="",
            background_color=(0, 0, 0, 0),
            padding=[dp(22), dp(15), dp(10), dp(10)],
        )
        self.command_input.bind(on_text_validate=self.send_command)
        self.command_input.bind(text=self.on_text_change)
        self.attach_btn = Button(text="+", font_size=dp(28), size_hint_x=None, width=dp(42),
                                 background_normal="", background_color=(0, 0, 0, 0),
                                 color=hex_color(theme["accent"]))
        self.attach_btn.bind(on_release=self.pick_attachment)
        self.stop_speech_btn = Button(text="\u00d7", font_size=dp(30), size_hint_x=None, width=dp(38),
                                      background_normal="", background_color=(0, 0, 0, 0),
                                      color=hex_color(theme["text"]))
        self.stop_speech_btn.bind(on_release=self.stop_speaking)
        self.action_btn = SendWaveButton(tint_rgb=tint, icon_rgba=hex_color(theme["text"]),
                                       size_hint_x=None, width=dp(52))
        self.action_btn.bind(on_release=self.on_action)
        command_row.add_widget(self.attach_btn)
        command_row.add_widget(self.command_input)
        command_row.add_widget(self.stop_speech_btn)
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

    def make_bubble(self, role, text, full_text=None):
        theme = THEMES[self.theme_index]
        user = role == "user"
        accent = hex_color(theme["accent"])[:3]
        if user:
            tint, fill, who = accent, 0.20, "Шумо"
            who_color = (accent[0], accent[1], accent[2], 1)
        else:
            tint, fill, who = (0.05, 0.07, 0.14), 0.55, "JARVIS"
            who_color = (accent[0], accent[1], accent[2], 1)
        # ChatGPT-like transcript: user messages appear in a distinct rounded
        # accent bubble; JARVIS replies use the full available width on the
        # dark background. Keep the animated engine area unchanged in size.
        bubble = BoxLayout(orientation="vertical", size_hint=(None, None),
                           padding=[dp(14), dp(11), dp(14), dp(11)] if user else [dp(12), dp(9), dp(12), dp(7)],
                           spacing=dp(4))
        bubble.bind(minimum_height=bubble.setter("height"))
        if user:
            # User bubble follows the selected engine accent without a bright glow.
            flat_style(bubble, (*accent, 0.20), dp(18))
        caption = Label(text=who, font_size=dp(10), bold=True, color=who_color,
                        halign="right" if user else "left", valign="middle",
                        size_hint_y=None, height=dp(14))
        caption.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        bubble.add_widget(caption)
        label = Label(text=text, font_size=dp(15),
                      color=(1, 1, 1, 1) if user else hex_color(theme["text"]),
                      halign="right" if user else "left", valign="top", size_hint_y=None)
        label.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        label.bind(texture_size=lambda inst, ts: setattr(inst, "height", ts[1]))
        bubble.add_widget(label)
        message = str(full_text if full_text is not None else text)
        if not user and message.strip():
            icon_color = (*hex_color(theme["text"])[:3], 0.8)
            actions = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(36), spacing=dp(4))
            copy_btn = FlatIconButton(icon_kind="copy", icon_rgba=icon_color,
                                      size_hint=(None, None), size=(dp(36), dp(36)))
            read_btn = FlatIconButton(icon_kind="speaker", icon_rgba=icon_color,
                                      size_hint=(None, None), size=(dp(36), dp(36)))
            copy_btn.bind(on_release=lambda btn, m=message: self.copy_message(m, btn))
            read_btn.bind(on_release=lambda btn, m=message: self.read_message(m))
            actions.add_widget(copy_btn)
            actions.add_widget(read_btn)
            actions.add_widget(Widget())
            bubble.add_widget(actions)
        row = BoxLayout(orientation="horizontal", size_hint=(1, None))
        row.jarvis_label = label
        # Explicitly size each bubble from the full chat-row width. This avoids
        # Kivy measuring assistant replies only as wide as their text texture.
        def fit_bubble_width(instance, width):
            target = width * (0.86 if user else 0.99)
            bubble.width = max(dp(80), target)
        row.bind(width=fit_bubble_width)
        bubble.width = dp(300)
        bubble.bind(height=lambda inst, h: setattr(row, "height", h))
        if user:
            row.add_widget(Widget())
            row.add_widget(bubble)
        else:
            row.add_widget(bubble)
            row.add_widget(Widget())
        return row

    def _init_chat_fade_overlay(self, theme):
        """Create a narrow fade overlay clipped to the visible chat viewport.

        The overlay is background-colored, so it visually dissolves whatever
        part of a message frame reaches the top/bottom edge. It does not change
        the alpha of the message Label or fade the entire message.
        """
        bg = hex_color(theme["background"])
        self._chat_fade_rects = []
        self._chat_fade_colors = []
        # Twelve half-dp strips on each edge create a smoother, narrow fade.
        # The edge is covered most strongly; each strip inward is lighter.
        with self.chat_scroll.canvas.after:
            for i in range(24):
                layer = i % 12
                alpha = 0.88 * (1.0 - (layer / 12.0))
                c = Color(bg[0], bg[1], bg[2], alpha)
                r = Rectangle(pos=(0, 0), size=(0, 0))
                self._chat_fade_colors.append(c)
                self._chat_fade_rects.append(r)
        self.update_chat_fades()

    def update_chat_fades(self, *_):
        """Position the edge-fade strips without changing message opacity."""
        if not getattr(self, "_chat_fade_rects", None):
            return
        x, y = self.chat_scroll.pos
        w, h = self.chat_scroll.size
        strip_h = dp(0.5)
        for i, rect in enumerate(self._chat_fade_rects):
            layer = i % 12
            if i < 12:
                # Top edge: strongest strip at the very top.
                rect.pos = (x, y + h - strip_h * (layer + 1))
            else:
                # Bottom edge: strongest strip at the very bottom.
                rect.pos = (x, y + strip_h * layer)
            rect.size = (w, strip_h)
        # The canvas overlay moves with the ScrollView and stays within its
        # visible bounds; command controls are outside this viewport.

    def scroll_chat_down(self):
        def finish_scroll(_dt):
            self.chat_scroll.scroll_y = 0
            self.update_chat_fades()
        Clock.schedule_once(finish_scroll, 0.15)

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
        Clock.schedule_once(lambda dt: self.update_chat_fades(), 0.2)
        self.scroll_chat_down()

    def append_bubble(self, role, text):
        self.chat_box.add_widget(self.make_bubble(role, text))
        Clock.schedule_once(lambda dt: self.update_chat_fades(), 0.1)
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

    def pick_attachment(self, *_):
        """Open Android document picker and send the selected image/text file to JARVIS."""
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
            intent.addCategory(Intent.CATEGORY_OPENABLE)
            intent.setType("*/*")
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            self._attachment_pending = True
            PythonActivity.mActivity.startActivityForResult(intent, 7412)
        except Exception as exc:
            save_crash("Attachment picker failed: " + str(exc))
            self.show_response("Интихоби файл дар ин дастгоҳ кушода нашуд.")

    def on_activity_result(self, request_code, result_code, data):
        if request_code != 7412 or not getattr(self, "_attachment_pending", False):
            return
        self._attachment_pending = False
        try:
            from jnius import autoclass
            Activity = autoclass("android.app.Activity")
            if result_code != Activity.RESULT_OK or data is None:
                return
            uri = data.getData()
            if uri is None:
                self.show_response("Файл интихоб нашуд.")
                return
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            resolver = PythonActivity.mActivity.getContentResolver()
            mime = str(resolver.getType(uri) or "application/octet-stream")
            stream = resolver.openInputStream(uri)
            output = autoclass("java.io.ByteArrayOutputStream")()
            buffer = autoclass("byte[]") if False else None
            # Read in bounded chunks without needing filesystem permission.
            chunk = autoclass("byte[]") if False else None
            from jnius import cast, jarray, jbyte
            buf = jarray(jbyte)(8192)
            total = 0
            while True:
                count = stream.read(buf)
                if count < 0:
                    break
                total += count
                if total > 20 * 1024 * 1024:
                    raise RuntimeError("Файл аз 20 МБ калон аст.")
                output.write(buf, 0, count)
            stream.close()
            raw = bytes(output.toByteArray())
            if not raw:
                raise RuntimeError("Файл холӣ аст.")
            name = str(uri.getLastPathSegment() or "attachment")
            prompt = self.command_input.text.strip() if hasattr(self, "command_input") else ""
            if hasattr(self, "command_input"):
                self.command_input.text = ""
            chat_id = self.chats.current_id
            display = "📎 " + name + ("\n" + prompt if prompt else "\nЛутфан ин файлро таҳлил кун.")
            self.add_chat_message(chat_id, "user", display)
            threading.Thread(target=self._upload_attachment, args=(chat_id, name, mime, raw, prompt), daemon=True).start()
        except Exception as exc:
            save_crash("Attachment read failed:\n" + traceback.format_exc())
            Clock.schedule_once(lambda dt, message=str(exc): self.show_response("Файл хонда нашуд: " + message), 0)

    def _upload_attachment(self, chat_id, filename, mime, raw, prompt):
        import ssl
        boundary = "----JARVIS" + uuid.uuid4().hex
        def field(name, value):
            return ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n" + value + "\r\n").encode("utf-8")
        safe_name = os.path.basename(filename).replace('"', "_")
        body = field("prompt", prompt or "Таҳлил кун ва натиҷаро фаҳмо шарҳ деҳ.")
        body += field("language", self.language if self.language in ("tg", "ru", "en") else "tg")
        body += ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + safe_name + "\"\r\nContent-Type: " + mime + "\r\n\r\n").encode("utf-8") + raw + ("\r\n--" + boundary + "--\r\n").encode("utf-8")
        try:
            ctx = None
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                pass
            req = urllib.request.Request("https://jarvis-ai-server-3vuo.onrender.com/analyze", data=body, method="POST", headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "X-Jarvis-Token": self.server_token})
            with urllib.request.urlopen(req, timeout=90, context=ctx) as response:
                result = json.loads(response.read().decode("utf-8"))
            answer = str(result.get("answer", "Ҷавоби таҳлил гирифта нашуд."))
            Clock.schedule_once(lambda dt: self._attachment_answer(chat_id, answer), 0)
        except urllib.error.HTTPError as exc:
            detail = "Хатои сервер: HTTP " + str(exc.code)
            try:
                payload = json.loads(exc.read().decode("utf-8")); detail = str(payload.get("detail", detail))
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._attachment_answer(chat_id, detail), 0)
        except Exception as exc:
            Clock.schedule_once(lambda dt, message=str(exc): self._attachment_answer(chat_id, "Фиристодани файл ноком шуд: " + message), 0)

    def _attachment_answer(self, chat_id, answer):
        self.deliver_answer(chat_id, answer)

    def send_command(self, *_):
        if not hasattr(self, "command_input"):
            return
        command_text = self.command_input.text.strip()
        if not command_text:
            return
        self.command_input.text = ""
        chat_id = self.chats.current_id
        # Keep prior turns for the API before adding the current user message.
        chat = self.chats.get(chat_id) or self.chats.current()
        history = []
        for item in chat.get("messages", [])[-12:]:
            role = "assistant" if item.get("role") == "jarvis" else item.get("role")
            if role in ("user", "assistant") and item.get("text"):
                history.append({"role": role, "content": item["text"][:4000]})
        self.add_chat_message(chat_id, "user", command_text)
        answer = run_command(self, command_text)
        if answer == self.t("unknown"):
            if not self.server_token.strip():
                answer = self.t("server_missing")
                self.deliver_answer(chat_id, answer)
                return
            self.set_orb_state("thinking")
            if hasattr(self, "status_label"):
                self.status_label.text = self.t("server_thinking")
            threading.Thread(target=self._ask_server,
                             args=(chat_id, command_text, history), daemon=True).start()
            return
        self.deliver_answer(chat_id, answer)
        self.set_orb_state("speaking", min(4.0, 0.8 + len(answer) * 0.03))


    def _ask_server(self, chat_id, message, history):
        import ssl
        url = "https://jarvis-ai-server-3vuo.onrender.com/chat"
        payload = {
            "message": message[:4000],
            "history": history[-12:],
            "web_search": False,
            "language": self.language if self.language in ("tg", "ru", "en") else "tg",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = None

        def extract(data):
            if isinstance(data, str):
                return data.strip()
            if isinstance(data, dict):
                for key in ("answer", "reply", "response", "message", "text", "content"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            return ""

        answer, last = "", ""
        for attempt in (1, 2):  # дафъаи дуюм барои Render-и хобида
            try:
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={"Content-Type": "application/json",
                             "X-Jarvis-Token": self.server_token})
                with urllib.request.urlopen(req, timeout=90, context=ctx) as response:
                    data = json.loads(response.read().decode("utf-8"))
                answer = extract(data)
                if not answer:
                    keys = ",".join(list(data.keys())[:6]) if isinstance(data, dict) else type(data).__name__
                    last = "формати ҷавоб: " + keys
                break
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = str(json.loads(exc.read().decode("utf-8")).get("detail", ""))[:120]
                except Exception:
                    pass
                if exc.code in (401, 403):
                    answer = self.t("server_missing") + " (%d)" % exc.code
                else:
                    last = "HTTP %d %s" % (exc.code, detail)
                break
            except Exception as exc:
                last = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        if not answer:
            answer = self.t("server_error") + "\n[" + last + "]"
        Clock.schedule_once(lambda dt: self._finish_server_answer(chat_id, answer), 0)

    def _finish_server_answer(self, chat_id, answer):
        if hasattr(self, "status_label"):
            self.status_label.text = self.t("ready")
        self.set_orb_state("speaking", min(4.0, 0.8 + len(answer) * 0.02))
        self.deliver_answer(chat_id, answer)

    def configure_server_token(self, *_):
        theme = THEMES[self.theme_index]
        content = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        content.add_widget(make_label(self.t("server_token_title"), 16, theme["accent"], True,
                                      size_hint_y=None, height=dp(30)))
        token_input = TextInput(text=self.server_token, hint_text=self.t("server_token_placeholder"),
                                password=True, multiline=False, size_hint_y=None, height=dp(48),
                                font_size=dp(13))
        content.add_widget(token_input)
        content.add_widget(make_label(self.t("server_token_hint"), 10, theme["accent2"], False))
        popup = self.make_popup(content, size_hint=(0.92, None), height=dp(260))
        buttons = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        def save_token(*_):
            self.server_token = token_input.text.strip()
            self.save_settings()
            popup.dismiss()
            self.show_response(self.t("server_saved") if self.server_token else self.t("server_missing"))
        buttons.add_widget(self.make_button(self.t("server_save"), save_token, theme["accent"], 44))
        buttons.add_widget(self.make_button(self.t("close"), lambda *_: popup.dismiss(), theme["accent2"], 44))
        content.add_widget(buttons)
        popup.open()

    def speak_answer(self, answer):
        """Prefer server-generated speech; fall back to Android TTS if the server is unavailable."""
        if not self.speak_enabled or not answer:
            return
        text = str(answer).strip()[:4000]
        if not text:
            return
        # Server speech/network work must run off the Kivy UI thread.
        if self.server_token.strip():
            threading.Thread(target=self._request_server_speech,
                             args=(text,), daemon=True).start()
        else:
            self._speak_local_fallback(text)

    def _speak_local_fallback(self, answer, force=False):
        """Use the Android TTS engine selected in system settings if server speech fails."""
        if (not self.speak_enabled and not force) or not answer:
            return
        try:
            self.tts.stop()
            def attempt(_dt=None, tries=0):
                if not self.speak_enabled and not force:
                    return
                try:
                    result = self.tts.speak(answer[:4000], lang=("ru" if self.language == "tg" else self.language),
                                           pitch=VOICE_LOCAL_PITCH.get(self.voice_profile, 1.0))
                    if result == "starting" and tries < 8:
                        Clock.schedule_once(lambda dt: attempt(dt, tries + 1), 0.5)
                except Exception as exc:
                    save_crash("Local speech fallback failed: " + str(exc))
                    if not self.tts_error_noted:
                        self.tts_error_noted = True
                        self.show_response("Овози сервер дастрас нест ва овози маҳаллӣ низ кор накард. Танзимоти Text-to-Speech-ро санҷед.")
            Clock.schedule_once(attempt, 0.15)
        except Exception:
            save_crash("Could not schedule local speech fallback:\n" + traceback.format_exc())

    def deliver_answer(self, chat_id, answer):
        """Show a JARVIS answer. With server speech the words appear while they are spoken."""
        text = str(answer).strip()[:4000]
        if self.speak_enabled and text and self.server_token.strip():
            threading.Thread(target=self._request_synced_speech,
                             args=(chat_id, answer, text), daemon=True).start()
        else:
            self.add_chat_message(chat_id, "jarvis", answer)
            self.speak_answer(answer)

    def _request_synced_speech(self, chat_id, answer, text):
        import ssl
        import base64
        import tempfile
        url = "https://jarvis-ai-server-3vuo.onrender.com/speak"
        payload = json.dumps({"text": text, "timings": True,
                              "voice_profile": VOICE_PROFILE_IDS.get(self.voice_profile, "aura")},
                             ensure_ascii=False).encode("utf-8")
        try:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = None
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "X-Jarvis-Token": self.server_token,
                         "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=75, context=ctx) as response:
                data = json.loads(response.read(30 * 1024 * 1024).decode("utf-8"))
            audio = base64.b64decode(data.get("audio_base64", ""))
            words = [[int(w[0]), int(w[1])] for w in data.get("words", [])]
            if not audio:
                raise RuntimeError("Audio is empty")
            fd, path = tempfile.mkstemp(prefix="jarvis_speech_", suffix=".mp3")
            with os.fdopen(fd, "wb") as out:
                out.write(audio)
            Clock.schedule_once(lambda dt: self._start_synced_answer(chat_id, answer, text, path, words), 0)
        except Exception as exc:
            detail = "network"
            if isinstance(exc, urllib.error.HTTPError):
                detail = "HTTP %d" % exc.code
                try:
                    detail = str(json.loads(exc.read().decode("utf-8")).get("detail", detail))[:140]
                except Exception:
                    pass
            save_crash("Synced speech failed: " + detail + "\n" + traceback.format_exc())
            def fallback(dt):
                self.add_chat_message(chat_id, "jarvis", answer)
                self._note_server_speech_error(detail)
                self._speak_local_fallback(text)
            Clock.schedule_once(fallback, 0)

    def _start_synced_answer(self, chat_id, answer, text, path, words):
        """Start the audio, then reveal the answer word by word at the spoken positions."""
        self.chats.add_message(chat_id, "jarvis", answer)
        label = None
        if self.chats.current_id == chat_id:
            if self.chat_placeholder:
                self.chat_box.clear_widgets()
                self.chat_placeholder = False
            row = self.make_bubble("jarvis", "", full_text=answer)
            self.chat_box.add_widget(row)
            label = getattr(row, "jarvis_label", None)
            self.response_title.text = self.chat_title()
            self.scroll_chat_down()
        started = self._play_server_audio(path)
        player = getattr(self, "_server_speech_player", None) if started else None
        if label is None or not words:
            if label is not None:
                label.text = answer
            return
        begin = time.monotonic()
        state = {"shown": 0}

        def finish():
            label.text = answer
            self.scroll_chat_down()

        def tick(dt):
            if player is None or getattr(self, "_server_speech_player", None) is not player:
                finish()  # no audio, or speech was stopped/replaced: show everything
                return False
            try:
                position = int(player.getCurrentPosition())
                playing = bool(player.isPlaying())
            except Exception:
                position, playing = int((time.monotonic() - begin) * 1000), True
            chars = visible_chars(words, position)
            if chars != state["shown"]:
                state["shown"] = chars
                label.text = text[:chars]
                self.scroll_chat_down()
            if not playing and position > 0:
                finish()
                return False
            return True

        if player is None:
            finish()
        else:
            Clock.schedule_interval(tick, 0.04)

    def copy_message(self, text, button=None):
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(str(text))
        except Exception:
            save_crash("Copy failed:\n" + traceback.format_exc())
            self.show_response("Нусхабардорӣ кор накард.")
            return
        if button is not None:
            button.set_kind("check")
            Clock.schedule_once(lambda dt: button.set_kind("copy"), 1.2)

    def read_message(self, text):
        """Read a reply aloud: the text goes to the server again (works even if auto-voice is off)."""
        text = str(text).strip()[:4000]
        if not text:
            return
        player = getattr(self, "_server_speech_player", None)
        try:
            playing = bool(player is not None and player.isPlaying())
        except Exception:
            playing = False
        self.stop_speaking()
        if playing:
            return  # a second tap while it is reading stops it
        if self.server_token.strip():
            self.set_orb_state("speaking", min(4.0, 0.8 + len(text) * 0.02))
            threading.Thread(target=self._request_server_speech, args=(text, True), daemon=True).start()
        else:
            self._speak_local_fallback(text, force=True)

    def stop_speaking(self, *_):
        try:
            self.tts.stop()
        except Exception:
            pass
        player = getattr(self, "_server_speech_player", None)
        if player is not None:
            try:
                player.stop()
                player.release()
            except Exception:
                pass
            self._server_speech_player = None
        self.set_orb_state("idle")

    def _request_server_speech(self, text, force=False):
        import ssl
        import tempfile
        url = "https://jarvis-ai-server-3vuo.onrender.com/speak"
        payload = json.dumps({"text": text, "voice_profile": VOICE_PROFILE_IDS.get(self.voice_profile, "aura")}, ensure_ascii=False).encode("utf-8")
        try:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = None
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "X-Jarvis-Token": self.server_token,
                         "Accept": "audio/mpeg"})
            with urllib.request.urlopen(req, timeout=75, context=ctx) as response:
                audio = response.read(15 * 1024 * 1024 + 1)
            if not audio or len(audio) > 15 * 1024 * 1024:
                raise RuntimeError("Audio is empty or too large")
            fd, path = tempfile.mkstemp(prefix="jarvis_speech_", suffix=".mp3")
            with os.fdopen(fd, "wb") as out:
                out.write(audio)
            Clock.schedule_once(lambda dt: self._play_server_audio(path), 0)
        except urllib.error.HTTPError as exc:
            detail = "HTTP %d" % exc.code
            try:
                data = json.loads(exc.read().decode("utf-8"))
                detail = str(data.get("detail", detail))[:140]
            except Exception:
                pass
            save_crash("Server speech error: " + detail)
            Clock.schedule_once(lambda dt: self._note_server_speech_error(detail), 0)
            Clock.schedule_once(lambda dt: self._speak_local_fallback(text, force), 0)
        except Exception:
            save_crash("Server speech failed:\n" + traceback.format_exc())
            Clock.schedule_once(lambda dt: self._note_server_speech_error("network"), 0)
            Clock.schedule_once(lambda dt: self._speak_local_fallback(text, force), 0)

    def _note_server_speech_error(self, detail):
        """Tell the user (once per launch) why the Edge voice was replaced by the phone voice."""
        if getattr(self, "server_speech_error_noted", False):
            return
        self.server_speech_error_noted = True
        try:
            self.show_response("Овози Edge дастрас нест (%s). Овози телефон истифода мешавад." % detail)
        except Exception:
            pass

    def _play_server_audio(self, path):
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            MediaPlayer = autoclass("android.media.MediaPlayer")
            File = autoclass("java.io.File")
            Uri = autoclass("android.net.Uri")
            player = MediaPlayer.create(PythonActivity.mActivity, Uri.fromFile(File(path)))
            if player is None:
                raise VoiceUnavailable("Could not open generated speech audio")
            old_player = getattr(self, "_server_speech_player", None)
            if old_player is not None:
                try:
                    old_player.stop()
                    old_player.release()
                except Exception:
                    pass
            self._server_speech_player = player
            player.start()
            return True
        except Exception:
            save_crash("Server audio playback failed:\n" + traceback.format_exc())
            try:
                os.remove(path)
            except Exception:
                pass
            return False

    def toggle_speak(self, *_):
        self.speak_enabled = not self.speak_enabled
        self.tts_error_noted = False
        self.save_settings()
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
            self.stop_listening()
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
        if not self.server_token.strip():
            self.show_response(self.t("server_missing"))
            return
        if not self.speech.is_supported():
            self.show_response(self.t("stt_unavailable"))
            return
        try:
            self.set_orb_state("listening", 30.0)
            self.speech.start(
                self.language, self.t("listen_prompt"),
                lambda text: Clock.schedule_once(lambda dt: self.on_speech_text(text), 0),
                lambda err: Clock.schedule_once(lambda dt: self.on_speech_error(err), 0))
            # Keep recording bounded; the mic button can stop it earlier.
            self._mic_timeout = Clock.schedule_once(lambda dt: self.stop_listening(), 30.0)
        except VoiceUnavailable as exc:
            self.set_orb_state("idle")
            if str(exc) == "server_token_missing":
                self.show_response(self.t("server_missing"))
            else:
                save_crash("start_listening failed: " + str(exc))
                self.show_response(self.t("stt_unavailable"))
        except Exception:
            self.set_orb_state("idle")
            save_crash("start_listening failed:\n" + traceback.format_exc())
            self.show_response(self.t("stt_failed"))

    def stop_listening(self):
        timeout = getattr(self, "_mic_timeout", None)
        if timeout is not None:
            try:
                timeout.cancel()
            except Exception:
                pass
            self._mic_timeout = None
        try:
            self.speech.stop()
            self.set_orb_state("thinking")
            if hasattr(self, "status_label"):
                self.status_label.text = self.t("server_thinking")
        except Exception:
            self.set_orb_state("idle")
            save_crash("stop_listening failed:\n" + traceback.format_exc())
            self.show_response(self.t("stt_failed"))

    def on_speech_text(self, text):
        self.set_orb_state("idle")
        if not text.strip():
            self.show_response(self.t("stt_failed"))
            return
        self.command_input.text = text
        self.send_command()

    def on_speech_error(self, err):
        self.set_orb_state("idle")
        if err == "unauthorized":
            self.show_response(self.t("server_missing"))
        elif err in ("network_error", "transcription_http_502", "transcription_http_503"):
            self.show_response(self.t("server_error"))
        elif err != "cancelled":
            self.show_response(self.t("stt_failed") + " (" + str(err)[:50] + ")")

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
                option(VOICE_LABELS.get(name, name), self.voice_profile == name,
                       lambda inst, n=name: self.choose_voice(n, self.settings_popup))
            content.add_widget(make_label(self.t("voice_note"), 10, theme["accent2"]))
            content.add_widget(make_label(self.t("voice_hint"), 10, theme["accent2"]))
            content.add_widget(Widget())
        else:
            content.add_widget(make_label(self.t("acct_status"), 13, theme["text"], False))
            content.add_widget(self.make_button(self.t("server_token_btn"),
                                                self.configure_server_token,
                                                theme["accent"], 48))
            content.add_widget(Widget())

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
