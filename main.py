# -*- coding: utf-8 -*-
"""
JARVIS Android Assistant
Kivy + Buildozer compatible. Uses built-in urllib for network tasks.
"""
from datetime import datetime
import copy
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
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.storage.jsonstore import JsonStore
from kivy.utils import get_color_from_hex
from kivy.graphics import (Color, Ellipse, Line, Point, Rectangle,
                           RoundedRectangle, Triangle, PushMatrix, PopMatrix, Rotate,
                           RenderContext, Mesh)
from kivy.graphics.texture import Texture
from kivy.graphics.fbo import Fbo
from kivy.graphics import ClearColor, ClearBuffers, Scale, Translate
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
                "Authorization": "Bearer " + self.app.server_token,
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
        self._init_status = None      # None = engine still starting, 0 = ready, other = failed
        self._listener = None

    def is_supported(self):
        return android_available()

    def _engine(self):
        if self._tts is None:
            from jnius import autoclass, PythonJavaClass, java_method
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            TextToSpeech = autoclass("android.speech.tts.TextToSpeech")
            owner = self

            class InitListener(PythonJavaClass):
                __javainterfaces__ = ["android/speech/tts/TextToSpeech$OnInitListener"]
                __javacontext__ = "app"

                @java_method("(I)V")
                def onInit(self, status):
                    owner._init_status = int(status)

            self._init_status = None
            self._listener = InitListener()      # keep a reference, or it is garbage collected
            self._tts = TextToSpeech(PythonActivity.mActivity, self._listener)
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
            if self._init_status is None:
                return "starting"             # the engine is still starting: the caller tries again shortly
            if self._init_status != 0:
                self.shutdown()               # next attempt creates a fresh engine
                raise VoiceUnavailable("engine init failed (status %s); is a Text-to-Speech engine installed?"
                                       % self._init_status)
            result = "ok"
            try:
                tts.setPitch(float(pitch))
            except Exception:
                pass
            codes = []
            code = tts.setLanguage(Locale(lang))
            codes.append("%s=%s" % (lang, code))
            if code < 0:
                # the wanted language has no voice data: try Russian (for Tajik), the phone's own language, English
                for name in (() if lang == "tg" else ("default", "en")):     # Tajik is never read with another language's voice
                    loc = Locale.getDefault() if name == "default" else Locale(name)
                    code = tts.setLanguage(loc)
                    codes.append("%s=%s" % (name, code))
                    if code >= 0:
                        result = "fallback"
                        break
            if code < 0:
                raise VoiceUnavailable("no voice data installed (%s)" % ", ".join(codes))
            try:
                speak_code = tts.speak(text, TextToSpeech.QUEUE_FLUSH, autoclass("android.os.Bundle")(), "jarvis")
            except Exception:
                params = autoclass("java.util.HashMap")()
                params.put("utteranceId", "jarvis")
                speak_code = tts.speak(text, TextToSpeech.QUEUE_FLUSH, params)
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
        self._init_status = None

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

BASE_THEMES = copy.deepcopy(THEMES)
LIGHT_THEMES = [
    {"accent": "#2563C7", "accent2": "#4F7BFF", "background": "#CDD6E4", "panel": "#DCE4EF", "text": "#0F1E38"},
    {"accent": "#0A7FA6", "accent2": "#1A8FB0", "background": "#CBDDE3", "panel": "#DAE7EC", "text": "#0B2230"},
    {"accent": "#0A74A6", "accent2": "#2B7FC0", "background": "#CCDCE6", "panel": "#DBE6EE", "text": "#08202E"},
]
UI_LIGHT = False


def apply_mode(light):
    """Light/dark mode: the theme dictionaries are updated in place, every screen reads them when it is built."""
    global UI_LIGHT
    UI_LIGHT = bool(light)
    for i, base in enumerate(BASE_THEMES):
        THEMES[i].update(base)
        if UI_LIGHT:
            THEMES[i].update(LIGHT_THEMES[i])


def muted():
    return "#5E7194" if UI_LIGHT else "#8FA6C8"


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
        "voice_preparing": "Овоз омода мешавад…",
        "attach_ready": "Фармон нависед ва тугмаи фиристоданро зер кунед",
        "sec_profile": "Дар бораи ман",
        "title_profile": "Дар бораи ман",
        "profile_note": "Ман ҳар чизеро, ки дар бораи худатон нависед (ном, шаҳр, кор, дӯстдоштаҳо), дар хотир дорам. Ин танҳо дар телефони шумо захира мешавад.",
        "profile_empty": "Ҳоло чизе нест. Нависед «Номи ман …» ё «Ман дар … зиндагӣ мекунам», ё дар поён илова кунед.",
        "profile_hint": "Чизе дар бораи худатон нависед",
        "profile_add": "Илова кардан",
        "sec_mode": "Равшан / торик",
        "title_mode": "Намуд",
        "mode_auto": "Мувофиқи система",
        "mode_dark": "Торик",
        "mode_light": "Равшан",
        "ob_or": "ё",
        "file_waiting": "Файл қабул шуд. Бо он чӣ кор кунам? (масалан: таҳлил кун, матнро хон, таҳрир кун)",
        "online": "Система фаъол аст",
        "welcome": "Ассалому алайкум! Хуш омадед. Чӣ хизмате аз дастам меояд?",
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
        "tajik_url_hint": "Овози тоҷикӣ",
        "tajik_voice_failed": "Овози тоҷикӣ ҳоло дастнорас аст (сервер шояд бедор шуда истодааст). Ҷавоб бе овоз монд, баъди як дақиқа дубора кӯшиш кунед.",
        "media_image": "Акс месозам, 20–60 сония интизор шавед…",
        "media_edit": "Таҳрир мекунам…",
        "media_search": "Аксҳоро меҷӯям…",
        "media_file": "Файл месозам…",
        "image_failed": "Акс сохта нашуд: {}",
        "edit_failed": "Таҳрир нашуд: {}",
        "search_none": "Аксе ёфт нашуд.",
        "search_title": "Аксҳо аз Wikimedia Commons (литсензияи озод):",
        "need_prompt": "Нависед, ки чӣ гуна. Масалан: «акс бисоз: шаби кӯҳсор».",
        "image_ready": "Акс тайёр аст.",
        "edit_ready": "Таҳрир шуд: {}",
        "file_created": "Файл сохта шуд: {}",
        "file_edited": "Файл таҳрир шуд: {}",
        "save_btn": "Захира",
        "d_sending": "Фиристода истодааст…",
        "d_ok": "Фиристода шуд",
        "d_fail": "Фиристода нашуд",
        "retry_btn": "Такрор",
        "open_btn": "Кушодан",
        "remix_ready": "Акси нав омода шуд. Он аз рӯи акси шумо ва хостаи шумо сохта шудааст, на нусхаи айнан.",
        "view_copy": "Нусха",
        "view_download": "Зеркашӣ",
        "view_copied": "Нусха гирифта шуд",
        "view_size": "Андоза",
        "view_type": "Навъ",
        "view_nopreview": "Намоиши пешакӣ барои ин файл нест",
        "view_actions": "Амалҳо",
        "view_failed": "Кушодан натавонист",
        "pf_title": "Бо шумо наздиктар шавем",
        "pf_first": "Ном",
        "pf_last": "Насаб",
        "pf_age": "Сол ё синну сол (масалан 1998 ё 27)",
        "pf_male": "Мард",
        "pf_female": "Зан",
        "pf_skip": "Баъдтар",
        "pf_note": "Ин маълумот танҳо дар телефони шумо нигоҳ дошта мешавад.",
        "pf_err": "Ном ва насабро нависед",
        "saved_to": "Захира шуд: {}",
        "save_failed": "Захира нашуд: {}",
        "err_network": "Интернет нест ё хеле суст аст. Пайвастшавиро санҷед.",
        "err_timeout": "Вақти интизорӣ тамом шуд. Сервер шояд ҳоло бедор шуда истодааст, баъди як дақиқа дубора кӯшиш кунед.",
        "err_busy": "Сервер ҳоло банд аст ё ҷавоб намедиҳад. Баъдтар дубора кӯшиш кунед.",
        "err_unsupported": "Ин навъи файл дастгирӣ намешавад.",
        "err_cannot": "Ин амалро бо абзорҳои ройгон иҷро кардан мумкин нест.",
        "err_too_many": "Дархостҳо аз ҳад зиёд ё ҳадди рӯзона тамом шуд. Баъдтар дубора кӯшиш кунед.",
        "err_bad": "Дархост нодуруст аст.",
        "err_generic": "Хатои номаълум рух дод. Дубора кӯшиш кунед.",
        "tts_no_tajik": "Дар телефон овози тоҷикӣ нест, барои ҳамин ҷавоб бе овоз монд.",
        "ob_name_title": "Ёрдамчии худро чӣ ном мегузоред?",
        "ob_name_hint": "Масалан: S.A.I",
        "ob_name_note": "Ин ном дар ҳама ҷо нишон дода мешавад ва AI бо ҳамин ном худро муаррифӣ мекунад.",
        "ob_continue": "Идома",
        "ob_account_title": "Ворид шудан",
        "ob_login_tab": "Аккаунти ман",
        "ob_signup_tab": "Аккаунти нав",
        "ob_username": "Номи аккаунт",
        "ob_password": "Парол",
        "ob_password2": "Паролро такрор кунед",
        "ob_login_btn": "Ворид шудан",
        "ob_signup_btn": "Сохтани аккаунт",
        "ob_google_btn": "Бо Google",
        "ob_google_soon": "Воридшавӣ бо Google ба зудӣ илова мешавад. Ҳоло номи аккаунт ва паролро истифода баред.",
        "ob_wait": "Интизор шавед… (сервер шояд бедор шавад, то як дақиқа)",
        "ob_err_empty": "Ном ва паролро пур кунед.",
        "ob_err_username": "Ном: аз 3 то 32 ҳарф, рақам, нуқта, тире ё зерхат (бе фосила).",
        "ob_err_short": "Парол бояд на камтар аз 8 аломат бошад.",
        "ob_err_mismatch": "Паролҳо мувофиқ нестанд.",
        "ob_err_wrong": "Ном ё парол нодуруст аст.",
        "ob_err_exists": "Ин ном аллакай гирифта шудааст. Номи дигар интихоб кунед.",
        "ob_welcome": "Хуш омадед, {}! Ҳисоби шумо омода аст. Чӣ хизмате аз дастам меояд?",
        "acct_line": "Аккаунт: {}",
        "acct_rename": "Тағйири номи ёрдамчӣ",
        "acct_logout": "Баромадан аз аккаунт",
        "acct_delete": "Нест кардани аккаунт",
        "acct_delete_ask": "Аккаунт барои ҳамеша нест карда мешавад. Идома медиҳед?",
        "acct_delete_yes": "Ҳа, нест кун",
        "acct_deleted": "Аккаунт нест карда шуд.",
        "session_expired": "Сессия тамом шуд. Лутфан аз нав ворид шавед.",
        "server_save": "Сабт кардан",
        "server_saved": "Токен сабт шуд. Паёмро санҷед.",
        "server_missing": "Аввал ба аккаунти худ ворид шавед (Танзимот → Ҳисоб).",
        "server_thinking": "JARVIS фикр мекунад...",
        "server_error": "Ба сервер пайваст нашуд. Интернет ва танзимоти Render-ро санҷед.",
        "mic_denied": "Иҷозати микрофон дода нашуд.",
        "lang_changed": "Забон иваз шуд: {}",
        "theme_changed": "Интерфейс: {}",
        "cmd_error": "Хатогӣ рух дод.",
        "info_error": "Маълумот гирифта нашуд. Интернетро санҷед ва дубора кӯшиш кунед.",
        "prayer_title": "Вақти намоз — Душанбе",
        "pr_Fajr": "Бомдод",
        "pr_Sunrise": "Офтоббаромад",
        "pr_Dhuhr": "Пешин",
        "pr_Asr": "Аср",
        "pr_Maghrib": "Шом",
        "pr_Isha": "Хуфтан",
        "hijri": "Санаи ҳиҷрӣ",
        "weather_title": "Ҳаво — Душанбе",
        "feels": "ҳис мешавад",
        "humidity": "намӣ",
        "wind": "бод",
        "wind_unit": "км/соат",
        "wc_clear": "Соф, офтобӣ",
        "wc_cloud": "Қисман абрнок",
        "wc_overcast": "Абрнок",
        "wc_fog": "Туман",
        "wc_drizzle": "Борони майда",
        "wc_rain": "Борон",
        "wc_snow": "Барф",
        "wc_showers": "Раъду барқ ва борон",
        "wc_thunder": "Раъду барқ",
        "rates_title": "Қурби асъор (бозорӣ, на расмии БМТ)",
        "rates_note": "1 воҳид = сомонӣ",
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
        "voice_preparing": "Готовлю голос…",
        "attach_ready": "Напишите, что сделать, и нажмите «Отправить»",
        "sec_profile": "Обо мне",
        "title_profile": "Обо мне",
        "profile_note": "Я запоминаю всё, что вы пишете о себе (имя, город, работа, увлечения). Это хранится только на вашем телефоне.",
        "profile_empty": "Пока пусто. Напишите «Меня зовут …» или «Я живу в …», либо добавьте ниже.",
        "profile_hint": "Напишите что-нибудь о себе",
        "profile_add": "Добавить",
        "sec_mode": "Светлая / тёмная",
        "title_mode": "Режим",
        "mode_auto": "Как в системе",
        "mode_dark": "Тёмный",
        "mode_light": "Светлый",
        "ob_or": "или",
        "file_waiting": "Файл получен. Что с ним сделать? (например: проанализируй, прочитай текст, отредактируй)",
        "online": "Система активна",
        "welcome": "Здравствуйте! Рад вас видеть. Чем могу быть вам полезен?",
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
        "tajik_url_hint": "Адрес таджикского голоса (https://…hf.space)",
        "tajik_voice_failed": "Таджикский голос пока недоступен (сервер, возможно, просыпается). Ответ не озвучен, повторите через минуту.",
        "media_image": "Рисую, подождите 20–60 секунд…",
        "media_edit": "Редактирую…",
        "media_search": "Ищу картинки…",
        "media_file": "Создаю файл…",
        "image_failed": "Не удалось создать изображение: {}",
        "edit_failed": "Не удалось отредактировать: {}",
        "search_none": "Картинки не найдены.",
        "search_title": "Картинки с Wikimedia Commons (свободные лицензии):",
        "need_prompt": "Напишите, что именно. Например: «нарисуй: ночные горы».",
        "image_ready": "Изображение готово.",
        "edit_ready": "Отредактировано: {}",
        "file_created": "Файл создан: {}",
        "file_edited": "Файл отредактирован: {}",
        "save_btn": "Сохранить",
        "d_sending": "Отправляется…",
        "d_ok": "Отправлено",
        "d_fail": "Не отправлено",
        "retry_btn": "Повторить",
        "open_btn": "Открыть",
        "remix_ready": "Новое изображение готово. Оно создано по вашему снимку и описанию, это не точная копия.",
        "view_copy": "Копировать",
        "view_download": "Скачать",
        "view_copied": "Скопировано",
        "view_size": "Размер",
        "view_type": "Тип",
        "view_nopreview": "Для этого файла нет предпросмотра",
        "view_actions": "Действия",
        "view_failed": "Не удалось открыть",
        "pf_title": "Давайте познакомимся",
        "pf_first": "Имя",
        "pf_last": "Фамилия",
        "pf_age": "Год рождения или возраст (например 1998 или 27)",
        "pf_male": "Мужской",
        "pf_female": "Женский",
        "pf_skip": "Позже",
        "pf_note": "Эти данные хранятся только на вашем телефоне.",
        "pf_err": "Введите имя и фамилию",
        "saved_to": "Сохранено: {}",
        "save_failed": "Не удалось сохранить: {}",
        "err_network": "Нет интернета или он очень медленный. Проверьте подключение.",
        "err_timeout": "Время ожидания истекло. Возможно, сервер ещё просыпается, повторите через минуту.",
        "err_busy": "Сервер сейчас занят или не отвечает. Попробуйте позже.",
        "err_unsupported": "Этот тип файла не поддерживается.",
        "err_cannot": "Это действие нельзя выполнить бесплатными инструментами.",
        "err_too_many": "Слишком много запросов или дневной лимит исчерпан. Попробуйте позже.",
        "err_bad": "Запрос некорректен.",
        "err_generic": "Произошла неизвестная ошибка. Попробуйте снова.",
        "tts_no_tajik": "На телефоне нет таджикского голоса, поэтому ответ не озвучен.",
        "ob_name_title": "Как назовёте своего помощника?",
        "ob_name_hint": "Например: S.A.I",
        "ob_name_note": "Это имя будет показано везде, и ИИ будет представляться этим именем.",
        "ob_continue": "Продолжить",
        "ob_account_title": "Вход",
        "ob_login_tab": "Мой аккаунт",
        "ob_signup_tab": "Новый аккаунт",
        "ob_username": "Имя аккаунта",
        "ob_password": "Пароль",
        "ob_password2": "Повторите пароль",
        "ob_login_btn": "Войти",
        "ob_signup_btn": "Создать аккаунт",
        "ob_google_btn": "Через Google",
        "ob_google_soon": "Вход через Google скоро появится. Пока используйте имя аккаунта и пароль.",
        "ob_wait": "Подождите… (сервер может просыпаться до минуты)",
        "ob_err_empty": "Заполните имя и пароль.",
        "ob_err_username": "Имя: от 3 до 32 букв, цифр, точек, дефисов или подчёркиваний (без пробелов).",
        "ob_err_short": "Пароль должен быть не короче 8 символов.",
        "ob_err_mismatch": "Пароли не совпадают.",
        "ob_err_wrong": "Неверное имя или пароль.",
        "ob_err_exists": "Это имя уже занято. Выберите другое.",
        "ob_welcome": "Добро пожаловать, {}! Ваш аккаунт готов. Чем могу быть полезен?",
        "acct_line": "Аккаунт: {}",
        "acct_rename": "Изменить имя помощника",
        "acct_logout": "Выйти из аккаунта",
        "acct_delete": "Удалить аккаунт",
        "acct_delete_ask": "Аккаунт будет удалён навсегда. Продолжить?",
        "acct_delete_yes": "Да, удалить",
        "acct_deleted": "Аккаунт удалён.",
        "session_expired": "Сессия истекла. Пожалуйста, войдите снова.",
        "server_save": "Сохранить",
        "server_saved": "Токен сохранён. Проверьте отправку сообщения.",
        "server_missing": "Сначала войдите в свой аккаунт (Настройки → Аккаунт).",
        "server_thinking": "JARVIS думает...",
        "server_error": "Не удалось подключиться к серверу. Проверьте интернет и настройки Render.",
        "mic_denied": "Доступ к микрофону не предоставлен.",
        "lang_changed": "Язык изменён: {}",
        "theme_changed": "Интерфейс: {}",
        "cmd_error": "Произошла ошибка.",
        "info_error": "Не удалось получить данные. Проверьте интернет и попробуйте снова.",
        "prayer_title": "Время намаза — Душанбе",
        "pr_Fajr": "Фаджр",
        "pr_Sunrise": "Восход",
        "pr_Dhuhr": "Зухр",
        "pr_Asr": "Аср",
        "pr_Maghrib": "Магриб",
        "pr_Isha": "Иша",
        "hijri": "Дата по хиджре",
        "weather_title": "Погода — Душанбе",
        "feels": "ощущается",
        "humidity": "влажность",
        "wind": "ветер",
        "wind_unit": "км/ч",
        "wc_clear": "Ясно",
        "wc_cloud": "Переменная облачность",
        "wc_overcast": "Пасмурно",
        "wc_fog": "Туман",
        "wc_drizzle": "Морось",
        "wc_rain": "Дождь",
        "wc_snow": "Снег",
        "wc_showers": "Ливни",
        "wc_thunder": "Гроза",
        "rates_title": "Курсы валют (рыночные, не официальные НБТ)",
        "rates_note": "1 единица = сомони",
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
        "voice_preparing": "Preparing voice…",
        "attach_ready": "Write what to do and press Send",
        "sec_profile": "About me",
        "title_profile": "About me",
        "profile_note": "I remember what you tell me about yourself (name, city, work, interests). It is stored only on your phone.",
        "profile_empty": "Nothing yet. Write: My name is … or I live in …, or add below.",
        "profile_hint": "Write something about yourself",
        "profile_add": "Add",
        "sec_mode": "Light / dark",
        "title_mode": "Mode",
        "mode_auto": "Same as system",
        "mode_dark": "Dark",
        "mode_light": "Light",
        "ob_or": "or",
        "file_waiting": "File received. What should I do with it? (for example: analyze, read the text, edit)",
        "online": "System online",
        "welcome": "Hello, and welcome! How may I assist you today?",
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
        "tajik_url_hint": "Tajik voice address (https://…hf.space)",
        "tajik_voice_failed": "The Tajik voice is not available yet (the server may be waking up). The answer was not read aloud; try again in a minute.",
        "media_image": "Drawing, please wait 20–60 seconds…",
        "media_edit": "Editing…",
        "media_search": "Searching pictures…",
        "media_file": "Creating the file…",
        "image_failed": "Could not create the image: {}",
        "edit_failed": "Could not edit: {}",
        "search_none": "No pictures found.",
        "search_title": "Pictures from Wikimedia Commons (free licences):",
        "need_prompt": "Say what exactly. For example: \"draw: mountains at night\".",
        "image_ready": "The image is ready.",
        "edit_ready": "Edited: {}",
        "file_created": "File created: {}",
        "file_edited": "File edited: {}",
        "save_btn": "Save",
        "d_sending": "Sending…",
        "d_ok": "Sent",
        "d_fail": "Not sent",
        "retry_btn": "Retry",
        "open_btn": "Open",
        "remix_ready": "Your new picture is ready. It is made from your photo and your description, not an exact copy.",
        "view_copy": "Copy",
        "view_download": "Download",
        "view_copied": "Copied",
        "view_size": "Size",
        "view_type": "Type",
        "view_nopreview": "No preview for this file",
        "view_actions": "Actions",
        "view_failed": "Could not open",
        "pf_title": "Let us get acquainted",
        "pf_first": "First name",
        "pf_last": "Last name",
        "pf_age": "Birth year or age (e.g. 1998 or 27)",
        "pf_male": "Male",
        "pf_female": "Female",
        "pf_skip": "Later",
        "pf_note": "This information stays on your phone only.",
        "pf_err": "Enter first and last name",
        "saved_to": "Saved: {}",
        "save_failed": "Could not save: {}",
        "err_network": "No internet or it is very slow. Check your connection.",
        "err_timeout": "The request timed out. The server may still be waking up, try again in a minute.",
        "err_busy": "The server is busy or not responding. Try again later.",
        "err_unsupported": "This file type is not supported.",
        "err_cannot": "This cannot be done with the free tools.",
        "err_too_many": "Too many requests or the daily limit is used up. Try again later.",
        "err_bad": "The request is not valid.",
        "err_generic": "An unknown error occurred. Try again.",
        "tts_no_tajik": "The phone has no Tajik voice, so the answer was not read aloud.",
        "ob_name_title": "What will you name your assistant?",
        "ob_name_hint": "For example: S.A.I",
        "ob_name_note": "This name is shown everywhere and the AI introduces itself with it.",
        "ob_continue": "Continue",
        "ob_account_title": "Sign in",
        "ob_login_tab": "My account",
        "ob_signup_tab": "New account",
        "ob_username": "Account name",
        "ob_password": "Password",
        "ob_password2": "Repeat the password",
        "ob_login_btn": "Sign in",
        "ob_signup_btn": "Create account",
        "ob_google_btn": "With Google",
        "ob_google_soon": "Google sign-in is coming soon. For now use an account name and password.",
        "ob_wait": "Please wait… (the server may need up to a minute to wake up)",
        "ob_err_empty": "Fill in the name and password.",
        "ob_err_username": "Name: 3 to 32 letters, digits, dots, dashes or underscores (no spaces).",
        "ob_err_short": "The password must be at least 8 characters.",
        "ob_err_mismatch": "The passwords do not match.",
        "ob_err_wrong": "Wrong name or password.",
        "ob_err_exists": "This name is already taken. Choose another.",
        "ob_welcome": "Welcome, {}! Your account is ready. How may I assist you today?",
        "acct_line": "Account: {}",
        "acct_rename": "Change the assistant's name",
        "acct_logout": "Sign out",
        "acct_delete": "Delete account",
        "acct_delete_ask": "The account will be deleted permanently. Continue?",
        "acct_delete_yes": "Yes, delete",
        "acct_deleted": "The account was deleted.",
        "session_expired": "The session expired. Please sign in again.",
        "server_save": "Save",
        "server_saved": "Token saved. Try sending a message.",
        "server_missing": "First sign in to your account (Settings → Account).",
        "server_thinking": "JARVIS is thinking...",
        "server_error": "Could not connect to the server. Check internet and Render settings.",
        "mic_denied": "No mic permission.",
        "lang_changed": "Language: {}",
        "theme_changed": "Theme: {}",
        "cmd_error": "Error.",
        "info_error": "Could not get the data. Check the internet and try again.",
        "prayer_title": "Prayer times — Dushanbe",
        "pr_Fajr": "Fajr",
        "pr_Sunrise": "Sunrise",
        "pr_Dhuhr": "Dhuhr",
        "pr_Asr": "Asr",
        "pr_Maghrib": "Maghrib",
        "pr_Isha": "Isha",
        "hijri": "Hijri date",
        "weather_title": "Weather — Dushanbe",
        "feels": "feels like",
        "humidity": "humidity",
        "wind": "wind",
        "wind_unit": "km/h",
        "wc_clear": "Clear",
        "wc_cloud": "Partly cloudy",
        "wc_overcast": "Overcast",
        "wc_fog": "Fog",
        "wc_drizzle": "Drizzle",
        "wc_rain": "Rain",
        "wc_snow": "Snow",
        "wc_showers": "Showers",
        "wc_thunder": "Thunderstorm",
        "rates_title": "Exchange rates (market, not official NBT)",
        "rates_note": "1 unit = somoni",
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


