"""An alternate terminal screen; Tk and command workers only send events here."""

import asyncio
import os
import sys
import threading
from collections import deque
from queue import Empty, Queue

from prompt_toolkit.application import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from wgtts.branding import APP_COMMAND, APP_NAME

WELCOME = """scan       собрать историю открытого чата
read       обновить доступные сообщения
list       список сообщений
show 12    прочитать сообщение
say        озвучить выбранный ответ
auto on    автоматически озвучивать новые ответы
help       все команды

Каждая команда заменяет результат на этом экране.
PgUp / PgDn — прокрутка результата; ↑ / ↓ — история команд.
quit или Ctrl+C — выйти и вернуть прежний экран консоли."""
COMMANDS = [
    "scan",
    "read",
    "list",
    "show",
    "next",
    "prev",
    "last",
    "say",
    "start",
    "stop",
    "auto",
    "applio",
    "settings",
    "status",
    "set",
    "preset",
    "help",
    "quit",
]


def interactive_terminal():
    """Do not send terminal controls into redirected output or a pipeline."""
    return (
        sys.stdin is not None
        and sys.stdout is not None
        and sys.stdin.isatty()
        and sys.stdout.isatty()
        and (sys.platform == "win32" or os.environ.get("TERM") != "dumb")
    )


class ScreenBuffer:
    """Only the current command's result and the latest background notice."""

    MAX_LINES = 4000
    MAX_CHARS = 400_000

    def __init__(self):
        self.command = "Начало работы"
        self.busy = False
        self.notice = ""
        self.lines = deque()
        self.partial = ""
        self.length = 0
        self.truncated = False
        self.append(WELCOME)

    def begin(self, command):
        self.command = command
        self.busy = True
        self.lines.clear()
        self.partial = ""
        self.length = 0
        self.truncated = False

    def append(self, text):
        # print("\r...") updates the scan progress line instead of adding rows.
        text = text.replace("\r\n", "\n")
        for line_index, line in enumerate(text.split("\n")):
            if line_index:
                self.lines.append(self.partial)
                self.length += len(self.partial) + 1
                self.partial = ""
            pieces = line.split("\r")
            if len(pieces) > 1:
                self.partial = pieces[-1]
            else:
                self.partial += line
            if len(self.partial) > self.MAX_CHARS:
                self.partial = self.partial[-self.MAX_CHARS :]
                self.truncated = True
            while self.lines and (
                len(self.lines) > self.MAX_LINES or self.length + len(self.partial) > self.MAX_CHARS
            ):
                self.length -= len(self.lines.popleft()) + 1
                self.truncated = True

    @property
    def text(self):
        text = "\n".join([*self.lines, self.partial])
        if self.truncated:
            text = "[Начало слишком длинного вывода скрыто]\n" + text
        return text


