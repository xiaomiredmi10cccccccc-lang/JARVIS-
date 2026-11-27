"""Adds the "Wake word" section (record the assistant's name, test it) to the settings of main.py.

Usage:   python3 apply_wake_patch.py path/to/main.py   (the build workflow runs it for you)
It makes main.py.bak first, checks that every place to change exists exactly once, and stops without changing
anything when one does not. Run it only once (it refuses a second time).
"""
import shutil
import sys

# ---------------------------------------------------------------------------------------------
# 1. texts (tg / ru / en), inserted after the "title_security" line of each language
# ---------------------------------------------------------------------------------------------
TEXTS = {
    "tg": [
        ("sec_wake", "Калимаи бедоркунӣ"),
        ("title_wake", "Калимаи бедоркунӣ"),
        ("wake_intro", "Номи ёрдамчиро чанд маротиба бигӯед. Телефон овози шуморо ёд мегирад ва овоз аз телефон намеравад. "
                       "Ҳоло ин танҳо сабт ва санҷиш аст. Бедоршавии худкор баъдтар меояд."),
        ("wake_na", "Ин нусхаи барнома бе қисми Java сохта шудааст, барои ҳамин калимаи бедоркунӣ дастнорас аст."),
        ("wake_ready", "Тайёр"),
        ("wake_none", "Ҳанӯз сабт нашудааст"),
        ("wake_rec", "Сабти ном"),
        ("wake_rerec", "Аз нав сабт кардан"),
        ("wake_test", "Санҷиш"),
        ("wake_delete", "Сабтро нест кардан"),
        ("wake_sens", "Ҳассосият"),
        ("wake_s_low", "Кам (камтар хато)"),
        ("wake_s_mid", "Миёна"),
        ("wake_s_high", "Зиёд (осонтар мешунавад)"),
        ("wake_say", "Номи ёрдамчиро бигӯед ({i}/{n})"),
        ("wake_short", "Хеле кӯтоҳ буд. Боз як бор бигӯед."),
        ("wake_long", "Хеле дароз буд. Танҳо номро бигӯед."),
        ("wake_nospeech", "Овоз шунида нашуд. Наздиктар бигӯед."),
        ("wake_other", "Акнун тақрибан 15 сония чанд ҷумларо оддӣ бигӯед ва байни онҳо каме таваққуф кунед."),
        ("wake_done_good", "Тайёр. Сифати сабт: хуб."),
        ("wake_done_mid", "Тайёр, вале суханони дигар кам буданд. Барои дақиқӣ аз нав сабт кунед ва ҷумлаҳои бештар бигӯед."),
        ("wake_done_weak", "Сабт суст аст. Дар ҷои ором аз нав кӯшиш кунед."),
        ("wake_busy_mic", "Микрофон ҳоло банд аст."),
        ("wake_nomic", "Иҷозати микрофон нест."),
        ("wake_test_say", "Номро бигӯед (3 сония)..."),
        ("wake_hit", "Шунида шуд. Хол: {s}, ҳадд: {t}"),
        ("wake_miss", "Нашунид. Хол: {s}, ҳадд: {t}"),
        ("wake_err", "Хато: {e}"),
    ],
    "ru": [
        ("sec_wake", "Слово пробуждения"),
        ("title_wake", "Слово пробуждения"),
        ("wake_intro", "Произнесите имя помощника несколько раз. Телефон запомнит ваш голос, звук не покидает телефон. "
                       "Пока это только запись и проверка. Автоматическое пробуждение появится позже."),
        ("wake_na", "Эта версия приложения собрана без части на Java, поэтому слово пробуждения недоступно."),
        ("wake_ready", "Готово"),
        ("wake_none", "Ещё не записано"),
        ("wake_rec", "Записать имя"),
        ("wake_rerec", "Записать заново"),
        ("wake_test", "Проверка"),
        ("wake_delete", "Удалить запись"),
        ("wake_sens", "Чувствительность"),
        ("wake_s_low", "Низкая (меньше ошибок)"),
        ("wake_s_mid", "Средняя"),
        ("wake_s_high", "Высокая (легче срабатывает)"),
        ("wake_say", "Произнесите имя помощника ({i}/{n})"),
        ("wake_short", "Слишком коротко. Скажите ещё раз."),
        ("wake_long", "Слишком длинно. Скажите только имя."),
        ("wake_nospeech", "Голос не слышен. Говорите ближе."),
        ("wake_other", "Теперь около 15 секунд просто говорите несколько фраз, делая небольшие паузы."),
        ("wake_done_good", "Готово. Качество записи: хорошее."),
        ("wake_done_mid", "Готово, но другой речи было мало. Для точности запишите заново и скажите больше фраз."),
        ("wake_done_weak", "Запись слабая. Попробуйте заново в тихом месте."),
        ("wake_busy_mic", "Микрофон сейчас занят."),
        ("wake_nomic", "Нет разрешения на микрофон."),
        ("wake_test_say", "Произнесите имя (3 секунды)..."),
        ("wake_hit", "Услышано. Оценка: {s}, порог: {t}"),
        ("wake_miss", "Не услышано. Оценка: {s}, порог: {t}"),
        ("wake_err", "Ошибка: {e}"),
    ],
    "en": [
        ("sec_wake", "Wake word"),
        ("title_wake", "Wake word"),
        ("wake_intro", "Say the assistant's name a few times. The phone learns your voice and the audio never leaves the phone. "
                       "For now this is only recording and testing. Automatic wake-up comes later."),
        ("wake_na", "This build of the app was made without the Java part, so the wake word is not available."),
        ("wake_ready", "Ready"),
        ("wake_none", "Not recorded yet"),
        ("wake_rec", "Record the name"),
        ("wake_rerec", "Record again"),
        ("wake_test", "Test"),
        ("wake_delete", "Delete the recording"),
        ("wake_sens", "Sensitivity"),
        ("wake_s_low", "Low (fewer mistakes)"),
        ("wake_s_mid", "Medium"),
        ("wake_s_high", "High (hears more easily)"),
        ("wake_say", "Say the assistant's name ({i}/{n})"),
        ("wake_short", "Too short. Say it once more."),
        ("wake_long", "Too long. Say only the name."),
        ("wake_nospeech", "No voice heard. Speak closer."),
        ("wake_other", "Now talk normally for about 15 seconds, a few sentences, with small pauses between them."),
        ("wake_done_good", "Ready. Recording quality: good."),
        ("wake_done_mid", "Ready, but there was little other speech. For accuracy record again and say more sentences."),
        ("wake_done_weak", "The recording is weak. Try again in a quiet place."),
        ("wake_busy_mic", "The microphone is busy right now."),
        ("wake_nomic", "No microphone permission."),
        ("wake_test_say", "Say the name (3 seconds)..."),
        ("wake_hit", "Heard it. Score: {s}, threshold: {t}"),
        ("wake_miss", "Did not hear it. Score: {s}, threshold: {t}"),
        ("wake_err", "Error: {e}"),
    ],
}