# ---------------------------------------------------------------------------------------------
# Full-colour emoji in chat bubbles. Kivy cannot draw colour emoji fonts, so the emoji are taken as
# small PNG pictures from assets/emoji.dat (built from Noto Color Emoji) and drawn over blank spaces
# that EmojiLabel reserves inside the text. Without the file the monochrome font is used instead.
# ---------------------------------------------------------------------------------------------
_EMOJI = {"index": None, "data": None, "tried": False}
_EMOJI_TEX = {}
NBSP = " "
# Emoji_Presentation=Yes characters below U+1F000 (everything above is treated as an emoji already)
_EMOJI_DEFAULT_RANGES = ((0x231A, 0x231B), (0x23E9, 0x23EC), (0x23F0, 0x23F0), (0x23F3, 0x23F3),
                         (0x25FD, 0x25FE), (0x2614, 0x2615), (0x2648, 0x2653), (0x267F, 0x267F),
                         (0x2693, 0x2693), (0x26A1, 0x26A1), (0x26AA, 0x26AB), (0x26BD, 0x26BE),
                         (0x26C4, 0x26C5), (0x26CE, 0x26CE), (0x26D4, 0x26D4), (0x26EA, 0x26EA),
                         (0x26F2, 0x26F3), (0x26F5, 0x26F5), (0x26FA, 0x26FA), (0x26FD, 0x26FD),
                         (0x2705, 0x2705), (0x270A, 0x270B), (0x2728, 0x2728), (0x274C, 0x274C),
                         (0x274E, 0x274E), (0x2753, 0x2755), (0x2757, 0x2757), (0x2795, 0x2797),
                         (0x27B0, 0x27B0), (0x27BF, 0x27BF), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50),
                         (0x2B55, 0x2B55),
                         # text-style by default in Unicode, but nearly always meant as a coloured emoji
                         (0x2600, 0x2601), (0x263A, 0x263A), (0x2639, 0x2639), (0x26A0, 0x26A0),
                         (0x270C, 0x270C), (0x2714, 0x2714), (0x2744, 0x2744), (0x2763, 0x2764))


def _emoji_default(cp):
    return cp >= 0x1F000 or any(a <= cp <= b for a, b in _EMOJI_DEFAULT_RANGES)


def emoji_pack():
    """Load assets/emoji.dat once: returns (index, bytes) or (None, None) when it is missing."""
    if _EMOJI["tried"]:
        return _EMOJI["index"], _EMOJI["data"]
    _EMOJI["tried"] = True
    try:
        import struct
        folders = [os.path.dirname(os.path.abspath(__file__)), os.getcwd(),
                   "/storage/emulated/0/Download", "/storage/emulated/0/JARVIS", "/sdcard/Download"]
        for folder in folders:
            for path in (os.path.join(folder, "assets", "emoji.dat"), os.path.join(folder, "emoji.dat")):
                if os.path.isfile(path):
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    if raw[:4] != b"JEMO":
                        continue
                    n = struct.unpack("<I", raw[4:8])[0]
                    _EMOJI["index"] = json.loads(raw[8:8 + n].decode("utf-8"))
                    _EMOJI["data"] = raw[8 + n:]
                    return _EMOJI["index"], _EMOJI["data"]
    except Exception:
        save_crash("Emoji pack failed:\n" + traceback.format_exc())
    return None, None


def emoji_texture(key):
    if key in _EMOJI_TEX:
        return _EMOJI_TEX[key]
    tex = None
    try:
        index, data = emoji_pack()
        if index and key in index:
            import io
            from kivy.core.image import Image as CoreImage
            off, length = index[key]
            tex = CoreImage(io.BytesIO(data[off:off + length]), ext="png", nocache=True).texture
    except Exception:
        save_crash("Emoji texture failed:\n" + traceback.format_exc())
        tex = None
    _EMOJI_TEX[key] = tex
    return tex


def split_emoji(text):
    """Split text into [("t", text) | ("e", key)] using the longest emoji sequence at each position
    (skin tones, flags, keycaps and ZWJ families are one picture)."""
    index, _data = emoji_pack()
    if not index:
        return [("t", text)]
    out, buf, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        o = ord(c)
        if o < 0x203C and c not in "#*0123456789":
            buf.append(c)
            i += 1
            continue
        cps, j, best = [], i, None
        while j < n and len(cps) < 12:
            cj = ord(text[j])
            if cj != 0xFE0F:
                cps.append(cj)
            j += 1
            key = "_".join("%x" % x for x in cps)
            if key in index:
                best = (j, key, len(cps))
            if j >= n:
                break
            nx = ord(text[j])
            more = (nx in (0xFE0F, 0x200D, 0x20E3) or 0x1F3FB <= nx <= 0x1F3FF or 0xE0020 <= nx <= 0xE007F
                    or cj == 0x200D or (len(cps) == 1 and 0x1F1E6 <= cps[0] <= 0x1F1FF and 0x1F1E6 <= nx <= 0x1F1FF))
            if not more:
                break
        if best is not None:
            end, key, count = best
            piece = text[i:end]
            if count == 1 and not _emoji_default(int(key, 16)) and "️" not in piece:
                best = None      # a plain symbol such as an arrow: keep it as text
        if best is None:
            buf.append(c)
            i += 1
            continue
        if buf:
            out.append(("t", "".join(buf)))
            buf = []
        out.append(("e", best[1]))
        i = best[0]
    if buf:
        out.append(("t", "".join(buf)))
    return out


def markup_escape(s):
    return s.replace("&", "&amp;").replace("[", "&bl;").replace("]", "&br;")


_TLDS = "com|org|net|io|ru|tj|uz|kz|kg|info|me|app|dev|ai|edu|gov|co|ly|tv|xyz|site|online|store|shop|by|ua|de|uk|fr|eu|us|cc|pro|biz"
LINK_RE = re.compile(
    r"\[([^\]\n]{1,120})\]\((https?://[^)\s]+)\)"                                   # [text](https://…)
    r"|((?:https?://|www\.)[^\s<>\"'«»]+)"                                              # https://… , www.…
    r"|([\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"                       # e-mail
    r"|(\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:" + _TLDS + r")\b(?:/[^\s<>\"'«»]*)?)")   # site.com/path


def open_url(url):
    """Open a link in the phone's browser / mail app."""
    url = url.strip()
    if "@" in url and not url.lower().startswith(("http", "mailto:")) and "/" not in url:
        url = "mailto:" + url
    elif not re.match(r"^[a-z]+:", url, re.I):
        url = "https://" + url
    try:
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        activity.startActivity(intent)
    except Exception:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            save_crash("Open link failed:\n" + traceback.format_exc())


def link_markup(text, links):
    """Escape text for markup and turn links into coloured, underlined, tappable [ref] pieces."""
    try:
        acc = THEMES[App.get_running_app().theme_index]["accent"].lstrip("#")
    except Exception:
        acc = "5CB8FF"
    out, last = [], 0
    for m in LINK_RE.finditer(text):
        shown, url = m.group(0), None
        if m.group(1):
            shown, url = m.group(1), m.group(2)
        else:
            shown = m.group(0)
            trail = ""
            while shown and shown[-1] in ".,;:!?)]}»\"'":
                trail = shown[-1] + trail
                shown = shown[:-1]
            url = shown
            if not shown:
                continue
        out.append(markup_escape(text[last:m.start()]))
        out.append("[color=%s][u][ref=l%d]%s[/ref][/u][/color]" % (acc, len(links), markup_escape(shown)))
        links.append(url)
        last = m.start() + len(m.group(0)) - (len(trail) if not m.group(1) else 0)
    out.append(markup_escape(text[last:]))
    return "".join(out)


class EmojiLabel(Label):
    """A Label whose emoji are shown as full-colour pictures. Set the text with set_plain()."""

    def __init__(self, **kwargs):
        kwargs["markup"] = True
        super().__init__(**kwargs)
        self._emoji = []
        self._plain = ""
        self._nb = None
        self._links = []
        self.bind(texture=self._draw_emoji, texture_size=self._draw_emoji, pos=self._draw_emoji,
                  size=self._draw_emoji)
        self.bind(on_ref_press=self._ref_pressed)

    def _ref_pressed(self, label, ref):
        if ref.startswith("l") and ref[1:].isdigit() and int(ref[1:]) < len(self._links):
            open_url(self._links[int(ref[1:])])

    def _nbsp_count(self):
        if self._nb is None:
            try:
                from kivy.core.text import Label as CoreLabel
                w = CoreLabel(font_size=int(self.font_size)).get_extents(NBSP * 10)[0] / 10.0
                self._nb = max(2, int(round(1.25 * self.font_size / max(w, 1.0))))
            except Exception:
                self._nb = 5
        return self._nb

    def set_plain(self, text):
        self._plain = str(text)
        self._links = []
        index, _data = emoji_pack()
        if not index:
            self._emoji = []
            self.text = link_markup(self._plain, self._links)
            return
        parts, keys = [], []
        for kind, value in split_emoji(self._plain):
            if kind == "t":
                parts.append(link_markup(value, self._links))
            else:
                parts.append("[ref=e%d]%s[/ref]" % (len(keys), NBSP * self._nbsp_count()))
                keys.append(value)
        self._emoji = keys
        self.text = "".join(parts)

    def _draw_emoji(self, *_):
        self.canvas.after.clear()
        if not self._emoji or self.texture is None:
            return
        tw, th = self.texture_size
        left = int(self.center_x - tw / 2.0)
        top = int(self.center_y + th / 2.0)
        with self.canvas.after:
            Color(1, 1, 1, 1)
            for i, key in enumerate(self._emoji):
                boxes = self.refs.get("e%d" % i)
                tex = emoji_texture(key)
                if not boxes or tex is None:
                    continue
                x1, y1, x2, y2 = boxes[0]
                side = min((x2 - x1) * 0.94, (y2 - y1) * 0.96)
                cx = left + (x1 + x2) / 2.0
                cy = top - (y1 + y2) / 2.0
                Rectangle(texture=tex, pos=(cx - side / 2.0, cy - side / 2.0), size=(side, side))


def set_label_text(label, text):
    """Set the text of a chat label (EmojiLabel shows colour emoji; a plain Label just takes the text)."""
    if isinstance(label, EmojiLabel):
        label.set_plain(text)
    else:
        label.text = text

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

FACT_PATTERNS = [re.compile(x, re.IGNORECASE) for x in (
    # Tajik
    r"номи ман\b", r"^\s*ман\s+.{2,}\s(ҳастам|будам|зиндагӣ мекунам|кор мекунам|мехонам|мехонам|дорам|дӯст медорам|таваллуд шудам)\b",
    r"\b(ёд гир|дар ёд дор|фаромӯш накун|дар хотир дор)\b",
    # Russian
    r"меня зовут", r"^\s*я\s+.{2,}\s(живу|работаю|учусь|люблю|занимаюсь|родил(ся|ась)|студент|врач|программист)\b",
    r"\b(запомни|не забудь)\b", r"\bмо(й|я|и)\s+\w+\s+(—|-|это)\s",
    # English
    r"\bmy name is\b", r"^\s*i\s+(am|live|work|study|like|love|have|was born)\b", r"\bremember (that|this)\b",
)]


def extract_facts(text):
    """Sentences in which the user tells something lasting about himself (no questions)."""
    facts = []
    for sentence in re.split(r"(?<=[.!\n])\s+", str(text)):
        sentence = " ".join(sentence.split())
        if not (4 <= len(sentence) <= 220) or sentence.endswith("?"):
            continue
        if any(rx.search(sentence) for rx in FACT_PATTERNS):
            facts.append(sentence)
    return facts


GREETINGS = {
    "tg": {
        "morning": ["Субҳатон бахайр{n}! Хуш омадед. Чӣ хизмате аз дастам меояд?",
                    "Субҳатон бахайр{n}! Аз дидани шумо шодам. Бигӯед, чӣ тавр ёрӣ расонам?"],
        "day": ["Рӯзатон бахайр{n}! Хуш омадед. Бигӯед, ба шумо чӣ тавр ёрӣ расонам?",
                "Ассалому алайкум{n}! Хуш омадед. Чӣ хизмате аз дастам меояд?"],
        "evening": ["Шоматон бахайр{n}! Хуш омадед. Чӣ хизмате барои шумо анҷом диҳам?",
                    "Шоматон бахайр{n}! Аз дидани шумо шодам. Бигӯед, чӣ лозим аст?"],
        "night": ["Шабатон бахайр{n}! Хуш омадед. Чӣ хизмате аз дастам меояд?",
                  "Шабатон бахайр{n}! Бигӯед, ба шумо чӣ тавр ёрӣ расонам?"],
    },
    "ru": {
        "morning": ["Доброе утро{n}! Рад вас видеть. Чем могу быть вам полезен?",
                    "Доброе утро{n}! Добро пожаловать. Чем я могу вам помочь?"],
        "day": ["Добрый день{n}! Рад вас видеть. Чем могу быть вам полезен?",
                "Здравствуйте{n}! Добро пожаловать. Чем я могу вам помочь?"],
        "evening": ["Добрый вечер{n}! Рад вас видеть. Чем могу быть вам полезен?",
                    "Добрый вечер{n}! Добро пожаловать. Что я могу для вас сделать?"],
        "night": ["Доброй ночи{n}! Рад вас видеть. Чем могу быть вам полезен?",
                  "Доброй ночи{n}! Чем я могу вам помочь?"],
    },
    "en": {
        "morning": ["Good morning{n}! Welcome back. How may I assist you today?",
                    "Good morning{n}! It is a pleasure to see you. What can I do for you?"],
        "day": ["Good afternoon{n}! Welcome back. How may I assist you today?",
                "Hello{n}, and welcome. How may I be of help?"],
        "evening": ["Good evening{n}! Welcome back. How may I assist you?",
                    "Good evening{n}! It is a pleasure to see you. What can I do for you?"],
        "night": ["Good evening{n}! Welcome back. How may I assist you?",
                  "Hello{n}, and welcome. How may I be of help tonight?"],
    },
}

NAME_PATTERNS = [re.compile(x, re.IGNORECASE) for x in (
    r"номи ман\s+([A-Za-zА-Яа-яЁёҒғӢӣҚқӮӯҲҳҶҷ\-]{2,20})", r"меня зовут\s+([A-Za-zА-Яа-яЁё\-]{2,20})",
    r"my name is\s+([A-Za-z\-]{2,20})")]


def greeting_for(language, facts, hour):
    """A respectful greeting for the time of day, with the user's first name when he has told it."""
    name = ""
    for fact in reversed(facts):
        for rx in NAME_PATTERNS:
            m = rx.search(fact)
            if m:
                name = m.group(1).strip().capitalize()
                break
        if name:
            break
    period = "morning" if 5 <= hour < 11 else "day" if 11 <= hour < 17 else "evening" if 17 <= hour < 22 else "night"
    options = GREETINGS.get(language, GREETINGS["tg"])[period]
    return random.choice(options).replace("{n}", (", " + name) if name else "")


class ProfileStore(object):
    """The "about the user" folder: facts the user told the assistant. Stored only on this phone."""

    def __init__(self, path):
        self.path = path
        self.facts = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                self.facts = [f for f in json.load(handle).get("facts", []) if isinstance(f, dict) and f.get("text")]
        except Exception:
            self.facts = []

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"facts": self.facts}, handle, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def add(self, text):
        text = " ".join(str(text).split())[:220]
        if not text or any(f["text"].lower() == text.lower() for f in self.facts):
            return False
        self.facts.append({"text": text, "time": datetime.now().strftime("%Y-%m-%d")})
        self.facts = self.facts[-80:]
        self.save()
        return True

    def remove(self, index):
        if 0 <= index < len(self.facts):
            del self.facts[index]
            self.save()


