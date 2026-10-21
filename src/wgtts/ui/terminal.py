"""Terminal and widget share selection, speech queue and runtime settings."""

import shlex
import threading
from queue import Empty, Queue

from wgtts.branding import APP_COMMAND, APP_NAME
from wgtts.chat.history import HistoryStore, refresh_history
from wgtts.chat.reader import ChatReader, ReaderError
from wgtts.chat.scanner import scan_chat
from wgtts.chat.watcher import AutoReader
from wgtts.settings import Settings
from wgtts.speech.applio import ApplioClient, ApplioError
from wgtts.speech.playback import format_time, parse_time
from wgtts.speech.service import SpeechService

HELP = """Команды:
  scan              прочитать весь открытый чат с прокруткой и сохранить
  read              обновить список доступными сейчас сообщениями
  list              текущий список; его номера работают в show и say
  show НОМЕР        показать сообщение целиком (нумерация с 1)
  next / prev       выбрать и показать следующее / предыдущее сообщение
  last              выбрать последний ответ ассистента
  say [НОМЕР]       озвучить выбранное сообщение или указанный номер
  start / stop      продолжить / поставить на паузу (и приостановить auto)
  seek +10 / -10    перемотать вперёд / назад на 10 секунд
  seek 1:30         перейти на 1 минуту 30 секунд (или seek 90)
  restart           воспроизвести текущую запись с начала
  auto on / off     новые ответы автоматически / ручной режим
  applio            взять последние рабочие настройки TTS из Applio
  settings          показать настройки озвучки и чтения
  status            состояние истории и текущий выбор
  set delay ЧИСЛО   задержка после прокрутки в секундах (0.1–3)
  set rate 20       скорость TTS от -100 до 100 процентов
  set volume 50     громкость озвучки 0–100%, меняется и во время речи
  set pitch -10     сдвиг тона от -24 до 24
  set voice ИМЯ     голос Edge TTS, например ru-RU-SvetlanaNeural
  set poll 1.5      частота проверки новых ответов, секунды
  set size 150      размер виджета в процентах (50–300); также колёсиком над GIF
  preset            список оформлений (анимации персонажа и рамка)
  preset modern     применить оформление: modern, classic, frost, minimal, bare
  preset save ИМЯ   сохранить обе анимации и рамку как свой пресет
  set idle ПУТЬ     анимация ожидания (GIF / WebP) или статичная картинка
  set talk ПУТЬ     анимация разговора (GIF / WebP) или статичная картинка
  set character-name ИМЯ   имя персонажа в плеере
  set image ПУТЬ    один портрет: анимация при речи, первый кадр в ожидании
  set border-width 9        толщина рамки при 100% (0–30)
  set border-color "#92999c" цвет рамки
  set border-style card    стиль: card, bevel, flat, none
  help / quit       справка / выход

При scan чат прокручивается автоматически. Подожди окончания прохода.
Ctrl+C прерывает сбор с сохранением уже прочитанного.
"""

ROLE_NAMES = {"user": "Ты", "assistant": "Ассистент"}