TITLE_ANCHORS = {
    "tg": '        "title_security": "Амният",\n',
    "ru": '        "title_security": "Безопасность",\n',
    "en": '        "title_security": "Security",\n',
}


def texts_block(lang):
    out = ""
    for key, value in TEXTS[lang]:
        out += "        " + repr(key).replace("'", '"') + ": " + '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '",\n'
    return out


# ---------------------------------------------------------------------------------------------
# 2. import of wake_audio, placed before microphone_permission_state()
# ---------------------------------------------------------------------------------------------
IMPORT_ANCHOR = "def microphone_permission_state():"
IMPORT_BLOCK = '''try:
    import wake_audio  # калимаи бедоркунии шахсӣ: сабт ва санҷиш (бе wake_audio.py хомӯш мешавад)
except Exception:
    wake_audio = None


'''

# ---------------------------------------------------------------------------------------------
# 3. the settings section and the methods
# ---------------------------------------------------------------------------------------------
BRANCH_ANCHOR = '        elif section == "mode":\n'
BRANCH_BLOCK = '''        elif section == "wake":
            self.build_wake_section(content)
'''

METHODS_ANCHOR = "    def choose_theme(self, index, popup):\n"
METHODS_BLOCK = '''    # ----- калимаи бедоркунии шахсӣ: сабти ном ва санҷиш (ҳоло бе service-и паси парда) -----
    def _wake_model(self):
        # Модели шахсиро як маротиба месозем ва аз файл мехонем. None = ин нусха бе қисми Java сохта шудааст.
        if wake_audio is None or not wake_audio.available():
            return None
        model = getattr(self, "_wake_m", None)
        if model is None:
            model = wake_audio.WakeModel(self.user_data_dir)
            model.load()
            self._wake_m = model
        return model

    def _wake_ready(self):
        model = self._wake_model()
        try:
            return bool(model is not None and model.core is not None and model.core.isReady())
        except Exception:
            return False

    def _wake_say(self, text):
        self._wk_msg = text
        label = getattr(self, "_wk_status", None)
        if label is not None and label.parent is not None:
            label.text = mk(text)

    def build_wake_section(self, content):
        theme = THEMES[self.theme_index]
        scroll = ScrollView(do_scroll_x=False, bar_width=0)
        box = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None)
        box.bind(minimum_height=box.setter("height"))
        scroll.add_widget(box)
        content.add_widget(scroll)

        def note(text, size, color, height):
            lab = make_label(text, size, color, False, halign="left", size_hint_y=None, height=dp(height))
            lab.bind(size=lambda inst, sz: setattr(inst, "text_size", (sz[0], None)))
            box.add_widget(lab)
            return lab

        note(self.t("wake_intro"), 12, muted(), 96)
        model = self._wake_model()
        if model is None:
            note(self.t("wake_na"), 13, theme["accent"], 70)
            return
        ready = self._wake_ready()
        note((self.assistant_name or "S.A.I") + ": " + (self.t("wake_ready") if ready else self.t("wake_none")), 15, theme["text"], 30)
        status = make_label(getattr(self, "_wk_msg", ""), 13, theme["accent"], False, halign="center", size_hint_y=None, height=dp(96))
        status.bind(size=lambda inst, sz: setattr(inst, "text_size", (sz[0], None)))
        self._wk_status = status
        box.add_widget(status)
        self.frost_row(box, self.t("wake_rerec") if ready else self.t("wake_rec"), not ready,
                       lambda *_: self._wake_start("enroll"), 54, center=True)
        if ready:
            self.frost_row(box, self.t("wake_test"), True, lambda *_: self._wake_start("test"), 54, center=True)
            note(self.t("wake_sens"), 13, muted(), 24)
            try:
                current = float(model.core.getSensitivity())
            except Exception:
                current = 0.5
            for key, value in (("wake_s_low", 0.2), ("wake_s_mid", 0.5), ("wake_s_high", 0.8)):
                self.frost_row(box, self.t(key), abs(current - value) < 0.15,
                               lambda inst, v=value: (model.set_sensitivity(v), self.open_section("wake")), 44, center=True)
            self.frost_row(box, self.t("wake_delete"), False,
                           lambda *_: (model.delete(), setattr(self, "_wake_m", None), setattr(self, "_wk_msg", ""), self.open_section("wake")),
                           46, center=True, tint=(1.0, 0.36, 0.42))

    def _wake_start(self, mode):
        if getattr(self, "_wk_busy", False):
            return
        if getattr(getattr(self, "speech", None), "_recorder", None) is not None:
            self._wake_say(self.t("wake_busy_mic"))
            return

        def go(granted):
            if not granted:
                Clock.schedule_once(lambda dt: self._wake_say(self.t("wake_nomic")), 0)
                return
            self._wk_busy = True
            threading.Thread(target=self._wake_job, args=(mode,), daemon=True).start()
        if microphone_permission_state() in ("granted", "not_android"):
            go(True)
        else:
            request_microphone(go)

    def _wake_job(self, mode):
        def say(text):
            Clock.schedule_once(lambda dt, t=text: self._wake_say(t), 0)
        model = self._wake_model()
        refresh = False
        try:
            if model is None:
                return
            if mode == "test":
                say(self.t("wake_test_say"))
                time.sleep(0.5)
                score, hit = model.test(3)
                thr = float(model.core.getThreshold()) * (0.8 + 0.4 * float(model.core.getSensitivity()))
                say((self.t("wake_hit") if hit else self.t("wake_miss")).format(s="%.1f" % score, t="%.1f" % thr))
                return
            model.start_enrollment()
            total, have, tries = 5, 0, 0
            while have < total and tries < 12:
                say(self.t("wake_say").format(i=have + 1, n=total))
                time.sleep(0.6)
                code = model.add_name(2.5)
                tries += 1
                if code == "ok":
                    have += 1
                else:
                    say(self.t({"too_short": "wake_short", "too_long": "wake_long"}.get(code, "wake_nospeech")))
                    time.sleep(1.2)
            if have < 3:
                say(self.t("wake_done_weak"))
                return
            say(self.t("wake_other"))
            time.sleep(1.0)
            model.add_other_speech(15)
            report = model.finish()
            for _ in range(2):  # a recording that does not fit the others is dropped
                fit = report.get("fit") or []
                if len(fit) <= 3:
                    break
                median = sorted(fit)[len(fit) // 2]
                worst = max(range(len(fit)), key=lambda k: fit[k])
                if fit[worst] <= 1.6 * median:
                    break
                model.drop_name(worst)
                report = model.finish()
            if not report.get("ok"):
                say(self.t("wake_done_weak"))
                return
            model.save()
            refresh = True
            if report["others"] < 5:
                msg = self.t("wake_done_mid")
            elif report["separable"]:
                msg = self.t("wake_done_good")
            else:
                msg = self.t("wake_done_weak")
            say(msg)
        except Exception as exc:
            save_crash("Wake word failed:\\n" + traceback.format_exc())
            say(self.t("wake_err").format(e=str(exc)[:80]))
        finally:
            self._wk_busy = False
            if refresh:
                def again(dt):
                    label = getattr(self, "_wk_status", None)
                    if label is not None and label.parent is not None:
                        self.open_section("wake")
                Clock.schedule_once(again, 0.3)

'''