class ChatStore(object):
    def __init__(self, path=None):
        self.path = path
        self.chats = []
        self.current_id = None
        self.deleted = []         # ids removed on this phone, still to be removed from the account
        self.synced = {}          # chat id -> signature last saved to the account
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
            if isinstance(data.get("deleted"), list):
                self.deleted = [str(x) for x in data["deleted"]][:500]
            if isinstance(data.get("synced"), dict):
                self.synced = {str(k): str(v) for k, v in data["synced"].items()}
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
                json.dump({"version": 1, "current": self.current_id, "chats": self.chats, "deleted": self.deleted, "synced": self.synced},
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

    @staticmethod
    def signature(chat):
        return "%s|%d|%s" % (chat.get("updated", ""), len(chat.get("messages", [])), chat.get("title", ""))

    def delete_chat(self, chat_id):
        if chat_id not in self.deleted:
            self.deleted.append(chat_id)
        self.synced.pop(chat_id, None)
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


# ---- Local information commands (need internet, no API keys): prayer times, weather, exchange rates.
INFO_COMMANDS = []


def info_command(name, *keywords):
    def register(fn):
        INFO_COMMANDS.append((name, tuple(k.lower() for k in keywords), fn))
        return fn
    return register


def find_info_command(text):
    for name, keywords, fn in INFO_COMMANDS:
        if command_matches(text, keywords):
            return fn
    return None



SERVER_BASE = "https://jarvis-ai-server-3vuo.onrender.com"
OLD_NAME_RE = re.compile(
    r"(?<![\w/.\-])(?:[Jj]\.?[Aa]\.?[Rr]\.?[Vv]\.?[Ii]\.?[Ss]|Джарвис|Ҷарвис|Жарвис|Чарвис|ДЖАРВИС|ҶАРВИС)"
    r"(?:[а-яёӣӯқғҳҷ]{0,3})?(?![\w\-])", re.IGNORECASE)


def dotted_name(name):
    """SAI -> S.A.I, Jarvis -> J.A.R.V.I.S (a dot after every letter, words stay apart)."""
    words = []
    for word in str(name).replace(".", "").split():
        words.append(".".join(word.upper()))
    return "  ".join(words) or "S.A.I"


MEDIA_RE = re.compile(r"\[\[(img|file):([^\]]+)\]\]")
RETRY_MARK = "[[retry]]"
BASE_WORDS = {
    "tg": "Салом Ташаккур Лутфан Мехоҳам Метавонед Метавонам Фармон Барнома Кӯмак Хотира Забон Имрӯз Фардо Дирӯз Ҳоло Чаро Куҷо Ҳамеша Хуб Бале Ҳаво Курс Нақша Тарҷума Нависед Бигӯед Бисоз Бикаш Ёбед Кушоед Зеркашӣ Паёмнависӣ Сомонӣ Доллар Рубл Шаҳр Душанбе Хуҷанд Тоҷикистон Тоҷикӣ Русӣ Англисӣ Маълумот Пурсиш Ҷавоб Саволи Мактаб Донишгоҳ Кор Оила Дӯст Ғизо Муҳаббат Сафар Мусиқӣ Филм Китоб Хондан Навиштан Шунидан Гуфтан Фаҳмондан Кӯтоҳ Пурра Тафсилот Мисол".split(),
    "ru": "Привет Здравствуйте Спасибо Пожалуйста Хочу Можете Можно Команда Приложение Помощь Память Язык Сегодня Завтра Вчера Сейчас Почему Где Всегда Хорошо Да Нет Погода Курс План Перевод Напиши Расскажи Нарисуй Найди Открой Скачать Сообщение Сомони Доллар Рубль Город Душанбе Таджикистан Таджикский Русский Английский Информация Вопрос Ответ Школа Университет Работа Семья Друг Еда Любовь Путешествие Музыка Фильм Книга Читать Писать Слушать Говорить Объясни Коротко Подробно Пример".split(),
    "en": "Hello Thanks Please Want Could Would Command Application Help Memory Language Today Tomorrow Yesterday Now Why Where Always Good Yes No Weather Rate Plan Translate Write Tell Draw Find Open Download Message Dollar City Tajikistan Tajik Russian English Information Question Answer School University Work Family Friend Food Love Travel Music Movie Book Read Listen Speak Explain Short Detailed Example".split(),
}
BUSY_PHRASES = {
    "tg": {"think": ["Интизор шавед…", "Фикр мекунам…", "Ҷавобро омода мекунам…", "Лаҳзае сабр кунед…", "Тақрибан тайёр аст…"],
           "image": ["Акс месозам…", "Интизор шавед, ин каме вақт мегирад…", "Рангҳоро мувофиқ мекунам…", "Лаҳзае сабр кунед…", "Тақрибан тайёр аст…"],
           "answer": ["Ҷавоб нишон дода мешавад…", "Лаҳзае сабр кунед…", "Ҳамааш ҳозир омода мешавад…"]},
    "ru": {"think": ["Подождите, пожалуйста…", "Думаю…", "Готовлю ответ…", "Минуточку…", "Почти готово…"],
           "image": ["Создаю изображение…", "Подождите, это займёт немного времени…", "Подбираю цвета…", "Минуточку…", "Почти готово…"],
           "answer": ["Ответ появляется…", "Минуточку…", "Сейчас всё будет готово…"]},
    "en": {"think": ["Please wait…", "Thinking…", "Preparing the answer…", "Just a moment…", "Almost ready…"],
           "image": ["Creating the picture…", "Please wait, this takes a little time…", "Matching the colours…", "Just a moment…", "Almost ready…"],
           "answer": ["The answer is appearing…", "Just a moment…", "Everything will be ready shortly…"]},
}
TEXT_FILE_EXTS = (".txt", ".md", ".csv", ".json", ".log", ".py", ".html", ".css", ".js", ".xml", ".yaml", ".yml", ".ini", ".kv")

MEDIA_PREFIXES = (
    # (kind, prefixes) – the message must START with one of them; the rest is the request
    ("image", ("акс бисоз", "акс созед", "акс кашед", "акс бикаш", "акс сохта деҳ", "тасвир бисоз", "тасвир созед", "тасвир бикаш",
               "нарисуй картинку", "нарисуй", "создай картинку", "создай изображение", "сгенерируй картинку", "сгенерируй изображение",
               "generate an image of", "generate image of", "generate an image", "generate image", "create an image of", "create image of",
               "create a picture of", "draw me", "draw")),
    ("search", ("аксҳоро ёб", "аксҳо ёб", "акс ёб", "аксҳо нишон деҳ", "акс нишон деҳ", "найди фотографии", "найди фото", "найди картинки",
                "найди картинку", "покажи фотографии", "покажи фото", "покажи картинки", "find photos of", "find pictures of", "find images of",
                "show me photos of", "show me pictures of", "show photos of", "show pictures of", "find photos", "find pictures", "find images")),
    ("file", ("файли нав бисоз", "файл бисоз", "файл созед", "файл эҷод кун", "файл эҷод кунед", "создай текстовый файл", "создай файл", "сделай файл",
              "create a file", "create file", "make a file", "make file", "write a file")),
)


_PICTURE_NOUNS = ("фотография ", "фото ", "картинка ", "картинку ", "изображение ", "рисунок ", "акси ", "расми ", "тасвири ",
                  "photo of ", "picture of ", "image of ")
_DRAW_VERBS = ("нарисуй", "сгенерируй", "создай картинку", "создай изображение", "создай фото", "сделай картинку", "сделай фото",
               "акс бисоз", "аксе бисоз", "расм бикаш", "расме бикаш", "тасвир бисоз", "акс созед", "draw me", "generate an image",
               "generate a picture", "create an image", "create a picture", "make a picture")


_PIC_WORDS = ("акс", "расм", "тасвир", "фото", "картин", "изображен", "рисунок", "photo", "picture", "image")
_GIVE_WORDS = ("деҳ", "дихед", "диҳед", "фирист", "нишон деҳ", "нишон диҳед", "хоҳам", "мехоҳам", "лозим", "дай", "дайте", "пришли", "отправь",
               "покажи", "хочу", "нужн", "give me", "send me", "show me", "i want", "i need", "need a", "want a")


_CHAT_WORDS = ("чатҳо", "чатҳои", "чатҳоро", "суҳбатҳо", "чатов", "чаты", "чатах", "переписк", "разговоры", "chats", "conversations")
_ANALYSIS_WORDS = ("таҳлил", "анализ", "analy", "ҷамъбаст", "итог", "резюм", "summar", "хулоса", "бозбинӣ", "ёд дорӣ", "помнишь", "review")


def parse_chats_analysis(text):
    low = " ".join(str(text).lower().split())
    return any(c in low for c in _CHAT_WORDS) and any(a in low for a in _ANALYSIS_WORDS)


def parse_media_intent(text):
    """('image'|'search'|'file', request) when the message starts with a media command, else None."""
    low = " ".join(str(text).lower().split())
    for kind, prefixes in MEDIA_PREFIXES:
        for prefix in sorted(prefixes, key=len, reverse=True):
            if low.startswith(prefix) and (len(low) == len(prefix) or not low[len(prefix)].isalnum()):
                rest = " ".join(str(text).split())[len(prefix):].lstrip(" :,;-–—")
                return kind, rest.strip()
    # a bare description of a picture ("photo of high mountains ...") or a drawing verb anywhere in the sentence
    plain = " ".join(str(text).split())
    if plain and not plain.endswith("?") and not low.startswith(_QUESTION_STARTS):
        if low.startswith(_PICTURE_NOUNS) and len(low.split()) >= 2:
            return "image", plain
        if any(v in low for v in _DRAW_VERBS):
            return "image", plain
        if any(n in low for n in _PIC_WORDS) and any(v in low for v in _GIVE_WORDS):
            return "image", plain
    return None


_EDIT_PREFIXES = ("таҳрир", "тағйир", "edit", "измени", "отредактируй", "исправь", "ислоҳ кун")
_QUESTION_STARTS = ("чӣ", "чи ", "ин чист", "киро", "оё", "тавсиф", "шарҳ", "опиши", "что", "кто", "объясни", "describe", "what", "explain", "who")
_TEXT_VERBS = ("таҳрир", "тағйир", "ислоҳ", "иваз", "илова", "тарҷума", "кӯтоҳ кун", "дароз кун", "ҳазф", "нест кун", "пок кун", "тоза кун",
               "нависед", "бо тартиб", "edit", "fix", "change", "replace", "add ", "translate", "rewrite", "shorten", "remove", "delete", "format",
               "исправ", "измени", "замени", "добав", "перевед", "сократи", "перепиши", "удали", "убери")
_IMAGE_VERBS = ("бурр", "чарх", "давр", "сиёҳ", "сафед", "равшан", "торик", "хира", "андоза", "калон кун", "хурд кун", "кӯчак", "инъикос",
                "мирор", "ранг", "фильтр", "сепия", "контраст", "тағйир", "таҳрир", "тез кун", "edit", "crop", "rotate", "flip", "mirror", "blur",
                "bright", "dark", "grayscale", "black", "sepia", "resize", "smaller", "bigger", "обрез", "поверн", "отзеркал", "чёрно", "чорно",
                "ярче", "темнее", "размыт", "размер", "измени", "отредакт", "цвет")


def looks_like_edit(prompt, kind):
    """Decide if an attachment request asks to CHANGE the file (True) or to analyze it (False)."""
    low = " ".join(str(prompt).lower().split())
    if not low:
        return False
    if low.startswith(_EDIT_PREFIXES):
        return True
    if low.startswith(_QUESTION_STARTS) or low.endswith("?"):
        return False
    return any(v in low for v in (_IMAGE_VERBS if kind == "image" else _TEXT_VERBS))


_REMIX_WORDS = ("бисоз", "созед", "сохта деҳ", "табдил", "мисли ин", "ҳамин гуна", "ба шакли", "мултфилм", "мультфильм", "аниме", "услуб", "стиль",
                "сделай", "создай", "преврати", "нарисуй", "в стиле", "cartoon", "anime", "style", "turn it", "turn into", "convert", "create",
                "make it", "make me", "make a", "paint", "draw")


def looks_like_remix(prompt):
    """A request for a NEW picture made from the photo (not a simple crop/rotate/filter)."""
    low = " ".join(str(prompt).lower().split())
    if not low or low.endswith("?") or low.startswith(_QUESTION_STARTS):
        return False
    return any(w in low for w in _REMIX_WORDS)


def is_text_file(name, mime):
    return str(mime).startswith("text/") or str(name).lower().endswith(TEXT_FILE_EXTS)


def strip_media(text):
    return MEDIA_RE.sub("", str(text)).replace(RETRY_MARK, "").strip()


def copy_java_stream(stream, output, limit=None):
    """Copy a Java InputStream into a Java OutputStream through NIO channels (works without Java arrays)."""
    from jnius import autoclass
    Channels = autoclass("java.nio.channels.Channels")
    ByteBuffer = autoclass("java.nio.ByteBuffer")
    src, dst = Channels.newChannel(stream), Channels.newChannel(output)
    buf = ByteBuffer.allocate(65536)
    total = 0
    while True:
        count = src.read(buf)
        if count < 0:
            break
        total += count
        if limit and total > limit:
            raise RuntimeError("too big")
        buf.flip()
        while buf.hasRemaining():
            dst.write(buf)
        buf.clear()
    return total


def java_bytes(array):
    """Python bytes from a Java byte[] (pyjnius may hand back signed ints)."""
    try:
        return bytes(bytearray(array))
    except Exception:
        return bytes((b & 0xFF) for b in array)



class LocalizedError(Exception):
    """An error whose text is already written in the app language."""


TAJIK_LETTERS = "ғӣқӯҳҷҒӢҚӮҲҶ"


def speech_lang_for(text, app_lang):
    """Language of a text for the phone voice: tg / ru / en (Tajik is never mixed up with Russian)."""
    if any(ch in TAJIK_LETTERS for ch in text):
        return "tg"
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return app_lang if app_lang in ("ru", "en") else "tg"
    cyr = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    if cyr / float(len(letters)) > 0.5:
        if any(ch in "ыэщЫЭЩ" for ch in text) or app_lang == "ru":
            return "ru"
        return "tg" if app_lang == "tg" else "ru"
    return "en"


def split_for_speech(text, first=110, rest=190):
    """Cut a text into short pieces at sentence ends so that the first piece is ready quickly."""
    text = " ".join(str(text).replace("\r", "").split("\n"))
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    chunks, cur = [], ""
    for part in parts:
        limit = first if not chunks else rest
        while len(part) > limit * 1.4:
            cut = max(part.rfind(", ", 0, int(limit * 1.3)), part.rfind(" ", 0, int(limit * 1.3)))
            if cut < 30:
                cut = int(limit * 1.3)
            head, part = part[:cut + 1].strip(), part[cut + 1:].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
            limit = rest
        if cur and len(cur) + 1 + len(part) > limit:
            chunks.append(cur)
            cur = part
        else:
            cur = (cur + " " + part).strip()
    if cur:
        chunks.append(cur)
    return chunks


def http_json(url, timeout=12):
    import ssl
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-Android"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
        return json.loads(response.read().decode("utf-8"))


@info_command("prayer", "намоз", "вақти намоз", "вақти намозҳо", "намозҳо", "намоз кай аст", "намози имрӯз",
              "время намаза", "намаз", "молитва", "prayer times", "prayer time", "namaz")
def info_prayer(app):
    # Hanafi school (the common one in Tajikistan); times come already in Dushanbe local time.
    data = http_json("https://api.aladhan.com/v1/timingsByCity?city=Dushanbe&country=Tajikistan&method=13&school=1")
    timings = data["data"]["timings"]
    lines = [app.t("prayer_title")]
    for key in ("Fajr", "Sunrise", "Dhuhr", "Asr", "Maghrib", "Isha"):
        lines.append("%s: %s" % (app.t("pr_" + key), str(timings[key]).split()[0]))
    try:
        h = data["data"]["date"]["hijri"]
        lines.append("%s: %s %s %s" % (app.t("hijri"), h["day"], h["month"]["en"], h["year"]))
    except Exception:
        pass
    return "\n".join(lines)


def weather_text(app, code):
    if code == 0:
        return app.t("wc_clear")
    if code in (1, 2):
        return app.t("wc_cloud")
    if code == 3:
        return app.t("wc_overcast")
    if code in (45, 48):
        return app.t("wc_fog")
    if code in (51, 53, 55, 56, 57):
        return app.t("wc_drizzle")
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return app.t("wc_rain")
    if code in (71, 73, 75, 77, 85, 86):
        return app.t("wc_snow")
    if code in (95,):
        return app.t("wc_thunder")
    if code in (96, 99):
        return app.t("wc_showers")
    return app.t("wc_cloud")


@info_command("weather", "ҳаво", "обу ҳаво", "ҳавои имрӯз", "ҳаво чӣ хел аст", "ҳаво чи хел аст",
              "погода", "погода сегодня", "weather")
def info_weather(app):
    data = http_json("https://api.open-meteo.com/v1/forecast?latitude=38.56&longitude=68.77"
                     "&current=temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m"
                     "&timezone=auto")
    cur = data["current"]
    return "%s\n%s, %+.0f°C (%s %+.0f°C)\n%s: %.0f%%\n%s: %.0f %s" % (
        app.t("weather_title"), weather_text(app, int(cur["weather_code"])), cur["temperature_2m"],
        app.t("feels"), cur["apparent_temperature"], app.t("humidity"), cur["relative_humidity_2m"],
        app.t("wind"), cur["wind_speed_10m"], app.t("wind_unit"))


@info_command("rates", "қурб", "қурби асъор", "қурби доллар", "курс", "курс валют", "курс доллара",
              "exchange rate", "exchange rates", "currency", "rates")
def info_rates(app):
    data = http_json("https://open.er-api.com/v6/latest/USD")
    rates = data["rates"]
    tjs = float(rates["TJS"])
    lines = [app.t("rates_title"), app.t("rates_note")]
    for code in ("USD", "EUR", "RUB", "CNY", "KZT", "UZS"):
        if code in rates and float(rates[code]) > 0:
            value, unit = tjs / float(rates[code]), 1
            while value < 0.5 and unit < 1000:      # small currencies are shown per 100 / 1000 units
                value, unit = value * 10, unit * 10
            lines.append("%s%s: %.2f" % ("%d " % unit if unit > 1 else "", code, value))
    return "\n".join(lines)


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


# ---------------------------------------------------------------------------------------------
# Frosted-glass look: layered translucent fills, a soft top sheen and a light edge (Kivy has no
# real live blur, so the "blur" is made from glow textures behind translucent panels).
# ---------------------------------------------------------------------------------------------
_GLOW = {}
_GRAD = {}


def glow_texture(rgb):
    """Soft round glow (colour fades to transparent at the edge)."""
    key = (round(rgb[0], 2), round(rgb[1], 2), round(rgb[2], 2))
    tex = _GLOW.get(key)
    if tex is None:
        n = 64
        buf = bytearray()
        half = (n - 1) / 2.0
        for y in range(n):
            for x in range(n):
                d = math.hypot(x - half, y - half) / (n / 2.0)
                a = max(0.0, 1.0 - d)
                a = a * a
                buf += bytes((int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255), int(a * 255)))
        tex = Texture.create(size=(n, n), colorfmt="rgba")
        tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
        _GLOW[key] = tex
    return tex


def vertical_gradient(top, bottom):
    key = (tuple(top), tuple(bottom))
    tex = _GRAD.get(key)
    if tex is None:
        n = 64
        buf = bytearray()
        for i in range(n):                      # row 0 is the bottom
            f = i / float(n - 1)
            buf += bytes((int((bottom[0] + (top[0] - bottom[0]) * f) * 255),
                          int((bottom[1] + (top[1] - bottom[1]) * f) * 255),
                          int((bottom[2] + (top[2] - bottom[2]) * f) * 255), 255))
        tex = Texture.create(size=(1, n), colorfmt="rgba")
        tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
        tex.mag_filter = "linear"
        tex.min_filter = "linear"
        _GRAD[key] = tex
    return tex


def mk(text):
    """Escape text for Kivy markup."""
    return str(text).replace("&", "&amp;").replace("[", "&bl;").replace("]", "&br;")


def frost(widget, radius=22, tint=(0.55, 0.78, 1.0), alpha=0.13, edge=0.34):
    r = dp(radius)
    if UI_LIGHT:
        if alpha >= 0.2:                                  # coloured (selected / primary) glass
            body = (tint[0], tint[1], tint[2], min(0.92, alpha * 2.2))
        else:                                             # neutral milk glass
            body = (1, 1, 1, min(0.85, 0.50 + alpha * 2.0))
        sheen = (1, 1, 1, 0.22)
        line = (tint[0] * 0.55, tint[1] * 0.55, tint[2] * 0.6, min(1.0, edge * 0.9 + 0.10))
    else:
        body = (tint[0], tint[1], tint[2], alpha)
        sheen = (1, 1, 1, alpha * 0.5)
        line = (tint[0] * 0.5 + 0.5, tint[1] * 0.5 + 0.5, tint[2] * 0.5 + 0.5, edge)
    widget._fr_a0 = body[3]
    with widget.canvas.before:
        widget._fr_body_c = Color(*body)
        widget._fr_body = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[r])
        Color(*sheen)
        widget._fr_sheen = RoundedRectangle(pos=(widget.x, widget.y + widget.height * 0.55),
                                            size=(widget.width, widget.height * 0.45), radius=[r, r, 0, 0])
        Color(*line)
        widget._fr_edge = Line(rounded_rectangle=(widget.x, widget.y, widget.width, widget.height, r), width=dp(1))

    def update(w, *_):
        w._fr_body.pos = w.pos
        w._fr_body.size = w.size
        w._fr_sheen.pos = (w.x, w.y + w.height * 0.55)
        w._fr_sheen.size = (w.width, w.height * 0.45)
        w._fr_edge.rounded_rectangle = (w.x, w.y, w.width, w.height, r)

    widget.bind(pos=update, size=update)


class FrostButton(Button):
    """Glass button. `marker=True` draws a small glowing dot on the left (selected item)."""

    def __init__(self, tint=(0.55, 0.78, 1.0), alpha=0.11, radius=18, edge=0.30, marker=False, **kwargs):
        kwargs.setdefault("markup", True)
        kwargs.setdefault("halign", "left")
        kwargs.setdefault("valign", "middle")
        super().__init__(**kwargs)
        self.background_normal = ""
        self.background_down = ""
        self.background_color = (0, 0, 0, 0)
        self._base_alpha = alpha
        self._margin = dp(36) if marker else dp(18)
        frost(self, radius, tint, alpha, edge)
        if marker:
            with self.canvas.after:
                Color(tint[0] * 0.4 + 0.6, tint[1] * 0.4 + 0.6, tint[2] * 0.4 + 0.6, 1)
                self._dot = Ellipse(size=(dp(9), dp(9)), pos=(self.x + dp(15), self.center_y - dp(4.5)))
            self.bind(pos=self._move_dot, size=self._move_dot)
        self.bind(size=self._fit_text, state=self._feedback)
        self._fit_text()

    def _move_dot(self, *_):
        self._dot.pos = (self.x + dp(15), self.center_y - dp(4.5))

    def _fit_text(self, *_):
        self.text_size = (max(0, self.width - 2 * self._margin), self.height)

    def _feedback(self, *_):
        self._fr_body_c.a = min(1.0, self._fr_a0 + (0.16 if self.state == "down" else 0.0))


class Hair(Widget):
    """A thin horizontal light line."""

    def __init__(self, alpha=0.16, **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(1))
        super().__init__(**kwargs)
        with self.canvas:
            Color(*((0.1, 0.2, 0.4, alpha) if UI_LIGHT else (1, 1, 1, alpha)))
            self._line = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *_):
        self._line.pos = self.pos
        self._line.size = self.size


BLUR_FS = """$HEADER$
uniform vec2 step_dir;
void main(void) {
    vec2 uv = tex_coord0;
    vec4 c = texture2D(texture0, uv) * 0.2270270270;
    c += texture2D(texture0, uv + step_dir * 1.3846153846) * 0.3162162162;
    c += texture2D(texture0, uv - step_dir * 1.3846153846) * 0.3162162162;
    c += texture2D(texture0, uv + step_dir * 3.2307692308) * 0.0702702703;
    c += texture2D(texture0, uv - step_dir * 3.2307692308) * 0.0702702703;
    gl_FragColor = c * frag_color;
}
"""
_BLUR_KEEP = []          # the frame buffers must stay alive as long as their textures are shown


def _blur_pass(texture, dx, dy):
    width, height = texture.size
    fbo = Fbo(size=(width, height))
    fbo.shader.fs = BLUR_FS
    if not fbo.shader.success:
        raise RuntimeError("blur shader did not compile")
    fbo["step_dir"] = (dx / float(width), dy / float(height))
    with fbo:
        Color(1, 1, 1, 1)
        Rectangle(texture=texture, pos=(0, 0), size=(width, height))
    fbo.draw()
    _BLUR_KEEP.append(fbo)
    out = fbo.texture
    out.mag_filter = "linear"
    out.min_filter = "linear"
    return out


def capture_blur(widget, scale=0.16):
    """A frosted-glass picture of what `widget` shows now: a small snapshot is smoothed with a real gaussian
    blur (shader, several passes) and stretched back, so no blocky pixels are visible.
    Returns a texture or None (then only the dim layer is used)."""
    parent = widget.parent
    index = -1
    try:
        width, height = int(widget.width * scale), int(widget.height * scale)
        if width < 8 or height < 8:
            return None
        del _BLUR_KEEP[:]
        if parent is not None:
            index = parent.canvas.indexof(widget.canvas)
            if index > -1:
                parent.canvas.remove(widget.canvas)
        fbo = Fbo(size=(width, height))
        with fbo:
            ClearColor(0, 0, 0, 1)
            ClearBuffers()
            Scale(scale, scale, 1)
            Translate(-widget.x, -widget.y, 0)
        fbo.add(widget.canvas)
        fbo.draw()
        fbo.remove(widget.canvas)
        _BLUR_KEEP.append(fbo)
        texture = fbo.texture
        texture.mag_filter = "linear"
        texture.min_filter = "linear"
    except Exception:
        save_crash("Blur capture failed:\n" + traceback.format_exc())
        return None
    finally:
        try:
            if parent is not None and index > -1 and widget.canvas not in parent.canvas.children:
                parent.canvas.insert(index, widget.canvas)
        except Exception:
            save_crash("Blur capture could not restore the canvas:\n" + traceback.format_exc())
    try:
        for radius in (1.0, 2.0, 3.0):
            texture = _blur_pass(texture, radius, 0)
            texture = _blur_pass(texture, 0, radius)
    except Exception:
        save_crash("Blur shader failed (the small snapshot is used):\n" + traceback.format_exc())
    return texture


class GlassSheet(FloatLayout):
    """Frosted panel over a dim scrim: used for the menu and for every section opened from it."""

    def __init__(self, app, title, body, back=None, anchor="center", height=None, **kwargs):
        super().__init__(size_hint=(1, 1), **kwargs)
        self.app = app
        self.closing = False
        theme = THEMES[app.theme_index]
        tint = hex_color(theme["accent"])[:3]
        with self.canvas.before:
            self._bg_c = Color(1, 1, 1, 0)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
            self._scrim_c = Color(*((0.86, 0.92, 1.0, 0.62) if UI_LIGHT else (0.0, 0.01, 0.05, 0.66)))
            self._scrim = Rectangle(pos=self.pos, size=self.size)
            self._glow_c = Color(1, 1, 1, 0.30 if UI_LIGHT else 0.55)
            self._glow = Rectangle(texture=glow_texture(tint), pos=(0, 0), size=(10, 10))
        self.bind(pos=self._layout, size=self._layout)
        self._fit = (0.94, 0.9) if anchor == "center" else (0.80, None)
        panel = BoxLayout(orientation="vertical", padding=(dp(14), dp(10), dp(14), dp(14)), spacing=dp(8))
        panel.size_hint = (self._fit[0] * 0.92, self._fit[1] * 0.92) if self._fit[1] else (self._fit[0], None)
        if not self._fit[1]:
            panel.height = dp(height or 360)
        panel.pos_hint = ({"center_x": 0.5, "center_y": 0.5} if anchor == "center"
                          else {"right": 0.96, "top": 0.93})
        frost(panel, 28, tint, 0.15, 0.45)
        self.panel = panel
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(8))
        if back is not None:
            b = FrostButton(tint=tint, alpha=0.12, radius=14, text="‹", font_size=dp(26), halign="center",
                            size_hint_x=None, width=dp(44), color=hex_color(theme["text"]))
            b.bind(on_release=lambda *_: back())
            head.add_widget(b)
        head.add_widget(make_label(title, 17, theme["accent"], True, halign="left"))
        x = FrostButton(tint=tint, alpha=0.12, radius=14, text="×", font_size=dp(28), halign="center",
                        size_hint_x=None, width=dp(44), color=hex_color(theme["text"]))
        x.bind(on_release=lambda *_: self.dismiss())
        head.add_widget(x)
        panel.add_widget(head)
        panel.add_widget(Hair())
        panel.add_widget(body)
        self._body = body
        self.add_widget(panel)
        self.opacity = 0

    def _layout(self, *_):
        self._scrim.pos = self.pos
        self._scrim.size = self.size
        self._bg_rect.pos = self.pos
        self._bg_rect.size = self.size
        size = max(self.width, self.height) * 1.2
        self._glow.size = (size, size)
        self._glow.pos = (self.right - size * 0.7, self.top - size * 0.6)

    def open(self):
        texture = capture_blur(self.app.root_layout) if hasattr(self.app, "root_layout") else None
        if texture is not None:                 # the app screen behind turns into fog
            self._bg_rect.texture = texture
            self._scrim_c.a = 0.30 if UI_LIGHT else 0.38
            Animation(a=1, d=0.30, t="out_quad").start(self._bg_c)
        Window.add_widget(self)
        self.app.sheet_opened(self)
        Animation(opacity=1, d=0.22, t="out_quad").start(self)
        if self._fit[1]:
            Animation(size_hint=self._fit, d=0.28, t="out_cubic").start(self.panel)
        body = getattr(self, "_body", None)
        if body is not None:                      # the rows float in one after another
            for i, row in enumerate(list(body.children)[::-1][:8]):
                row.opacity = 0
                Clock.schedule_once(lambda dt, w=row: Animation(opacity=1, d=0.24, t="out_quad").start(w),
                                    0.06 + 0.05 * i)

    def dismiss(self, *args, **kwargs):
        if self.closing:
            return
        self.closing = True
        self.app.sheet_closed(self)

        def remove(*_):
            if self.parent is not None:
                self.parent.remove_widget(self)

        anim = Animation(opacity=0, d=0.16, t="in_quad")
        anim.bind(on_complete=remove)
        anim.start(self)

    def on_touch_down(self, touch):
        if self.closing:
            return True
        if super().on_touch_down(touch):
            return True
        if not self.panel.collide_point(*touch.pos):
            self.dismiss()
        return True

    def on_touch_move(self, touch):
        super().on_touch_move(touch)
        return True

    def on_touch_up(self, touch):
        super().on_touch_up(touch)
        return True


