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
                           RoundedRectangle, Triangle, PushMatrix, PopMatrix, Rotate,
                           RenderContext, Mesh)
from kivy.graphics.texture import Texture
from kivy.core.text import LabelBase
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
        "you": "Шумо",
        "file_picker_failed": "Интихоби файл дар ин дастгоҳ кушода нашуд.",
        "file_not_selected": "Файл интихоб нашуд.",
        "file_too_big": "Файл аз 20 МБ калон аст.",
        "file_empty": "Файл холӣ аст.",
        "file_default_prompt": "Лутфан ин файлро таҳлил кун.",
        "file_prompt_server": "Таҳлил кун ва натиҷаро фаҳмо шарҳ деҳ.",
        "file_read_failed": "Файл хонда нашуд: {}",
        "file_send_failed": "Фиристодани файл ноком шуд: {}",
        "file_no_answer": "Ҷавоби таҳлил гирифта нашуд.",
        "server_http_error": "Хатои сервер: HTTP {}",
        "reply_format": "формати ҷавоб: {}",
        "tts_failed_all": "Овози сервер дастрас нест ва овози маҳаллӣ низ кор накард. Танзимоти Text-to-Speech-ро санҷед.",
        "copy_failed": "Нусхабардорӣ кор накард.",
        "edge_unavailable": "Овози Edge дастрас нест ({}). Овози телефон истифода мешавад.",
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
        "you": "Вы",
        "file_picker_failed": "Не удалось открыть выбор файла на этом устройстве.",
        "file_not_selected": "Файл не выбран.",
        "file_too_big": "Файл больше 20 МБ.",
        "file_empty": "Файл пуст.",
        "file_default_prompt": "Пожалуйста, проанализируй этот файл.",
        "file_prompt_server": "Проанализируй и понятно объясни результат.",
        "file_read_failed": "Не удалось прочитать файл: {}",
        "file_send_failed": "Не удалось отправить файл: {}",
        "file_no_answer": "Результат анализа не получен.",
        "server_http_error": "Ошибка сервера: HTTP {}",
        "reply_format": "формат ответа: {}",
        "tts_failed_all": "Голос сервера недоступен, и локальный голос тоже не сработал. Проверьте настройки Text-to-Speech.",
        "copy_failed": "Не удалось скопировать.",
        "edge_unavailable": "Голос Edge недоступен ({}). Используется голос телефона.",
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
        "you": "You",
        "file_picker_failed": "The file picker could not be opened on this device.",
        "file_not_selected": "No file selected.",
        "file_too_big": "The file is larger than 20 MB.",
        "file_empty": "The file is empty.",
        "file_default_prompt": "Please analyze this file.",
        "file_prompt_server": "Analyze it and explain the result clearly.",
        "file_read_failed": "Could not read the file: {}",
        "file_send_failed": "Sending the file failed: {}",
        "file_no_answer": "No analysis result was received.",
        "server_http_error": "Server error: HTTP {}",
        "reply_format": "reply format: {}",
        "tts_failed_all": "The server voice is unavailable and the local voice failed too. Check the Text-to-Speech settings.",
        "copy_failed": "Copy failed.",
        "edge_unavailable": "The Edge voice is unavailable ({}). The phone voice is used instead.",
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

def register_app_fonts():
    """Use JarvisSans (Roboto + monochrome Noto Emoji merged) as the app's default font, so emoji
    and symbols are drawn instead of empty boxes. Falls back silently to the built-in Roboto if the
    font files are not found (put JarvisSans-Regular.ttf / JarvisSans-Bold.ttf in assets/)."""
    folders = [os.path.dirname(os.path.abspath(__file__)), os.getcwd(),
               "/storage/emulated/0/Download", "/storage/emulated/0/JARVIS", "/sdcard/Download"]

    def find(name):
        for folder in folders:
            for path in (os.path.join(folder, "assets", name), os.path.join(folder, name)):
                try:
                    if os.path.isfile(path):
                        return path
                except Exception:
                    pass
        return None

    regular = find("JarvisSans-Regular.ttf")
    bold = find("JarvisSans-Bold.ttf") or regular
    if not regular:
        return False
    try:
        LabelBase.register(name="Roboto", fn_regular=regular, fn_bold=bold,
                           fn_italic=regular, fn_bolditalic=bold)
        return True
    except Exception:
        return False


FONT_OK = register_app_fonts()

# Emoji picker (works without the phone keyboard): (tab label, emoji characters)
EMOJI_GROUPS = (
    ('😀', '😀😃😄😁😆😅😂🤣😊😇🙂😉😌😍🥰😘😋😛😜🤪😎🤩🥳😏😒😞😔😟😕🙁😣😖😫😩🥺😢😭😤😠😡🤬🤯😳🥵🥶😱😨😰😥😓🤗🤔🤭🤫😶😐😑😬🙄😯😴🤤😪😵🤐🤢🤮🤧😷🤒🤕🤠🥸🤓🧐'),
    ('👍', '👍👎👌✌🤞🤟🤘🤙👈👉👆👇☝✋🤚🖐🖖👋🤝👏🙌👐🤲🙏✍💪🦾👀👂👃👄👅🧠'),
    ('❤', '❤🧡💛💚💙💜🖤🤍🤎💔💕💞💓💗💖💘💝💯💢💥💫💦💨🔥✨⭐🌟⚡🎉🎊🎁🏆🥇🎯'),
    ('🎉', '🤖👻💀👽🙈🙉🙊🐶🐱🐭🐹🐰🦊🐻🐼🦁🐯🐸🐵🐔🐧🐦🦋🐝🌸🌹🌻🌳🌍🌙☀🌈☁❄🍎🍌🍉🍕🍔🍟🍩🍰☕🍺⚽🏀🎵🎶🎮📱💻📷💡📚✏📌📎🔑🔒🔔📞✉🚗✈🚀🏠💰💳📈✅❌⚠❓❗➕➖➡⬅⬆⬇▶⏸⭕'),
)

ANIM = {"phase": 0.0, "clock_t": 0.0, "level": 0.0}
# Orb pulse period per state, in milliseconds (ready = slow, listening = fast).
PULSE_PERIOD_MS = {"idle": 3000, "thinking": 1500, "speaking": 800, "listening": 300}

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


AURA_LAYER_NAMES = ("aura_body", "aura_ribbon1", "aura_ribbon2", "aura_ribbon3", "aura_glare")
_LAYER_TEXTURES = {}


def aura_layers():
    """Separate Aura layers (body, 3 ribbons, glare) for the multi-layer animation, or None.

    Looks in the same folders as orb_texture(); if any layer is missing the single
    orb_aura.png (or the drawn orb) is used instead, so nothing breaks.
    """
    if "aura" in _LAYER_TEXTURES:
        return _LAYER_TEXTURES["aura"]
    folders = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    try:
        app = App.get_running_app()
        if app is not None and getattr(app, "directory", None):
            folders.append(app.directory)
    except Exception:
        pass
    folders += ["/storage/emulated/0/Download", "/storage/emulated/0/JARVIS", "/sdcard/Download"]
    layers = {}
    for name in AURA_LAYER_NAMES:
        for folder in folders:
            for path in (os.path.join(folder, "assets", name + ".png"), os.path.join(folder, name + ".png")):
                try:
                    if os.path.exists(path):
                        from kivy.core.image import Image as CoreImage
                        tex = CoreImage(path).texture
                        if tex is not None:
                            layers[name] = tex
                            break
                except Exception:
                    pass
            if name in layers:
                break
    result = layers if len(layers) == len(AURA_LAYER_NAMES) else None
    _LAYER_TEXTURES["aura"] = result
    return result


# GPU shader for the Aura orb: flowing energy veins, voice rings and orbiting sparks, all procedural.
# If the shader cannot be compiled on a device, AnimatedCore falls back to the PNG layers / drawn orb.
AURA_FRAG = r"""
$HEADER$
uniform float u_time;
uniform float u_phase;
uniform float u_level;
uniform float u_think;
uniform float u_gather;
uniform float u_ofac;
uniform float u_flash;
uniform float u_listen;
uniform float u_speak;
uniform float u_cycles;
uniform float u_orbit;
uniform float u_age;
uniform vec4 u_clip;

float hash31(vec3 p) {
    p = fract(p * 0.3183099 + vec3(0.1, 0.2, 0.3));
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float hash21(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}
float vnoise(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash31(i);
    float b = hash31(i + vec3(1.0, 0.0, 0.0));
    float c = hash31(i + vec3(0.0, 1.0, 0.0));
    float d = hash31(i + vec3(1.0, 1.0, 0.0));
    float e = hash31(i + vec3(0.0, 0.0, 1.0));
    float g = hash31(i + vec3(1.0, 0.0, 1.0));
    float h = hash31(i + vec3(0.0, 1.0, 1.0));
    float k = hash31(i + vec3(1.0, 1.0, 1.0));
    return mix(mix(mix(a, b, f.x), mix(c, d, f.x), f.y),
               mix(mix(e, g, f.x), mix(h, k, f.x), f.y), f.z);
}
float fbm(vec3 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 3; i++) {
        v += a * vnoise(p);
        p = p * 2.03 + vec3(1.7, 9.2, 3.1);
        a *= 0.5;
    }
    return v;
}
vec3 rotY(vec3 v, float a) {
    float c = cos(a); float s = sin(a);
    return vec3(c * v.x + s * v.z, v.y, -s * v.x + c * v.z);
}
vec3 rotX(vec3 v, float a) {
    float c = cos(a); float s = sin(a);
    return vec3(v.x, c * v.y - s * v.z, s * v.y + c * v.z);
}

void main() {
    vec2 p = (tex_coord0 - 0.5) * 2.0 * 2.32;   // sphere radius 0.58 as before; the quad is 4x larger for the surroundings
    float r = length(p);
    float R = 0.58;
    float lv = clamp(u_level, 0.0, 1.0);
    float ph = u_phase;
    float ofac = max(u_ofac, 0.04);
    vec2 spo = p / R;
    float rso = r / R;
    vec3 cCyan = vec3(0.35, 0.85, 1.0);
    vec3 cBlue = vec3(0.14, 0.38, 1.0);
    vec3 cViol = vec3(0.62, 0.32, 1.0);

    vec3 col = vec3(0.0);
    float alpha = 0.0;

    // ---- outer halo, breathing with the voice
    float hr = max(r - R, 0.0);
    float halo = exp(-hr * (5.2 - 1.6 * lv)) * (0.55 + 0.5 * lv) * smoothstep(0.0, 0.05, r - R * 0.5);
    col += mix(cBlue, cViol, 0.5 + 0.5 * sin(ph * 0.3 + p.x * 2.0)) * halo * 0.55;
    alpha = max(alpha, halo * 0.8);

    // ---- radiating energy rings
    for (int k = 0; k < 3; k++) {
        float u = fract(ph * 0.28 + float(k) / 3.0);
        float rr = R * (1.03 + 0.60 * u);
        float ring = exp(-pow((r - rr) / 0.012, 2.0)) * (1.0 - u) * (0.10 + 0.9 * lv);
        col += cCyan * ring;
        alpha = max(alpha, ring);
    }

    // ---- orbiting particles on a tilted ring (back half is dimmer)
    for (int k = 0; k < 2; k++) {
        float tilt = 0.9 + 0.5 * float(k);
        float speed = (k == 0) ? 0.55 : -0.38;
        float ang = atan(p.y, p.x);
        // inverse of the tilted ellipse: take the point on the ellipse nearest in angle
        float ra = R * (1.35 + 0.18 * float(k)) * ofac;   // the orbiting sparks fly in and burst out with the energy
        float rb = ra * (0.30 + 0.12 * float(k));
        float ca = cos(tilt); float sa = sin(tilt);
        vec2 q = vec2(ca * p.x + sa * p.y, -sa * p.x + ca * p.y);
        float e = length(vec2(q.x / ra, q.y / rb));
        float band = exp(-pow((e - 1.0) / 0.022, 2.0));
        float th = atan(q.y / rb, q.x / ra);
        float cells = 90.0;
        float x = (th + ph * speed) * cells / 6.2831853;
        float id = floor(x);
        float fx = fract(x) - 0.5;
        float rnd = hash21(vec2(id, float(k) * 7.0));
        float dot_ = smoothstep(0.30, 0.0, abs(fx)) * step(0.45, rnd);
        float front = 0.55 + 0.45 * sin(th + ph * speed * 0.0 + 1.5708 * 2.0);
        float tw = 0.5 + 0.5 * sin(u_time * (2.0 + 4.0 * rnd) + rnd * 20.0);
        float behind = (q.y > 0.0 && length(p) < R * 1.0) ? 0.0 : 1.0;
        float pv = band * dot_ * tw * behind * (0.5 + 0.9 * front);
        col += mix(cCyan, vec3(1.0), 0.4) * pv * 1.6;
        alpha = max(alpha, pv);
    }

    // ---- the sphere
    if (r < R) {
        float edge = smoothstep(R, R - 0.008, r);
        vec2 sp = p / R;
        float z = sqrt(max(1.0 - dot(sp, sp), 0.0));
        vec3 n = vec3(sp, z);
        // the energy pattern can shrink to the core and burst outwards ("answer ready")
        vec2 spv = sp / ofac;
        float zv = sqrt(max(1.0 - dot(spv, spv), 0.0));
        vec3 nv = vec3(spv, zv);
        float vm = smoothstep(1.0, 0.90, length(spv));
        vec3 q = rotX(rotY(nv, ph * 0.22 + u_think * 0.9 * sin(ph * 0.9) * nv.y), 0.35);

        // deep glassy body
        float lam = clamp(dot(n, normalize(vec3(-0.4, -0.5, 0.75))), 0.0, 1.0);
        vec3 body = vec3(0.008, 0.02, 0.06) * (0.5 + lam);

        // domain-warped flowing energy
        vec3 w = q * 1.55 + vec3(0.0, ph * 0.10, 0.0);
        float f1 = fbm(w + 1.8 * vec3(fbm(w + ph * 0.11), fbm(w + 3.1 - ph * 0.09), fbm(w + 5.2)));
        // counter-rotating second layer
        vec3 q2 = rotY(rotX(nv, -0.5), -ph * 0.34);
        float f2 = fbm(q2 * 2.1 + 1.3 * vec3(fbm(q2 * 1.2 + ph * 0.15), 0.0, fbm(q2 + 4.0)));

        float wdt = 0.020 + 0.014 * lv;
        float rib1 = exp(-pow((f1 - 0.50) / wdt, 2.0));
        float rib2 = exp(-pow((f1 - 0.64) / (wdt * 1.3), 2.0));
        float rib3 = exp(-pow((f2 - 0.47) / (wdt * 1.1), 2.0));
        float glowF = smoothstep(0.30, 0.75, f1);

        float vis = 0.25 + 1.2 * pow(1.0 - zv, 1.1);
        vec3 e = vec3(0.0);
        e += cCyan * rib1 * 1.1;
        e += cViol * rib2 * 0.95;
        e += cBlue * rib3 * 1.0;
        e += vec3(0.9, 0.95, 1.0) * (rib1 * rib1 * 0.45 + rib3 * rib3 * 0.3);
        e *= vis * (0.58 + 0.55 * lv);
        e += cBlue * glowF * 0.035 * (0.4 + z);   // soft inner plasma

        // sparks inside the sphere
        vec3 sq = q * 16.0;
        float sh = hash31(floor(sq));
        float sparkle = step(0.965, sh) * (0.5 + 0.5 * sin(u_time * 3.0 + sh * 50.0));
        sparkle *= smoothstep(0.5, 0.0, length(fract(sq) - 0.5));
        e += vec3(0.7, 0.9, 1.0) * sparkle * (0.4 + 0.8 * lv);

        e *= vm;
        // bright core while the energy gathers and when it bursts
        e += vec3(0.85, 0.93, 1.0) * exp(-dot(sp, sp) * (12.0 - 8.0 * u_gather)) * (0.9 * u_gather + 0.9 * u_flash + 0.25 * lv * u_speak);
        // Fresnel rim and bright outline
        float fres = pow(1.0 - z, 2.4);
        e += cBlue * fres * 0.75 + cViol * pow(1.0 - z, 5.0) * (0.5 + 0.5 * sin(ph * 0.5 + 3.0 * sp.x)) * 0.8;
        e += vec3(0.55, 0.8, 1.0) * exp(-pow((r / R - 0.985) / 0.012, 2.0)) * 0.9;
        // moving glare
        vec2 gc = vec2(-0.42 + 0.03 * sin(u_time * 0.7), -0.5 + 0.03 * cos(u_time * 0.9));
        e += vec3(0.5, 0.7, 1.0) * exp(-dot(sp - gc, sp - gc) / 0.035) * 0.28;

        vec3 sph = body + e;
        sph = 1.0 - exp(-sph * 1.35);
        col = mix(col, sph, edge);
        alpha = max(alpha, edge);
    }

    // ================= surroundings: soft glowing rings, waves, sparks (all in the shader) =================
    vec3 fx = vec3(0.0);
    float fxa = 0.0;
    // answer burst: a bright shock ring (plane + sphere) and radial sparks
    float fl = u_flash;
    float eq = length(vec2(spo.x, spo.y / 0.22));
    float xs = (eq - (1.1 + 0.9 * (1.0 - fl))) / 0.05;
    float ps = (xs > 0.0) ? exp(-xs * xs * 9.0) : exp(xs * 0.7);
    fx += vec3(1.0, 0.96, 0.9) * ps * fl * 0.9;
    float xr = (rso - (1.1 + 0.9 * (1.0 - fl))) / 0.05;
    float pr = (xr > 0.0) ? exp(-xr * xr * 9.0) : exp(xr * 0.7);
    fx += vec3(0.85, 0.93, 1.0) * pr * fl * 0.7;
    if (u_age >= 0.0) {
        float aang = atan(spo.y, spo.x);
        float cells = 64.0;
        float cf = (aang + 3.14159265) / 6.2831853 * cells;
        float cid = floor(cf);
        float rnd = hash31(vec3(cid, 3.0, 7.0));
        float spd = 0.8 + 0.9 * hash31(vec3(cid, 11.0, 5.0));
        float dcur = 0.2 + spd * 1.4 * (1.0 - exp(-2.5 * u_age));
        float inside = smoothstep(dcur * 0.86, dcur * 0.97, rso) * smoothstep(dcur * 1.03, dcur * 0.97, rso);
        float side = abs(fract(cf) - 0.5) * 6.2831853 / cells * rso;
        float spark = inside * smoothstep(0.008, 0.0, side) * step(0.45, rnd) * max(0.0, 1.0 - u_age / 1.8);
        fx += vec3(0.9, 0.96, 1.0) * spark * 1.6;
    }
    // keep every effect inside the widget so it never paints over neighbouring UI
    float cm = smoothstep(u_clip.x, u_clip.x + 0.15, spo.x) * smoothstep(u_clip.y, u_clip.y - 0.15, spo.x)
             * smoothstep(u_clip.z, u_clip.z + 0.15, spo.y) * smoothstep(u_clip.w, u_clip.w - 0.15, spo.y);
    fx *= cm;
    col += fx;
    alpha = max(alpha, clamp(max(max(fx.r, fx.g), fx.b) * 1.6, 0.0, 1.0));

    col = clamp(col, 0.0, 1.0);
    float cmAll = smoothstep(u_clip.x, u_clip.x + 0.15, spo.x) * smoothstep(u_clip.y, u_clip.y - 0.15, spo.x)
                * smoothstep(u_clip.z, u_clip.z + 0.15, spo.y) * smoothstep(u_clip.w, u_clip.w - 0.15, spo.y);
    gl_FragColor = vec4(col, clamp(alpha, 0.0, 1.0) * cmAll) * frag_color;
}
"""