class Terminal:
    def __init__(self, directory, delay=None):
        self.screen = None
        self.closed = False
        self.reader = ChatReader()
        self.store = HistoryStore(directory)
        self.history = self.store.load()
        self.selected = (
            next(
                (
                    i
                    for i in range(len(self.history.messages) - 1, -1, -1)
                    if self.history.messages[i].role == "assistant"
                    and self.history.messages[i].complete
                ),
                0,
            )
            if self.history
            else 0
        )
        self.settings = Settings(directory.parent / "settings.json")
        if delay is not None:
            self.settings.data["delay"] = delay
        self.read_lock = threading.Lock()
        self.history_lock = threading.RLock()
        self.history_session = self.history.session_key if self.history else None
        self.commands = Queue()
        self.done = threading.Event()
        self.speech = SpeechService(self.settings, directory.parent / "audio", self.notify)
        self.auto = AutoReader(self.settings, self.read_lock, self._auto_reply, self.notify)
        self.auto_mode = False
        self.command_worker = None
        self.command_revision = None

    def enqueue(self, command):
        self.commands.put((command, self.speech.cancel_revision))

    @property
    def delay(self):
        return self.settings.data["delay"]

    def write(self, *values, sep=" ", end="\n", flush=False):
        if self.closed:
            return
        screen = self.screen
        if screen is not None:
            screen.write(sep.join(str(value) for value in values) + end)
        else:
            print(*values, sep=sep, end=end, flush=flush)

    def notify(self, text):
        if self.closed:
            return
        screen = self.screen
        if screen is not None:
            screen.notify(text)
        else:
            self.write("\n" + text, flush=True)

    def screen_status(self):
        speech = {
            "idle": "готов",
            "preparing": "готовлю голос",
            "playing": "говорю",
            "paused": "пауза",
            "error": "ошибка",
        }.get(self.speech.state, self.speech.state)
        count = len(self.history.messages) if self.history else 0
        selection = f"#{self.selected + 1} из {count}" if count else "нет сообщений"
        mode = "авто" if self.auto_mode else "ручной"
        position, duration = self.speech.progress
        timeline = f" · {format_time(position)} / {format_time(duration)}" if duration else ""
        return (
            f"{speech}{timeline} · {mode} · громкость {self.settings.data['volume']}% · {selection}"
        )

    def submit_command(self, command):
        if command.strip() in {"stop", "pause"}:
            # Stop bypasses the queue, including a long scan or network request.
            self.stop()
        elif command.strip() == "start" and self.speech.state == "paused":
            if self.speech.resume():
                if self.auto_mode:
                    self.auto.set_enabled(True)
                self.notify("Продолжаю с сохранённой позиции.")
        elif command.strip().split(" ")[0] == "seek":
            # Transport controls stay responsive even while scan owns the worker.
            try:
                self.execute(command)
            except ValueError as error:
                self.notify(str(error))
        elif command.strip() in {"quit", "exit"}:
            self.done.set()
        elif command.strip():
            self.enqueue(command)

    def _auto_reply(self, snapshot, message):
        with self.history_lock:
            if not self.auto.enabled.is_set():
                return
            indexes = self._refresh(snapshot)
            if not indexes:
                return
            self.selected = indexes[-1]
            try:
                self.store.save(self.history)
            except OSError as error:
                self.notify(f"Не удалось сохранить новый ответ: {error}")
            self.speech.say(message.text, replace=False)
        self.notify(f"Новый ответ #{self.selected + 1}: {message.text.replace(chr(10), ' ')[:100]}")

    def _refresh(self, snapshot):
        self.history, indexes, note = refresh_history(self.history, snapshot)
        if not indexes:
            return []
        self.history_session = self.history.session_key
        self.selected = next(
            (
                index
                for index in reversed(indexes)
                if self.history.messages[index].role == "assistant"
                and self.history.messages[index].complete
            ),
            indexes[-1],
        )
        if note:
            self.notify(note)
        return indexes

    def stop(self):
        with self.history_lock:
            self.auto.set_enabled(False)
            self.speech.stop()
        self.notify("Пауза. start — продолжить; seek -10 — назад; restart — с начала.")

    def seek(self, seconds, *, relative=False):
        try:
            self.speech.seek(seconds, relative=relative)
        except ValueError as error:
            self.notify(str(error))

    def resize_widget(self, size):
        try:
            self.settings.set("size", str(size))
        except OSError as error:
            self.notify(f"Не удалось сохранить размер виджета: {error}")

    def close(self):
        self.done.set()
        try:
            self.auto.set_enabled(False)
            self.speech.stop()
            self.auto.close()
            self.speech.close()
            if self.command_worker:
                # A cancelled scan saves its result and restores chat scrolling
                # while its progress is still inside the application's screen.
                self.command_worker.join(timeout=15)
        finally:
            # A delayed network callback must not print into the restored shell.
            self.closed = True
            if self.screen is not None:
                screen, self.screen = self.screen, None
                screen.close()

    def _say(self, args):
        with self.history_lock:
            if not self.history or not self.history.messages:
                with self.read_lock:
                    snapshot = self.reader.snapshot()
                self._refresh(snapshot)
            if args:
                if len(args) != 1:
                    raise ValueError("Используй: say или say НОМЕР")
                self.selected = int(args[0]) - 1
            if not self.history or not 0 <= self.selected < len(self.history.messages):
                raise ValueError("Ответ не выбран. Выполни scan, list, затем say НОМЕР.")
            message = self.history.messages[self.selected]
            if not message.complete:
                raise ValueError(
                    "Это незавершённый ответ. Дождись окончания и обнови список командой read."
                )
            self.speech.say(message.text, expected_revision=self.command_revision)
            self.write(f"Выбрана озвучка #{self.selected + 1}.")

    def execute(self, command):
        parts = shlex.split(command)
        if not parts:
            return True
        name, *args = parts
        if name in ("quit", "exit"):
            return False
        if name == "help":
            self.write(HELP)
        elif name == "scan":
            self.write("Читаю чат; временно прокручиваю переписку…", flush=True)

            def progress(percent, count):
                self.write(
                    f"\rПрокрутка {max(0, percent):5.1f}% · сообщений собрано: {count}   ",
                    end="",
                    flush=True,
                )

            with self.read_lock:
                session = self.reader.connect().session_key
                result = scan_chat(
                    self.reader, delay=self.delay, progress=progress, cancel=self.done
                )
            self.write()
            if not result.messages:
                self.write("Сообщения не прочитаны. Предыдущая история сохранена.")
                for warning in result.warnings:
                    self.write("  " + warning)
                return True
            with self.history_lock:
                path = self.store.save(result)
                self.history = result
                self.history_session = session
                self.selected = max(0, len(result.messages) - 1)
            self.status()
            self.write(f"Сохранено: {path}")
        elif name == "read":
            with self.read_lock:
                snapshot = self.reader.snapshot()
            with self.history_lock:
                indexes = self._refresh(snapshot)
                if not indexes:
                    self.write("Новых доступных сообщений нет. Текущий список сохранён.")
                    return True
                self.store.save(self.history)
                self.write(
                    f"Прочитано: {len(indexes)} сообщений. В списке: {len(self.history.messages)}. Чат: {snapshot.title}"
                )
                for index in indexes:
                    message = self.history.messages[index]
                    selected = ">" if index == self.selected else " "
                    draft = " [черновик]" if not message.complete else ""
                    self.write(
                        f"{selected}{index + 1:3} {ROLE_NAMES.get(message.role, message.role):9}{draft} {message.text.replace(chr(10), ' ')[:100]}"
                    )
                self.write(
                    "Номера совпадают с list: show НОМЕР / say НОМЕР; say — выбранный ответ."
                )
        elif name == "list":
            if self._has_history():
                for index, message in enumerate(self.history.messages, 1):
                    selected = ">" if index - 1 == self.selected else " "
                    preview = message.text.replace("\n", " ")[:105]
                    draft = " [черновик]" if not message.complete else ""
                    self.write(
                        f"{selected}{index:3} {ROLE_NAMES.get(message.role, message.role):9}{draft} {preview}"
                    )
        elif name in ("show", "next", "prev", "last"):
            if not self._has_history():
                return True
            if name == "show":
                if len(args) != 1:
                    raise ValueError("Используй: show НОМЕР")
                self.selected = int(args[0]) - 1
            elif name == "next":
                self.selected += 1
            elif name == "prev":
                self.selected -= 1
            else:
                self.selected = next(
                    (
                        index
                        for index in range(len(self.history.messages) - 1, -1, -1)
                        if self.history.messages[index].role == "assistant"
                    ),
                    len(self.history.messages) - 1,
                )
            self.selected = max(0, min(self.selected, len(self.history.messages) - 1))
            message = self.history.messages[self.selected]
            self.write(
                f"\n#{self.selected + 1} · {ROLE_NAMES.get(message.role, message.role)}\n\n{message.text}\n"
            )
        elif name == "status":
            self.status()
            self.write(
                f"Озвучка: {self.speech.state}. Режим: {'авто' if self.auto_mode else 'ручной'}; наблюдение: {'включено' if self.auto.enabled.is_set() else 'выключено'}"
            )
            self.write("Авто: " + self.auto.status)
            position, duration = self.speech.progress
            self.write(f"Запись: {format_time(position)} / {format_time(duration)}")
        elif name in ("say", "play"):
            self._say(args)
        elif name == "start":
            if not args and self.speech.resume(expected_revision=self.command_revision):
                if self.auto_mode:
                    self.auto.set_enabled(True)
                self.write("Продолжаю с сохранённой позиции.")
            elif self.auto_mode:
                self.auto.set_enabled(True)
                self.write("Жду новые ответы в открытом чате. Держи чат прокрученным к концу.")
            else:
                self._say(args)
        elif name in ("stop", "pause"):
            self.stop()
        elif name == "seek":
            if len(args) != 1:
                raise ValueError("Используй: seek +10, seek -10 или seek 1:30.")
            seconds, relative = parse_time(args[0])
            position = self.speech.seek(seconds, relative=relative)
            if position is not None:
                self.notify(f"Позиция: {format_time(position)}")
        elif name == "restart":
            if args:
                raise ValueError("Используй: restart — текущая запись с начала.")
            if not self.speech.restart(expected_revision=self.command_revision):
                self._say([])
            else:
                self.write("Воспроизвожу запись с начала.")
        elif name == "auto":
            if args not in (["on"], ["off"]):
                raise ValueError("Используй: auto on или auto off")
            with self.history_lock:
                self.auto_mode = args == ["on"]
                self.auto.set_enabled(self.auto_mode)
            self.write(
                "Автоозвучка включена. Жду новые завершённые ответы; держи чат внизу."
                if self.auto_mode
                else "Ручной режим. Для озвучки: say."
            )
        elif name == "applio":
            params = ApplioClient(self.settings.data["url"]).discover()
            self.settings.data["tts"] = params
            self.settings.save()
            self.write(
                f"Настройки Applio сохранены: {params['ttsVoice']}, тон {params.get('pitch', 0)}, скорость {params.get('ttsRate', 0)}%; модель {params['pthPath']}"
            )
        elif name == "settings":
            import json

            self.write(json.dumps(self.settings.data, ensure_ascii=False, indent=2))
        elif name == "preset":
            if not args:
                for key, (label, appearance) in self.settings.presets().items():
                    marker = ">" if appearance == self.settings.appearance else " "
                    self.write(f"{marker} {key}: {label}")
            elif len(args) == 1:
                self.settings.apply_preset(args[0])
                self.write(f"Оформление {args[0]} применено.")
            elif len(args) == 2 and args[0] == "save":
                self.settings.save_preset(args[1])
                self.write(f"Анимации персонажа и рамка сохранены: {args[1]}.")
            else:
                raise ValueError("Используй: preset, preset ИМЯ или preset save ИМЯ")
        elif name == "set":
            if len(args) != 2:
                raise ValueError("Используй: set НАСТРОЙКА ЗНАЧЕНИЕ")
            value = self.settings.set(*args)
            message = {
                "size": "Размер виджета изменён.",
                "volume": "Громкость изменена.",
                **dict.fromkeys(
                    (
                        "image",
                        "gif",
                        "idle",
                        "talk",
                        "character-name",
                        "border-width",
                        "border-color",
                        "border-style",
                    ),
                    "Оформление виджета изменено.",
                ),
            }.get(args[0], "Применится к следующим задачам.")
            self.write(f"{args[0]} = {value}. {message}")
        else:
            raise ValueError("Неизвестная команда. Введи help.")
        return True

    def _has_history(self):
        if self.history and self.history.messages:
            return True
        self.write("История пуста. Открой чат и введи scan.")
        return False

    def status(self):
        if not self._has_history():
            return
        result = self.history
        self.write(
            f"Чат: {result.title}\nСообщений: {len(result.messages)} · твоих: {result.user_count}/{result.expected_users if result.expected_users is not None else '?'}"
        )
        self.write(
            "Полнота подтверждена." if result.complete else "Полнота истории пока не подтверждена."
        )
        for warning in result.warnings:
            self.write("  " + warning)
        self.write(f"Выбрано: #{self.selected + 1}. Задержка чтения: {self.delay} с")

    def _input(self):
        while not self.done.is_set():
            try:
                self.submit_command(input(APP_COMMAND + "> "))
            except EOFError:
                self.done.set()

    def _process_commands(self):
        from comtypes import CoInitializeEx, CoUninitialize

        CoInitializeEx(0)
        try:
            while not self.done.is_set():
                try:
                    command, revision = self.commands.get(timeout=0.1)
                except Empty:
                    continue
                self.command_revision = revision
                if revision != self.speech.cancel_revision and command.strip().split(" ")[0] in {
                    "start",
                    "say",
                    "play",
                    "restart",
                }:
                    continue
                screen = self.screen
                if screen is not None:
                    screen.begin_command(command)
                try:
                    if not self.execute(command):
                        self.done.set()
                except (ReaderError, ApplioError, ValueError, OSError) as error:
                    self.write(f"Ошибка: {error}")
                finally:
                    if screen is not None:
                        screen.end_command()
        finally:
            CoUninitialize()

    def run(self, widget=True, full_screen=True):
        if full_screen:
            from wgtts.ui.console import ConsoleScreen, interactive_terminal

            if interactive_terminal():
                self.screen = ConsoleScreen(self.submit_command, self.done.set, self.screen_status)
        if self.screen is None:
            self.write(
                APP_NAME
                + "\nscan — прочитать чат; say — озвучить выбранный ответ; auto on — новые ответы автоматически; help — команды."
            )
        if widget:
            from wgtts.ui.widget import SpeechWidget

            window = SpeechWidget(
                self.settings.appearance.image,
                self.speech,
                lambda: self.submit_command("start"),
                self.stop,
                self.done.set,
                size=self.settings.data["size"],
                on_resize=self.resize_widget,
                size_provider=lambda: self.settings.data["size"],
                appearance=self.settings.appearance,
                appearance_provider=lambda: self.settings.appearance,
                presets_provider=self.settings.presets,
                on_preset=lambda name: self.enqueue(shlex.join(["preset", name])),
                on_option=lambda name, value: self.enqueue(shlex.join(["set", name, value])),
                on_save_preset=lambda name: self.enqueue(shlex.join(["preset", "save", name])),
                on_seek=self.seek,
                on_restart=lambda: self.enqueue("restart"),
            )
        self.command_worker = threading.Thread(
            target=self._process_commands, name="commands", daemon=True
        )
        self.command_worker.start()
        if self.screen is not None:
            self.screen.start()
        else:
            threading.Thread(target=self._input, name="terminal-input", daemon=True).start()
        if widget:

            def tick():
                if self.done.is_set():
                    window.root.destroy()
                else:
                    window.root.after(50, tick)

            window.root.after(50, tick)
            window.root.mainloop()
        else:
            while not self.done.wait(0.1):
                pass