class ConsoleScreen:
    def __init__(self, on_command, on_exit, status_provider, *, input=None, output=None):
        self.on_command = on_command
        self.on_exit = on_exit
        self.status_provider = status_provider
        self.model = ScreenBuffer()
        self.events = Queue()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = None
        self.output = TextArea(
            text=self.model.text,
            read_only=True,
            focusable=False,
            scrollbar=True,
            wrap_lines=True,
            style="class:result",
        )
        self.input = TextArea(
            height=1,
            prompt=f"{APP_COMMAND}> ",
            multiline=False,
            wrap_lines=False,
            history=InMemoryHistory(),
            completer=WordCompleter(COMMANDS),
            complete_while_typing=False,
            accept_handler=self._accept,
            style="class:input",
        )
        keys = KeyBindings()

        @keys.add("c-c")
        @keys.add("f10")
        def quit_(event):
            self.on_exit()

        @keys.add("c-d")
        def eof(event):
            if not self.input.text:
                self.on_exit()
            else:
                self.input.buffer.delete()

        @keys.add("f1")
        def help_(event):
            self.on_command("help")

        @keys.add("pageup")
        def page_up(event):
            self.output.buffer.cursor_up(count=self._page_size())

        @keys.add("pagedown")
        def page_down(event):
            self.output.buffer.cursor_down(count=self._page_size())

        @keys.add("c-home")
        def beginning(event):
            self.output.buffer.cursor_position = 0

        @keys.add("c-end")
        def end(event):
            self.output.buffer.cursor_position = len(self.output.text)

        @keys.add("c-l")
        def clear_(event):
            self.begin_command("Экран очищен")
            self.end_command()

        layout = HSplit(
            [
                Window(FormattedTextControl(f" {APP_NAME}"), height=1, style="class:header"),
                Window(
                    FormattedTextControl(lambda: " " + self.status_provider()),
                    height=1,
                    style="class:status",
                ),
                Window(FormattedTextControl(self._command_title), height=1, style="class:command"),
                self.output,
                Window(
                    FormattedTextControl(lambda: " " + self.model.notice),
                    height=2,
                    style="class:notice",
                    wrap_lines=True,
                ),
                self.input,
                Window(
                    FormattedTextControl(
                        " F1 справка · Tab дополнение · ↑↓ история · PgUp/PgDn прокрутка · Ctrl+C выход"
                    ),
                    height=1,
                    style="class:hint",
                ),
            ]
        )
        self.app = Application(
            layout=Layout(layout, focused_element=self.input),
            key_bindings=keys,
            full_screen=True,
            mouse_support=True,
            refresh_interval=0.2,
            min_redraw_interval=0.04,
            before_render=self._refresh,
            input=input,
            output=output,
            style=Style.from_dict(
                {
                    "header": "bg:#25364a #ffffff bold",
                    "status": "bg:#1b2633 #a9c4d9",
                    "command": "#83bbdd bold",
                    "result": "#dce3ea",
                    "notice": "#d4b56a",
                    "input": "bg:#25364a #ffffff",
                    "hint": "#8b9ba9",
                    "scrollbar.background": "bg:#26323d",
                    "scrollbar.button": "bg:#6e879a",
                }
            ),
        )

    def _command_title(self):
        suffix = " · выполняется…" if self.model.busy else ""
        return " " + self.model.command + suffix

    def _page_size(self):
        info = self.output.window.render_info
        return max(1, info.window_height - 1) if info else 10

    def _accept(self, buffer):
        command = buffer.text.strip()
        if command:
            self.on_command(command)
        return False  # Let prompt_toolkit save history and reset the input.

    def _send(self, kind, value=""):
        if not self.closed.is_set():
            self.events.put((kind, value))
            self.app.invalidate()  # Safe from the command, speech and Tk threads.

    def begin_command(self, command):
        self._send("begin", command)

    def write(self, text):
        self._send("write", text)

    def notify(self, text):
        self._send("notice", " ".join(text.split()))

    def end_command(self):
        self._send("end")

    def _refresh(self, app):
        reset_cursor = False
        changed = False
        while True:
            try:
                kind, value = self.events.get_nowait()
            except Empty:
                break
            if kind == "begin":
                self.model.begin(value)
                reset_cursor = changed = True
            elif kind == "write":
                self.model.append(value)
                changed = True
            elif kind == "notice":
                self.model.notice = value
            elif kind == "end":
                self.model.busy = False
        if changed:
            text = self.model.text
            position = 0 if reset_cursor else min(self.output.buffer.cursor_position, len(text))
            self.output.buffer.set_document(Document(text, position), bypass_readonly=True)
        if self.closed.is_set() and app.is_running and not app.is_done:
            app.exit()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="console-screen", daemon=True)
        self.thread.start()

    def _prepare_loop(self):
        # Async redraw failures otherwise only reach asyncio's default logger,
        # leaving an unusable application running inside the alternate screen.
        asyncio.get_running_loop().set_exception_handler(self._loop_error)
        self.ready.set()

    def _loop_error(self, loop, context):
        error = context.get("exception") or RuntimeError(context.get("message", "Ошибка терминала"))
        self.error = error
        if self.app.is_running and not self.app.is_done:
            self.app.exit(exception=error)

    def _run(self):
        try:
            # Tk owns the main thread and handles process signals.
            self.app.run(
                pre_run=self._prepare_loop,
                handle_sigint=False,
                set_exception_handler=False,
            )
        except EOFError:
            pass
        except BaseException as error:
            self.error = error
        finally:
            # Application.run restores the original buffer and terminal modes,
            # including when rendering or input fails.
            self.on_exit()

    def close(self):
        self.closed.set()
        self.app.invalidate()
        if self.thread is not None:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise OSError("Не удалось закрыть экран терминала.")
        if self.error is not None:
            raise OSError(
                f"Ошибка экрана терминала: {self.error}. Для обычного режима: {APP_COMMAND} --plain."
            ) from self.error