class GlassDialog(GlassSheet):
    """Small frosted dialog (rename, confirm, emoji panel...): same look as the sheets."""

    def __init__(self, app, content, size_hint=(0.92, None), height=None, pos_hint=None, auto_dismiss=True, **kwargs):
        FloatLayout.__init__(self, size_hint=(1, 1))
        self.app = app
        self.closing = False
        self.auto_dismiss = auto_dismiss
        self._body = None
        theme = THEMES[app.theme_index]
        tint = hex_color(theme["accent"])[:3]
        with self.canvas.before:
            self._bg_c = Color(1, 1, 1, 0)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
            self._scrim_c = Color(*((0.86, 0.92, 1.0, 0.62) if UI_LIGHT else (0.0, 0.01, 0.05, 0.66)))
            self._scrim = Rectangle(pos=self.pos, size=self.size)
            self._glow_c = Color(1, 1, 1, 0.30 if UI_LIGHT else 0.55)
            self._glow = Rectangle(texture=glow_texture(tint), pos=(0, 0), size=(10, 10))
        self.bind(pos=self._layout, size=self._layout)
        sh = (size_hint[0], size_hint[1]) if size_hint else (0.92, None)
        self._fit = sh if sh[1] is not None else (sh[0], None)
        panel = BoxLayout(orientation="vertical")
        if sh[1] is not None:
            panel.size_hint = (sh[0] * 0.94, sh[1] * 0.94)
        else:
            panel.size_hint = (sh[0], None)
            panel.height = height or dp(240)
        panel.pos_hint = pos_hint or {"center_x": 0.5, "center_y": 0.5}
        frost(panel, 28, tint, 0.15, 0.45)
        panel.add_widget(content)
        self.panel = panel
        self.add_widget(panel)
        self.opacity = 0

    def on_touch_down(self, touch):
        if self.closing:
            return True
        if FloatLayout.on_touch_down(self, touch):
            return True
        if self.auto_dismiss and not self.panel.collide_point(*touch.pos):
            self.dismiss()
        return True


STEP_LABELS = {"tg": ("Забон", "Ном", "Ҳисоб", "Профил"), "ru": ("Язык", "Имя", "Аккаунт", "Профиль"), "en": ("Language", "Name", "Account", "Profile")}


class StepBar(FloatLayout):
    """Checkout-style progress: finished steps are filled circles with a tick, the current one glows, the rest are dim rings."""

    def __init__(self, acc, text_color, labels, **kwargs):
        super().__init__(size_hint=(0.88, None), height=dp(52), pos_hint={"center_x": 0.5, "top": 0.985}, **kwargs)
        self.acc = acc
        self.step = 0
        self.labels = []
        for text in labels:
            lab = make_label(text, 10, "#FFFFFF", False, size_hint=(None, None), height=dp(14))
            self.labels.append(lab)
            self.add_widget(lab)
        self._dim = (0.1, 0.2, 0.4, 0.28) if UI_LIGHT else (1, 1, 1, 0.22)
        self._text = text_color
        self.bind(pos=self._draw, size=self._draw)

    def set_step(self, index):
        self.step = index
        self.opacity = 0 if index < 0 else 1
        self._draw()

    def _draw(self, *_):
        n = len(self.labels)
        if n == 0 or self.width < 10:
            return
        gap = self.width / n
        r = dp(11)
        cy = self.top - dp(16)
        a = self.acc
        self.canvas.clear()
        with self.canvas:
            for i in range(n):
                cx = self.x + gap * (i + 0.5)
                if i < n - 1:
                    Color(*(a + (0.85,) if i < self.step else self._dim))
                    Line(points=[cx + r + dp(4), cy, cx + gap - r - dp(4), cy], width=dp(1.6), cap="round")
                if i < self.step:                                  # done: filled circle with a tick
                    Color(a[0], a[1], a[2], 0.95)
                    Ellipse(pos=(cx - r, cy - r), size=(2 * r, 2 * r))
                    Color(1, 1, 1, 1)
                    Line(points=[cx - r * 0.42, cy - r * 0.02, cx - r * 0.1, cy - r * 0.38, cx + r * 0.46, cy + r * 0.34],
                         width=dp(1.8), cap="round", joint="round")
                elif i == self.step:                                # current: glowing ring and dot
                    Color(a[0], a[1], a[2], 0.22)
                    Ellipse(pos=(cx - r - dp(4), cy - r - dp(4)), size=(2 * (r + dp(4)), 2 * (r + dp(4))))
                    Color(a[0], a[1], a[2], 1)
                    Line(circle=(cx, cy, r), width=dp(1.8))
                    Ellipse(pos=(cx - dp(4), cy - dp(4)), size=(dp(8), dp(8)))
                else:                                               # next: dim ring
                    Color(*self._dim)
                    Line(circle=(cx, cy, r), width=dp(1.4))
        for i, lab in enumerate(self.labels):
            cx = self.x + gap * (i + 0.5)
            lab.width = gap
            lab.pos = (cx - gap / 2, self.top - dp(46))
            lab.color = hex_color("#FFFFFF") if False else (hex_color(self._text) if i <= self.step else hex_color("#8FA6C8"))
            lab.opacity = 1 if i <= self.step else 0.7


class AuroraScreen(FloatLayout):
    """First-run screen: moving glows, a living orb, a dotted wordmark and a frosted card per step."""

    def __init__(self, app, **kwargs):
        super().__init__(size_hint=(1, 1), **kwargs)
        self.app = app
        theme = THEMES[app.theme_index]
        self.acc = hex_color(theme["accent"])[:3]
        self.acc2 = hex_color(theme["accent2"])[:3]
        self.step = 0
        self._t = 0.0
        self._kick = 0.0
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size,
                                 texture=(vertical_gradient((0.78, 0.84, 0.93), (0.86, 0.90, 0.95)) if UI_LIGHT
                                          else vertical_gradient((0.05, 0.11, 0.25), (0.008, 0.015, 0.05))))
            self._blobs = []
            for rgb, alpha in ((self.acc, 0.50), (self.acc2, 0.55), ((0.55, 0.35, 1.0), 0.42)):
                alpha = alpha * (0.55 if UI_LIGHT else 1.0)
                Color(1, 1, 1, alpha)
                self._blobs.append(Rectangle(texture=glow_texture(rgb), pos=(0, 0), size=(10, 10)))
            Color(1, 1, 1, 0.9)
            self._orb_glow = Rectangle(texture=glow_texture(self.acc), pos=(0, 0), size=(10, 10))
            Color(self.acc[0] * 0.3 + 0.7, self.acc[1] * 0.3 + 0.7, self.acc[2] * 0.3 + 0.7, 0.95)
            self._orb_core = Ellipse(pos=(0, 0), size=(10, 10))
            self._ring_c1 = Color(self.acc[0], self.acc[1], self.acc[2], 0.35)
            self._ring1 = Line(circle=(0, 0, 10), width=dp(1))
            self._ring_c2 = Color(self.acc[0], self.acc[1], self.acc[2], 0.18)
            self._ring2 = Line(circle=(0, 0, 10), width=dp(1))
            self._particles = []
            rnd = random.Random(7)
            for _i in range(22):
                Color(*((0.2, 0.4, 0.9, 0.45) if UI_LIGHT else (0.75, 0.9, 1.0, 0.55)))
                size = dp(rnd.uniform(5, 13))
                self._particles.append([Rectangle(texture=glow_texture((0.8, 0.92, 1.0)), pos=(0, 0), size=(size, size)),
                                        rnd.random(), rnd.random(), rnd.uniform(0.012, 0.04), rnd.uniform(0, 6.28), size])
            self._dim = (0.1, 0.2, 0.4, 0.2) if UI_LIGHT else (1, 1, 1, 0.18)
            self._dot_cols, self._dots = [], []
            for _i in range(0):
                self._dot_cols.append(Color(*self._dim))
                self._dots.append(RoundedRectangle(pos=(0, 0), size=(dp(26), dp(4)), radius=[dp(2)]))
        self.wordmark = make_label(dotted_name(app.assistant_name or "S.A.I"), 34, theme["accent"], True,
                                   size_hint=(1, None), height=dp(52), pos_hint={"center_x": 0.5, "top": 0.765})
        self.add_widget(self.wordmark)
        self.stage = FloatLayout(size_hint=(1, 1))
        self.add_widget(self.stage)
        self.stepbar = StepBar(self.acc, theme["text"], STEP_LABELS.get(app.language, STEP_LABELS["tg"]))
        self.add_widget(self.stepbar)
        self.bind(pos=self._layout, size=self._layout)
        self._layout()
        self._ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    # -- geometry ---------------------------------------------------------------------------
    def _orb_center(self):
        return self.center_x, self.y + self.height * 0.85

    def _layout(self, *_):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._place(0)

    def _place(self, t):
        w, h = self.width, self.height
        base = max(w, h)
        for i, blob in enumerate(self._blobs):
            size = base * (0.95 - 0.12 * i)
            ox = math.sin(t * (0.23 + 0.07 * i) + i * 2.1) * w * 0.18
            oy = math.cos(t * (0.19 + 0.05 * i) + i * 1.3) * h * 0.08
            anchors = ((0.05, 0.92), (0.95, 0.35), (0.25, 0.08))
            cx = self.x + w * anchors[i][0] + ox
            cy = self.y + h * anchors[i][1] + oy
            blob.size = (size, size)
            blob.pos = (cx - size / 2, cy - size / 2)
        cx, cy = self._orb_center()
        pulse = (1.0 + 0.045 * math.sin(t * 1.7)) * (1.0 + 0.14 * self._kick)
        for part in self._particles:
            rect, px, py, speed, phase, psize = part
            yy = (py + t * speed) % 1.0
            rect.pos = (self.x + w * px + math.sin(t * 0.6 + phase) * dp(16), self.y + h * yy)
        r = dp(40) * pulse
        gsize = r * 5.2
        self._orb_glow.size = (gsize, gsize)
        self._orb_glow.pos = (cx - gsize / 2, cy - gsize / 2)
        self._orb_core.size = (r * 2, r * 2)
        self._orb_core.pos = (cx - r, cy - r)
        self._ring1.circle = (cx, cy, r * (1.35 + 0.05 * math.sin(t * 1.1)))
        self._ring2.circle = (cx, cy, r * (1.75 + 0.08 * math.sin(t * 0.8 + 1.0)))
        for i, rect in enumerate(self._dots):
            total = 3 * dp(26) + 2 * dp(8)
            rect.pos = (self.center_x - total / 2 + i * (dp(26) + dp(8)), self.top - dp(26))
            if i <= self.step:
                self._dot_cols[i].rgba = (self.acc[0], self.acc[1], self.acc[2], 0.95)
            else:
                self._dot_cols[i].rgba = self._dim

    def _tick(self, dt):
        if getattr(self.app, "app_paused", False):
            return
        self._t += dt
        self._kick = max(0.0, self._kick - dt * 2.2)
        self._place(self._t)

    def kick(self):
        """The orb answers every key the user types."""
        self._kick = 1.0

    # -- steps ------------------------------------------------------------------------------
    def set_step(self, index):
        self.step = index
        self.stepbar.set_step(index)
        self._place(self._t)

    def set_wordmark(self, name):
        shown = dotted_name(name or "S.A.I")
        self.wordmark.text = shown
        self.wordmark.font_size = dp(34 if len(shown) <= 9 else 24 if len(shown) <= 16 else 17)

    def stepbar_labels(self, code):
        for lab, text in zip(self.stepbar.labels, STEP_LABELS.get(code, STEP_LABELS["tg"])):
            lab.text = text

    def spin(self, on):
        sp = getattr(self, "_spin", None)
        if on and sp is None:
            sp = Spinner(color=self.acc2, size_hint=(None, None), size=(dp(38), dp(38)), pos_hint={"center_x": 0.5, "y": 0.09})
            self.add_widget(sp)
            self._spin = sp
        elif not on and sp is not None:
            if sp.parent is not None:
                sp.parent.remove_widget(sp)
            self._spin = None

    def new_panel(self, height):
        """A fresh frosted card; the previous one fades out."""
        for old in list(self.stage.children):
            Animation(opacity=0, d=0.12).start(old)
            Clock.schedule_once(lambda dt, o=old: self.stage.remove_widget(o) if o.parent else None, 0.14)
        panel = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10), size_hint=(0.86, None),
                          height=dp(min(height, max(300, Window.height / dp(1) * 0.70))),
                          pos_hint={"center_x": 0.5, "top": 0.70}, opacity=0)
        frost(panel, 30, self.acc, 0.15, 0.45)
        self.stage.add_widget(panel)
        Animation(opacity=1, size_hint_x=0.92, d=0.3, t="out_cubic").start(panel)
        return panel

    def dismiss(self, *args, **kwargs):
        try:
            self._ev.cancel()
        except Exception:
            pass
        self.app.sheet_closed(self)
        if getattr(self.app, "_ob_screen", None) is self:
            self.app._ob_screen = None
        if self.parent is not None:
            self.parent.remove_widget(self)

    def on_touch_down(self, touch):
        super().on_touch_down(touch)
        return True

    def on_touch_move(self, touch):
        super().on_touch_move(touch)
        return True

    def on_touch_up(self, touch):
        super().on_touch_up(touch)
        return True


_ASSET_TEX = {}


def asset_texture(name):
    """A texture from the assets folder (same search places as the orb pictures), or None."""
    if name in _ASSET_TEX:
        return _ASSET_TEX[name]
    tex = None
    folders = [os.path.dirname(os.path.abspath(__file__)), os.getcwd()]
    try:
        app = App.get_running_app()
        if app is not None and getattr(app, "directory", None):
            folders.append(app.directory)
    except Exception:
        pass
    for folder in folders:
        for path in (os.path.join(folder, "assets", name), os.path.join(folder, name)):
            try:
                if os.path.exists(path):
                    from kivy.core.image import Image as CoreImage
                    tex = CoreImage(path).texture
                    break
            except Exception:
                tex = None
        if tex is not None:
            break
    _ASSET_TEX[name] = tex
    return tex


class Spinner(Widget):
    """A turning arc. Stops by itself when it is removed from the screen."""

    def __init__(self, color=(0.4, 0.7, 1), **kwargs):
        super().__init__(**kwargs)
        self._angle = 0
        with self.canvas:
            self._c = Color(color[0], color[1], color[2], 0.95)
            self._line = Line(circle=(0, 0, 10, 0, 270), width=dp(2.4), cap="round")
        self._ev = Clock.schedule_interval(self._tick, 1 / 40.0)

    def _tick(self, dt):
        if self.parent is None and self.get_root_window() is None:
            if getattr(self, "_seen", False):
                self._ev.cancel()
                return False
            return True
        self._seen = True
        self._angle = (self._angle + 330 * dt) % 360
        r = min(self.width, self.height) / 2 - dp(3)
        self._line.circle = (self.center_x, self.center_y, max(r, 1), self._angle, self._angle + 260)
        return True


class IndeterminateBar(Widget):
    """A thin bar with a sliding highlight: something is being sent."""

    def __init__(self, color=(0.4, 0.7, 1), **kwargs):
        super().__init__(size_hint_y=None, height=dp(4), **kwargs)
        self._t = 0.0
        with self.canvas:
            Color(color[0], color[1], color[2], 0.22)
            self._track = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(2)])
            Color(color[0], color[1], color[2], 0.95)
            self._seg = RoundedRectangle(pos=self.pos, size=(0, 0), radius=[dp(2)])
        self.bind(pos=self._draw, size=self._draw)
        self._ev = Clock.schedule_interval(self._tick, 1 / 40.0)

    def _tick(self, dt):
        self._t = (self._t + dt * 0.9) % 1.0
        self._draw()
        return True

    def _draw(self, *_):
        self._track.pos, self._track.size = self.pos, self.size
        w = self.width * 0.32
        x = self.x + (self.width + w) * self._t - w
        left, right = max(x, self.x), min(x + w, self.x + self.width)
        self._seg.pos = (left, self.y)
        self._seg.size = (max(right - left, 0), self.height)

    def stop(self):
        self._ev.cancel()


class SkeletonRect(Widget):
    alpha = NumericProperty(0.10)

    def __init__(self, color=(0.4, 0.7, 1), **kwargs):
        super().__init__(**kwargs)
        with self.canvas:
            self._c = Color(color[0], color[1], color[2], self.alpha)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(14)])
        self.bind(pos=self._draw, size=self._draw, alpha=self._draw)
        anim = Animation(alpha=0.30, d=0.9, t="in_out_sine") + Animation(alpha=0.08, d=0.9, t="in_out_sine")
        anim.repeat = True
        anim.start(self)

    def _draw(self, *_):
        self._c.a = self.alpha
        self._r.pos, self._r.size = self.pos, self.size


class TransitionCover(FloatLayout):
    """Full-screen cover with the logo, shown while one screen is swapped for the next."""

    def __init__(self, app, **kwargs):
        super().__init__(size_hint=(1, 1), opacity=0, **kwargs)
        theme = THEMES[app.theme_index]
        bg = hex_color(theme["background"])
        with self.canvas.before:
            Color(bg[0] * 0.55, bg[1] * 0.55, bg[2] * 0.55, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_: setattr(self._bg, "pos", self.pos), size=lambda *_: setattr(self._bg, "size", self.size))
        from kivy.uix.image import Image as KivyImage
        tex = asset_texture("adaptive_fg.png")
        if tex is not None:
            logo = KivyImage(texture=tex, size_hint=(None, None), size=(dp(190), dp(190)), fit_mode="contain",
                             pos_hint={"center_x": 0.5, "center_y": 0.56})
        else:
            logo = make_label("S.A.I", 40, theme["accent"], True, size_hint=(None, None), width=dp(190), height=dp(80),
                              pos_hint={"center_x": 0.5, "center_y": 0.56})
        pulse = Animation(opacity=0.65, d=0.8, t="in_out_sine") + Animation(opacity=1, d=0.8, t="in_out_sine")
        pulse.repeat = True
        pulse.start(logo)
        self.add_widget(logo)
        self.add_widget(Spinner(color=hex_color(theme["accent"])[:3], size_hint=(None, None), size=(dp(34), dp(34)),
                                pos_hint={"center_x": 0.5, "center_y": 0.38}))

    def on_touch_down(self, touch):
        return True

    def on_touch_move(self, touch):
        return True

    def on_touch_up(self, touch):
        return True


class DotsButton(FrostButton):
    """A round button with three vertical dots (drawn, so it does not depend on a font glyph)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.after:
            self._dc = Color(1, 1, 1, 0.92)
            self._d = [Ellipse(pos=(0, 0), size=(0, 0)) for _ in range(3)]
        self.bind(pos=self._place, size=self._place)
        self._place()

    def _place(self, *_):
        r = dp(2.6)
        cx, cy = self.center_x, self.center_y
        for i, e in enumerate(self._d):
            e.size = (2 * r, 2 * r)
            e.pos = (cx - r, cy - r + (i - 1) * dp(7.5))


def emoji_cell(ch, size, on_release):
    """A tappable full-colour emoji picture (None when the colour pack or this emoji is missing)."""
    from kivy.uix.behaviors import ButtonBehavior
    from kivy.uix.image import Image as KivyImage
    parts = [v for k, v in split_emoji(ch) if k == "e"]
    tex = emoji_texture(parts[0]) if parts else None
    if tex is None:
        return None

    class Cell(ButtonBehavior, KivyImage):
        pass

    cell = Cell(texture=tex, fit_mode="contain", **size)
    cell.bind(on_release=on_release)
    return cell


def make_zoom_image(path):
    from kivy.uix.scatter import Scatter
    from kivy.uix.image import Image as KivyImage

    class ZoomImage(Scatter):
        def on_touch_down(self, touch):
            if self.collide_point(*touch.pos) and getattr(touch, "is_double_tap", False):
                self.reset()
                return True
            return super().on_touch_down(touch)

        def reset(self):
            self.scale = 1
            self.pos = (0, 0)

        def on_touch_up(self, touch):
            result = super().on_touch_up(touch)
            if self.scale <= 1.02:
                self.reset()
            return result

    zoom = ZoomImage(do_rotation=False, do_translation=True, do_scale=True, scale_min=1.0, scale_max=8.0,
                     auto_bring_to_front=False, size_hint=(None, None))
    zoom.add_widget(KivyImage(source=path, fit_mode="contain", nocache=True, size_hint=(1, 1)))
    zoom.bind(size=lambda inst, sz: [setattr(c, "size", sz) for c in inst.children])
    return zoom


class MediaViewer(FloatLayout):
    """Full-screen picture/file viewer: back, title and a three-dot menu (copy / download)."""

    def __init__(self, app, path, **kwargs):
        super().__init__(size_hint=(1, 1), **kwargs)
        self.app = app
        self.path = path
        self.closing = False
        theme = THEMES[app.theme_index]
        tint = hex_color(theme["accent"])[:3]
        bg = hex_color(theme["background"])
        with self.canvas.before:
            Color(bg[0] * 0.6, bg[1] * 0.6, bg[2] * 0.6, 1)
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda *_: setattr(self._bg, "pos", self.pos), size=lambda *_: setattr(self._bg, "size", self.size))
        self.name = os.path.basename(path).split("__", 1)[-1]
        import mimetypes
        self.mime = mimetypes.guess_type(self.name)[0] or "application/octet-stream"
        bar = BoxLayout(orientation="horizontal", size_hint=(1, None), height=dp(56), padding=(dp(8), dp(6)), spacing=dp(8),
                        pos_hint={"top": 1})
        back = FrostButton(tint=tint, alpha=0.14, radius=18, text="‹", font_size=dp(28), halign="center",
                           size_hint=(None, 1), width=dp(46), color=hex_color(theme["text"]))
        back.bind(on_release=lambda *_: self.dismiss())
        bar.add_widget(back)
        bar.add_widget(make_label(self.name, 15, theme["text"], True, halign="left"))
        menu = DotsButton(tint=tint, alpha=0.14, radius=18, size_hint=(None, 1), width=dp(46))
        menu.bind(on_release=lambda *_: self.show_menu())
        bar.add_widget(menu)
        self.area = FloatLayout(size_hint=(1, None))
        self.area.bind(size=self._fit_zoom)
        self.add_widget(self.area)
        self.add_widget(bar)
        self.bind(height=lambda *_: setattr(self.area, "height", max(1, self.height - dp(56))))
        self.zoom = None
        try:
            self._fill()
        except Exception:
            save_crash("Viewer failed:\n" + traceback.format_exc())
            self.area.add_widget(make_label(app.t("view_failed"), 15, theme["text"], False))
        self.toast_label = make_label("", 13, theme["text"], True, size_hint=(None, None), width=dp(240), height=dp(38),
                                      pos_hint={"center_x": 0.5, "y": 0.06}, opacity=0)
        frost(self.toast_label, 16, tint, 0.30, 0.5)
        self.add_widget(self.toast_label)

    def _fit_zoom(self, *_):
        if self.zoom is not None:
            self.zoom.size = self.area.size
            self.zoom.reset()

    def _fill(self):
        app = self.app
        theme = THEMES[app.theme_index]
        size = os.path.getsize(self.path)
        if self.mime.startswith("image/"):
            self.zoom = make_zoom_image(self.path)
            self.area.add_widget(self.zoom)
            self._fit_zoom()
            return
        if is_text_file(self.name, self.mime):
            with open(self.path, "rb") as handle:
                data = handle.read(30000)
            text = data.decode("utf-8", "replace")
            if size > 30000:
                text += "\n…"
            from kivy.uix.scrollview import ScrollView
            scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False, bar_width=dp(3))
            label = EmojiLabel(text="", font_size=dp(14), color=hex_color(theme["text"]), halign="left", valign="top",
                               size_hint_y=None)
            label.set_plain(text)
            label.bind(width=lambda inst, w: setattr(inst, "text_size", (w - dp(24), None)))
            label.bind(texture_size=lambda inst, ts: setattr(inst, "height", ts[1] + dp(24)))
            scroll.add_widget(label)
            self.area.add_widget(scroll)
            return
        ext = (os.path.splitext(self.name)[1][1:6] or "FILE").upper()
        card = BoxLayout(orientation="vertical", size_hint=(0.8, None), height=dp(190), padding=dp(16), spacing=dp(6),
                         pos_hint={"center_x": 0.5, "center_y": 0.55})
        frost(card, 26, hex_color(theme["accent"])[:3], 0.14, 0.4)
        card.add_widget(make_label(ext, 34, theme["accent"], True))
        card.add_widget(make_label(self.name, 14, theme["text"], True, size_hint_y=None, height=dp(34)))
        card.add_widget(make_label(app.t("view_type") + ": " + self.mime + "   ·   " + app.t("view_size") + ": " + human_size(size),
                                   11, muted(), False, size_hint_y=None, height=dp(34)))
        card.add_widget(make_label(app.t("view_nopreview"), 11, muted(), False, size_hint_y=None, height=dp(22)))
        self.area.add_widget(card)

    def show_menu(self):
        app = self.app
        theme = THEMES[app.theme_index]
        body = BoxLayout(orientation="vertical", spacing=dp(8))
        sheet = GlassSheet(app, app.t("view_actions"), body, anchor="menu", height=170)

        def act(fn):
            def run(*_):
                sheet.dismiss()
                fn()
            return run

        app.frost_row(body, app.t("view_copy"), False, act(lambda: app.copy_media(self.path)), height=52)
        app.frost_row(body, app.t("view_download"), False, act(lambda: app.save_media(self.path)), height=52)
        sheet.open()

    def toast(self, text):
        self.toast_label.text = text
        self.toast_label.width = min(Window.width - dp(24), max(dp(160), len(text) * dp(9) + dp(32)))
        Animation.cancel_all(self.toast_label)
        anim = Animation(opacity=1, d=0.15) + Animation(opacity=1, d=1.8) + Animation(opacity=0, d=0.3)
        anim.start(self.toast_label)

    def open(self):
        Window.add_widget(self)
        self.app.sheet_opened(self)

    def dismiss(self, *args, **kwargs):
        if self.closing:
            return
        self.closing = True
        self.app.sheet_closed(self)
        if self.parent is not None:
            self.parent.remove_widget(self)


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def glass_input(app, hint, text="", password=False, center=False, size=15):
    theme = THEMES[app.theme_index]
    wrap = BoxLayout(size_hint_y=None, height=dp(50), padding=(dp(14), 0))
    frost(wrap, 16, (1, 1, 1), 0.08, 0.28)
    ti = TextInput(text=text, hint_text=hint, password=password, multiline=False,
                   background_normal="", background_active="", background_color=(0, 0, 0, 0),
                   foreground_color=hex_color(theme["text"]), hint_text_color=(1, 1, 1, 0.38),
                   cursor_color=hex_color(theme["accent"]), font_size=dp(size),
                   padding=[0, dp(14.5), 0, 0], write_tab=False, input_type="null")
    if center:
        ti.halign = "center"
    wrap.add_widget(ti)
    return wrap, ti


EMOJI_OVERLAY = True      # coloured pictures over the field's text; the plain (single-colour) glyphs underneath are blanked


class EmojiInput(TextInput):
    """Text field that paints typed emoji as full-colour pictures (the same ones the chat shows).
    The text itself is untouched; the pictures are drawn over the plain glyphs. If anything about the
    geometry cannot be read, nothing is drawn and the plain glyphs stay visible."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._emoji_ev = None
        for prop in ("text", "pos", "size", "scroll_x", "scroll_y", "font_size", "padding"):
            self.bind(**{prop: self._emoji_later})

    def _create_line_label(self, text, hint=False):
        """Draw the plain glyph of every emoji as blank space: only the coloured picture is visible."""
        try:
            if EMOJI_OVERLAY and emoji_pack()[0] and any(ord(ch) >= 0x203C for ch in text):
                text = "".join("\u2007" * len(v) if k == "e" else v for k, v in split_emoji(text))
        except Exception:
            pass
        return super()._create_line_label(text, hint)

    def _emoji_later(self, *_):
        if self._emoji_ev is not None:
            self._emoji_ev.cancel()
        self._emoji_ev = Clock.schedule_once(self._emoji_draw, 0.04)

    def _emoji_draw(self, *_):
        self.canvas.after.clear()
        try:
            if not EMOJI_OVERLAY or not self.text or not emoji_pack()[0]:
                return
            label = self._label_cached
            line_h = self.line_height + self.line_spacing
            left = self.x + self.padding[0] - self.scroll_x
            top = self.top - self.padding[1] + self.scroll_y
            side = self.font_size * 1.25
            with self.canvas.after:
                Color(1, 1, 1, 1)
                for row, line in enumerate(self._lines):
                    if not any(ord(ch) >= 0x203C for ch in line) and not any(ch in "#*0123456789" for ch in line):
                        continue
                    before = ""
                    bottom = top - (row + 1) * line_h
                    for kind, value in split_emoji(line):
                        if kind == "t":
                            before += value
                            continue
                        tex = emoji_texture(value)
                        start = label.get_extents(before)[0] if before else 0
                        width = label.get_extents(value)[0]
                        before += value
                        if tex is None:
                            continue
                        cx = left + start + width / 2.0
                        cy = bottom + line_h / 2.0
                        if cy < self.y or cy > self.top:        # scrolled out of sight
                            continue
                        Rectangle(texture=tex, pos=(cx - side / 2.0, cy - side / 2.0), size=(side, side))
        except Exception:
            self.canvas.after.clear()