PARTICLE_FRAG = r"""
$HEADER$
uniform float u_time;
uniform float u_phase;
uniform float u_level;

float hash31(vec3 p) {
    p = fract(p * 0.3183099 + vec3(0.1, 0.2, 0.3));
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float hash21(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
}
float vnoise(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash31(i);
    float b = hash31(i + vec3(1.0, 0.0, 0.0));
    float c = hash31(i + vec3(0.0, 1.0, 0.0));
    float d = hash31(i + vec3(1.0, 1.0, 0.0));
    float e = hash31(i + vec3(0.0, 0.0, 1.0));
    float g = hash31(i + vec3(1.0, 0.0, 1.0));
    float h = hash31(i + vec3(0.0, 1.0, 1.0));
    float k = hash31(i + vec3(1.0, 1.0, 1.0));
    return mix(mix(mix(a, b, f.x), mix(c, d, f.x), f.y),
               mix(mix(e, g, f.x), mix(h, k, f.x), f.y), f.z);
}
float fbm(vec3 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 3; i++) {
        v += a * vnoise(p);
        p = p * 2.03 + vec3(1.7, 9.2, 3.1);
        a *= 0.5;
    }
    return v;
}
vec3 rotY(vec3 v, float a) {
    float c = cos(a); float s = sin(a);
    return vec3(c * v.x + s * v.z, v.y, -s * v.x + c * v.z);
}
vec3 rotX(vec3 v, float a) {
    float c = cos(a); float s = sin(a);
    return vec3(v.x, c * v.y - s * v.z, s * v.y + c * v.z);
}

// one hemisphere of the dotted sphere
vec3 hemi(vec2 sp, float z, float side, float ph, float lv) {
    vec3 n = vec3(sp, side * z);
    vec3 q = rotX(rotY(n, ph * 0.20), 0.45);
    float turb = fbm(q * 1.9 + vec3(0.0, ph * 0.10, 0.0));
    float scale = 24.0;
    vec3 g = q * scale;
    vec3 cell = floor(g);
    vec3 fr = fract(g) - 0.5;
    float rnd = hash31(cell);
    vec3 jit = vec3(hash31(cell + 3.1), hash31(cell + 7.7), hash31(cell + 11.3)) - 0.5;
    float dist = length(fr - jit * 0.35);
    float size = 0.20 + 0.30 * turb + 0.10 * lv;
    float dotv = smoothstep(size, size * 0.25, dist) * step(0.18, rnd);
    float depth = 0.5 + 0.5 * side * z;
    float I = (0.35 + 1.4 * pow(depth, 1.4)) * (0.55 + 1.5 * turb) * (0.9 + 0.5 * lv);
    vec3 teal = vec3(0.38, 0.9, 1.0);
    vec3 deep = vec3(0.08, 0.50, 0.70);
    vec3 c = mix(deep, teal, depth) * dotv * I;
    // soft glow between dots, strongest on dense (turbulent) regions
    c += deep * 0.10 * turb * (0.5 + 0.5 * depth);
    return c;
}

void main() {
    vec2 p = (tex_coord0 - 0.5) * 2.0;
    float r = length(p);
    float R = 0.56;
    float lv = clamp(u_level, 0.0, 1.0);
    float ph = u_phase;
    vec3 col = vec3(0.0);
    float alpha = 0.0;

    // faint teal atmosphere
    float hr = max(r - R, 0.0);
    float halo = exp(-hr * (5.5 - 1.5 * lv)) * (0.30 + 0.40 * lv);
    col += vec3(0.08, 0.55, 0.70) * halo;
    alpha = max(alpha, halo * 0.7);

    // drifting dust that streams slowly outward
    float ang = atan(p.y, p.x);
    float u = fract(r * 5.0 - ph * 0.06);
    float cellA = floor((ang + 3.14159) * 22.0 / 6.2831853 * 3.0);
    float rid = floor(r * 5.0 - ph * 0.06);
    float rnd = hash21(vec2(cellA, rid));
    float fa = fract((ang + 3.14159) * 22.0 / 6.2831853 * 3.0) - 0.5;
    float dd = length(vec2(fa * r * 0.9, u - 0.5) * vec2(1.0, 0.6));
    float dust = smoothstep(0.10, 0.0, dd) * step(0.88, rnd) * smoothstep(R * 1.05, R * 1.4, r) * smoothstep(1.0, 0.7, r);
    dust *= 0.5 + 0.5 * sin(u_time * 2.0 + rnd * 40.0);
    col += vec3(0.4, 0.85, 1.0) * dust * (0.5 + 0.8 * lv);
    alpha = max(alpha, dust);

    if (r < R) {
        vec2 sp = p / R;
        float z = sqrt(max(1.0 - dot(sp, sp), 0.0));
        vec3 c = hemi(sp, z, 1.0, ph, lv) + 0.55 * hemi(sp, z, -1.0, ph, lv);
        c += vec3(0.05, 0.45, 0.60) * pow(1.0 - z, 3.0) * 0.45;
        float edge = smoothstep(R, R - 0.01, r);
        c = 1.0 - exp(-c * 2.3);
        col = mix(col, c, edge);
        alpha = max(alpha, edge * clamp(max(max(c.r, c.g), c.b) * 2.2 + 0.05, 0.0, 1.0));
    }
    gl_FragColor = vec4(clamp(col, 0.0, 1.0), clamp(alpha, 0.0, 1.0)) * frag_color;
}
"""


