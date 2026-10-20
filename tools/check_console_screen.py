"""Run in a real Windows console to verify screen and terminal-mode restoration."""

import ctypes
import time
from ctypes import wintypes

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.win32_types import CONSOLE_SCREEN_BUFFER_INFO, COORD

from wgtts.ui.console import ConsoleScreen


def main():
    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.restype = wintypes.HANDLE
    handle = kernel.GetStdHandle(-11)
    input_handle = kernel.GetStdHandle(-10)
    kernel.GetConsoleScreenBufferInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO),
    ]
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.ReadConsoleOutputCharacterW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        COORD,
        ctypes.POINTER(wintypes.DWORD),
    ]

    def snapshot():
        info = CONSOLE_SCREEN_BUFFER_INFO()
        assert kernel.GetConsoleScreenBufferInfo(handle, ctypes.byref(info))
        count = info.dwSize.X * info.dwSize.Y
        chars = ctypes.create_unicode_buffer(count + 1)
        read = wintypes.DWORD()
        assert kernel.ReadConsoleOutputCharacterW(
            handle, chars, count, COORD(0, 0), ctypes.byref(read)
        )
        input_mode, output_mode = wintypes.DWORD(), wintypes.DWORD()
        assert kernel.GetConsoleMode(input_handle, ctypes.byref(input_mode))
        assert kernel.GetConsoleMode(handle, ctypes.byref(output_mode))
        return (
            chars[: read.value],
            info.dwCursorPosition.X,
            info.dwCursorPosition.Y,
            input_mode.value,
            output_mode.value,
        )

    print("This pre-existing console output must survive the application.", flush=True)
    before = snapshot()
    with create_pipe_input() as pipe:
        screen = ConsoleScreen(
            lambda _: None, lambda: None, lambda: "native console check", input=pipe
        )
        try:
            screen.start()
            assert screen.ready.wait(3)
            limit = time.monotonic() + 3
            while screen.app.render_counter < 2:
                assert time.monotonic() < limit
                time.sleep(0.02)
            screen.begin_command("status")
            screen.write("A result inside the application's screen.\n")
            screen.end_command()
            time.sleep(0.15)
        finally:
            screen.close()
    after = snapshot()
    assert before == after, "Original screen, cursor or input/output modes were changed"
    print("PASS: original console text, cursor and console modes restored.", flush=True)


if __name__ == "__main__":
    main()