class JarvisApp(App):
    def build(self):
        self.title = "S.A.I"
        ExceptionManager.add_handler(JarvisExceptionHandler())
        self.startup_error = None
        self._last_err = None
        self._err_count = 0
        self.store = JsonStore(os.path.join(self.user_data_dir, "jarvis_settings.json"))
        self.profile = ProfileStore(os.path.join(self.user_data_dir, "user_profile.json"))
        self.language = "tg"
        self.theme_index = 0
        self.voice_profile = "JARVIS Classic"
        self.ui_mode = "dark"
        self.speak_enabled = True
        self.server_token = ""
        self.assistant_name = "S.A.I"
        self.username = ""
        self.onboarded = False
        self.profile_done = False
        self._delivery = {}
        self._delivery_labels = {}
        self._delivery_bars = {}
        self._skeleton = None
        self._retry = None
        self._busy = False
        self._busy_ev = None
        self._busy_safety = None
        self.tajik_tts_url = ""
        self.last_response = ""
        self.animation_paused = False
        self.orb_state = "idle"
        self._orb_event = None
        self.load_settings()
        self.title = self.assistant_name
        self.account_service = get_service(self.user_data_dir)
        self.speech = SpeechRecognizer(self)
        self.tts = SpeechSynthesizer()
        Clock.schedule_once(lambda dt: self.tts.warm_up(), 1.5)   # start the phone voice engine early
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
            strength = (1.0 - t) ** 1.65 * (0.40 if UI_LIGHT else 1.0)
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

    def brand(self, text):
        """The user chose the assistant's name: it replaces JARVIS (any spelling, also Cyrillic) in every text,
        in old chats and AI replies too. File paths, URLs and media markers are left alone."""
        name = getattr(self, "assistant_name", "") or "S.A.I"
        text = str(text)
        if not OLD_NAME_RE.search(text):
            return text
        return OLD_NAME_RE.sub(lambda m: name, text)

    def t(self, key):
        return self.brand(TEXTS.get(self.language, TEXTS["tg"]).get(key, key))

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
            self.tajik_tts_url = str(data.get("tajik_tts_url", ""))
            self.assistant_name = str(data.get("assistant_name", "S.A.I")) or "S.A.I"
            self.username = str(data.get("username", ""))
            self.ui_mode = str(data.get("ui_mode", "dark"))
            if self.ui_mode not in ("auto", "dark", "light"):
                self.ui_mode = "auto"
            self.onboarded = bool(data.get("onboarded", False))
            self.profile_done = bool(data.get("profile_done", self.onboarded))   # existing users are not asked again
            if not self.onboarded:
                self.server_token = ""      # an old shared token is no longer used
        if self.language not in ("tg", "ru", "en"):
            self.language = "tg"
        self.apply_ui_mode()

    def system_is_dark(self):
        try:
            from jnius import autoclass
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            return (activity.getResources().getConfiguration().uiMode & 0x30) == 0x20
        except Exception:
            return True

    def apply_ui_mode(self):
        light = self.ui_mode == "light" or (self.ui_mode == "auto" and not self.system_is_dark())
        apply_mode(light)

    def choose_mode(self, mode, popup):
        self.ui_mode = mode
        self.save_settings()
        popup.dismiss()
        self.apply_ui_mode()
        self.update_colors()
        Clock.schedule_once(lambda dt: self.build_interface(), 0.2)

    def save_settings(self):
        self.store.put("settings", language=self.language,
                       theme_v2=self.theme_index,
                       voice_profile=self.voice_profile,
                       speak=self.speak_enabled,
                       speak_v2=self.speak_enabled,
                       server_token=getattr(self, "server_token", ""),
                       tajik_tts_url=getattr(self, "tajik_tts_url", ""),
                       assistant_name=getattr(self, "assistant_name", "S.A.I"),
                       username=getattr(self, "username", ""),
                       ui_mode=getattr(self, "ui_mode", "auto"),
                       onboarded=getattr(self, "onboarded", False),
                       profile_done=getattr(self, "profile_done", False))

    def update_colors(self):
        theme = THEMES[self.theme_index]
        Window.clearcolor = hex_color(theme["background"])
        self.bg_color.rgba = hex_color(theme["background"])
        self.grad_color.rgba = (1, 1, 1, 1)
        self.grad_rect.texture = self.make_gradient(theme)

    def make_button(self, text, callback, color=None, height=44):
        theme = THEMES[self.theme_index]
        accent = color or theme["accent"]
        btn = FrostButton(tint=hex_color(accent)[:3], alpha=0.40, edge=0.55, radius=20,
                          text="[b]" + mk(text) + "[/b]", size_hint_y=None, height=dp(height), font_size=dp(14),
                          halign="center", color=(1, 1, 1, 1) if UI_LIGHT else hex_color(theme["text"]))
        btn.bind(on_release=callback)
        return btn

    def build_interface(self):
        self.root_layout.clear_widgets()
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        main = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        self.root_layout.add_widget(main)

        header = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        shown = dotted_name(getattr(self, "assistant_name", "") or "S.A.I")
        title = make_label(shown, 21 if len(shown) <= 14 else 15, theme["accent"], True, halign="left")
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
        self.command_input = EmojiInput(
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
        self.emoji_btn = emoji_cell("\U0001F600", {"size_hint_x": None, "width": dp(34)}, self.open_emoji_panel)
        if self.emoji_btn is None:
            self.emoji_btn = Button(text=":)", font_size=dp(22), size_hint_x=None, width=dp(34), background_normal="",
                                    background_color=(0, 0, 0, 0), color=hex_color(theme["accent"]))
            self.emoji_btn.bind(on_release=self.open_emoji_panel)
        self.action_btn = SendWaveButton(tint_rgb=tint, icon_rgba=hex_color(theme["text"]),
                                       size_hint_x=None, width=dp(52))
        self.action_btn.bind(on_release=self.on_action)
        command_row.add_widget(self.attach_btn)
        command_row.add_widget(self.command_input)
        command_row.add_widget(self.stop_speech_btn)
        command_row.add_widget(self.emoji_btn)
        command_row.add_widget(self.action_btn)
        self.attach_strip = BoxLayout(orientation="horizontal", spacing=dp(10), size_hint_y=None, height=0,
                                      padding=(dp(10), dp(6), dp(8), dp(6)), opacity=0)
        frost(self.attach_strip, 20, tint, 0.10, 0.30)
        main.add_widget(self.attach_strip)
        self.sugg_strip = BoxLayout(orientation="horizontal", spacing=dp(6), size_hint_y=None, height=0, opacity=0,
                                    padding=(dp(6), 0))
        main.add_widget(self.sugg_strip)
        main.add_widget(command_row)
        self._refresh_attach_strip()
        self._sync_action_mode()

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
        text = self.brand(text)
        if full_text is not None:
            full_text = self.brand(full_text)
        theme = THEMES[self.theme_index]
        user = role == "user"
        accent = hex_color(theme["accent"])[:3]
        if user:
            tint, fill, who = accent, 0.20, self.t("you")
            who_color = (accent[0], accent[1], accent[2], 1)
        else:
            tint, fill, who = (0.05, 0.07, 0.14), 0.55, self.assistant_name
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
        label = EmojiLabel(text="", font_size=dp(15),
                           color=(1, 1, 1, 1) if user else hex_color(theme["text"]),
                           halign="right" if user else "left", valign="top", size_hint_y=None)
        media_items = MEDIA_RE.findall(str(text))
        label.set_plain(strip_media(text) if media_items else text)
        label.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
        label.bind(texture_size=lambda inst, ts: setattr(inst, "height", ts[1]))
        user_media = user and bool(media_items)
        if not user_media:
            bubble.add_widget(label)
        for kind_m, path_m in media_items:
            path_m = path_m.strip()
            if not os.path.isfile(path_m):
                continue
            try:
                if kind_m == "img":
                    from kivy.uix.image import Image as KivyImage
                    from kivy.uix.behaviors import ButtonBehavior

                    class TapImage(ButtonBehavior, KivyImage):
                        pass

                    thumb = TapImage(source=path_m, size_hint_y=None, height=dp(230), fit_mode="contain", nocache=True)
                    thumb.bind(on_release=lambda inst, pth=path_m: self.open_media(pth))
                    bubble.add_widget(thumb)
                else:
                    open_btn = GlassButton(text=os.path.basename(path_m).split("__", 1)[-1], tint=accent, active=False, radius=10,
                                           size_hint_y=None, height=dp(40), font_size=dp(13), color=hex_color(theme["text"]))
                    open_btn.bind(on_release=lambda btn, pth=path_m: self.open_media(pth))
                    bubble.add_widget(open_btn)
                btn_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(38), spacing=dp(6))
                if kind_m == "img":
                    view_btn = GlassButton(text=self.t("open_btn"), tint=accent, active=False, radius=10, font_size=dp(13),
                                           color=hex_color(theme["text"]))
                    view_btn.bind(on_release=lambda btn, pth=path_m: self.open_media(pth))
                    btn_row.add_widget(view_btn)
                save_btn = GlassButton(text=self.t("save_btn"), tint=accent, active=False, radius=10, font_size=dp(13),
                                       color=hex_color(theme["text"]))
                save_btn.bind(on_release=lambda btn, pth=path_m: self.save_media(pth))
                btn_row.add_widget(save_btn)
                bubble.add_widget(btn_row)
                state = getattr(self, "_delivery", {}).get(path_m)
                if user and state:
                    dl = make_label("", 11, "#8FA6C8", False, size_hint_y=None, height=dp(16), halign="right")
                    dl.bind(width=lambda inst, w: setattr(inst, "text_size", (w, None)))
                    self._delivery_labels[path_m] = dl
                    self._paint_delivery(dl, state)
                    if state == "sending":
                        bar = IndeterminateBar(color=accent)
                        self._delivery_bars[path_m] = bar
                        bubble.add_widget(bar)
                    bubble.add_widget(dl)
            except Exception:
                save_crash("Media bubble failed:\n" + traceback.format_exc())
        if user_media and strip_media(text):
            bubble.add_widget(label)
        if RETRY_MARK in str(text) and getattr(self, "_retry", None) is not None:
            retry_btn = GlassButton(text=self.t("retry_btn"), tint=accent, active=True, radius=12, size_hint_y=None,
                                    height=dp(40), font_size=dp(14), color=hex_color(theme["text"]))

            def do_retry(btn):
                btn.disabled = True
                btn.opacity = 0.4
                self.retry_last()

            retry_btn.bind(on_release=do_retry)
            bubble.add_widget(retry_btn)
        message = strip_media(full_text if full_text is not None else text)
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

    # ---------------------------------------------------------------- chats saved in the account
    def _json_call(self, method, path, obj=None, timeout=40):
        """Quiet JSON request to the server (background sync must not fill the error log when the phone is offline)."""
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = None
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8") if obj is not None else None
        headers = {"Authorization": "Bearer " + self.server_token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(SERVER_BASE + path, data=body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _chat_for_sync(chat):
        messages = []
        for m in chat.get("messages", [])[-600:]:
            text = MEDIA_RE.sub("[акс/файл]", str(m.get("text", ""))).replace(RETRY_MARK, "").strip()
            if text:
                messages.append({"role": m.get("role", "user"), "text": text[:8000], "time": str(m.get("time", ""))[:32]})
        return {"id": chat["id"], "title": str(chat.get("title", ""))[:120], "created": str(chat.get("created", ""))[:32],
                "updated": str(chat.get("updated", ""))[:32], "messages": messages}

    def _sync_snapshot(self):
        """(payload chats with their signatures, ids to delete) – taken on the Kivy thread."""
        dirty = []
        for chat in self.chats.chats:
            sig = ChatStore.signature(chat)
            if chat.get("messages") and self.chats.synced.get(chat["id"]) != sig:
                dirty.append((self._chat_for_sync(chat), sig))
        return dirty, list(self.chats.deleted)

    def _sync_send(self, dirty, deleted):
        """Runs in a worker thread. Returns the (id, signature) pairs saved and the ids deleted."""
        done, gone = [], []
        for i in range(0, len(dirty), 20):
            part = dirty[i:i + 20]
            self._json_call("POST", "/chats/upsert", {"chats": [c for c, _ in part]}, timeout=60)
            done.extend((c["id"], sig) for c, sig in part)
        if deleted:
            self._json_call("POST", "/chats/delete", {"ids": deleted[:200]}, timeout=30)
            gone = deleted[:200]
        return done, gone

    def _sync_applied(self, done, gone):
        for chat_id, sig in done:
            self.chats.synced[chat_id] = sig
        self.chats.deleted = [x for x in self.chats.deleted if x not in gone]
        self.chats.save()

    def chat_sync_tick(self, dt=None):
        if not self.server_token.strip() or getattr(self, "_sync_busy", False) or not getattr(self, "_chats_pulled", False):
            return
        dirty, deleted = self._sync_snapshot()
        if not dirty and not deleted:
            return
        self._sync_busy = True

        def job():
            try:
                done, gone = self._sync_send(dirty, deleted)
                Clock.schedule_once(lambda d: self._sync_applied(done, gone), 0)
            except Exception:
                pass                                   # offline or server asleep: the next tick tries again
            finally:
                self._sync_busy = False

        threading.Thread(target=job, daemon=True).start()

    def pull_chats(self):
        """Bring the account's chats to this phone (after sign-in, and when the app starts)."""
        if not self.server_token.strip():
            return

        def job():
            try:
                data = self._json_call("GET", "/chats", None, timeout=60)
                chats = data.get("chats", []) if isinstance(data, dict) else []
                Clock.schedule_once(lambda d: self._merge_chats(chats), 0)
            except Exception:
                pass

        threading.Thread(target=job, daemon=True).start()

    def _merge_chats(self, remote):
        changed = False
        for item in remote:
            try:
                chat_id = str(item["id"])
                messages = [{"role": m["role"], "text": str(m["text"]), "time": str(m.get("time", ""))}
                            for m in item.get("messages", []) if m.get("role") in ("user", "jarvis")]
                local = self.chats.get(chat_id)
                if chat_id in self.chats.deleted:
                    continue
                if local is None:
                    self.chats.chats.append({"id": chat_id, "title": str(item.get("title", "")), "created": str(item.get("created", "")) or ChatStore.now(),
                                             "updated": str(item.get("updated", "")) or ChatStore.now(), "messages": messages})
                    local = self.chats.get(chat_id)
                    changed = True
                elif str(item.get("updated", "")) > str(local.get("updated", "")) and len(messages) >= len(local.get("messages", [])):
                    local["messages"], local["title"], local["updated"] = messages, str(item.get("title", "")), str(item["updated"])
                    changed = True
                else:
                    continue
                self.chats.synced[chat_id] = ChatStore.signature(local)
            except Exception:
                continue
        self._chats_pulled = True
        if changed:
            self.chats.save()
            try:
                self.render_chat()
            except Exception:
                pass

    def _switch_chat_owner(self):
        """A different account signed in on this phone: its chats must not be mixed with the previous person's."""
        try:
            owner = self.store.get("chats_owner")["name"] if self.store.exists("chats_owner") else ""
        except Exception:
            owner = ""
        if owner and owner.lower() != self.username.lower():
            self.chats.chats, self.chats.deleted, self.chats.synced, self.chats.current_id = [], [], {}, None
            self.chats.new_chat()
        try:
            self.store.put("chats_owner", name=self.username)
        except Exception:
            pass
        self._chats_pulled = False

    def _start_chat_analysis(self, chat_id, question):
        if not self.server_token.strip():
            self._media_done(chat_id, self.t("server_missing"))
            return
        self.set_orb_state("thinking")
        self.busy_start("think")
        dirty, deleted = self._sync_snapshot()

        def job():
            try:
                done, gone = self._sync_send(dirty, deleted)          # the account must hold the latest chats first
                Clock.schedule_once(lambda d: self._sync_applied(done, gone), 0)
                result = self._json_call("POST", "/chats/analyze", {"question": question[:1000], "language": self._lang_code()}, timeout=170)
                answer = str(result.get("answer", "")) or self.t("file_no_answer")
                Clock.schedule_once(lambda d: self._finish_server_answer(chat_id, answer), 0)
            except Exception as exc:
                message = self.friendly_error(exc)
                Clock.schedule_once(lambda d: self._media_done(chat_id, message), 0)

        threading.Thread(target=job, daemon=True).start()

    def cover_transition(self, action, hold=1.3):
        """Logo cover over the whole screen; the next screen is built underneath and the cover leaves calmly."""
        cover = TransitionCover(self)
        Window.add_widget(cover)

        def leave(dt):
            anim = Animation(opacity=0, d=0.5, t="in_out_sine")
            anim.bind(on_complete=lambda *a: cover.parent.remove_widget(cover) if cover.parent else None)
            anim.start(cover)

        def covered(*_):
            try:
                action()
            except Exception:
                save_crash("Transition action failed:\n" + traceback.format_exc())
            if cover.parent is not None:          # keep the cover above anything the action added to the window
                Window.remove_widget(cover)
                Window.add_widget(cover)
            Clock.schedule_once(leave, hold)

        anim = Animation(opacity=1, d=0.22)
        anim.bind(on_complete=covered)
        anim.start(cover)

    def _show_skeleton(self):
        self._remove_skeleton()
        theme = THEMES[self.theme_index]
        accent = hex_color(theme["accent"])[:3]
        card = BoxLayout(orientation="vertical", size_hint=(0.82, None), height=dp(268), padding=dp(12), spacing=dp(8))
        frost(card, 18, (0.05, 0.07, 0.14), 0.55, 0.30)
        card.add_widget(make_label(self.assistant_name, 11, theme["accent"], True, size_hint_y=None, height=dp(16), halign="left"))
        card.add_widget(SkeletonRect(color=accent))
        card.add_widget(SkeletonRect(color=accent, size_hint_y=None, height=dp(12)))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(276), padding=(dp(6), dp(4)))
        row.add_widget(card)
        row.add_widget(Widget())
        self._skeleton = row
        self.chat_box.add_widget(row)
        self.scroll_chat_down()

    def _remove_skeleton(self):
        row = getattr(self, "_skeleton", None)
        if row is not None and row.parent is not None:
            row.parent.remove_widget(row)
        self._skeleton = None

    def _pulse_start(self):
        label = getattr(self, "status_label", None)
        if label is None:
            return
        Animation.cancel_all(label, "opacity")
        anim = Animation(opacity=0.35, d=0.75, t="in_out_sine") + Animation(opacity=1, d=0.75, t="in_out_sine")
        anim.repeat = True
        anim.start(label)

    def _pulse_stop(self):
        label = getattr(self, "status_label", None)
        if label is not None:
            Animation.cancel_all(label, "opacity")
            label.opacity = 1

    def append_bubble(self, role, text):
        self.chat_box.add_widget(self.make_bubble(role, text))
        Clock.schedule_once(lambda dt: self.update_chat_fades(), 0.1)
        self.scroll_chat_down()

    def add_chat_message(self, chat_id, role, text):
        text = self.brand(text)
        self.chats.add_message(chat_id, role, text)
        if role == "jarvis":
            self.busy_stop()
            self._remove_skeleton()
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

    def _read_uri_bytes(self, resolver, uri, limit):
        """Bytes of a picked document. Three independent ways; the first one that returns data wins."""
        from jnius import autoclass
        raw = b""
        try:                                                        # 1) plain file descriptor, read by Python
            pfd = resolver.openFileDescriptor(uri, "r")
            fd = pfd.detachFd()
            with os.fdopen(fd, "rb") as handle:
                raw = handle.read(limit + 1)
        except Exception:
            save_crash("Read way 1 (descriptor) failed:\n" + traceback.format_exc())
            raw = b""
        if raw:
            return raw
        try:                                                        # 2) Android copies the stream into a temp file
            tmp = os.path.join(self.media_dir(), "_incoming.tmp")
            stream = resolver.openInputStream(uri)
            out = autoclass("java.io.FileOutputStream")(tmp)
            autoclass("android.os.FileUtils").copy(stream, out)
            out.close()
            stream.close()
            with open(tmp, "rb") as handle:
                raw = handle.read(limit + 1)
            os.remove(tmp)
        except Exception:
            save_crash("Read way 2 (FileUtils) failed:\n" + traceback.format_exc())
            raw = b""
        if raw:
            return raw
        stream = resolver.openInputStream(uri)                      # 3) Java channels
        output = autoclass("java.io.ByteArrayOutputStream")()
        try:
            copy_java_stream(stream, output, limit)
        except RuntimeError:
            raise LocalizedError(self.t("file_too_big"))
        stream.close()
        return java_bytes(output.toByteArray())

    def on_activity_result(self, request_code, result_code, data):
        """Runs on the Android thread: only the file is read here, everything on screen happens in the Kivy thread."""
        if request_code != 7412 or not getattr(self, "_attachment_pending", False):
            return
        self._attachment_pending = False
        stage = "start"
        try:
            from jnius import autoclass
            Activity = autoclass("android.app.Activity")
            if result_code != Activity.RESULT_OK or data is None:
                return
            uri = data.getData()
            if uri is None:
                Clock.schedule_once(lambda dt: self.show_response(self.t("file_not_selected")), 0)
                return
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            resolver = PythonActivity.mActivity.getContentResolver()
            mime = str(resolver.getType(uri) or "application/octet-stream")
            stage = "read"
            limit = 20 * 1024 * 1024
            raw = self._read_uri_bytes(resolver, uri, limit)
            if len(raw) > limit:
                raise LocalizedError(self.t("file_too_big"))
            if not raw:
                raise LocalizedError(self.t("file_empty"))
            stage = "name"
            name = str(uri.getLastPathSegment() or "attachment")
            try:
                cursor = resolver.query(uri, None, None, None, None)
                if cursor is not None:
                    if cursor.moveToFirst():
                        idx = cursor.getColumnIndex("_display_name")
                        if idx >= 0 and cursor.getString(idx):
                            name = str(cursor.getString(idx))
                    cursor.close()
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._attachment_ui(name, mime, raw), 0)
        except Exception as exc:
            save_crash("Attachment read failed at stage " + stage + ":\n" + traceback.format_exc())
            message = self.friendly_error(exc) + " <" + stage + ">"
            Clock.schedule_once(lambda dt: self.show_response(self.t("file_read_failed").format(message)), 0)

    def _attachment_ui(self, name, mime, raw):
        """Kivy thread: the picked picture/file waits above the input field; it goes to the AI with the send button."""
        stage = "save"
        try:
            path = self._save_private(raw, os.path.basename(name).replace("]", "_"))
            stage = "stage"
            self._pending_attachment = {"chat_id": self.chats.current_id, "name": name, "mime": mime, "raw": raw, "path": path}
            self._refresh_attach_strip()
            self._sync_action_mode()
            if hasattr(self, "command_input"):
                self.command_input.focus = True
        except Exception as exc:
            save_crash("Attachment display failed at stage " + stage + ":\n" + traceback.format_exc())
            self.show_response(self.t("file_read_failed").format(self.friendly_error(exc) + " <" + stage + ">"))

    def _sync_action_mode(self):
        if hasattr(self, "action_btn") and hasattr(self, "command_input"):
            has = bool(self.command_input.text.strip()) or bool(getattr(self, "_pending_attachment", None))
            self.action_btn.set_mode("plane" if has else "wave")

    def clear_attachment(self, *_):
        self._pending_attachment = None
        self._refresh_attach_strip()
        self._sync_action_mode()

    def _refresh_attach_strip(self):
        """The preview row above the input: thumbnail or file type, name, a hint and a remove button."""
        strip = getattr(self, "attach_strip", None)
        if strip is None:
            return
        strip.clear_widgets()
        att = getattr(self, "_pending_attachment", None)
        if not att:
            strip.height = 0
            strip.opacity = 0
            return
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        strip.height = dp(68)
        strip.opacity = 1
        thumb = None
        if att["mime"].startswith("image/"):
            try:
                from kivy.uix.image import Image as KivyImage
                thumb = KivyImage(source=att["path"], size_hint=(None, 1), width=dp(56), fit_mode="cover", nocache=True)
            except Exception:
                thumb = None
        if thumb is None:
            ext = (os.path.splitext(att["name"])[1][1:5] or "FILE").upper()
            thumb = make_label(ext, 13, theme["accent"], True, size_hint=(None, 1), width=dp(56))
            frost(thumb, 14, tint, 0.16, 0.4)
        strip.add_widget(thumb)
        text = BoxLayout(orientation="vertical", spacing=dp(2))
        text.add_widget(make_label(att["name"], 13, theme["text"], True, halign="left"))
        text.add_widget(make_label(self.t("attach_ready"), 11, muted(), False, halign="left"))
        strip.add_widget(text)
        gone = FrostButton(tint=(1.0, 0.42, 0.48), alpha=0.14, radius=16, text="×", font_size=dp(24), halign="center",
                           size_hint=(None, None), size=(dp(44), dp(44)), pos_hint={"center_y": 0.5},
                           color=hex_color(theme["text"]))
        gone.bind(on_release=self.clear_attachment)
        strip.add_widget(gone)

    def _process_attachment(self, chat_id, att, prompt):
        name, mime, raw = att["name"], att["mime"], att["raw"]
        self._last_att = (chat_id, att, prompt)
        self._set_delivery(att.get("path"), "sending")
        mode = "analyze"
        if mime.startswith("image/") and looks_like_remix(prompt):
            mode = "image_remix"
        elif mime.startswith("image/") and looks_like_edit(prompt, "image"):
            mode = "image_edit"
        elif is_text_file(name, mime) and looks_like_edit(prompt, "text"):
            mode = "text_edit"
        if mode in ("image_remix", "image_edit"):
            self._show_skeleton()
        self._set_busy(self.t("media_image") if mode == "image_remix" else self.t("media_edit") if mode != "analyze" else self.t("server_thinking"))
        target = self._upload_attachment if mode == "analyze" else self._attachment_edit_job
        args = (chat_id, name, mime, raw, prompt) if mode == "analyze" else (chat_id, name, mime, raw, prompt, mode)
        threading.Thread(target=target, args=args, kwargs={"path": att.get("path")}, daemon=True).start()

    def _attachment_net_failed(self, chat_id, path, message):
        """No connection: show the error with a Retry button below the sent file."""
        def show(dt):
            self._set_delivery(path, "failed")
            last = getattr(self, "_last_att", None)
            self._retry = (lambda: self._process_attachment(*last)) if last else None
            self._attachment_answer(chat_id, message + ("\n" + RETRY_MARK if self._retry else ""))
        Clock.schedule_once(show, 0)

    def _upload_attachment(self, chat_id, filename, mime, raw, prompt, path=None):
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
            req = urllib.request.Request("https://jarvis-ai-server-3vuo.onrender.com/analyze", data=body, method="POST", headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "Authorization": "Bearer " + self.server_token})
            with urllib.request.urlopen(req, timeout=90, context=ctx) as response:
                result = json.loads(response.read().decode("utf-8"))
            answer = str(result.get("answer", self.t("file_no_answer")))
            Clock.schedule_once(lambda dt: self._set_delivery(path, "sent"), 0)
            Clock.schedule_once(lambda dt: self._attachment_answer(chat_id, answer), 0)
        except urllib.error.HTTPError as exc:
            save_crash("Attachment upload HTTP error: %s" % exc.code)
            detail = self.friendly_error(exc)
            Clock.schedule_once(lambda dt: self._set_delivery(path, "sent"), 0)
            Clock.schedule_once(lambda dt: self._attachment_answer(chat_id, detail), 0)
        except Exception as exc:
            save_crash("Attachment upload failed:\n" + traceback.format_exc())
            self._attachment_net_failed(chat_id, path, self.t("file_send_failed").format(self.friendly_error(exc)))

    def _attachment_answer(self, chat_id, answer):
        self._status_after_answer()
        self.deliver_answer(chat_id, answer)

    # ------------------------------------------------------------------ media: pictures and files
    def media_dir(self):
        path = os.path.join(self.user_data_dir, "media")
        os.makedirs(path, exist_ok=True)
        return path

    def busy_start(self, kind="think"):
        """While an answer is not fully shown, the status line cycles through several waiting phrases."""
        self._busy = True
        phrases = BUSY_PHRASES.get(self.language, BUSY_PHRASES["tg"]).get(kind) or BUSY_PHRASES["tg"]["think"]
        for ev in (self._busy_ev, self._busy_safety):
            if ev is not None:
                ev.cancel()
        self._busy_i = 0

        def tick(dt):
            label = getattr(self, "status_label", None)
            if label is None or not self._busy:
                return False
            label.text = phrases[self._busy_i % len(phrases)]
            self._busy_i += 1
            return True

        tick(0)
        self._pulse_start()
        self._busy_ev = Clock.schedule_interval(tick, 2.2)
        self._busy_safety = Clock.schedule_once(lambda dt: self.busy_stop(), 150)

    def busy_stop(self):
        if not self._busy:
            return
        self._busy = False
        self._pulse_stop()
        for ev in (self._busy_ev, self._busy_safety):
            if ev is not None:
                ev.cancel()
        self._busy_ev = self._busy_safety = None
        label = getattr(self, "status_label", None)
        if label is not None:
            label.text = self.t("speaking" if getattr(self, "orb_state", "idle") == "speaking" else "ready")

    def _set_busy(self, message):
        self.set_orb_state("thinking")
        self.busy_start("image" if message == self.t("media_image") else "think")

    def _multipart(self, fields, file_part=None):
        boundary = "----JARVIS" + uuid.uuid4().hex
        body = b""
        for key, value in fields.items():
            body += ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + key + "\"\r\n\r\n" + str(value) + "\r\n").encode("utf-8")
        if file_part:
            fname, mime, raw = file_part
            fname = os.path.basename(fname).replace('"', "_")
            body += ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + fname + "\"\r\nContent-Type: " + mime + "\r\n\r\n").encode("utf-8") + raw + b"\r\n"
        body += ("--" + boundary + "--\r\n").encode("utf-8")
        return body, "multipart/form-data; boundary=" + boundary

    def _server_call(self, path, body, content_type, timeout=120):
        """POST to the JARVIS server. Returns (lower-case headers, bytes). Raises LocalizedError (text in the app language)."""
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = None
        failure = None
        for attempt in (1, 2):   # the second try covers a sleeping Render server
            try:
                req = urllib.request.Request(SERVER_BASE + path, data=body, method="POST",
                                             headers={"Content-Type": content_type, "Authorization": "Bearer " + self.server_token})
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as response:
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    return headers, response.read(30 * 1024 * 1024)
            except urllib.error.HTTPError as exc:
                failure = exc
                save_crash("Server call %s failed with HTTP %s" % (path, exc.code))
                if exc.code == 422:        # the AI explains in the user's language why this edit is impossible
                    try:
                        raise LocalizedError(str(json.loads(exc.read().decode("utf-8")).get("detail", "")) or self.friendly_error(exc))
                    except LocalizedError:
                        raise
                    except Exception:
                        raise LocalizedError(self.friendly_error(exc))
                if exc.code in (502, 503, 504) and attempt == 1:
                    time.sleep(4)
                    continue
                raise LocalizedError(self.friendly_error(exc))
            except Exception as exc:
                failure = exc
                save_crash("Server call %s failed:\n%s" % (path, traceback.format_exc()))
        raise LocalizedError(self.friendly_error(failure))

    def _save_private(self, data, name):
        path = os.path.join(self.media_dir(), uuid.uuid4().hex[:8] + "__" + name)
        with open(path, "wb") as out:
            out.write(data)
        return path

    def _media_done(self, chat_id, text, say=""):
        self._status_after_answer() if say else self._voice_ready()
        self.set_orb_state("idle")
        self.add_chat_message(chat_id, "jarvis", text)
        if say:
            self.speak_answer(say)

    def _start_media_job(self, chat_id, kind, request):
        if not self.server_token.strip():
            self.add_chat_message(chat_id, "jarvis", self.t("server_missing"))
            return
        if not request:
            self.add_chat_message(chat_id, "jarvis", self.t("need_prompt"))
            return
        self._set_busy(self.t({"image": "media_image", "search": "media_search", "file": "media_file"}[kind]))
        if kind == "image":
            self._show_skeleton()
        target = {"image": self._job_generate, "search": self._job_search, "file": self._job_create_file}[kind]
        threading.Thread(target=target, args=(chat_id, request), daemon=True).start()

    def _lang_code(self):
        return self.language if self.language in ("tg", "ru", "en") else "tg"

    def _job_generate(self, chat_id, prompt):
        try:
            body = json.dumps({"prompt": prompt[:500], "language": self._lang_code()}, ensure_ascii=False).encode("utf-8")
            headers, data = self._server_call("/image/generate", body, "application/json", timeout=150)
            ext = ".png" if headers.get("content-type", "").startswith("image/png") else ".jpg"
            path = self._save_private(data, "jarvis_image" + ext)
            text = self.t("image_ready") + "\n[[img:" + path + "]]"
            say = self.t("image_ready")
        except Exception as exc:
            save_crash("Image generation failed:\n" + traceback.format_exc())
            text, say = self.t("image_failed").format(self.friendly_error(exc)), ""
        Clock.schedule_once(lambda dt: self._media_done(chat_id, text, say), 0)

    def _job_search(self, chat_id, query):
        import ssl
        try:
            body = json.dumps({"query": query[:200], "language": self._lang_code(), "limit": 4}, ensure_ascii=False).encode("utf-8")
            _headers, data = self._server_call("/image/search", body, "application/json", timeout=60)
            results = json.loads(data.decode("utf-8")).get("results", [])
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = None
            lines = [self.t("search_title")]
            count = 0
            for item in results[:4]:
                try:
                    req = urllib.request.Request(item["thumb"], headers={"User-Agent": "JARVIS-app/1.0"})
                    with urllib.request.urlopen(req, timeout=25, context=ctx) as response:
                        blob = response.read(6 * 1024 * 1024)
                    ext = ".png" if blob[:4] == b"\x89PNG" else ".jpg"
                    path = self._save_private(blob, "photo" + ext)
                    caption = item.get("title", "")[:60] + (" – " + item["license"] if item.get("license") else "")
                    lines.append(caption + "\n[[img:" + path + "]]")
                    count += 1
                except Exception:
                    save_crash("Search thumbnail failed:\n" + traceback.format_exc())
            text = "\n".join(lines) if count else self.t("search_none")
        except Exception as exc:
            save_crash("Image search failed:\n" + traceback.format_exc())
            text = self.t("image_failed").format(self.friendly_error(exc))
        Clock.schedule_once(lambda dt: self._media_done(chat_id, text, ""), 0)

    def _job_create_file(self, chat_id, request):
        try:
            body, ctype = self._multipart({"prompt": request[:3000], "language": self._lang_code()})
            _headers, data = self._server_call("/edit-text", body, ctype, timeout=120)
            result = json.loads(data.decode("utf-8"))
            name, content = str(result.get("filename", "jarvis.txt")), str(result.get("text", ""))
            path = self._save_private(content.encode("utf-8"), name)
            preview = content[:700] + ("\n…" if len(content) > 700 else "")
            text = self.t("file_created").format(name) + "\n\n" + preview + "\n[[file:" + path + "]]"
            say = self.t("file_created").format(name)
        except Exception as exc:
            save_crash("File creation failed:\n" + traceback.format_exc())
            text, say = self.t("edit_failed").format(self.friendly_error(exc)), ""
        Clock.schedule_once(lambda dt: self._media_done(chat_id, text, say), 0)

    def _attachment_edit_job(self, chat_id, name, mime, raw, prompt, mode, path=None):
        saved = None
        try:
            if mode == "image_remix":
                body, ctype = self._multipart({"prompt": prompt[:600], "language": self._lang_code()}, (name, mime, raw))
                headers, data = self._server_call("/image/remix", body, ctype, timeout=200)
                saved = self._save_private(data, "sai_new" + headers.get("x-edit-ext", ".jpg"))
                plan = ""
                try:
                    import base64
                    plan = base64.b64decode(headers.get("x-edit-summary-b64", "")).decode("utf-8").strip()
                except Exception:
                    pass
                say = plan or self.t("remix_ready")
                text = say + "\n[[img:" + saved + "]]"
            elif mode == "image_edit":
                body, ctype = self._multipart({"prompt": prompt[:1000], "language": self._lang_code()}, (name, mime, raw))
                headers, data = self._server_call("/image/edit", body, ctype, timeout=150)
                import base64
                summary = ""
                try:
                    summary = base64.b64decode(headers.get("x-edit-summary-b64", "")).decode("utf-8")
                except Exception:
                    pass
                ext = headers.get("x-edit-ext", ".jpg")
                saved = self._save_private(data, "jarvis_edited" + ext)
                text = self.t("edit_ready").format(summary or "") + "\n[[img:" + saved + "]]"
                say = summary
            else:
                fname = name if name.lower().endswith(TEXT_FILE_EXTS) else name + ".txt"
                body, ctype = self._multipart({"prompt": prompt[:3000], "language": self._lang_code()}, (fname, "text/plain", raw))
                _headers, data = self._server_call("/edit-text", body, ctype, timeout=150)
                result = json.loads(data.decode("utf-8"))
                content = str(result.get("text", ""))
                base, ext = os.path.splitext(fname)
                out_name = base + "_edited" + ext
                saved = self._save_private(content.encode("utf-8"), out_name)
                preview = content[:700] + ("\n…" if len(content) > 700 else "")
                text = self.t("file_edited").format(out_name) + "\n\n" + preview + "\n[[file:" + saved + "]]"
                say = self.t("file_edited").format(out_name)
            Clock.schedule_once(lambda dt: self._set_delivery(path, "sent"), 0)
        except Exception as exc:
            save_crash("Attachment edit failed:\n" + traceback.format_exc())
            text, say = self.t("edit_failed").format(self.friendly_error(exc)), ""
            if isinstance(exc, urllib.error.HTTPError):
                Clock.schedule_once(lambda dt: self._set_delivery(path, "sent"), 0)
            else:
                self._attachment_net_failed(chat_id, path, text)
                return
        Clock.schedule_once(lambda dt: self._media_done(chat_id, text, say), 0)

    def open_media(self, path):
        try:
            MediaViewer(self, path).open()
        except Exception:
            detail = traceback.format_exc()
            save_crash("Open media failed:\n" + detail)
            self.show_error_popup("Open media failed:\n" + detail)

    # ---- delivery state of a sent picture/file and the retry button
    def _paint_delivery(self, label, state):
        label.text = self.t({"sending": "d_sending", "sent": "d_ok", "failed": "d_fail"}[state])
        label.color = {"sending": hex_color("#8FA6C8"), "sent": hex_color("#7CE0A0"), "failed": hex_color("#FFB4B4")}[state]

    def _set_delivery(self, path, state):
        if not path:
            return
        if state != "sending":
            bar = self._delivery_bars.pop(path, None)
            if bar is not None:
                bar.stop()
                if bar.parent is not None:
                    bar.parent.remove_widget(bar)
        if not hasattr(self, "_delivery"):
            self._delivery = {}
        self._delivery[path] = state
        label = getattr(self, "_delivery_labels", {}).get(path)
        if label is not None:
            self._paint_delivery(label, state)

    def retry_last(self):
        fn, self._retry = getattr(self, "_retry", None), None
        if fn is not None:
            fn()

    def _media_notice(self, text):
        for widget in Window.children:
            if isinstance(widget, MediaViewer) and not widget.closing:
                widget.toast(text)
                return
        self.show_response(text)

    def copy_media(self, path):
        """Text files: the text goes to the clipboard. Pictures/other files: a content link that other apps can paste."""
        import mimetypes
        name = os.path.basename(path).split("__", 1)[-1]
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        try:
            if is_text_file(name, mime):
                from kivy.core.clipboard import Clipboard
                with open(path, "rb") as handle:
                    Clipboard.copy(handle.read(200000).decode("utf-8", "replace"))
            else:
                from jnius import autoclass
                cache = getattr(self, "_clip_uris", None)
                if cache is None:
                    cache = self._clip_uris = {}
                activity = autoclass("org.kivy.android.PythonActivity").mActivity
                resolver = activity.getContentResolver()
                uri = cache.get(path)
                if uri is None:
                    ContentValues = autoclass("android.content.ContentValues")
                    values = ContentValues()
                    values.put("_display_name", name)
                    values.put("mime_type", mime)
                    is_image = mime.startswith("image/")
                    values.put("relative_path", "Pictures/SAI" if is_image else "Download/SAI")
                    collection = (autoclass("android.provider.MediaStore$Images$Media") if is_image
                                  else autoclass("android.provider.MediaStore$Downloads")).EXTERNAL_CONTENT_URI
                    uri = resolver.insert(collection, values)
                    if uri is None:
                        raise RuntimeError("MediaStore refused the file")
                    out = resolver.openOutputStream(uri)
                    inp = autoclass("java.io.FileInputStream")(path)
                    copy_java_stream(inp, out)
                    inp.close()
                    out.close()
                    cache[path] = uri
                clip = autoclass("android.content.ClipData").newUri(resolver, name, uri)
                service = activity.getSystemService("clipboard")
                service.setPrimaryClip(clip)
            self._media_notice(self.t("view_copied"))
        except Exception as exc:
            save_crash("Copy media failed:\n" + traceback.format_exc())
            self._media_notice(self.t("copy_failed"))

    def save_media(self, path):
        """Copy a generated picture/file to the phone's gallery (Pictures/JARVIS) or Downloads (Download/JARVIS)."""
        import mimetypes
        name = os.path.basename(path).split("__", 1)[-1]
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        is_image = mime.startswith("image/")
        try:
            from jnius import autoclass
            sdk = autoclass("android.os.Build$VERSION").SDK_INT
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            if sdk < 29:
                raise RuntimeError("Android 10+ is required")
            ContentValues = autoclass("android.content.ContentValues")
            values = ContentValues()
            values.put("_display_name", name)
            values.put("mime_type", mime)
            rel = "Pictures/SAI" if is_image else "Download/SAI"
            values.put("relative_path", rel)
            collection = (autoclass("android.provider.MediaStore$Images$Media") if is_image
                          else autoclass("android.provider.MediaStore$Downloads")).EXTERNAL_CONTENT_URI
            resolver = activity.getContentResolver()
            uri = resolver.insert(collection, values)
            if uri is None:
                raise RuntimeError("MediaStore refused the file")
            out = resolver.openOutputStream(uri)
            inp = autoclass("java.io.FileInputStream")(path)
            copy_java_stream(inp, out)
            inp.close()
            out.close()
            self._media_notice(self.t("saved_to").format(rel + "/" + name))
        except Exception as exc:
            save_crash("Saving media failed:\n" + traceback.format_exc())
            try:
                import shutil
                target = os.path.join(os.path.expanduser("~"), "Downloads")
                if not os.path.isdir(target):
                    raise RuntimeError(str(exc))
                shutil.copy(path, os.path.join(target, name))
                self._media_notice(self.t("saved_to").format(os.path.join(target, name)))
            except Exception:
                self._media_notice(self.t("save_failed").format(self.friendly_error(exc)))

    def friendly_error(self, exc):
        """A short, non-technical error text in the app language. Details go to the crash log only."""
        if isinstance(exc, LocalizedError):
            return str(exc)
        code = ""
        key = "err_generic"
        if isinstance(exc, urllib.error.HTTPError):
            c = int(exc.code)
            code = " [%d]" % c
            if c in (401, 403):
                key = "server_missing"
                Clock.schedule_once(lambda dt: self._session_expired(), 0)
            elif c == 413:
                key = "file_too_big"
            elif c == 415:
                key = "err_unsupported"
            elif c == 422:
                key = "err_cannot"
            elif c == 429:
                key = "err_too_many"
            elif c in (400, 404):
                key = "err_bad"
            elif c >= 500:
                key = "err_busy"
            try:                                    # short technical hint from the server (provider names only)
                detail = json.loads(exc.read(400).decode("utf-8", "replace")).get("detail", "")
                if isinstance(detail, str) and detail:
                    code += "\n" + detail[:140]
            except Exception:
                pass
        elif isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
            key = "err_timeout"
        elif isinstance(exc, OSError) or str(exc) == "network":
            key = "err_network"
        else:
            where = ""
            try:                                             # where it happened: our last two code lines
                tb = exc.__traceback__
                mine = []
                while tb is not None:
                    if str(tb.tb_frame.f_code.co_filename).endswith("main.py"):
                        mine.append(tb.tb_frame.f_code.co_name[:22] + ":" + str(tb.tb_lineno))
                    tb = tb.tb_next
                if mine:
                    where = " @" + ">".join(mine[-2:])
            except Exception:
                pass
            code = " [" + type(exc).__name__[:30] + where + "]"
        return self.t(key) + code

    # ------------------------------------------------------------------ Tajik voice: short pieces, text first
    def _tajik_stream(self, text):
        self._tajik_gen = getattr(self, "_tajik_gen", 0) + 1
        gen = self._tajik_gen
        threading.Thread(target=self._tajik_stream_run, args=(strip_media(text)[:900], gen), daemon=True).start()

    def _tajik_stream_run(self, text, gen):
        chunks = split_for_speech(text)
        if not chunks:
            Clock.schedule_once(lambda dt: self._voice_ready(), 0)
            return
        got = {}

        def fetch(i):
            got[i] = self._tajik_speech_path(chunks[i])

        fetch(0)
        for i in range(len(chunks)):
            if gen != getattr(self, "_tajik_gen", 0) or not got.get(i):
                Clock.schedule_once(lambda dt: self._voice_ready(), 0)
                return                      # stopped by the user, or the voice failed (the user was told once)
            following = None
            if i + 1 < len(chunks):
                following = threading.Thread(target=fetch, args=(i + 1,), daemon=True)
                following.start()           # the next piece is made while this one is spoken
            started = threading.Event()

            def play(dt, path=got[i], size=len(chunks[i])):
                self.set_orb_state("speaking", min(20.0, 1.0 + size * 0.08))
                self._play_server_audio(path)
                started.set()

            Clock.schedule_once(play, 0)
            started.wait(5)
            time.sleep(0.4)
            while gen == getattr(self, "_tajik_gen", 0):
                player = getattr(self, "_server_speech_player", None)
                try:
                    playing = bool(player is not None and player.isPlaying())
                except Exception:
                    playing = False
                if not playing:
                    break
                time.sleep(0.2)
            if following is not None:
                following.join(timeout=100)
        if gen == getattr(self, "_tajik_gen", 0):
            Clock.schedule_once(lambda dt: self.set_orb_state("idle"), 0)

    # ------------------------------------------------------------------ first start: language, name, account
    def start_onboarding(self):
        if getattr(self, "_ob_active", False):
            return
        self._ob_active = True
        if self.onboarded:
            self._ob_account_step("login")
        else:
            self._ob_welcome_step()

    def _ob_screen_get(self):
        screen = getattr(self, "_ob_screen", None)
        if screen is None or screen.parent is None:
            screen = AuroraScreen(self)
            Window.add_widget(screen)
            self._ob_screen = screen
            self.sheet_opened(screen)
        return screen

    def _ob_welcome_step(self):
        """The very first screen: a thank-you and a warm welcome in all three languages."""
        theme = THEMES[self.theme_index]
        screen = self._ob_screen_get()
        screen.set_step(-1)
        panel = screen.new_panel(400)
        brand = "S.A.I"
        panel.add_widget(make_label("Хуш омадед!", 26, theme["text"], True, size_hint_y=None, height=dp(40)))
        panel.add_widget(make_label("Ташаккур, ки " + brand + "-ро интихоб кардед.\nМан дар ҳар қадам ҳамроҳ ва ёвари шумо мешавам.", 14,
                                    theme["text"], False, size_hint_y=None, height=dp(66)))
        panel.add_widget(make_label("Добро пожаловать!  Спасибо, что выбрали " + brand + ".\nЯ буду рядом на каждом шаге.", 12, muted(), False,
                                    size_hint_y=None, height=dp(52)))
        panel.add_widget(make_label("Welcome!  Thank you for choosing " + brand + ".\nI will be with you every step of the way.", 12, muted(), False,
                                    size_hint_y=None, height=dp(52)))
        panel.add_widget(Widget())
        panel.add_widget(FrostButton(tint=screen.acc2, alpha=0.60, edge=0.65, radius=22,
                                     text="[b]Оғоз  ·  Начать  ·  Start[/b]", font_size=dp(16), halign="center",
                                     size_hint_y=None, height=dp(54), color=(1, 1, 1, 1),
                                     on_release=lambda *_: self.cover_transition(self._ob_language_step, 1.0)))

    def _ob_language_step(self):
        theme = THEMES[self.theme_index]
        screen = self._ob_screen_get()
        screen.set_step(0)
        panel = screen.new_panel(330)
        panel.add_widget(make_label("Choose your language", 20, theme["text"], True, size_hint_y=None, height=dp(34)))
        panel.add_widget(make_label("Тоҷикӣ  ·  Русский  ·  English", 12, muted(), False, size_hint_y=None, height=dp(22)))
        panel.add_widget(Widget(size_hint_y=None, height=dp(4)))

        def pick(code):
            self.language = code
            self.save_settings()
            screen.stepbar_labels(code)
            self.cover_transition(self._ob_name_step, 1.2)

        for label, code, hello in (("Тоҷикӣ", "tg", "Салом"), ("Русский", "ru", "Привет"), ("English", "en", "Hello")):
            btn = FrostButton(tint=screen.acc, alpha=0.12, radius=20, markup=True,
                              text="[b]" + label + "[/b]\n[size=12sp][color=" + muted() + "]" + hello + "[/color][/size]",
                              font_size=dp(18), halign="center", size_hint_y=None, height=dp(66),
                              color=hex_color(theme["text"]))
            btn.bind(on_release=lambda inst, c=code: pick(c))
            panel.add_widget(btn)

    def _ob_name_step(self):
        theme = THEMES[self.theme_index]
        screen = self._ob_screen_get()
        screen.set_step(1)
        panel = screen.new_panel(300)
        panel.add_widget(make_label(self.t("ob_name_title"), 17, theme["text"], True, size_hint_y=None, height=dp(44)))
        wrap, name_input = glass_input(self, self.t("ob_name_hint"), self.assistant_name or "S.A.I", center=True, size=18)
        panel.add_widget(wrap)
        screen.set_wordmark(name_input.text)
        name_input.bind(text=lambda inst, value: (screen.set_wordmark(value), screen.kick()))
        panel.add_widget(make_label(self.t("ob_name_note"), 11, muted(), False, size_hint_y=None, height=dp(52)))
        panel.add_widget(Widget())

        def go(*_):
            name = " ".join(name_input.text.split())[:24] or "S.A.I"
            self.assistant_name = name
            self.title = name
            self.save_settings()
            screen.set_wordmark(name)
            self.cover_transition(lambda: self._ob_account_step("signup"), 1.2)

        name_input.bind(on_text_validate=go)
        panel.add_widget(FrostButton(tint=screen.acc2, alpha=0.60, edge=0.65, radius=22, text="[b]" + mk(self.t("ob_continue")) + "[/b]",
                                     font_size=dp(16), halign="center", size_hint_y=None, height=dp(54),
                                     color=(1, 1, 1, 1), on_release=go))

    def _ob_account_step(self, mode="login", prefill="", note=""):
        theme = THEMES[self.theme_index]
        signup = mode == "signup"
        screen = self._ob_screen_get()
        screen.set_step(2)
        screen.set_wordmark(self.assistant_name or "S.A.I")
        panel = screen.new_panel(500 if signup else 440)
        panel.add_widget(make_label(self.t("ob_account_title"), 17, theme["text"], True, size_hint_y=None, height=dp(30)))
        tabs = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(46), spacing=dp(6))
        user_in_holder = {}

        def switch(new_mode):
            if new_mode != mode:
                text = user_in_holder["u"].text if "u" in user_in_holder else ""
                self._ob_account_step(new_mode, text)

        for key, target in (("ob_login_tab", "login"), ("ob_signup_tab", "signup")):
            on = target == mode
            tab = FrostButton(tint=screen.acc2 if on else screen.acc, alpha=0.50 if on else 0.08, radius=16,
                              text=("[b]" + mk(self.t(key)) + "[/b]") if on else mk(self.t(key)), font_size=dp(13),
                              halign="center", color=hex_color(theme["text"]))
            tab.bind(on_release=lambda inst, m=target: switch(m))
            tabs.add_widget(tab)
        panel.add_widget(tabs)
        user_wrap, user_in = glass_input(self, self.t("ob_username"), prefill)
        pass_wrap, pass_in = glass_input(self, self.t("ob_password"), password=True)
        user_in_holder["u"] = user_in
        user_in.bind(text=lambda *_: screen.kick())
        pass_in.bind(text=lambda *_: screen.kick())
        panel.add_widget(user_wrap)
        panel.add_widget(pass_wrap)
        pass2_in = None
        if signup:
            pass2_wrap, pass2_in = glass_input(self, self.t("ob_password2"), password=True)
            panel.add_widget(pass2_wrap)
        status = make_label(note, 12, "#FFB4B4", False, size_hint_y=None, height=dp(44))
        panel.add_widget(status)
        state = {"busy": False}

        def submit(*_):
            if state["busy"]:
                return
            username, password = user_in.text.strip(), pass_in.text
            if not username or not password:
                status.text = self.t("ob_err_empty")
                return
            if signup:
                if not re.fullmatch(r"[\w.\-]{3,32}", username):
                    status.text = self.t("ob_err_username")
                    return
                if len(password) < 8:
                    status.text = self.t("ob_err_short")
                    return
                if password != pass2_in.text:
                    status.text = self.t("ob_err_mismatch")
                    return
            state["busy"] = True
            status.text = self.t("ob_wait")
            screen.spin(True)
            threading.Thread(target=self._auth_job, args=(mode, username, password, screen, status, state), daemon=True).start()

        pass_in.bind(on_text_validate=submit)
        panel.add_widget(FrostButton(tint=screen.acc2, alpha=0.60, edge=0.65, radius=22,
                                     text="[b]" + mk(self.t("ob_signup_btn" if signup else "ob_login_btn")) + "[/b]",
                                     font_size=dp(16), halign="center", size_hint_y=None, height=dp(54),
                                     color=(1, 1, 1, 1), on_release=submit))
        divider = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(18), spacing=dp(10))
        divider.add_widget(Hair(pos_hint={"center_y": 0.5}))
        divider.add_widget(make_label(self.t("ob_or"), 11, muted(), False, size_hint_x=None, width=dp(30)))
        divider.add_widget(Hair(pos_hint={"center_y": 0.5}))
        panel.add_widget(divider)
        panel.add_widget(FrostButton(tint=(1, 1, 1), alpha=0.09, radius=20, text=mk(self.t("ob_google_btn")),
                                     font_size=dp(14), halign="center", size_hint_y=None, height=dp(48),
                                     color=hex_color(theme["text"]),
                                     on_release=lambda inst: setattr(status, "text", self.t("ob_google_soon"))))

    def _auth_job(self, mode, username, password, popup, status, state):
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = None
        body = json.dumps({"username": username, "password": password}, ensure_ascii=False).encode("utf-8")
        result, message = None, ""
        for attempt in (1, 2):      # the second try covers a sleeping Render server
            try:
                req = urllib.request.Request(SERVER_BASE + ("/auth/signup" if mode == "signup" else "/auth/signin"),
                                             data=body, method="POST", headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=90, context=ctx) as response:
                    result = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    message = self.t("ob_err_wrong")
                elif exc.code == 409:
                    message = self.t("ob_err_exists")
                elif exc.code in (400, 422):
                    message = self.t("ob_err_username")
                elif exc.code in (502, 503, 504) and attempt == 1:
                    time.sleep(5)
                    continue
                else:
                    message = self.friendly_error(exc)
                break
            except Exception as exc:
                save_crash("Sign-in request failed:\n" + traceback.format_exc())
                message = self.friendly_error(exc)
                if attempt == 1:
                    continue

        def done(dt):
            state["busy"] = False
            if result and result.get("access_token"):
                self.server_token = str(result["access_token"])
                self.username = str(result.get("username", username))
                self.onboarded = True
                self._ob_active = False
                self.save_settings()
                popup.spin(False)
                self._switch_chat_owner()
                self.pull_chats()
                if not self.profile_done:
                    self.cover_transition(lambda: self._ob_profile_step(popup), 1.3)
                    return
                self._ob_finish(popup)
            else:
                popup.spin(False)
                status.text = message or self.t("err_generic")

        Clock.schedule_once(done, 0)

    def _ob_finish(self, popup):
        def go():
            popup.dismiss()
            self.build_interface()
            welcome = self.t("ob_welcome").format(self.username, self.assistant_name)
            self.show_response(welcome)
            self.speak_answer(welcome)
        self.cover_transition(go, 1.5)

    def _ob_profile_step(self, screen):
        """After sign-in: first name, last name, birth year/age and gender (can be skipped)."""
        theme = THEMES[self.theme_index]
        screen.set_step(3)
        panel = screen.new_panel(470)
        panel.add_widget(make_label(self.t("pf_title"), 18, theme["text"], True, size_hint_y=None, height=dp(30)))
        first_w, first_in = glass_input(self, self.t("pf_first"))
        last_w, last_in = glass_input(self, self.t("pf_last"))
        age_w, age_in = glass_input(self, self.t("pf_age"))
        for w in (first_w, last_w, age_w):
            panel.add_widget(w)
        chosen = {"g": ""}
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48), spacing=dp(8))
        buttons = {}

        def pick(code):
            chosen["g"] = code
            for key, b in buttons.items():
                on = key == code
                b._fr_a0 = 0.50 if on else 0.10
                b._fr_body_c.a = b._fr_a0
                b.text = ("[b]" + mk(self.t("pf_male" if key == "m" else "pf_female")) + "[/b]") if on else mk(self.t("pf_male" if key == "m" else "pf_female"))

        for key, label in (("m", "pf_male"), ("f", "pf_female")):
            b = FrostButton(tint=screen.acc2, alpha=0.10, radius=16, text=mk(self.t(label)), font_size=dp(14), halign="center",
                            color=hex_color(theme["text"]))
            b.bind(on_release=lambda inst, k=key: pick(k))
            buttons[key] = b
            row.add_widget(b)
        panel.add_widget(row)
        status = make_label("", 12, "#FFB4B4", False, size_hint_y=None, height=dp(22))
        panel.add_widget(status)
        panel.add_widget(make_label(self.t("pf_note"), 11, muted(), False, size_hint_y=None, height=dp(30)))

        def finish(skip=False):
            first = " ".join(first_in.text.split())[:24]
            last = " ".join(last_in.text.split())[:30]
            if not skip:
                if not first or not last:
                    status.text = self.t("pf_err")
                    return
                facts = ["Номи ман " + first, "Насаби ман " + last]
                digits = re.sub(r"\D", "", age_in.text)
                if digits:
                    n = int(digits)
                    year = datetime.now().year
                    if 1900 <= n <= year:
                        facts.append("Соли таваллуди ман %d (синну сол: %d)" % (n, year - n))
                    elif 3 <= n <= 110:
                        facts.append("Синну соли ман %d" % n)
                if chosen["g"]:
                    facts.append("Ҷинси ман: " + ("мард" if chosen["g"] == "m" else "зан"))
                for fact in facts:
                    self.profile.add(fact)
            self.profile_done = True
            self.save_settings()
            self._ob_finish(screen)

        panel.add_widget(FrostButton(tint=screen.acc2, alpha=0.60, edge=0.65, radius=22, text="[b]" + mk(self.t("ob_continue")) + "[/b]",
                                     font_size=dp(16), halign="center", size_hint_y=None, height=dp(52),
                                     color=(1, 1, 1, 1), on_release=lambda *_: finish(False)))
        skip = FrostButton(tint=(1, 1, 1), alpha=0.06, radius=18, text=mk(self.t("pf_skip")), font_size=dp(13), halign="center",
                           size_hint_y=None, height=dp(38), color=hex_color(theme["text"]))
        skip.bind(on_release=lambda *_: finish(True))
        panel.add_widget(skip)

    def _session_expired(self):
        if getattr(self, "_ob_active", False):
            return
        self._ob_active = True
        self.server_token = ""
        self.save_settings()
        self._ob_account_step("login", self.username, self.t("session_expired"))

    def rename_assistant(self, *_):
        theme = THEMES[self.theme_index]
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        box.add_widget(make_label(self.t("ob_name_title"), 16, theme["accent"], True, size_hint_y=None, height=dp(38)))
        name_wrap, name_input = glass_input(self, "", self.assistant_name, center=True, size=16)
        box.add_widget(name_wrap)
        popup = self.make_popup(box, size_hint=(0.92, None), height=dp(220), pos_hint={"center_x": 0.5, "top": 0.97})

        def save(*_):
            self.assistant_name = " ".join(name_input.text.split())[:24] or "S.A.I"
            self.title = self.assistant_name
            self.save_settings()
            popup.dismiss()
            if getattr(self, "settings_popup", None) is not None:
                self.settings_popup.dismiss()
            self.build_interface()

        name_input.bind(on_text_validate=save)
        box.add_widget(self.make_button(self.t("ob_continue"), save, theme["accent"], 46))
        popup.open()

    def logout_account(self, *_):
        if getattr(self, "settings_popup", None) is not None:
            self.settings_popup.dismiss()
        self.server_token = ""
        self.save_settings()
        self._ob_active = True
        self._ob_account_step("login", self.username)

    def delete_account_ask(self, *_):
        theme = THEMES[self.theme_index]
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        box.add_widget(make_label(self.t("acct_delete_ask"), 14, theme["text"], False))
        popup = self.make_popup(box, size_hint=(0.9, None), height=dp(230))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))

        def confirm(*_):
            popup.dismiss()
            threading.Thread(target=self._delete_account_job, daemon=True).start()

        row.add_widget(self.make_button(self.t("acct_delete_yes"), confirm, "#FF5C6C", 46))
        row.add_widget(self.make_button(self.t("close"), lambda *_: popup.dismiss(), theme["accent2"], 46))
        box.add_widget(row)
        popup.open()

    def _delete_account_job(self):
        try:
            self._server_call("/auth/delete", b"{}", "application/json", timeout=60)
        except Exception as exc:
            text = self.friendly_error(exc)
            Clock.schedule_once(lambda dt: self.show_response(text), 0)
            return

        def after(dt):
            if getattr(self, "settings_popup", None) is not None:
                self.settings_popup.dismiss()
            self.server_token = ""
            self.username = ""
            self.save_settings()
            self.show_response(self.t("acct_deleted"))
            self._ob_active = True
            self._ob_account_step("signup")

        Clock.schedule_once(after, 0)

    def send_command(self, *_):
        if not hasattr(self, "command_input"):
            return
        command_text = self.command_input.text.strip()
        pending = getattr(self, "_pending_attachment", None)
        if not command_text and not pending:
            return
        self.command_input.text = ""
        chat_id = self.chats.current_id
        if pending:                         # picture/file + the typed instruction go to the AI together
            self._pending_attachment = None
            self._refresh_attach_strip()
            self._sync_action_mode()
            marker = ("[[img:" if pending["mime"].startswith("image/") else "[[file:") + pending["path"] + "]]"
            shown = (command_text + "\n" if command_text else "") + marker
            self._set_delivery(pending["path"], "sending")
            self.add_chat_message(chat_id, "user", shown)
            for fact in extract_facts(command_text):
                self.profile.add(fact)
            self._process_attachment(chat_id, pending, command_text)
            return
        # Keep prior turns for the API before adding the current user message.
        chat = self.chats.get(chat_id) or self.chats.current()
        history = []
        for item in chat.get("messages", [])[-24:]:
            role = "assistant" if item.get("role") == "jarvis" else item.get("role")
            if role in ("user", "assistant") and item.get("text"):
                history.append({"role": role, "content": self.brand(item["text"])[:4000]})
        history = self._with_memory(chat_id, history, command_text)
        for fact in extract_facts(command_text):
            self.profile.add(fact)
        self.add_chat_message(chat_id, "user", command_text)
        if parse_chats_analysis(command_text):
            self._start_chat_analysis(chat_id, command_text)
            return
        media = parse_media_intent(command_text)
        if media is not None:
            self._start_media_job(chat_id, media[0], media[1])
            return
        info_fn = find_info_command(command_text)
        if info_fn is not None:
            self.set_orb_state("thinking")
            self.busy_start("think")
            threading.Thread(target=self._run_info, args=(chat_id, info_fn), daemon=True).start()
            return
        answer = run_command(self, command_text)
        if answer == self.t("unknown"):
            if not self.server_token.strip():
                answer = self.t("server_missing")
                self.deliver_answer(chat_id, answer)
                return
            self.set_orb_state("thinking")
            self.busy_start("think")
            threading.Thread(target=self._ask_server,
                             args=(chat_id, command_text, history), daemon=True).start()
            return
        self.deliver_answer(chat_id, answer)
        self.set_orb_state("speaking", min(4.0, 0.8 + len(answer) * 0.03))


    def _with_memory(self, chat_id, history, query=""):
        """Everything the assistant should know before answering, as two leading messages: who the user is,
        all older commands of this chat, the topics of other chats and the earlier messages that fit the question.
        (The server accepts 12 messages; older turns are squeezed into this note instead of being dropped.)"""
        try:
            def one_line(text, n):
                return " ".join(strip_media(text).split())[:n]

            recent = history[-10:]
            cut = max(0, len(history) - 10)
            chat = self.chats.get(chat_id)
            parts = ["Background about this user and this app (use it when relevant, never mention this note). "
                     "assistant name: " + str(self.assistant_name) + "; app language: " + str(self.language) +
                     ("; account: " + str(self.username) if self.username else "") + "."]
            facts = [f["text"] for f in self.profile.facts][-40:]
            if facts:
                block, size = [], 0
                for fact in reversed(facts):
                    if size + len(fact) > 1300:
                        break
                    block.append("- " + fact)
                    size += len(fact) + 3
                parts.append("What the user told about himself:\n" + "\n".join(reversed(block)))
            messages = chat.get("messages", []) if chat else []
            older = [one_line(m.get("text", ""), 110) for m in messages[:-10] if m.get("role") == "user"]
            older = [o for o in older if o][-30:]
            if older:
                parts.append("Earlier user messages in this chat (oldest first):\n" + "\n".join("- " + o for o in older))
            titles = [c.get("title") for c in self.chats.sorted_chats() if c is not chat and c.get("title")][:14]
            if titles:
                parts.append("Topics of the user's other chats: " + "; ".join(titles) + ".")
            tokens = {w[:5] for w in re.findall(r"\w{4,}", str(query).lower())}
            if tokens:
                scored = []
                for c in self.chats.chats:
                    msgs = c.get("messages", [])
                    for i, m in enumerate(msgs):
                        if c is chat and i >= len(msgs) - 10:
                            continue
                        low = strip_media(m.get("text", "")).lower()
                        score = sum(1 for t in tokens if t in low)
                        if score:
                            scored.append((score, c.get("updated", ""), c.get("title") or "?", m.get("role"), m.get("text", "")))
                scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
                lines = ["- [" + t + "] " + ("user" if r == "user" else "assistant") + ": " + one_line(txt, 200)
                         for _sc, _up, t, r, txt in scored[:6]]
                if lines:
                    parts.append("Earlier messages that may be related:\n" + "\n".join(lines))
            note = "\n\n".join(parts)[:3800]
            return [{"role": "user", "content": note}, {"role": "assistant", "content": "OK."}] + recent
        except Exception:
            save_crash("Memory block failed:\n" + traceback.format_exc())
            return history[-12:]

    def _maybe_retitle(self, chat_id):
        """Name the chat after its topic (not after the first command): after the first answer, and again later."""
        try:
            chat = self.chats.get(chat_id)
            if not chat or not self.server_token.strip():
                return
            count, done = len(chat.get("messages", [])), int(chat.get("titled_at", 0))
            step = 2 if (count >= 2 and done < 2) else 10 if (count >= 10 and done < 10) else 30 if (count >= 30 and done < 30) else 0
            if not step:
                return
            chat["titled_at"] = step
            self.chats.save()
            lines = []
            for m in chat["messages"][:14]:
                lines.append(("user: " if m.get("role") == "user" else "assistant: ") + " ".join(strip_media(m.get("text", "")).split())[:180])
            threading.Thread(target=self._title_job, args=(chat_id, "\n".join(lines)), daemon=True).start()
        except Exception:
            save_crash("Retitle failed:\n" + traceback.format_exc())

    def _title_job(self, chat_id, transcript):
        try:
            name = {"tg": "Tajik (Cyrillic)", "ru": "Russian", "en": "English"}[self._lang_code()]
            ask = ("Name the topic of this conversation with a very short title: 2 to 5 words, in " + name +
                   ", no quotes, no final punctuation. Output only the title.\n\nConversation:\n" + transcript)
            payload = json.dumps({"message": ask[:3900], "history": [], "web_search": False,
                                  "language": self._lang_code(), "assistant_name": self.assistant_name},
                                 ensure_ascii=False).encode("utf-8")
            _headers, data = self._server_call("/chat", payload, "application/json", timeout=60)
            answer = str(json.loads(data.decode("utf-8")).get("answer", "")).strip()
            title = answer.splitlines()[0].strip().strip("\"'«»*#").strip() if answer else ""
            for prefix in ("Title:", "Сарлавҳа:", "Название:", "Тема:"):
                if title.lower().startswith(prefix.lower()):
                    title = title[len(prefix):].strip()
            title = title.rstrip(".!:;,")[:40]
            if len(title) < 3:
                return

            def apply(dt):
                chat = self.chats.get(chat_id)
                if chat is not None:
                    chat["title"] = title
                    self.chats.save()
                    if self.chats.current_id == chat_id and hasattr(self, "response_title"):
                        self.response_title.text = self.chat_title()
            Clock.schedule_once(apply, 0)
        except Exception:
            save_crash("Title job failed:\n" + traceback.format_exc())

    def _voice_ready(self):
        if self._busy:
            return
        if hasattr(self, "status_label"):
            self.status_label.text = self.t("ready")

    def _status_after_answer(self):
        if self._busy:
            return
        if hasattr(self, "status_label"):
            waiting = self.speak_enabled and self.server_token.strip()
            self.status_label.text = self.t("voice_preparing" if waiting else "ready")
            if waiting:
                Clock.schedule_once(lambda dt: self._voice_ready(), 45)   # safety: never stay in "preparing"

    def _run_info(self, chat_id, fn):
        try:
            answer = fn(self)
        except Exception:
            save_crash("Info command failed:\n" + traceback.format_exc())
            answer = self.t("info_error")
        Clock.schedule_once(lambda dt: self._finish_server_answer(chat_id, answer), 0)

    def _ask_server(self, chat_id, message, history):
        import ssl
        url = "https://jarvis-ai-server-3vuo.onrender.com/chat"
        payload = {
            "message": message[:4000],
            "history": history[-12:],
            "web_search": False,
            "language": self.language if self.language in ("tg", "ru", "en") else "tg",
            "assistant_name": self.assistant_name,
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

        answer, last, net_failed = "", "", False
        for attempt in (1, 2):  # дафъаи дуюм барои Render-и хобида
            try:
                req = urllib.request.Request(
                    url, data=body, method="POST",
                    headers={"Content-Type": "application/json",
                             "Authorization": "Bearer " + self.server_token})
                with urllib.request.urlopen(req, timeout=90, context=ctx) as response:
                    data = json.loads(response.read().decode("utf-8"))
                answer = extract(data)
                if not answer:
                    keys = ",".join(list(data.keys())[:6]) if isinstance(data, dict) else type(data).__name__
                    last = self.t("reply_format").format(keys)
                break
            except urllib.error.HTTPError as exc:
                save_crash("Chat HTTP error: %s" % exc.code)
                if exc.code in (401, 403):
                    answer = self.friendly_error(exc)
                else:
                    last = self.friendly_error(exc)
                break
            except Exception as exc:
                save_crash("Chat request failed:\n" + traceback.format_exc())
                last = self.friendly_error(exc)
                net_failed = True
        if not answer:
            answer = last or self.t("server_error")
            if net_failed:
                def offer(dt):
                    self._retry = lambda: self._resend_text(chat_id, message, history)
                    self._finish_server_answer(chat_id, answer + "\n" + RETRY_MARK)
                Clock.schedule_once(offer, 0)
                return
        Clock.schedule_once(lambda dt: self._finish_server_answer(chat_id, answer), 0)

    def _resend_text(self, chat_id, message, history):
        self.set_orb_state("thinking")
        self.busy_start("think")
        threading.Thread(target=self._ask_server, args=(chat_id, message, history), daemon=True).start()

    def _finish_server_answer(self, chat_id, answer):
        m = re.search(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)", str(answer))
        if m:
            # the AI wrote a picture link that cannot be shown: make the picture itself from its description
            from urllib.parse import urlparse, parse_qs
            try:
                prompt = (parse_qs(urlparse(m.group(2)).query).get("prompt") or [""])[0]
            except Exception:
                prompt = ""
            prompt = (prompt or m.group(1)).replace("+", " ").strip()
            rest = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", str(answer)).strip()
            if prompt and not rest:
                self._start_media_job(chat_id, "image", prompt)
                return
            answer = rest or answer
        self._status_after_answer()
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
        popup = self.make_popup(content, size_hint=(0.92, None), height=dp(280))
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
        text = self.brand(answer).strip()[:4000]
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
        Clock.schedule_once(lambda dt: self._voice_ready(), 0)
        if (not self.speak_enabled and not force) or not answer:
            return
        try:
            self.tts.stop()
            def attempt(_dt=None, tries=0):
                if not self.speak_enabled and not force:
                    return
                try:
                    result = self.tts.speak(answer[:4000], lang=speech_lang_for(answer, self.language),
                                           pitch=VOICE_LOCAL_PITCH.get(self.voice_profile, 1.0))
                    if result == "starting" and tries < 8:
                        Clock.schedule_once(lambda dt: attempt(dt, tries + 1), 0.5)
                except Exception as exc:
                    save_crash("Local speech fallback failed: " + str(exc))
                    if not self.tts_error_noted:
                        self.tts_error_noted = True
                        tajik = speech_lang_for(answer, self.language) == "tg"
                        self.show_response(self.t("tts_no_tajik" if tajik else "tts_failed_all"))
            Clock.schedule_once(attempt, 0.15)
        except Exception:
            save_crash("Could not schedule local speech fallback:\n" + traceback.format_exc())

    def deliver_answer(self, chat_id, answer):
        """Show a JARVIS answer. With server speech the words appear while they are spoken."""
        Clock.schedule_once(lambda dt: self._maybe_retitle(chat_id), 10)
        answer = self.brand(answer)
        if RETRY_MARK in str(answer):
            self.add_chat_message(chat_id, "jarvis", answer)       # an error with a Retry button: shown, not read aloud
            return
        text = strip_media(answer)[:4000]
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
        if self._wants_tajik_voice(text):
            Clock.schedule_once(lambda dt: self.add_chat_message(chat_id, "jarvis", answer), 0)   # text at once
            self._tajik_stream(text)
            return
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
                         "Authorization": "Bearer " + self.server_token,
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
            detail = self.friendly_error(exc)
            save_crash("Synced speech failed: " + detail + "\n" + traceback.format_exc())
            def fallback(dt):
                self.add_chat_message(chat_id, "jarvis", answer)
                self._note_server_speech_error(detail)
                self._speak_local_fallback(text)
            Clock.schedule_once(fallback, 0)

    def _start_synced_answer(self, chat_id, answer, text, path, words):
        """Start the audio, then reveal the answer word by word at the spoken positions."""
        self.chats.add_message(chat_id, "jarvis", answer)
        self.busy_start("answer")
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
                set_label_text(label, answer)
            self.busy_stop()
            return
        begin = time.monotonic()
        state = {"shown": 0}

        def finish():
            set_label_text(label, answer)
            self.scroll_chat_down()
            self.busy_stop()

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
                set_label_text(label, text[:chars])
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
        self._tajik_gen = getattr(self, "_tajik_gen", 0) + 1      # also stops a running Tajik read-out
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
        if self._wants_tajik_voice(text):
            self._tajik_stream(text)
            return
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
                         "Authorization": "Bearer " + self.server_token,
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
            detail = self.friendly_error(exc)
            save_crash("Server speech error: " + detail)
            Clock.schedule_once(lambda dt: self._note_server_speech_error(detail), 0)
            Clock.schedule_once(lambda dt: self._speak_local_fallback(text, force), 0)
        except Exception as exc:
            save_crash("Server speech failed:\n" + traceback.format_exc())
            detail = self.friendly_error(exc)
            Clock.schedule_once(lambda dt: self._note_server_speech_error(detail), 0)
            Clock.schedule_once(lambda dt: self._speak_local_fallback(text, force), 0)

    def _wants_tajik_voice(self, text):
        """Tajik text is read by the Tajik voice service (Edge has no Tajik voice) when its address is set."""
        if not self.server_token.strip():
            return False
        if any(ch in "ғӣқӯҳҷҒӢҚӮҲҶ" for ch in text):
            return True
        letters = [ch for ch in text if ch.isalpha()]
        cyr = sum(1 for ch in letters if "\u0400" <= ch <= "\u04ff")
        russian_only = any(ch in "ыэЫЭ" for ch in text)
        return self.language == "tg" and bool(letters) and cyr / float(len(letters)) > 0.6 and not russian_only

    def _tajik_speech_path(self, text):
        """Blocking: ask the Tajik voice service for a WAV file. Returns its path, or None on any failure."""
        import ssl
        import tempfile
        try:
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except Exception:
                ctx = None
            payload = json.dumps({"text": text[:1500], "language": "tg"}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request("https://jarvis-ai-server-3vuo.onrender.com/speak", data=payload, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": "Bearer " + self.server_token})
            with urllib.request.urlopen(req, timeout=90, context=ctx) as response:
                audio = response.read(30 * 1024 * 1024)
            if not audio or audio[:4] != b"RIFF":
                raise RuntimeError("not a WAV file")
            fd, path = tempfile.mkstemp(prefix="jarvis_tg_", suffix=".wav")
            with os.fdopen(fd, "wb") as out:
                out.write(audio)
            return path
        except Exception as exc:
            save_crash("Tajik voice failed:\n" + traceback.format_exc())
            detail = self.friendly_error(exc)
            Clock.schedule_once(lambda dt: self._note_tajik_voice_error(detail), 0)
            return None

    def _note_tajik_voice_error(self, detail):
        if getattr(self, "tajik_voice_error_noted", False):
            return
        self.tajik_voice_error_noted = True
        try:
            self.show_response(self.t("tajik_voice_failed") + "\n" + detail)
        except Exception:
            pass

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
            self._voice_ready()
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
        self._sync_action_mode()

    def _vocab(self):
        """Word -> weight: words from the user's chats (weighted) plus a small built-in list. Cached for a while."""
        now = time.monotonic()
        cache = getattr(self, "_vocab_cache", None)
        if cache is not None and now - cache[0] < 20:
            return cache[1]
        vocab = {}
        for w in BASE_WORDS.get(self.language, []) + BASE_WORDS["tg"]:
            vocab[w] = vocab.get(w, 0) + 1
        try:
            for chat in self.chats.chats[-30:]:
                for m in chat.get("messages", [])[-60:]:
                    for w in re.findall(r"[A-Za-zА-Яа-яЁёҒғӢӣҚқӮӯҲҳҶҷ]{4,24}", strip_media(m.get("text", ""))[:1500]):
                        key = w[0].upper() + w[1:].lower() if w[0].isupper() else w.lower()
                        vocab[key] = vocab.get(key, 0) + 3
        except Exception:
            pass
        self._vocab_cache = (now, vocab)
        return vocab

    def _update_suggestions(self, value):
        strip = getattr(self, "sugg_strip", None)
        if strip is None:
            return
        strip.clear_widgets()
        m = re.search(r"([A-Za-zА-Яа-яЁёҒғӢӣҚқӮӯҲҳҶҷ]{2,})$", value or "")
        found = []
        if m:
            prefix = m.group(1).lower()
            ranked = sorted(self._vocab().items(), key=lambda kv: (-kv[1], len(kv[0])))
            for word, _w in ranked:
                if word.lower().startswith(prefix) and word.lower() != prefix and word not in found:
                    found.append(word)
                if len(found) == 3:
                    break
        if not found:
            strip.height = 0
            strip.opacity = 0
            return
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        strip.height = dp(38)
        strip.opacity = 1
        for word in found:
            b = FrostButton(tint=tint, alpha=0.14, radius=14, text=mk(word), font_size=dp(14), halign="center",
                            color=hex_color(theme["text"]))
            b.bind(on_release=lambda inst, w=word, pre=m.group(1): self._apply_suggestion(w, pre))
            strip.add_widget(b)

    def _apply_suggestion(self, word, prefix):
        text = self.command_input.text
        if text.endswith(prefix):
            if prefix[:1].isupper() and not word[:1].isupper():
                word = word[:1].upper() + word[1:]
            self.command_input.text = text[:len(text) - len(prefix)] + word + " "
            self.command_input.cursor = self.command_input.get_cursor_from_index(len(self.command_input.text))
        self.command_input.focus = True

    def on_action(self, *_):
        if self.command_input.text.strip() or getattr(self, "_pending_attachment", None):
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
                cell = emoji_cell(ch, {"size_hint_y": None, "height": dp(44)}, lambda inst, c=ch: put(c))
                if cell is not None:           # only full-colour emoji are offered
                    grid.add_widget(cell)
            scroll.scroll_y = 1

        for i, (label, _chars) in enumerate(EMOJI_GROUPS):
            t = emoji_cell(label, {}, lambda inst, k=i: fill(k))
            if t is None:
                continue
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
            self.busy_start("think")
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
        if label is not None and not (self._busy and state != "listening"):
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
        return GlassDialog(self, content, size_hint=size_hint, height=extra.get("height"),
                           pos_hint=extra.get("pos_hint"), auto_dismiss=extra.get("auto_dismiss", True))

    def glass_option(self, content, text, active, callback, height=54, tint=None):
        theme = THEMES[self.theme_index]
        btn = GlassButton(text=text, tint=tint or hex_color(theme["accent"])[:3], active=active,
                          size_hint_y=None, height=dp(height), font_size=dp(13), bold=active,
                          color=hex_color(theme["text"]), radius=18)
        btn.bind(on_release=callback)
        content.add_widget(btn)
        return btn

    # ------------------------------------------------------------------ frosted sheets (menu and its sections)
    def sheet_opened(self, sheet):
        self.open_popups.add(sheet)
        self.update_pause()

    def sheet_closed(self, sheet):
        self.open_popups.discard(sheet)
        self.update_pause()

    def _on_back_key(self, window, key, *args):
        if key != 27:
            return False
        for widget in Window.children:          # the first child is the top one
            if isinstance(widget, (GlassSheet, MediaViewer)) and not widget.closing:
                widget.dismiss()
                return True
            if isinstance(widget, AuroraScreen):
                return True                     # no leaving the first-run screens with the back key
        return False

    def frost_row(self, content, text, active, callback, height=54, tint=None, center=False):
        theme = THEMES[self.theme_index]
        btn = FrostButton(tint=tint or hex_color(theme["accent"])[:3], alpha=0.26 if active else 0.10,
                          marker=bool(active), text=("[b]" + mk(text) + "[/b]") if active else mk(text),
                          size_hint_y=None, height=dp(height), font_size=dp(15),
                          color=hex_color(theme["text"]), halign="center" if center else "left")
        btn.bind(on_release=callback)
        content.add_widget(btn)
        return btn

    def show_menu(self, *_):
        theme = THEMES[self.theme_index]
        accent = theme["accent"].lstrip("#")
        body = BoxLayout(orientation="vertical", spacing=dp(8))
        sheet = GlassSheet(self, dotted_name(self.assistant_name or "S.A.I"), body,
                           anchor="menu", height=368)
        items = (("menu_chats", self.show_chats), ("menu_new", self.new_chat),
                 ("menu_settings", self.show_settings), ("menu_about", self.show_about))
        for i, (key, action) in enumerate(items, 1):
            def go(inst, f=action):
                sheet.dismiss()
                Clock.schedule_once(lambda dt: f(), 0.14)
            btn = FrostButton(tint=hex_color(theme["accent"])[:3], alpha=0.11, size_hint_y=None, height=dp(60),
                              font_size=dp(16), color=hex_color(theme["text"]),
                              text="[color=#%s][b]%02d[/b][/color]   %s" % (accent, i, mk(self.t(key))))
            btn.bind(on_release=go)
            body.add_widget(btn)
        sheet.open()

    def show_chats(self, *_):
        theme = THEMES[self.theme_index]
        tint = hex_color(theme["accent"])[:3]
        body = BoxLayout(orientation="vertical", spacing=dp(8))
        scroll = ScrollView(do_scroll_x=False)
        grid = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        chats = self.chats.sorted_chats()
        for chat in chats:
            row = BoxLayout(orientation="horizontal", spacing=dp(6), size_hint_y=None, height=dp(64))
            active = chat["id"] == self.chats.current_id
            text = ("[b]" + mk(self.chat_title(chat)) + "[/b]" if active else mk(self.chat_title(chat))) + \
                   "\n[size=11sp][color=" + muted() + "]" + mk(format_time(chat.get("created", ""))) + "[/color][/size]"
            open_btn = FrostButton(tint=tint, alpha=0.24 if active else 0.10, marker=active, text=text,
                                   font_size=dp(14), color=hex_color(theme["text"]))
            open_btn.bind(on_release=lambda inst, cid=chat["id"]: self.open_chat(cid))
            del_btn = FrostButton(tint=(1.0, 0.42, 0.48), alpha=0.12, radius=16, text="×", font_size=dp(26),
                                  halign="center", size_hint_x=None, width=dp(56), color=hex_color(theme["text"]))
            del_btn.bind(on_release=lambda inst, cid=chat["id"]: self.confirm_delete(cid))
            row.add_widget(open_btn)
            row.add_widget(del_btn)
            grid.add_widget(row)
        if not chats:
            grid.add_widget(make_label(self.t("chats_empty"), 13, theme["text"], False, size_hint_y=None, height=dp(50)))
        scroll.add_widget(grid)
        body.add_widget(scroll)
        self.chats_popup = GlassSheet(self, self.t("chats_title"), body)
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
        body = BoxLayout(orientation="vertical", spacing=dp(10))
        body.add_widget(make_label(self.t("about_body"), 13, theme["text"], False))
        body.add_widget(make_label(self.t("about_chats").format(len(self.chats.chats)), 12, theme["accent"], False,
                                   size_hint_y=None, height=dp(28)))
        GlassSheet(self, self.t("about_title"), body, anchor="menu", height=330).open()

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
        content = BoxLayout(orientation="vertical", spacing=dp(8))
        titles = {"main": "settings", "theme": "title_theme", "language": "title_lang",
                  "voice": "title_voice", "account": "title_account", "mode": "title_mode",
                  "profile": "title_profile"}

        def option(text, active, callback, height=54):
            self.frost_row(content, text, active, callback, height)

        if section == "main":
            for key, target in (("sec_profile", "profile"), ("sec_theme", "theme"), ("sec_mode", "mode"),
                                ("sec_lang", "language"), ("sec_voice", "voice"), ("sec_account", "account")):
                option(self.t(key), False, lambda inst, s=target: self.open_section(s), height=56)
            content.add_widget(Widget())
        elif section == "profile":
            content.add_widget(make_label(self.t("profile_note"), 11, muted(), False, size_hint_y=None, height=dp(44)))
            scroll = ScrollView(do_scroll_x=False)
            grid = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None)
            grid.bind(minimum_height=grid.setter("height"))
            if not self.profile.facts:
                grid.add_widget(make_label(self.t("profile_empty"), 13, theme["text"], False, size_hint_y=None, height=dp(60)))
            for index, fact in enumerate(self.profile.facts):
                row = BoxLayout(orientation="horizontal", spacing=dp(6), size_hint_y=None, height=dp(52))
                chip = FrostButton(tint=hex_color(theme["accent"])[:3], alpha=0.10, text=mk(fact["text"]), font_size=dp(13),
                                   color=hex_color(theme["text"]))
                gone = FrostButton(tint=(1.0, 0.42, 0.48), alpha=0.12, radius=16, text="×", font_size=dp(24),
                                   halign="center", size_hint_x=None, width=dp(48), color=hex_color(theme["text"]))
                gone.bind(on_release=lambda inst, i=index: (self.profile.remove(i), self.open_section("profile")))
                row.add_widget(chip)
                row.add_widget(gone)
                grid.add_widget(row)
            scroll.add_widget(grid)
            content.add_widget(scroll)
            wrap, fact_in = glass_input(self, self.t("profile_hint"), "", size=14)
            content.add_widget(wrap)

            def add_fact(*_):
                if fact_in.text.strip():
                    self.profile.add(fact_in.text)
                    self.open_section("profile")
            fact_in.bind(on_text_validate=add_fact)
            self.frost_row(content, self.t("profile_add"), True, add_fact, 48, center=True)
        elif section == "mode":
            for key, code in (("mode_auto", "auto"), ("mode_dark", "dark"), ("mode_light", "light")):
                option(self.t(key), self.ui_mode == code,
                       lambda inst, c=code: self.choose_mode(c, self.settings_popup))
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
            content.add_widget(make_label(self.t("acct_line").format(self.username or "-"), 15, theme["text"], True,
                                          size_hint_y=None, height=dp(36)))
            self.frost_row(content, self.t("acct_rename"), False, self.rename_assistant, 52)
            self.frost_row(content, self.t("acct_logout"), False, self.logout_account, 52)
            self.frost_row(content, self.t("acct_delete"), False, self.delete_account_ask, 52, tint=(1.0, 0.36, 0.42))
            content.add_widget(Widget())

        self.settings_popup = GlassSheet(self, self.t(titles[section]), content,
                                         back=(lambda: self.open_section("main")) if section != "main" else None)
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
        try:
            Window.bind(on_keyboard=self._on_back_key)
        except Exception:
            pass
        Clock.schedule_once(lambda dt: self.show_pending_errors(), 1.0)
        Clock.schedule_interval(self.chat_sync_tick, 10)
        if self.server_token.strip():
            Clock.schedule_once(lambda dt: self.pull_chats(), 3.0)
        if not self.onboarded or not self.server_token.strip():
            Clock.schedule_once(lambda dt: self.start_onboarding(), 0.5)
        else:
            Clock.schedule_once(lambda dt: self.greet_user(), 1.8)

    def greet_user(self):
        """The assistant welcomes the user when the app opens: shown in the chat (not saved) and read aloud."""
        try:
            if getattr(self, "_ob_active", False):
                return
            text = greeting_for(self.language, [f["text"] for f in self.profile.facts], datetime.now().hour)
            if self.chat_placeholder:
                self.notice = text
                self.render_chat()
            else:
                self.append_bubble("jarvis", text)
            self.speak_answer(text)
        except Exception:
            save_crash("Greeting failed:\n" + traceback.format_exc())

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
            pop = Popup(title=self.brand("JARVIS") + " error log", content=box, size_hint=(0.96, 0.9))
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
        if getattr(self, "ui_mode", "auto") == "auto" and hasattr(self, "root_layout"):
            before = UI_LIGHT
            self.apply_ui_mode()
            if UI_LIGHT != before:                  # the phone switched between light and dark
                self.update_colors()
                self.build_interface()

    def on_stop(self):
        self.save_settings()
        self.tts.shutdown()

if __name__ == "__main__":
    JarvisApp().run()