WATER_FRAG = r"""
$HEADER$
uniform float u_time;
uniform float u_phase;
uniform float u_level;
uniform float u_think;
uniform float u_flash;
uniform float u_speak;
uniform float u_ring;
uniform vec4 u_geo;

float h21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}
float vn(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), f.x),
               mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), f.x), f.y);
}
vec2 wv(vec2 w, vec2 dir, float k, float sp, float amp, float t) {
    return dir * (k * amp * cos(dot(w, dir) * k + t * sp));
}
// drifting luminous motes (one candidate per cell)
float motes(vec2 q, float t) {
    vec2 pq = q * 2.4;
    vec2 id = floor(pq);
    vec2 f = fract(pq);
    float rn = h21(id);
    float y = fract(rn * 3.7 + t * 0.035 * (0.6 + rn));
    vec2 p0 = vec2(0.5 + 0.3 * sin(t * 0.6 + rn * 40.0), y);
    float d = length(f - p0);
    float tw = 0.5 + 0.5 * sin(t * (0.9 + rn * 1.6) + rn * 60.0);
    return smoothstep(0.075, 0.0, d) * tw * step(0.6, rn) * sin(3.14159 * y);
}
vec3 skyc(vec3 r, vec3 L, float orbA, float lv) {
    float elev = max(r.y, 0.0);
    float ang = acos(clamp(dot(r, L), -1.0, 1.0));
    vec3 c = mix(vec3(0.05, 0.20, 0.36), vec3(0.0, 0.02, 0.06), smoothstep(0.0, 0.45, elev)) * 0.55;
    c += vec3(0.10, 0.45, 0.75) * exp(-elev * 14.0) * 0.55;
    float s2 = orbA * 2.6;
    c += vec3(0.20, 0.70, 0.95) * exp(-ang * ang / (2.0 * s2 * s2)) * (0.20 + 0.3 * lv);
    return c;
}
void main(void) {
    vec2 uv = tex_coord0;
    vec2 q = (uv - u_geo.xy) * u_geo.zw;          // units of sphere radius, origin = ring centre on the water
    float t = u_phase;
    float lv = u_level;
    const float S = 0.35;
    const float H0 = 0.80;                         // horizon behind the sphere: we look at the water from the front, with distance
    const float hc = 0.60;                         // camera height above the water
    float zc = hc / (H0 * S);                      // distance to the contact point (screen origin)
    float Rw = S * zc;                             // world radius of the sphere
    vec3 C = vec3(0.0, -hc + Rw, zc);              // centre of the sphere
    vec3 L = normalize(C);
    float orbA = atan(Rw / zc);
    vec3 d = normalize(vec3(q.x * S, (q.y - H0) * S, 1.0));
    vec3 col = vec3(0.0);
    float alpha = 0.0;
    if (q.y > H0) {
        // ---- sky: haze at the horizon, soft light of the sphere
        vec3 c = skyc(d, L, orbA, lv);
        float ha = exp(-(q.y - H0) * 3.2) * 0.45 + exp(-pow((q.y - H0) / 0.02, 2.0)) * 0.5;
        col = c;
        alpha = clamp(ha * (1.0 + 0.6 * lv + u_flash) + length(c) * 0.25, 0.0, 0.9);
    } else {
        // ---- water in perspective
        float tt = hc / max(-d.y, 0.002);
        vec3 w3 = d * tt;
        vec2 w = vec2(w3.x, w3.z);
        float att = 1.0 / (1.0 + tt * 0.07);
        float boost = 1.0 + 1.4 * lv + 0.8 * u_speak;
        vec2 g = vec2(0.0);
        g += wv(w, vec2(0.30, 1.00), 1.7, 0.8, 0.018, t);
        g += wv(w, vec2(-0.50, 1.00), 2.9, -1.1, 0.014, t);
        g += wv(w, vec2(0.90, 0.40), 4.1, 0.9, 0.012, t);
        g += wv(w, vec2(-0.20, 1.00), 7.0, 1.6, 0.010, t);
        g += wv(w, vec2(0.77, -0.40), 11.3, -2.3, 0.007, t);
        g *= att * boost * 0.8;
        // rings spreading from the point under the sphere (spiral while thinking, inward while listening)
        vec2 rc = vec2(0.0, zc);
        vec2 rv = w - rc;
        float r = length(rv) + 0.001;
        float th = atan(rv.x, rv.y);
        float ph = r * 7.0 - u_ring * 6.28318 + u_think * 2.0 * th;
        float env = exp(-r * 0.6) * (0.45 + 0.9 * lv + 0.4 * u_speak) * smoothstep(Rw * 0.8, Rw * 1.25, r);
        g += rv / r * (7.0 * cos(ph) * 0.022 * env);
        float crest = pow(max(sin(ph), 0.0), 5.0) * env;
        float fr = Rw + 8.0 * (1.0 - u_flash);
        float fk = exp(-pow((r - fr) / 0.30, 2.0)) * u_flash;
        g += rv / r * (fk * 0.6);
        vec3 n = normalize(vec3(-g.x, 1.0, -g.y));
        float cosv = clamp(dot(n, -d), 0.0, 1.0);
        float F = 0.22 + 0.78 * pow(1.0 - cosv, 3.0);
        vec3 rf = reflect(d, n);
        vec3 refl = skyc(rf, L, orbA, lv);
        // the sphere itself, mirrored and shattered by every wave facet
        vec3 oc = C - w3;
        float tca = dot(oc, rf);
        float d2 = dot(oc, oc) - tca * tca;
        float fw = step(0.0, tca);
        float hitm = smoothstep(Rw * Rw, Rw * Rw * 0.6, d2) * fw;
        float kk = sqrt(max(1.0 - d2 / (Rw * Rw), 0.0));
        vec3 orbc = mix(vec3(0.80, 0.97, 1.0), vec3(0.25, 0.75, 1.0), kk);
        refl += orbc * hitm * 2.2 * (0.8 + 0.6 * lv);
        refl += vec3(0.20, 0.70, 0.95) * exp(-d2 / (Rw * Rw * 3.0)) * 0.30 * fw;
        // body of the water, lit from the sphere with a drifting caustic net
        float gl = exp(-r * r / (4.5 * Rw * Rw));
        float cau = pow(1.0 - abs(vn(w * 3.0 + vec2(t * 0.35, -t * 0.25)) * 2.0 - 1.0), 7.0)
                  + 0.7 * pow(1.0 - abs(vn(w * 5.3 - vec2(t * 0.3, t * 0.2)) * 2.0 - 1.0), 8.0);
        vec3 body = vec3(0.0, 0.035, 0.075) + vec3(0.02, 0.16, 0.24) * exp(-tt * 0.06);
        body += vec3(0.08, 0.50, 0.75) * gl * (0.08 + 0.55 * cau * cau) * (0.55 + 0.5 * lv);
        col = mix(body, refl, F);
        col += vec3(0.22, 0.88, 1.0) * crest * (0.40 + 0.9 * lv) * (0.5 + 0.5 * att);
        col += vec3(0.8, 0.97, 1.0) * fk * 0.9;
        // reflected motes
        col += vec3(0.3, 0.9, 1.0) * motes(vec2(q.x + g.x * 2.0, -q.y), t) * 0.35 * F * 1.5;
        // sheen along the horizon line
        col += vec3(0.5, 0.9, 1.0) * exp(-pow((q.y - H0) / 0.012, 2.0)) * 0.45 * (0.85 + 0.15 * sin(u_time * 0.9 + q.x * 3.0));
        alpha = 0.95;
    }
    // motes in the air around the sphere
    float orbd = length(q - vec2(0.0, 1.0));
    col += vec3(0.4, 0.95, 1.0) * motes(q, t) * exp(-orbd * 0.30) * 1.3;
    alpha = max(alpha, motes(q, t) * exp(-orbd * 0.30));
    col = 1.0 - exp(-col * 1.35);
    // soft edges: the water fades out before the borders of the widget, so no box is visible
    alpha *= smoothstep(0.0, 0.16, uv.x) * smoothstep(1.0, 0.84, uv.x) * smoothstep(0.0, 0.22, uv.y) * smoothstep(1.0, 0.70, uv.y);
    gl_FragColor = vec4(clamp(col, 0.0, 1.0), clamp(alpha, 0.0, 1.0)) * frag_color;
}
"""

GLASS_FRAG = r"""
$HEADER$
uniform float u_time;
uniform float u_phase;
uniform float u_level;
uniform float u_think;
uniform float u_gather;
uniform float u_ofac;
uniform float u_flash;
uniform float u_listen;
uniform float u_speak;
uniform float u_cycles;
uniform float u_orbit;
uniform float u_age;
uniform vec4 u_clip;


float hash31(vec3 p) {
    p = fract(p * 0.3183099 + vec3(0.1, 0.2, 0.3));
    p *= 17.0;
    return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float vnoise(vec3 x) {
    vec3 i = floor(x);
    vec3 f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash31(i);
    float b = hash31(i + vec3(1.0, 0.0, 0.0));
    float c = hash31(i + vec3(0.0, 1.0, 0.0));
    float d = hash31(i + vec3(1.0, 1.0, 0.0));
    float e = hash31(i + vec3(0.0, 0.0, 1.0));
    float g = hash31(i + vec3(1.0, 0.0, 1.0));
    float h = hash31(i + vec3(0.0, 1.0, 1.0));
    float k = hash31(i + vec3(1.0, 1.0, 1.0));
    return mix(mix(mix(a, b, f.x), mix(c, d, f.x), f.y),
               mix(mix(e, g, f.x), mix(h, k, f.x), f.y), f.z);
}
float fbm2(vec3 p) {
    return 0.62 * vnoise(p) + 0.38 * vnoise(p * 2.07 + vec3(1.7, 9.2, 3.1));
}
vec3 rotY(vec3 v, float a) {
    float c = cos(a); float s = sin(a);
    return vec3(c * v.x + s * v.z, v.y, -s * v.x + c * v.z);
}
vec3 rotX(vec3 v, float a) {
    float c = cos(a); float s = sin(a);
    return vec3(v.x, c * v.y - s * v.z, s * v.y + c * v.z);
}

// studio environment seen in the glass: soft light pillars and a horizon glow
vec3 env(vec3 d, float ph) {
    float az = atan(d.x, d.z) + ph * 0.05;
    float st = pow(max(0.0, cos(az * 3.0 + 1.0)), 24.0) * 0.9 + pow(max(0.0, cos(az * 5.0 - 0.6)), 50.0) * 0.6;
    st *= 0.4 + 0.6 * smoothstep(-0.3, 0.7, d.y);
    float hor = exp(-pow(d.y / 0.22, 2.0)) * 0.16;
    return vec3(0.25, 0.75, 1.0) * st + vec3(0.08, 0.30, 0.50) * hor + vec3(0.01, 0.03, 0.06);
}

void main() {
    vec2 uv = (tex_coord0 - 0.5) * 2.0;
    float Rs = 0.25;
    vec2 sp = uv / Rs;
    float r = length(sp);
    float lv = clamp(u_level, 0.0, 1.0);
    float ph = u_phase;
    float ofac = max(u_ofac, 0.04);
    vec3 col = vec3(0.0);
    float alpha = 0.0;

    float streak = exp(-pow(sp.y / 0.020, 2.0)) * exp(-abs(sp.x) * 1.2) * (0.08 + 0.35 * lv + 0.9 * u_flash);
    col += vec3(0.75, 0.88, 1.0) * streak;
    alpha = max(alpha, streak);
    float hr = max(r - 1.0, 0.0);
    float halo = exp(-hr * 3.4) * (0.18 + 0.22 * lv + 0.35 * u_flash) * smoothstep(0.0, 0.3, r);
    col += vec3(0.20, 0.38, 0.60) * halo;
    alpha = max(alpha, halo * 0.9);

    if (r < 1.0) {
        float z0 = sqrt(max(1.0 - r * r, 0.0));
        vec3 n = vec3(sp, z0);
        vec3 I = vec3(0.0, 0.0, -1.0);
        vec3 rf = refract(I, n, 0.72 + 0.02 * sin(ph * 0.5));
        float tmax = max(-2.0 * dot(n, rf), 0.0);

        // ---- volumetric energy inside the glass (6 samples along the refracted ray)
        vec3 acc = vec3(0.0);
        float flow = ph * 0.10;
        for (int i = 0; i < 6; i++) {
            float t = (float(i) + 0.5) / 6.0 * tmax;
            vec3 pos = (n + rf * t) / ofac;
            float rr = length(pos);
            if (rr < 1.0) {
                float tw = u_think * 1.4 * sin(ph * 0.8) + 0.5 * sin(ph * 0.2);
                vec3 q = rotY(pos, tw * pos.y + ph * 0.12);
                q = rotX(q, 0.35);
                // listening pulls the flow inward, otherwise it rises slowly
                vec3 fl = vec3(0.0, flow, 0.0) - u_listen * q * ph * 0.05;
                float d1 = fbm2(q * 2.3 + fl);
                float d2 = fbm2(q * 4.0 - fl * 1.6 + 5.0);
                float neb = smoothstep(0.38, 0.85, d1);
                float fil = exp(-pow((d1 - 0.52) / (0.035 + 0.02 * lv), 2.0));
                float fil2 = exp(-pow((d2 - 0.50) / 0.03, 2.0));
                float core = exp(-rr * rr * (14.0 - 9.0 * u_gather));
                // spiral arms turning around the core (faster while thinking)
                float sang = atan(q.z, q.x);
                float spiral = pow(0.5 + 0.5 * sin(3.0 * sang + 7.0 * log(rr + 0.05) - ph * (0.8 + u_think * 2.4)), 6.0)
                               * smoothstep(0.9, 0.12, rr) * smoothstep(0.03, 0.18, rr);
                // radial energy bolts from the core to the glass
                vec3 bdir = pos / max(rr, 0.001);
                float bn = vnoise(bdir * 4.5 + vec3(0.0, 0.0, u_time * 1.6));
                float bolt = pow(smoothstep(0.66, 0.92, bn), 2.0) * smoothstep(0.95, 0.25, rr) * smoothstep(0.05, 0.25, rr);
                float fade = smoothstep(1.0, 0.75, rr);
                vec3 c = vec3(0.02, 0.10, 0.20) * neb * neb * 0.8;
                c += mix(vec3(0.80, 0.93, 1.0), vec3(0.40, 0.60, 1.0), smoothstep(0.2, 0.9, rr)) * fil * (0.9 + 1.1 * lv + 1.2 * u_think);
                c += vec3(0.85, 0.92, 1.0) * fil2 * 0.30 * (0.4 + lv + u_think);
                c += mix(vec3(0.88, 0.94, 1.0), vec3(0.45, 0.65, 1.0), rr) * spiral * (0.20 + 0.65 * u_think + 0.35 * lv);
                c += vec3(0.85, 0.92, 1.0) * bolt * (0.15 + 1.2 * lv + 0.9 * u_think + 0.7 * u_flash);
                c *= fade;
                c += vec3(1.0, 0.92, 0.80) * core * (0.55 + 1.1 * lv + 1.6 * u_gather + 1.4 * u_flash);
                // floating motes
                vec3 mq = pos * 9.0 + vec3(0.0, ph * 0.3, 0.0);
                float mh = hash31(floor(mq));
                float mote = step(0.982, mh) * smoothstep(0.42, 0.0, length(fract(mq) - 0.5)) * fade;
                c += vec3(0.7, 0.95, 1.0) * mote * (0.6 + 0.5 * sin(u_time * 2.5 + mh * 60.0));
                acc += c;
            }
        }
        acc *= (tmax * 0.55 + 0.05) * 0.26;

        // ---- glass surface: reflections, rim, dispersion, highlights
        vec3 Rv = reflect(I, n);
        float F = 0.06 + 0.94 * pow(1.0 - z0, 4.0);
        vec3 refl = env(Rv, ph) * vec3(0.8, 0.9, 1.0) * (0.6 + 1.3 * F);
        float rim = pow(1.0 - z0, 2.6);
        vec3 disp = mix(vec3(0.25, 0.55, 0.9), vec3(1.0, 0.72, 0.45), smoothstep(0.82, 1.0, r));
        vec3 c = acc + refl * (0.35 + F * 1.2) + disp * rim * 0.55;
        c += vec3(0.75, 0.88, 1.0) * exp(-pow((r - 0.985) / 0.008, 2.0)) * 0.9;
        // softbox highlights
        vec2 h1 = (sp - vec2(-0.42, 0.50)) * vec2(1.0, 2.2);
        c += vec3(0.85, 0.95, 1.0) * exp(-dot(h1, h1) / 0.045) * 0.55;
        vec2 h2 = (sp - vec2(0.50, -0.45)) * vec2(2.4, 1.0);
        c += vec3(0.5, 0.8, 1.0) * exp(-dot(h2, h2) / 0.03) * 0.25;
        float ang = atan(sp.y, sp.x);
        float arcW = exp(-pow((r - 0.90) / 0.014, 2.0));
        float a1 = smoothstep(0.0, 0.08, ang - 2.05) * smoothstep(0.0, 0.08, 2.55 - ang);
        float a2 = smoothstep(0.0, 0.08, ang + 1.05) * smoothstep(0.0, 0.08, -0.55 - ang);
        c += vec3(1.0) * arcW * (a1 + a2) * 0.7;
        // shock-wave when the answer bursts out
        float sw = exp(-pow((r - (0.25 + 0.95 * (1.0 - u_flash))) / 0.05, 2.0)) * u_flash;
        c += vec3(0.8, 0.95, 1.0) * sw * 1.3;
        c = 1.0 - exp(-c * 1.35);
        // tiny colour grade: deeper teal shadows
        c = pow(c, vec3(0.95, 0.92, 0.88));
        float edge = smoothstep(1.0, 0.99, r);
        col = mix(col, c, edge);
        alpha = max(alpha, edge);
    }

    // ================= surroundings: soft glowing rings, waves, sparks (all in the shader) =================
    vec3 fx = vec3(0.0);
    float fxa = 0.0;
    // gyroscope rings (3D rings seen in perspective; the far half is hidden behind the glass)
    for (int i = 0; i < 3; i++) {
        float fi = float(i);
        float ga = u_orbit * 0.3 * (fi + 1.0) * (mod(fi, 2.0) < 0.5 ? 1.0 : -1.0) + fi * 1.1;
        float gt = 1.0 + 0.5 * fi + 0.4 * sin(ph * 0.5 + fi * 1.7);
        float A = (1.42 + 0.12 * fi) * ofac;
        float B = A * abs(cos(gt)) + 0.015;
        float cg = cos(ga); float sg = sin(ga);
        vec2 p2 = vec2(cg * sp.x + sg * sp.y, -sg * sp.x + cg * sp.y);
        float e = length(vec2(p2.x / A, p2.y / B));
        float dd = (e - 1.0) * min(A, B);
        float line = exp(-pow(dd / 0.012, 2.0)) + 0.22 * exp(-pow(dd / 0.05, 2.0));
        float tl = atan(p2.y / B, p2.x / A);
        float front = step(0.0, -sin(tl));            // near half passes in front of the glass
        float vis = (r > 1.0) ? (0.45 + 0.55 * front) : front;
        float tn = u_orbit * (1.3 + 0.5 * fi) + fi * 2.0;
        float da = abs(mod(tl - tn + 3.14159265, 6.2831853) - 3.14159265);
        float tail = exp(-pow(da / (0.25 + 0.6 * u_think), 2.0));
        float node = exp(-pow(length(p2 - vec2(cos(tn) * A, sin(tn) * B)) / 0.035, 2.0));
        float amp = 0.10 + 0.16 * lv + 0.20 * u_think;
        fx += vec3(0.75, 0.88, 1.0) * (line * (amp + 0.35 * tail * (0.3 + u_think)) + node * 0.9) * vis * smoothstep(0.0, 0.3, ofac);
    }
    // outgoing waves in the equator plane while reading aloud (sharp front, soft trailing glow)
    float eq = length(vec2(sp.x, sp.y / 0.22));
    for (int k = 0; k < 3; k++) {
        float u = fract(u_cycles * 0.5 + float(k) / 3.0);
        float x = (eq - (1.1 + 0.8 * u)) / 0.06;
        float prof = (x > 0.0) ? exp(-x * x * 9.0) : exp(x * 0.9) * (0.75 + 0.25 * sin(x * 7.0));
        fx += vec3(0.6, 0.82, 1.0) * prof * (1.0 - u) * (0.10 + 0.55 * lv) * u_speak;
    }
    // listening: soft rings drawn in towards the sphere
    float rc = r;
    for (int k = 0; k < 3; k++) {
        float u = fract(u_cycles * 0.25 + float(k) / 3.0);
        float x = (rc - (2.0 - 0.7 * u)) / 0.05;
        float prof = (x < 0.0) ? exp(-x * x * 9.0) : exp(-x * 0.9) * (0.75 + 0.25 * sin(x * 7.0));
        fx += vec3(0.8, 0.95, 0.9) * prof * u * (0.10 + 0.45 * lv) * u_listen;
    }
    // answer burst: a bright shock ring (plane + sphere) and radial sparks
    float fl = u_flash;
    float xs = (eq - (1.1 + 0.9 * (1.0 - fl))) / 0.05;
    float ps = (xs > 0.0) ? exp(-xs * xs * 9.0) : exp(xs * 0.7);
    fx += vec3(1.0, 0.96, 0.9) * ps * fl * 0.9;
    float xr = (r - (1.1 + 0.9 * (1.0 - fl))) / 0.05;
    float pr = (xr > 0.0) ? exp(-xr * xr * 9.0) : exp(xr * 0.7);
    fx += vec3(0.85, 0.93, 1.0) * pr * fl * 0.7;
    if (u_age >= 0.0) {
        float aang = atan(sp.y, sp.x);
        float cells = 64.0;
        float cf = (aang + 3.14159265) / 6.2831853 * cells;
        float cid = floor(cf);
        float rnd = hash31(vec3(cid, 3.0, 7.0));
        float spd = 0.8 + 0.9 * hash31(vec3(cid, 11.0, 5.0));
        float dcur = 0.2 + spd * 1.4 * (1.0 - exp(-2.5 * u_age));
        float inside = smoothstep(dcur * 0.86, dcur * 0.97, r) * smoothstep(dcur * 1.03, dcur * 0.97, r);
        float side = abs(fract(cf) - 0.5) * 6.2831853 / cells * r;
        float spark = inside * smoothstep(0.008, 0.0, side) * step(0.45, rnd) * max(0.0, 1.0 - u_age / 1.8);
        fx += vec3(0.9, 0.96, 1.0) * spark * 1.6;
    }
    // ripples on the floor under the sphere
    float fe = length(vec2(sp.x, (sp.y + 1.02) / 0.16));
    float energy = max(fl, lv * max(u_speak, u_listen));
    for (int k = 0; k < 3; k++) {
        float u = fract(u_cycles * 0.4 + float(k) / 3.0);
        float x = (fe - (0.7 + 2.4 * u)) / 0.10;
        float prof = (x > 0.0) ? exp(-x * x * 6.0) : exp(x * 0.7);
        fx += vec3(0.55, 0.8, 1.0) * prof * (1.0 - u) * 0.22 * min(1.0, energy * 1.6) * smoothstep(-1.02, -1.4, -abs(sp.y + 0.2));
    }
    // keep every effect inside the widget so it never paints over neighbouring UI
    float cm = smoothstep(u_clip.x, u_clip.x + 0.15, sp.x) * smoothstep(u_clip.y, u_clip.y - 0.15, sp.x)
             * smoothstep(u_clip.z, u_clip.z + 0.15, sp.y) * smoothstep(u_clip.w, u_clip.w - 0.15, sp.y);
    fx *= cm;
    col += fx;
    alpha = max(alpha, clamp(max(max(fx.r, fx.g), fx.b) * 1.6, 0.0, 1.0));
    gl_FragColor = vec4(clamp(col, 0.0, 1.0), clamp(alpha, 0.0, 1.0)) * frag_color;
}
"""