# ---------------------------------------------------------------------------------------------
# 4. small replacements
# ---------------------------------------------------------------------------------------------
SMALL = [
    ('"security": "title_security", "messages": "title_messages", "diag": "title_diag"}',
     '"security": "title_security", "messages": "title_messages", "diag": "title_diag", "wake": "title_wake"}'),
    ('("sec_messages", "messages"), ("sec_account", "account"), ("sec_diag", "diag")):',
     '("sec_messages", "messages"), ("sec_wake", "wake"), ("sec_account", "account"), ("sec_diag", "diag")):'),
    ('"security": "lock", "messages": "bell", "account": "key", "diag": "doc"}[target]',
     '"security": "lock", "messages": "bell", "wake": "mic", "account": "key", "diag": "doc"}[target]'),
    ('if section in ("apps", "security", "messages", "diag") else None',
     'if section in ("apps", "security", "messages", "diag", "wake") else None'),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "main.py"
    src = open(path, encoding="utf-8").read()
    if "build_wake_section" in src:
        print("Already applied. Nothing changed.")
        return 0

    edits = []  # (anchor, replacement)
    edits.append((IMPORT_ANCHOR, IMPORT_BLOCK + IMPORT_ANCHOR))
    for lang in ("tg", "ru", "en"):
        a = TITLE_ANCHORS[lang]
        edits.append((a, a + texts_block(lang)))
    edits.extend(SMALL)
    edits.append((BRANCH_ANCHOR, BRANCH_BLOCK + BRANCH_ANCHOR))
    edits.append((METHODS_ANCHOR, METHODS_BLOCK + METHODS_ANCHOR))

    for anchor, _ in edits:
        n = src.count(anchor)
        if n != 1:
            print("STOP: expected exactly one place but found %d for:\n%s" % (n, anchor.strip()[:120]))
            print("main.py was not changed.")
            return 2

    for anchor, repl in edits:
        src = src.replace(anchor, repl, 1)

    shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(src)
    print("Done. %d changes. Backup: %s.bak" % (len(edits), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