class AuraShaderView(Widget):
    """A quad drawn with AURA_FRAG; `ok` is False when the shader failed to compile."""

    def __init__(self, frag=None, **kwargs):
        self.canvas = RenderContext(use_parent_projection=True, use_parent_modelview=True,
                                    use_parent_frag_modelview=True)
        super().__init__(**kwargs)
        self.ok = False
        try:
            self.src = frag or AURA_FRAG
            self.canvas.shader.fs = self.src
            self.ok = bool(getattr(self.canvas.shader, "success", True))
        except Exception:
            self.ok = False
        if self.ok:
            with self.canvas:
                Color(1, 1, 1, 1)
                self.quad = Rectangle(pos=self.pos, size=self.size)

    def place(self, cx, cy, side):
        self.quad.pos = (cx - side / 2, cy - side / 2)
        self.quad.size = (side, side)

    def use_mesh(self):
        """Replace the Rectangle by a Mesh with explicit texture coordinates: v = 0 at the bottom edge and
        1 at the top edge on every device (a Rectangle's default coordinates can come out upside down)."""
        self.canvas.remove(self.quad)
        with self.canvas:
            self.mesh = Mesh(vertices=[0, 0, 0, 0, 1, 0, 1, 0, 1, 1, 1, 1, 0, 1, 0, 1],
                             indices=[0, 1, 2, 0, 2, 3], mode="triangles")

    def place_rect(self, x, y, w, h):
        self.mesh.vertices = [x, y, 0.0, 0.0, x + w, y, 1.0, 0.0, x + w, y + h, 1.0, 1.0, x, y + h, 0.0, 1.0]

    def update(self, t, phase, level, **extra):
        self.canvas["u_time"] = float(t % 1000.0)
        self.canvas["u_phase"] = float(phase)
        self.canvas["u_level"] = float(level)
        for key, value in extra.items():
            # only set uniforms the shader really declares (the compiler drops unused ones)
            if ("uniform float " + key + ";") in getattr(self, "src", ""):
                self.canvas[key] = float(value)


class AnimatedCore(Widget):
    def __init__(self, theme_index=0, **kwargs):
        super().__init__(**kwargs)
        self.theme_index = theme_index
        self.phase = ANIM["phase"]
        self.level = ANIM["level"]
        self.clock_t = ANIM["clock_t"]
        self.fib, self.dashes, self.reds = [], [], []
        if theme_index == 1:
            n = 1300
            for k in range(n):
                y = 1 - 2 * (k + 0.5) / n
                rr = math.sqrt(1 - y * y)
                t = k * 2.39996
                self.fib.append((math.cos(t) * rr, y, math.sin(t) * rr))
            # per-particle constants for the "answer ready" sequence: stagger, burst speed, swirl side, angle
            seq_rnd = random.Random(5)
            self.pd = [seq_rnd.random() for _ in range(n)]
            self.pk = [seq_rnd.uniform(1.3, 2.7) for _ in range(n)]
            self.psgn = [1.0 if seq_rnd.random() < 0.5 else -1.0 for _ in range(n)]
            self.pa = [seq_rnd.uniform(0.0, math.pi * 2) for _ in range(n)]
            # orbiting dust on three tilted rings: (ring, start angle, speed, size)
            orb_rnd = random.Random(21)
            self.orbit = []
            for ring in range(3):
                for _ in range(150):
                    # belt-like cloud: angle, speed, size, plus radial and vertical scatter (thickness of the belt)
                    self.orbit.append((ring, orb_rnd.uniform(0, math.pi * 2),
                                       orb_rnd.uniform(0.85, 1.15), orb_rnd.uniform(0.3, 1.0),
                                       orb_rnd.gauss(0, 0.07), orb_rnd.gauss(0, 0.06)))
        if theme_index == 2:
            rnd = random.Random(7)
            self.dashes = [(rnd.uniform(0, math.pi * 2), rnd.uniform(-1.5, 1.5)) for _ in range(300)]
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
        # Pulse period by state (ms): ready 3000, answering 800, listening 300.
        period = PULSE_PERIOD_MS.get(state, 1500) / 1000.0
        self.pulse_cycles = getattr(self, "pulse_cycles", 0.0) + dt / period
        self.pulse = 0.5 + 0.5 * math.sin(self.pulse_cycles * 2 * math.pi)
        # Particle orb: smooth rotation speed per state and a one-shot "answer ready" flash
        rk_target = {"idle": 0.5, "thinking": 2.4, "speaking": 1.3, "listening": 1.0}.get(state, 0.8)
        self.rotk = getattr(self, "rotk", 0.5)
        self.rotk += (rk_target - self.rotk) * min(1.0, dt * 3.0)
        self.rot = getattr(self, "rot", 0.0) + dt * 0.4 * self.rotk
        osp = {"idle": 0.5, "thinking": 2.2, "speaking": 1.2, "listening": -1.4}.get(state, 0.8)
        self.orbit_ang = getattr(self, "orbit_ang", 0.0) + dt * 0.5 * osp
        # "Answer ready" sequence (thinking -> speaking): the orbiting dust runs to the centre for 2 s,
        # bursts outward fast, then flows back to its working orbit over ~1.6 s.
        if getattr(self, "state_now", "idle") == "thinking" and state == "speaking":
            self.ready_t = 0.0
            self.ready_burst = False
        rt = getattr(self, "ready_t", None)
        if rt is not None:
            rt += dt
            if rt >= 2.0 and not getattr(self, "ready_burst", False):
                self.ready_burst = True
                self.flash = 1.0
            self.ready_t = rt if rt < 4.0 else None
        self.flash = max(0.0, getattr(self, "flash", 0.0) - dt / 0.9)
        tw_target = 1.0 if state == "thinking" else 0.0
        self.think_w = getattr(self, "think_w", 0.0)
        self.think_w += (tw_target - self.think_w) * min(1.0, dt * 4.0)
        self.state_now = state
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
        """Dotted particle sphere: 1300 fine points, bright wave crests over a cyan glow, faint links, surface waves, a scan band and
        orbiting dust. Calm when ready, twisting while thinking, a flash when the answer is ready,
        outward waves while reading aloud, inward ripples while the microphone listens."""
        lv = self.level
        state = getattr(self, "state_now", "idle")
        pulse = getattr(self, "pulse", 0.0)
        flash = getattr(self, "flash", 0.0)
        cycles = getattr(self, "pulse_cycles", 0.0)
        rot = getattr(self, "rot", 0.0)
        R = R * (1 + 0.10 * lv + 0.02 * pulse + 0.04 * flash)
        # no backing disc / outline ring: the particles float free
        # cyan light filling the whole orb, brighter towards a soft rim, then fading into the dark
        fill = radial_texture("p_fill", ((0, (30, 150, 220, 70)), (0.55, (40, 185, 245, 95)),
                                         (0.82, (70, 215, 255, 140)), (0.93, (110, 232, 255, 110)),
                                         (1, (40, 200, 255, 0))))
        fs = R * 2.5 * (1 + 0.05 * lv + 0.04 * flash)
        Color(1, 1, 1, min(1.0, 0.85 + 0.3 * lv + 0.4 * flash))
        Rectangle(texture=fill, pos=(cx - fs / 2, cy - fs / 2), size=(fs, fs))
        glow = radial_texture("p_glow", ((0, (40, 200, 255, 0)), (0.55, (40, 200, 255, 0)),
                                         (0.8, (40, 200, 255, 40)), (1, (40, 200, 255, 0))))
        gs = min(R * 3 * (1 + 0.3 * lv + 0.25 * flash), min(self.width, self.height))
        Color(1, 1, 1, min(1.0, 0.8 + 0.5 * flash))
        Rectangle(texture=glow, pos=(cx - gs / 2, cy - gs / 2), size=(gs, gs))
        p = self.phase
        cs, sn = math.cos(rot), math.sin(rot)
        tc, ts = 0.9004, 0.4350
        base = {"idle": 0.085, "thinking": 0.13, "speaking": 0.15, "listening": 0.12}.get(state, 0.09)
        amp = base * (1 + (0.6 * pulse if state == "thinking" else 2.4 * lv)) * (1 + 0.8 * flash)
        squeeze = (1.0 - 0.05 * pulse * (0.4 + lv)) if state == "listening" else 1.0
        # twist (spiral) of the whole shell and a travelling surface wave
        twist = {"idle": 0.15 * math.sin(p * 0.3), "thinking": 1.1 * math.sin(p * 0.9),
                 "speaking": 0.35 * lv * math.sin(p * 1.3),
                 "listening": -0.25 * math.sin(p * 1.1)}.get(state, 0.0)
        wave_k = {"idle": 0.12, "thinking": 0.30, "speaking": 0.25 + 0.6 * lv,
                  "listening": 0.22 + 0.4 * lv}.get(state, 0.12)
        wave_dir = 3.0 if state != "listening" else -4.0
        tw_c, tw_s = {}, {}

        def shell(x, y, z):
            ta = twist * y
            tcx, tsx = math.cos(ta), math.sin(ta)
            x, z = x * tcx + z * tsx, -x * tsx + z * tcx
            n = (math.sin(x * 2.3 + p * 1.2) * math.sin(y * 2.9 - p * 0.9)
                 + 0.6 * math.sin(z * 3.3 + p * 1.5) + 0.4 * math.sin((x + y + z) * 5 + p * 2)
                 + wave_k * math.sin(7.0 * y - p * wave_dir)
                 + 0.7 * math.sin(x * 6.0 + z * 4.0 + p * 1.1) * math.sin(y * 5.0 - p * 0.7))
            rr = (1 + amp * n) * squeeze
            X, Y, Z = x * rr, y * rr, z * rr
            x1 = X * cs + Z * sn
            z1 = -X * sn + Z * cs
            y1 = Y * tc - z1 * ts
            z2 = Y * ts + z1 * tc
            return cx + x1 * R, cy + y1 * R, z2, n

        rt = getattr(self, "ready_t", None)
        seq = rt is not None
        line_k = 1.0
        if seq:                              # the wire grid dissolves while the particles gather, then returns
            line_k = (1.0 - rt / 2.0) if rt < 2.0 else (0.0 if rt < 3.0 else min(1.0, (rt - 3.0) / 0.95))
        if line_k > 0.02:
            Color(*accent, (0.06 + 0.08 * lv + 0.25 * flash) * line_k)
            for k in range(1, 13):
                y = -1 + 2 * k / 13.0
                r0 = math.sqrt(1 - y * y)
                pts = []
                for n in range(37):
                    t = n / 36.0 * math.pi * 2
                    px, py, _z, _n = shell(math.cos(t) * r0, y, math.sin(t) * r0)
                    pts.extend((px, py))
                Line(points=pts, width=dp(0.5))
        proj = []
        near, mid, far, hot, crest = [], [], [], [], []
        scan = math.sin(p * (0.6 if state == "idle" else 1.6))
        for idx, (x, y, z) in enumerate(self.fib):
            px, py, z2, wn = shell(x, y, z)
            if seq:
                d, kk, sg = self.pd[idx], self.pk[idx], self.psgn[idx]
                vx, vy = px - cx, py - cy
                if rt < 2.0:
                    # 1) every particle spirals into the centre, each with its own small delay
                    u = min(1.0, max(0.0, (rt - 0.5 * d) / 1.5))
                    f = 1.0 - 0.97 * u * u
                    ang = u * u * 2.4 * sg
                    ca, sa = math.cos(ang), math.sin(ang)
                    px, py = cx + (vx * ca - vy * sa) * f, cy + (vx * sa + vy * ca) * f
                else:
                    vlen = math.hypot(vx, vy)
                    if vlen < 0.08 * R:
                        dx, dy = math.cos(self.pa[idx]), math.sin(self.pa[idx])
                    else:
                        dx, dy = vx / vlen, vy / vlen
                    if rt < 2.35:
                        # 2) burst: each particle flies out along its own direction
                        ub = (rt - 2.0) / 0.35
                        dist = R * (0.03 + kk * (1.0 - (1.0 - ub) ** 3))
                        px, py = cx + dx * dist, cy + dy * dist
                    else:
                        # 3) each particle curves back to its own place; the last one lands exactly at 4.0 s
                        ur = min(1.0, max(0.0, (rt - 2.35 - 0.4 * d) / 1.2))
                        sm = ur * ur * (3 - 2 * ur)
                        ex, ey = cx + dx * R * (0.03 + kk), cy + dy * R * (0.03 + kk)
                        bend = R * 0.30 * sg * math.sin(math.pi * sm)
                        px = ex + (px - ex) * sm - dy * bend
                        py = ey + (py - ey) * sm + dx * bend
            proj.append((px, py, z2))
            if not seq and abs(y - scan) < 0.045:
                if z2 > -0.2:
                    hot.extend((px, py))
            if z2 > -0.1 and wn > 0.9:
                crest.extend((px, py))      # wave crests catch the light
            elif z2 > 0.35:
                near.extend((px, py))
            elif z2 > -0.3:
                mid.extend((px, py))
            else:
                far.extend((px, py))
        # web of links between neighbouring points on the near side
        Color(*accent, (0.05 + 0.08 * lv + 0.22 * flash) * line_k)
        n_pts = len(proj)
        for i in range(0, n_pts, 5):
            if line_k <= 0.02:
                break
            a = proj[i]
            b = proj[(i + 34) % n_pts]
            if a[2] > 0.25 and b[2] > 0.25:
                Line(points=[a[0], a[1], b[0], b[1]], width=dp(0.4))
        mid_col = (*accent2, 0.85) if state == "thinking" else (0.38, 0.90, 1, 0.8)
        if far:
            Color(0.10, 0.56, 0.69, 0.50)
            Point(points=far, pointsize=dp(0.42))
        if mid:
            Color(*mid_col)
            Point(points=mid, pointsize=dp(0.55))
        if near:
            Color(0.85, 0.98, 1, 1)
            Point(points=near, pointsize=dp(0.70 + 0.3 * flash))
        if crest:
            Color(1, 1, 1, 0.95)
            Point(points=crest, pointsize=dp(0.95 + 0.3 * flash))
        if hot:
            Color(1, 1, 1, 0.9 if state != "idle" else 0.55)
            Point(points=hot, pointsize=dp(1.1))
        # orbiting dust on three tilted rings (dimmer behind the sphere)
        speed_k = {"idle": 0.5, "thinking": 2.2, "speaking": 1.2, "listening": -1.4}.get(state, 0.8)
        orb_ang = getattr(self, "orbit_ang", 0.0)
        tw = getattr(self, "think_w", 0.0)
        rt = getattr(self, "ready_t", None)
        gather = 0.0
        if rt is None:
            ofac, burst = 1.0, False
        elif rt < 2.0:                      # 1) run to the centre (ease-in)
            u = rt / 2.0
            ofac, burst, gather = 1.0 - 0.97 * u * u, False, u
        elif rt < 2.3:                      # 2) fast burst outward
            u = (rt - 2.0) / 0.3
            ofac, burst = 0.03 + 2.2 * (1 - (1 - u) ** 3), True
        else:                               # 3) flow back to the working orbit
            u = min(1.0, (rt - 2.3) / 1.6)
            sm = u * u * (3 - 2 * u)
            ofac, burst = 2.23 + (1.0 - 2.23) * sm, True
        if gather > 0:                      # the centre glows while the dust gathers
            core = radial_texture("p_core", ((0, (200, 250, 255, 255)), (0.4, (60, 200, 255, 120)),
                                             (1, (40, 200, 255, 0))))
            cr = R * (0.15 + 0.55 * gather)
            Color(1, 1, 1, min(1.0, 0.25 + 0.75 * gather))
            Rectangle(texture=core, pos=(cx - cr, cy - cr), size=(2 * cr, 2 * cr))
        tilts = (0.55, -0.95, 1.35)
        radii = (1.28, 1.46, 1.64)
        fr = ([], [], [])
        bk = ([], [], [])
        haze = []
        ca_y, sa_y = math.cos(rot * 0.5), math.sin(rot * 0.5)
        if rt is None:
            dust_scatter = 0.0
        elif rt < 2.3:
            dust_scatter = 1.0
        else:
            _u = min(1.0, (rt - 2.3) / 1.6)
            dust_scatter = 1.0 - _u * _u * (3 - 2 * _u)   # fades to 0, so nothing jumps when the sequence ends
        for oi, (ring, a0, sp, sz, rj, hz) in enumerate(self.orbit):
            # thinking: every dot wobbles, so each ring twists like a rope
            a = a0 + orb_ang * sp * (1.0 + 0.35 * ring) + tw * 0.9 * math.sin(a0 * 3 + p * 2.6)
            tilt = tilts[ring] + tw * 0.55 * math.sin(p * 1.4 + ring * 2.0 + a0)
            f = ofac * (1.0 + ((0.75 + 0.5 * sz) - 1.0) * dust_scatter)
            if rt is not None and rt < 2.0:
                a += gather * 4.0 * sp          # spiral inward while gathering
            # slow breathing drift of each grain so the belt looks like drifting dust, not a rigid ring
            drift = 0.03 * math.sin(a0 * 7 + p * 0.5)
            rad = (radii[ring] + rj + drift) * f * (1 + 0.10 * tw * math.sin(a0 * 5 + p * 3.1))
            rx, rz = math.cos(a) * rad, math.sin(a) * rad
            ct, st = math.cos(tilt), math.sin(tilt)
            y1, z1 = -rz * st + hz * f, rz * ct
            x2, z2 = rx * ca_y + z1 * sa_y, -rx * sa_y + z1 * ca_y
            pt = (cx + x2 * R, cy + y1 * R)
            cls = 0 if sz < 0.55 else (1 if sz < 0.8 else 2)
            if z2 > 0 or rad < 0.5:
                fr[cls].extend(pt)
            else:
                bk[cls].extend(pt)
            if oi % 5 == 0:
                haze.append((pt[0], pt[1], z2 > 0 or rad < 0.5, sz))
        # soft translucent puffs first (the "cloud" body), then the grains on top
        hz_tex = radial_texture("p_haze", ((0, (150, 230, 255, 70)), (0.5, (80, 200, 255, 30)),
                                           (1, (40, 170, 255, 0))))
        for hx, hy, hfront, hsz in haze:
            hs = R * (0.10 + 0.16 * hsz) * (0.6 + 0.4 * ofac)
            Color(1, 1, 1, (0.55 if hfront else 0.22) * (1.0 if not burst else 1.3))
            Rectangle(texture=hz_tex, pos=(hx - hs, hy - hs), size=(2 * hs, 2 * hs))
        for cls, (psz, alpha) in enumerate(((0.45, 0.30), (0.65, 0.40), (0.95, 0.50))):
            if bk[cls]:
                Color(0.15, 0.6, 0.75, alpha)
                Point(points=bk[cls], pointsize=dp(psz * 0.8))
        for cls, (psz, alpha) in enumerate(((0.55, 0.55), (0.85, 0.80), (1.25, 0.95))):
            if fr[cls]:
                Color(0.6, 0.95, 1, 1.0 if burst else alpha)
                Point(points=fr[cls], pointsize=dp(psz * (1.25 if burst else 1.0)))
        # ---- state effects around the sphere
        if state == "thinking":
            a0 = rot * 57.3 * 1.6
            Color(*accent2, 0.85)
            Line(circle=(cx, cy, R * 1.42, a0, a0 + 75), width=dp(1.4), cap="round")
            Color(*accent, 0.6)
            Line(circle=(cx, cy, R * 1.52, a0 + 180, a0 + 225), width=dp(1.0), cap="round")
        elif state == "speaking":
            for k in range(3):
                u = (cycles * 0.5 + k / 3.0) % 1.0
                Color(*accent, (1 - u) * (0.15 + 0.6 * lv))
                Line(circle=(cx, cy, R * (1.3 + 0.7 * u)), width=dp(1.0))
        elif state == "listening":
            for k in range(3):
                u = (cycles * 0.25 + k / 3.0) % 1.0
                Color(0.7, 1, 0.9, u * (0.20 + 0.55 * lv))
                Line(circle=(cx, cy, R * (2.0 - 0.7 * u)), width=dp(1.0))
        if flash > 0:
            u = 1.0 - flash
            Color(1, 1, 1, flash * 0.85)
            Line(circle=(cx, cy, R * (1.25 + 0.9 * u)), width=dp(1.8))

    def ready_factor(self):
        """(radius factor, gather 0..1, burst?) of the "answer ready" sequence: gather, burst, return."""
        rt = getattr(self, "ready_t", None)
        if rt is None:
            return 1.0, 0.0, False
        if rt < 2.0:
            u = rt / 2.0
            return 1.0 - 0.97 * u * u, u, False
        if rt < 2.3:
            u = (rt - 2.0) / 0.3
            return 0.03 + 1.6 * (1 - (1 - u) ** 3), 0.0, True
        u = min(1.0, (rt - 2.3) / 1.6)
        return 1.63 + (1.0 - 1.63) * (u * u * (3 - 2 * u)), 0.0, True

    def glass_geometry(self):
        size = min(self.width, self.height)
        # the sphere bobs gently on the water
        return self.center_x, self.y + self.height * 0.56 + size * 0.34 * 0.03 * math.sin(self.phase * 0.8), size * 0.34

    def update_glass_shader(self):
        """GPU glass sphere (shader child) + drawn floor/pillars (canvas.before). False if unavailable."""
        if getattr(self, "_shader_failed", False):
            return False
        try:
            wview = getattr(self, "_water_view", None)
            if wview is None and not getattr(self, "_water_failed", False):
                wview = AuraShaderView(frag=WATER_FRAG)
                if wview.ok:
                    wview.use_mesh()
                    self._water_view = wview
                    self.add_widget(wview)        # added first, so it is drawn behind the glass sphere
                else:
                    self._water_failed = True
                    wview = None
            view = getattr(self, "_shader_view", None)
            if view is None:
                view = AuraShaderView(frag=GLASS_FRAG)
                if not view.ok:
                    self._shader_failed = True
                    self.canvas.before.clear()
                    return False
                self._shader_view = view
                self.add_widget(view)
            cx, cy, R = self.glass_geometry()
            self.canvas.before.clear()
            if wview is not None:
                state_w = getattr(self, "state_now", "idle")
                rs = {"idle": 0.10, "thinking": 0.25, "speaking": 0.40 + 0.3 * self.level,
                      "listening": -0.22}.get(state_w, 0.12)
                self._ring_t = getattr(self, "_ring_t", 0.0) + 0.016 * rs
                size_w = min(self.width, self.height)
                Rw = size_w * 0.34
                wl = self.y + self.height * 0.56 - Rw * 1.02   # fixed waterline: the sphere bobs above it
                wview.place_rect(self.x, self.y, self.width, self.height)
                wview.canvas["u_geo"] = ((cx - self.x) / max(self.width, 1.0), (wl - self.y) / max(self.height, 1.0),
                                         self.width / Rw, self.height / Rw)
                wview.update(self.clock_t, self.phase, min(1.0, self.level),
                             u_think=getattr(self, "think_w", 0.0), u_flash=getattr(self, "flash", 0.0),
                             u_speak=getattr(self, "_speak_w", 0.0), u_ring=self._ring_t)
            else:
                with self.canvas.before:
                    self.draw_glass_scene(cx, cy, R)
            view.place(cx, cy, R * 8.0)  # the shader sphere has radius 0.25 of the half quad; rings need room
            ofac, gather, _burst = self.ready_factor()
            state = getattr(self, "state_now", "idle")
            lv = self.level
            self._listen_w = getattr(self, "_listen_w", 0.0)
            self._listen_w += ((1.0 if state == "listening" else 0.0) - self._listen_w) * 0.15
            if state == "listening":
                ofac *= 1.0 - 0.06 * getattr(self, "pulse", 0.0) * (0.4 + lv)
            self._speak_w = getattr(self, "_speak_w", 0.0)
            self._speak_w += ((1.0 if state == "speaking" else 0.0) - self._speak_w) * 0.15
            rt = getattr(self, "ready_t", None)
            age = (rt - 2.0) if (rt is not None and 2.0 <= rt < 3.8) else -1.0
            clip = (-(cx - self.x) / R, (self.right - cx) / R, -(cy - self.y) / R, (self.top - cy) / R)
            if "uniform vec4 u_clip;" in getattr(view, "src", ""):
                view.canvas["u_clip"] = tuple(float(v) for v in clip)
            view.update(self.clock_t, self.phase, min(1.0, lv + 0.08 * getattr(self, "pulse", 0.0)),
                        u_think=getattr(self, "think_w", 0.0), u_gather=gather, u_ofac=ofac,
                        u_flash=getattr(self, "flash", 0.0), u_listen=self._listen_w,
                        u_speak=self._speak_w, u_cycles=getattr(self, "pulse_cycles", 0.0),
                        u_orbit=getattr(self, "orbit_ang", 0.0), u_age=age)
            return True
        except Exception:
            self._shader_failed = True
            try:
                self.canvas.before.clear()
                if getattr(self, "_shader_view", None) is not None:
                    self.remove_widget(self._shader_view)
                if getattr(self, "_water_view", None) is not None:
                    self.remove_widget(self._water_view)
                    self._water_view = None
                    self._water_failed = True
            except Exception:
                pass
            return False

    def draw_glass_scene(self, cx, cy, R):
        """The sphere floats on top of dark water: a waterline with haze, wavy perspective ripples, rings
        spreading from where the sphere touches the surface, a broken reflection column, glints and
        splash drops when the answer bursts."""
        lv = self.level
        flash = getattr(self, "flash", 0.0)
        state = getattr(self, "state_now", "idle")
        p = self.phase
        cycles = getattr(self, "pulse_cycles", 0.0)
        w, x0, y0 = self.width, self.x, self.y
        wl = self.y + self.height * 0.56 - R * 1.02     # fixed waterline (the sphere bobs a little above it)
        wh = max(wl - y0, 1)
        boost = min(1.8, 1 + 0.5 * lv + 0.8 * flash)
        # --- water body: glossy dark teal at the horizon, black in the foreground
        body = linear_texture("s_body", ((0, (0, 4, 12, 220)), (0.55, (3, 22, 40, 190)), (1, (14, 70, 100, 190))))
        Color(1, 1, 1, 1)
        Rectangle(texture=body, pos=(x0, y0), size=(w, wh))
        # --- haze hanging just above the horizon, and a bright waterline
        haze = linear_texture("s_haze", ((0, (60, 170, 230, 70)), (1, (30, 120, 200, 0))))
        Color(1, 1, 1, min(1.0, 0.8 * boost))
        Rectangle(texture=haze, pos=(x0, wl), size=(w, R * 0.7))
        glowl = radial_texture("s_line", ((0, (120, 220, 255, 150)), (1, (60, 170, 255, 0))))
        Color(1, 1, 1, min(1.0, 0.55 * boost))
        Rectangle(texture=glowl, pos=(x0 - w * 0.1, wl - dp(7)), size=(w * 1.2, dp(14)))
        # --- slow mist banks drifting along the horizon
        for k in range(3):
            mx = x0 + w * ((0.2 + 0.3 * k + p * 0.01 * (1 + k)) % 1.2 - 0.1)
            Color(1, 1, 1, 0.16)
            Rectangle(texture=glowl, pos=(mx - w * 0.3, wl - dp(4)), size=(w * 0.6, R * 0.5))
        # --- wavy ripple lines in perspective, gliding towards the viewer
        for k in range(10):
            t = (k / 10.0 + p * 0.02) % 1.0
            y = wl - wh * t ** 1.7
            amp = dp(1.0 + 3.0 * t) * (1 + 0.8 * lv)
            pts = []
            for n in range(21):
                u = n / 20.0
                pts.extend((x0 + w * u, y + amp * math.sin(u * 15 + p * 1.2 + k * 1.7)))
            Color(0.45, 0.85, 1, (0.04 + 0.13 * t) * (1 - 0.3 * t) * min(1.6, boost))
            Line(points=pts, width=dp(0.7 + 0.5 * t))
        # --- reflection column under the sphere, broken into streaks by the ripples
        nstreak = 16
        for k in range(nstreak):
            d = k / float(nstreak)
            y = wl - R * (0.03 + 0.09 * k ** 1.08) * 0.9
            if y < y0:
                break
            half = R * 0.95 * (1.0 - d) ** 0.6
            off = R * 0.07 * d * math.sin(p * 2.0 + k * 0.9) * (1 + 1.5 * lv)
            Color(0.5, 0.92, 1, min(1.0, (0.42 * (1 - d) + 0.04) * boost))
            Line(points=[cx - half + off, y, cx + half + off, y], width=dp(2.4 - 1.2 * d))
        refl = radial_texture("g_refl", ((0, (70, 190, 255, 140)), (1, (70, 190, 255, 0))))
        Color(1, 1, 1, min(1.0, 0.8 * boost))
        Rectangle(texture=refl, pos=(cx - R * 1.9, wl - R * 0.38), size=(R * 3.8, R * 0.76))
        Color(1, 1, 1, min(1.0, 0.35 * boost))
        Rectangle(texture=refl, pos=(cx - R * 0.9, wl - R * 1.5), size=(R * 1.8, R * 1.5))
        # --- rings spreading from the contact point: slow when calm, faster while talking
        speed = {"idle": 0.12, "thinking": 0.30, "speaking": 0.45 + 0.3 * lv, "listening": 0.22}.get(state, 0.15)
        self._ring_t = getattr(self, "_ring_t", 0.0) + 0.016 * speed
        for k in range(4):
            u = (self._ring_t + k * 0.25) % 1.0
            rr = R * (0.8 + 2.6 * u)
            Color(0.55, 0.92, 1, (1 - u) ** 1.6 * (0.35 + 0.35 * lv) * min(1.5, boost))
            Line(ellipse=(cx - rr, wl - rr * 0.17, rr * 2, rr * 0.34), width=dp(1.1), segments=48)
        if flash > 0:
            u = 1.0 - flash
            rr = R * (0.9 + 3.2 * u)
            Color(1, 1, 1, flash * 0.8)
            Line(ellipse=(cx - rr, wl - rr * 0.17, rr * 2, rr * 0.34), width=dp(2), segments=48)
            # splash drops thrown up from the contact point
            for i in range(14):
                f = (i - 6.5) / 6.5
                hgt = R * (1.5 * u - 2.2 * u * u) * (0.5 + (i % 3) / 3.0)
                if hgt > 0:
                    Color(0.8, 0.97, 1, flash * 0.9)
                    Point(points=[cx + f * R * 1.3 * (0.3 + u), wl + hgt], pointsize=dp(1.8))
        # --- glints on the waves, strongest in the light column under the sphere
        if not hasattr(self, "_glints"):
            rnd = random.Random(52)
            self._glints = [(rnd.random(), rnd.random(), rnd.uniform(0, 6.3), rnd.uniform(1.0, 2.2)) for _ in range(70)]
        for rx, rt, ph, spd in self._glints:
            t = 0.04 + 0.96 * rt
            y = wl - wh * t ** 1.7
            x = x0 + w * rx
            near = math.exp(-((x - cx) / (R * 1.4)) ** 2)
            tw_ = max(0.0, math.sin(p * spd + ph)) ** 6
            a = tw_ * (0.25 + 0.75 * near) * (0.4 + 0.6 * t) * boost
            if a > 0.03:
                Color(0.85, 0.98, 1, min(1.0, a))
                Point(points=[x, y], pointsize=dp(0.8 + 1.6 * t))

    def draw_glass_overlay(self):
        """Scanning rings, orbiting comets and state rings drawn above the shader sphere."""
        cx, cy, R = self.glass_geometry()
        p, lv = self.phase, self.level
        state = getattr(self, "state_now", "idle")
        tw = getattr(self, "think_w", 0.0)
        flash = getattr(self, "flash", 0.0)
        cycles = getattr(self, "pulse_cycles", 0.0)
        accent = hex_color(THEMES[self.theme_index]["accent"])[:3]
        accent2 = hex_color(THEMES[self.theme_index]["accent2"])[:3]
        ofac, _g, _b = self.ready_factor()
        rt = getattr(self, "ready_t", None)
        with self.canvas.after:
            if tw > 0.02:
                for i in range(2):
                    tilt = 0.30 + 0.28 * math.sin(p * 1.1 + i * 2.4)
                    pts = []
                    for n in range(49):
                        t = n / 48.0 * math.pi * 2 + p * (1.3 if i == 0 else -1.0)
                        pts.extend((cx + math.cos(t) * R * 1.04, cy + math.sin(t) * R * 1.04 * tilt))
                    Color(*(accent if i == 0 else accent2), 0.7 * tw)
                    Line(points=pts, width=dp(1.1))
            oa = getattr(self, "orbit_ang", 0.0)
            for ring in range(2):
                tilt = 0.32 + 0.22 * ring + tw * 0.2 * math.sin(p * 1.6 + ring)
                rad = R * (1.22 + 0.2 * ring) * (ofac if rt is not None else 1.0)
                head = oa * (1.0 + 0.4 * ring) * (1 if ring == 0 else -1) + ring * 2.0
                for k in range(14):
                    t = head - k * 0.09
                    px = cx + math.cos(t) * rad
                    py = cy + math.sin(t) * rad * tilt
                    Color(0.6, 0.95, 1, (1 - k / 14.0) * (0.55 + 0.4 * lv) * (1.0 if math.sin(t) < 0 else 0.5))
                    Point(points=[px, py], pointsize=dp(1.0 - 0.04 * k))
            # three thin gyroscope rings that tumble slowly around the sphere, each with a bright node
            for i in range(3):
                ga = oa * 0.3 * (i + 1) * (1 if i % 2 == 0 else -1) + i * 1.1
                gt = 1.0 + 0.5 * i + 0.4 * math.sin(p * 0.5 + i * 1.7)
                semi_a = R * (1.42 + 0.12 * i) * (ofac if rt is not None else 1.0)
                semi_b = semi_a * abs(math.cos(gt)) + dp(1.5)
                cg, sg = math.cos(ga), math.sin(ga)
                pts = []
                for n in range(49):
                    t = n / 48.0 * math.pi * 2
                    ex, ey = math.cos(t) * semi_a, math.sin(t) * semi_b
                    pts.extend((cx + ex * cg - ey * sg, cy + ex * sg + ey * cg))
                Color(0.75, 0.88, 1.0, 0.12 + 0.18 * lv + 0.25 * tw)
                Line(points=pts, width=dp(0.7))
                tn = oa * (1.3 + 0.5 * i) + i * 2.0
                ex, ey = math.cos(tn) * semi_a, math.sin(tn) * semi_b
                Color(1, 1, 1, 0.8)
                Point(points=[cx + ex * cg - ey * sg, cy + ex * sg + ey * cg], pointsize=dp(1.5))
            # sparks fly out of the centre when the answer bursts
            if rt is not None and 2.0 <= rt < 3.8:
                age = rt - 2.0
                fade = max(0.0, 1.0 - age / 1.8)
                for i in range(48):
                    ang = i * 2.39996
                    spd = 0.8 + 0.9 * ((i * 0.618) % 1.0)
                    d = R * (0.2 + spd * 1.4 * (1 - math.exp(-2.5 * age)))
                    ca, sa = math.cos(ang), math.sin(ang)
                    Color(0.85, 0.95, 1.0, fade)
                    Line(points=[cx + ca * d * 0.82, cy + sa * d * 0.82, cx + ca * d, cy + sa * d], width=dp(0.8))
            if state == "speaking":
                for k in range(3):
                    u = (cycles * 0.5 + k / 3.0) % 1.0
                    rr = R * (1.1 + 0.7 * u)
                    Color(*accent, (1 - u) * (0.15 + 0.6 * lv))
                    Line(ellipse=(cx - rr, cy - rr * 0.22, rr * 2, rr * 0.44, 0, 360, 60), width=dp(1.0))
            elif state == "listening":
                for k in range(3):
                    u = (cycles * 0.25 + k / 3.0) % 1.0
                    Color(0.7, 1, 0.9, u * (0.20 + 0.55 * lv))
                    Line(circle=(cx, cy, R * (2.0 - 0.7 * u)), width=dp(1.0))
            if flash > 0:
                u = 1.0 - flash
                rr = R * (1.1 + 0.9 * u)
                Color(1, 1, 1, flash * 0.85)
                Line(ellipse=(cx - rr, cy - rr * 0.22, rr * 2, rr * 0.44, 0, 360, 60), width=dp(1.8))
                Line(circle=(cx, cy, rr), width=dp(1.4))

    def draw_glass(self, cx, cy, R, accent, accent2):
        """Glass orb. Calm when ready, twisting meridians and scanning rings while thinking, dust that
        gathers, bursts and returns when the answer is ready, waves while reading aloud, ripples
        converging while the microphone listens."""
        p = self.phase
        lv = self.level
        state = getattr(self, "state_now", "idle")
        pulse = getattr(self, "pulse", 0.0)
        flash = getattr(self, "flash", 0.0)
        cycles = getattr(self, "pulse_cycles", 0.0)
        rot = getattr(self, "rot", 0.0)
        tw = getattr(self, "think_w", 0.0)
        rt = getattr(self, "ready_t", None)
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
            Color(1, 1, 1, min(1.0, a * (1 + 0.6 * lv + 0.8 * flash)))
            Rectangle(texture=pillar, pos=(x - dp(2.5), fy), size=(dp(5), max(self.top - fy - dp(6), 1)))
            rw = w * 0.32
            Rectangle(texture=refl, pos=(x - rw / 2, fy - rw * 0.125), size=(rw, rw * 0.25))
        Color(1, 1, 1, min(1.0, 0.8 + 0.5 * lv + 0.4 * flash))
        Rectangle(texture=refl, pos=(cx - R * 1.1, fy - R * 0.22), size=(R * 2.2, R * 0.44))
        Color(0.22, 0.78, 1, 0.2)
        for k in range(-6, 7):
            x = cx + k * R * 0.13
            Line(points=[x, fy, x + k * 2, fy - R * 0.5], width=dp(0.6))
        body = radial_texture("g_body", ((0, (10, 24, 34, 255)), (0.8, (3, 9, 15, 255)),
                                         (1, (11, 42, 60, 255))))
        Color(1, 1, 1, 1)
        Rectangle(texture=body, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        spin = rot * 1.4
        for k in range(14):
            a = k / 14.0 * math.pi + spin + tw * 0.6 * math.sin(p * 1.2 + k * 0.7)
            rx = max(R * abs(math.cos(a)), dp(0.4))
            Color(0.22, 0.78, 1, 0.07 + 0.2 * abs(math.sin(a)) + 0.25 * flash)
            Line(ellipse=(cx - rx, cy - R * 0.995, 2 * rx, 2 * R * 0.995, 0, 360, 40), width=dp(0.6))
        # ---- "answer ready" sequence for the surface dust: gather 2 s, burst, flow back
        gather = 0.0
        burst = False
        if rt is None:
            ofac = 1.0
        elif rt < 2.0:
            u = rt / 2.0
            ofac, gather = 1.0 - 0.97 * u * u, u
        elif rt < 2.3:
            u = (rt - 2.0) / 0.3
            ofac, burst = 0.03 + 1.6 * (1 - (1 - u) ** 3), True
        else:
            u = min(1.0, (rt - 2.3) / 1.6)
            ofac, burst = 1.63 + (1.0 - 1.63) * (u * u * (3 - 2 * u)), True
        front, back, red = [], [], []
        pull = 0.06 * pulse * (0.4 + lv) if state == "listening" else 0.0
        for lon, lat in self.dashes:
            lo = lon + spin + tw * 0.8 * math.sin(lat * 3 + p * 1.5)
            if gather > 0:
                lo += gather * 3.0
            x = math.cos(lat) * math.sin(lo)
            z = math.cos(lat) * math.cos(lo)
            y = math.sin(lat)
            f = 0.97 * ofac * (1 - pull) * ((0.8 + 0.4 * ((lon * 7) % 1.0)) if burst else 1.0)
            (front if z > 0.1 or ofac < 0.6 else back).extend((cx + x * R * f, cy + y * R * f))
        for lon, lat in self.reds:
            lo = lon + spin
            if math.cos(lat) * math.cos(lo) > 0:
                red.extend((cx + math.cos(lat) * math.sin(lo) * R * 0.97 * ofac,
                            cy + math.sin(lat) * R * 0.97 * ofac))
        if back:
            Color(0.37, 0.85, 1, 0.35)
            Point(points=back, pointsize=dp(0.5))
        if front:
            Color(0.45, 0.90, 1, 0.9)
            Point(points=front, pointsize=dp(0.65 if not burst else 0.9))
        if red:
            Color(1, 0.35, 0.29, 0.9)
            Point(points=red, pointsize=dp(0.9))
        inner = radial_texture("g_inner", ((0, (40, 160, 230, 0)), (0.7, (40, 160, 230, 0)),
                                           (1, (40, 170, 240, 70))))
        Color(1, 1, 1, 1)
        Rectangle(texture=inner, pos=(cx - R, cy - R), size=(2 * R, 2 * R))
        pl = min(1.0, 0.7 + 0.3 * math.sin(p * 2) + 0.5 * lv + 0.3 * pulse * (0.0 if state == "idle" else 1.0))

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
        fs = R * 0.22 * (1 + 0.8 * lv + 2.4 * gather + 1.5 * flash)
        Color(1, 1, 1, min(1.0, pl + gather))
        Rectangle(texture=flare, pos=(cx - fs, cy - fs), size=(fs * 2, fs * 2))
        # thinking: two scanning rings sweep over the sphere (tilt oscillates)
        if tw > 0.02:
            for i in range(2):
                tilt = 0.30 + 0.28 * math.sin(p * 1.1 + i * 2.4)
                pts = []
                for n in range(49):
                    t = n / 48.0 * math.pi * 2 + p * (1.3 if i == 0 else -1.0)
                    pts.extend((cx + math.cos(t) * R * 1.04, cy + math.sin(t) * R * 1.04 * tilt))
                Color(*(accent if i == 0 else accent2), 0.7 * tw)
                Line(points=pts, width=dp(1.1))
        Color(0.35, 0.82, 1, 0.85)
        Line(circle=(cx, cy, R), width=dp(1.3))
        Color(1, 1, 1, 0.92)
        Line(circle=(cx, cy, R * 0.9, 303, 327), width=R * 0.035, cap="round")
        Line(circle=(cx, cy, R * 0.9, 33, 57), width=R * 0.035, cap="round")
        # orbiting comets on two tilted rings; they follow the same gather/burst/return sequence
        osp = {"idle": 0.5, "thinking": 2.2, "speaking": 1.2, "listening": -1.4}.get(state, 0.8)
        oa = getattr(self, "orbit_ang", 0.0)
        for ring in range(2):
            tilt = 0.32 + 0.22 * ring + tw * 0.2 * math.sin(p * 1.6 + ring)
            rad = R * (1.22 + 0.2 * ring) * (ofac if rt is not None else 1.0)
            head = oa * (1.0 + 0.4 * ring) * (1 if ring == 0 else -1) + ring * 2.0
            for k in range(14):
                t = head - k * 0.09
                px = cx + math.cos(t) * rad
                py = cy + math.sin(t) * rad * tilt
                Color(0.6, 0.95, 1, (1 - k / 14.0) * (0.55 + 0.4 * lv) * (1.0 if math.sin(t) < 0 else 0.5))
                Point(points=[px, py], pointsize=dp(1.0 - 0.04 * k))
        # state rings
        if state == "speaking":
            for k in range(3):
                u = (cycles * 0.5 + k / 3.0) % 1.0
                rr = R * (1.1 + 0.7 * u)
                Color(*accent, (1 - u) * (0.15 + 0.6 * lv))
                Line(ellipse=(cx - rr, cy - rr * 0.22, rr * 2, rr * 0.44, 0, 360, 60), width=dp(1.0))
        elif state == "listening":
            for k in range(3):
                u = (cycles * 0.25 + k / 3.0) % 1.0
                Color(0.7, 1, 0.9, u * (0.20 + 0.55 * lv))
                Line(circle=(cx, cy, R * (2.0 - 0.7 * u)), width=dp(1.0))
        if flash > 0:
            u = 1.0 - flash
            rr = R * (1.1 + 0.9 * u)
            Color(1, 1, 1, flash * 0.85)
            Line(ellipse=(cx - rr, cy - rr * 0.22, rr * 2, rr * 0.44, 0, 360, 60), width=dp(1.8))
            Line(circle=(cx, cy, rr), width=dp(1.4))

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
        if self.theme_index == 0:
            layers = aura_layers()
            if layers is not None:
                self.draw_aura_layers(layers, size)
                return
        side = size * 1.3 * breathe
        cx, cy = self.center
        angle = 7 * math.sin(t * 0.35) if self.theme_index == 0 else (t * 8) % 360
        PushMatrix()
        Rotate(angle=angle, origin=(cx, cy))
        Color(1, 1, 1, 0.9 + 0.1 * lv)
        Rectangle(texture=tex, pos=(cx - side / 2, cy - side / 2), size=(side, side))
        PopMatrix()

    def shader_extras(self, view, cx, cy, R):
        """Per-frame uniforms shared by the state-aware shader orbs (think/listen/speak/ready)."""
        state = getattr(self, "state_now", "idle")
        ofac, gather, _burst = self.ready_factor()
        lv = self.level
        self._listen_w = getattr(self, "_listen_w", 0.0)
        self._listen_w += ((1.0 if state == "listening" else 0.0) - self._listen_w) * 0.15
        self._speak_w = getattr(self, "_speak_w", 0.0)
        self._speak_w += ((1.0 if state == "speaking" else 0.0) - self._speak_w) * 0.15
        if state == "listening":
            ofac *= 1.0 - 0.06 * getattr(self, "pulse", 0.0) * (0.4 + lv)
        rt = getattr(self, "ready_t", None)
        age = (rt - 2.0) if (rt is not None and 2.0 <= rt < 3.8) else -1.0
        clip = (-(cx - self.x) / R, (self.right - cx) / R, -(cy - self.y) / R, (self.top - cy) / R)
        if "uniform vec4 u_clip;" in getattr(view, "src", ""):
            view.canvas["u_clip"] = tuple(float(v) for v in clip)
        self._ofac_now = ofac
        view.update(self.clock_t, self.phase, min(1.0, lv + 0.10 * getattr(self, "pulse", 0.0)),
                    u_think=getattr(self, "think_w", 0.0), u_gather=gather, u_ofac=ofac,
                    u_flash=getattr(self, "flash", 0.0), u_listen=self._listen_w,
                    u_speak=self._speak_w, u_cycles=getattr(self, "pulse_cycles", 0.0),
                    u_orbit=getattr(self, "orbit_ang", 0.0), u_age=age)

    def update_aura_shader(self, size):
        """Run the GPU Aura orb. Returns False (once and for all) if the shader is unavailable."""
        if getattr(self, "_shader_failed", False):
            return False
        try:
            view = getattr(self, "_shader_view", None)
            if view is None:
                view = AuraShaderView(frag=AURA_FRAG)
                if not view.ok:
                    self._shader_failed = True
                    return False
                self._shader_view = view
                self.add_widget(view)
            cx, cy = self.center
            R = size * 1.34 * 0.58 / 2.0      # sphere radius in pixels
            view.place(cx, cy, R * 8.0)        # sphere = 0.25 of the half quad; rings need room
            self.shader_extras(view, cx, cy, R)
            return True
        except Exception:
            self._shader_failed = True
            try:
                if getattr(self, "_shader_view", None) is not None:
                    self.remove_widget(self._shader_view)
            except Exception:
                pass
            return False

    def draw_ribbon_overlay(self, cx, cy, size):
        """PNG ribbons spinning over the shader orb (drawn in canvas.after, i.e. above the child)."""
        layers = aura_layers()
        if layers is None:
            return
        lv, p, t = self.level, self.phase, self.clock_t
        # shader sphere diameter = 0.58 of its quad (size*1.34); PNG sphere = 0.60 of its image
        side = size * 1.34 * 0.58 / 0.60
        spec = (("aura_ribbon1", 13.0, 0.0, 1.0, 1.0), ("aura_ribbon2", -19.0, 2.1, 0.96, 1.0),
                ("aura_ribbon3", 27.0, 4.2, 1.04, 1.0))
        with self.canvas.after:
            for name, speed, ph, scale, alpha in spec * 2:  # drawn twice: PNG ribbons glow brighter than the shader veins
                angle = p * speed + 6 * math.sin(t * 0.5 + ph)
                s2 = side * (scale + 0.03 * math.sin(t * 1.3 + ph) + 0.06 * lv * math.sin(t * 5 + ph)) * max(getattr(self, "_ofac_now", 1.0), 0.05)
                Color(1, 1, 1, min(1.0, alpha * (0.75 + 0.2 * math.sin(t * 1.1 + ph) + 0.3 * lv)))
                PushMatrix()
                Rotate(angle=angle, origin=(cx, cy))
                Rectangle(texture=layers[name], pos=(cx - s2 / 2, cy - s2 / 2), size=(s2, s2))
                PopMatrix()

    def draw_aura_layers(self, layers, size):
        """Multi-layer Aura: three ribbons spin at different speeds/directions, the body breathes,
        the glare drifts, and energy rings radiate while JARVIS listens or speaks."""
        lv, p, t = self.level, self.phase, self.clock_t
        app = App.get_running_app()
        state = getattr(app, "orb_state", "idle") if app is not None else "idle"
        cx, cy = self.center
        breathe = 1 + 0.022 * math.sin(t * 0.9) + 0.07 * lv
        side = size * 1.3 * breathe
        R = side * 0.30  # sphere radius inside the image
        # radiating rings (only when active)
        if state in ("listening", "speaking") or lv > 0.05:
            for k in range(3):
                u = ((p * 0.55) + k / 3.0) % 1.0
                Color(0.45, 0.7, 1.0, (1 - u) ** 1.6 * min(0.55, 0.15 + lv))
                rad = R * (1.02 + 0.55 * u)
                Line(circle=(cx, cy, rad), width=dp(1.3))
        Color(1, 1, 1, 0.95 + 0.05 * lv)
        Rectangle(texture=layers["aura_body"], pos=(cx - side / 2, cy - side / 2), size=(side, side))
        spec = (("aura_ribbon1", 13.0, 0.0, 1.0), ("aura_ribbon2", -19.0, 2.1, 0.96),
                ("aura_ribbon3", 27.0, 4.2, 1.04))
        for name, speed, ph, scale in spec:
            angle = p * speed + 6 * math.sin(t * 0.5 + ph)
            s2 = side * (scale + 0.03 * math.sin(t * 1.3 + ph) + 0.06 * lv * math.sin(t * 5 + ph))
            Color(1, 1, 1, min(1.0, 0.72 + 0.2 * math.sin(t * 1.1 + ph) + 0.3 * lv))
            PushMatrix()
            Rotate(angle=angle, origin=(cx, cy))
            Rectangle(texture=layers[name], pos=(cx - s2 / 2, cy - s2 / 2), size=(s2, s2))
            PopMatrix()
        gx, gy = 0.012 * side * math.sin(t * 0.7), 0.012 * side * math.cos(t * 0.9)
        Color(1, 1, 1, 0.75 + 0.25 * math.sin(t * 1.6))
        Rectangle(texture=layers["aura_glare"], pos=(cx - side / 2 + gx, cy - side / 2 + gy), size=(side, side))

    def draw_overlay(self):
        self.canvas.after.clear()
        if self.width <= 0 or self.height <= 0:
            return
        if self.theme_index == 0 and getattr(self, "_shader_view", None) is not None \
                and not getattr(self, "_shader_failed", False):
            cx, cy = self.center
            self.draw_ribbon_overlay(cx, cy, min(self.width, self.height))

    def redraw(self, *_):
        try:
            self._redraw_impl()
            self.draw_overlay()
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
        if self.width <= 0 or self.height <= 0:
            self.canvas.clear()
            return
        # The shader orb is a child widget: do not clear the canvas (that would drop it).
        if self.theme_index == 0 and self.update_aura_shader(min(self.width, self.height)):
            return
        if self.theme_index == 2 and self.update_glass_shader():
            return
        self.canvas.clear()
        theme = THEMES[self.theme_index]
        accent = hex_color(theme["accent"])[:3]
        accent2 = hex_color(theme["accent2"])[:3]
        cx, cy = self.center
        size = min(self.width, self.height)
        with self.canvas:
            if self.theme_index == 0:
                layers = aura_layers()
                if layers is not None:
                    self.draw_aura_layers(layers, size)
                    return
            photo = orb_texture(self.theme_index) if self.theme_index == 0 else None
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

def command(name, *keywords, priority=10, targets=()):
    def register(fn):
        COMMANDS.append((priority, name, tuple(k.lower() for k in keywords), fn, tuple(targets)))
        COMMANDS.sort(key=lambda c: -c[0])
        return fn
    return register

def keyword_match(text, keyword):
    pattern = r"(?<!\w)" + re.escape(keyword) + (r"(?!\w)" if len(keyword) <= 3 else "")
    return re.search(pattern, text) is not None

# Polite / connecting words that may surround an offline command without changing its meaning.
COMMAND_FILLER = frozenset((
    "лутфан", "пожалуйста", "please", "ба", "кун", "кунед", "бикун", "иваз", "шав", "нишон", "деҳ", "деҳед",
    "бидеҳ", "кушо", "кушоед", "бикушо", "ман", "ту", "мне", "покажи", "открой", "смени", "сменить",
    "измени", "поставь", "на", "в", "show", "open", "change", "switch", "set", "to", "the", "my", "me",
    "ҳоло", "ҳозир", "сейчас", "now",
))


def command_matches(text, keywords, targets=()):
    """An offline command runs only when the WHOLE message is that command (ignoring punctuation and
    polite filler words). A message that merely contains a similar word goes to the AI instead.
    Commands with `targets` (language, theme) may also carry one or two target words, e.g. "забон русӣ"."""
    rest = [w for w in re.findall(r"\w+", text.lower()) if w not in COMMAND_FILLER]
    if not rest:
        return False
    for k in keywords:
        kw = [w for w in re.findall(r"\w+", k) if w not in COMMAND_FILLER]
        if not kw:
            continue
        if rest == kw:
            return True
        if targets and len(rest) > len(kw):
            extra = None
            if rest[:len(kw)] == kw:
                extra = rest[len(kw):]
            elif rest[-len(kw):] == kw:
                extra = rest[:-len(kw)]
            if extra and len(extra) <= 2 and all(any(e.startswith(t) for t in targets) for e in extra):
                return True
    return False


def run_command(app, text):
    low = text.lower().strip()
    for priority, name, keywords, fn, targets in COMMANDS:
        if command_matches(low, keywords, targets):
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

@command("language", "забон", "забонро", "забони барнома", "язык", "язык приложения", "language",
         priority=70, targets=("тоҷик", "таджик", "tajik", "tg", "рус", "russian", "ru", "англис", "англий", "english", "en"))
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

@command("theme", "мавзӯъ", "мавзуъ", "мавзӯъро", "тема", "тему", "theme", "design", "интерфейс",
         "aura", "particle", "glass", priority=50, targets=("aura", "particle", "glass"))
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

@command("time", "соат", "соат чанд аст", "вақт чанд аст", "время", "который час", "сколько времени",
         "time", "what time is it", "clock", priority=10)
def cmd_time(app, text):
    return app.t("time") + ": " + datetime.now().strftime("%H:%M:%S")

@command("date", "сана", "санаи имрӯз", "имрӯз кадом сана аст", "дата", "какое сегодня число",
         "date", "what is the date", "what date is it", "today", priority=10)
def cmd_date(app, text):
    return app.t("date") + ": " + datetime.now().strftime("%d.%m.%Y")

@command("status", "ҳолат", "статус", "status", "system", priority=10)
def cmd_status(app, text):
    return app.t("system") + "\n\n" + THEMES[app.theme_index]["name"]

@command("help", "кӯмак", "кумак", "фармонҳо", "помощь", "команды", "help", "commands", priority=10)
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
            hint_text=self.t("input"), multiline=True, size_hint_x=1,
            font_size=dp(15), foreground_color=hex_color(theme["text"]),
            hint_text_color=(*hex_color(theme["accent"])[:3], 0.52),
            cursor_color=hex_color(theme["accent"]),
            background_normal="", background_active="",
            background_color=(0, 0, 0, 0),
            padding=[dp(8), dp(15), dp(4), dp(10)],
            input_type="text", keyboard_suggestions=True,
        )
        # Enter starts a new paragraph (multiline); a message is sent only with the send button.
        self.command_input.bind(text=self.on_text_change)

        def fit_input_height(*_):
            # the input row grows with the text (up to ~5 lines) and shrinks back after sending
            try:
                need = self.command_input.minimum_height + dp(6)
                command_row.height = max(dp(52), min(dp(140), need))
            except Exception:
                pass

        self.command_input.bind(minimum_height=fit_input_height)
        self.attach_btn = Button(text="+", font_size=dp(28), size_hint_x=None, width=dp(36),
                                 background_normal="", background_color=(0, 0, 0, 0),
                                 color=hex_color(theme["accent"]))
        self.attach_btn.bind(on_release=self.pick_attachment)
        self.stop_speech_btn = Button(text="\u00d7", font_size=dp(30), size_hint_x=None, width=dp(32),
                                      background_normal="", background_color=(0, 0, 0, 0),
                                      color=hex_color(theme["text"]))
        self.stop_speech_btn.bind(on_release=self.stop_speaking)
        self.emoji_btn = Button(text="\u263a" if FONT_OK else ":)", font_size=dp(24), size_hint_x=None,
                                width=dp(34), background_normal="", background_color=(0, 0, 0, 0),
                                color=hex_color(theme["accent"]))
        self.emoji_btn.bind(on_release=self.open_emoji_panel)
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
            tint, fill, who = accent, 0.20, self.t("you")
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
            self.show_response(self.t("file_picker_failed"))

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
                self.show_response(self.t("file_not_selected"))
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
                    raise RuntimeError(self.t("file_too_big"))
                output.write(buf, 0, count)
            stream.close()
            raw = bytes(output.toByteArray())
            if not raw:
                raise RuntimeError(self.t("file_empty"))
            name = str(uri.getLastPathSegment() or "attachment")
            prompt = self.command_input.text.strip() if hasattr(self, "command_input") else ""
            if hasattr(self, "command_input"):
                self.command_input.text = ""
            chat_id = self.chats.current_id
            display = "📎 " + name + ("\n" + prompt if prompt else "\n" + self.t("file_default_prompt"))
            self.add_chat_message(chat_id, "user", display)
            threading.Thread(target=self._upload_attachment, args=(chat_id, name, mime, raw, prompt), daemon=True).start()
        except Exception as exc:
            save_crash("Attachment read failed:\n" + traceback.format_exc())
            Clock.schedule_once(lambda dt, message=str(exc): self.show_response(self.t("file_read_failed").format(message)), 0)

    def _upload_attachment(self, chat_id, filename, mime, raw, prompt):
        import ssl
        boundary = "----JARVIS" + uuid.uuid4().hex
        def field(name, value):
            return ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n" + value + "\r\n").encode("utf-8")
        safe_name = os.path.basename(filename).replace('"', "_")
        body = field("prompt", prompt or self.t("file_prompt_server"))
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
            answer = str(result.get("answer", self.t("file_no_answer")))
            Clock.schedule_once(lambda dt: self._attachment_answer(chat_id, answer), 0)
        except urllib.error.HTTPError as exc:
            detail = self.t("server_http_error").format(exc.code)
            try:
                payload = json.loads(exc.read().decode("utf-8")); detail = str(payload.get("detail", detail))
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._attachment_answer(chat_id, detail), 0)
        except Exception as exc:
            Clock.schedule_once(lambda dt, message=str(exc): self._attachment_answer(chat_id, self.t("file_send_failed").format(message)), 0)

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
                    last = self.t("reply_format").format(keys)
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
                        self.show_response(self.t("tts_failed_all"))
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
            self.show_response(self.t("copy_failed"))
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
            self.show_response(self.t("edge_unavailable").format(detail))
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

    def open_emoji_panel(self, *_):
        """Emoji picker drawn by the app itself: taps insert the emoji into the command field."""
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        box = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        tabs = BoxLayout(orientation="horizontal", spacing=dp(6), size_hint_y=None, height=dp(44))
        scroll = ScrollView(do_scroll_x=False)
        grid = GridLayout(cols=7, spacing=dp(4), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        scroll.add_widget(grid)
        popup = self.make_popup(box, size_hint=(1, 0.5), pos_hint={"x": 0, "y": 0})

        def put(ch, *_):
            try:
                self.command_input.insert_text(ch)
            except Exception:
                self.command_input.text += ch

        def fill(index, *_):
            grid.clear_widgets()
            for ch in EMOJI_GROUPS[index][1]:
                b = GlassButton(text=ch, tint=tint, active=False, radius=10, size_hint_y=None,
                                height=dp(44), font_size=dp(24), color=hex_color(theme["text"]))
                b.bind(on_release=lambda inst, c=ch: put(c))
                grid.add_widget(b)
            scroll.scroll_y = 1

        for i, (label, _chars) in enumerate(EMOJI_GROUPS):
            t = GlassButton(text=label, tint=tint, active=True, radius=12, font_size=dp(22),
                            color=hex_color(theme["text"]))
            t.bind(on_release=lambda inst, k=i: fill(k))
            tabs.add_widget(t)
        back = GlassButton(text="\u232b", tint=tint, active=False, radius=12, font_size=dp(22),
                           color=hex_color(theme["text"]), size_hint_x=None, width=dp(52))
        back.bind(on_release=lambda *_: self.command_input.do_backspace())
        close = GlassButton(text="\u00d7", tint=tint, active=False, radius=12, font_size=dp(26),
                            color=hex_color(theme["text"]), size_hint_x=None, width=dp(52))
        close.bind(on_release=lambda *_: popup.dismiss())
        tabs.add_widget(back)
        tabs.add_widget(close)
        box.add_widget(tabs)
        box.add_widget(scroll)
        fill(0)
        popup.open()

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
