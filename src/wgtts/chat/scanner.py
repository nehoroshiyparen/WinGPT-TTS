"""Collect overlapping snapshots from the top to the bottom of one chat."""

import time
from collections.abc import Callable

from wgtts.chat.history import HistoryGapError, merge_window
from wgtts.chat.models import ScanResult


def scan_chat(
    reader, *, delay=0.3, max_steps=500, timeout=600, progress: Callable | None = None, cancel=None
):
    def pause():
        if cancel is not None:
            if cancel.wait(delay):
                raise KeyboardInterrupt()
        else:
            time.sleep(delay)

    initial = reader.snapshot()
    result = ScanResult(
        initial.title, [], expected_users=initial.expected_users, session_key=initial.session_key
    )
    session = initial.session_key
    original_position = initial.scroll_percent
    started = time.monotonic()
    current = None
    try:
        reader.scroll_to(0, session)
        pause()
        current = reader.snapshot()
        if current.session_key != session:
            raise RuntimeError("Во время сбора открыт другой чат.")
        result.reached_top = current.scroll_percent <= 0.05
        merge_window(result.messages, current.messages)
        for _ in range(max_steps):
            if cancel is not None and cancel.is_set():
                raise KeyboardInterrupt()
            if progress:
                progress(current.scroll_percent, len(result.messages))
            if time.monotonic() - started > timeout:
                result.warnings.append("Достигнут лимит времени; сохранена прочитанная часть.")
                break
            if (
                current.scroll_percent < 0
                or current.scroll_percent >= 99.95
                or current.view_size >= 100
            ):
                result.reached_bottom = True
                break
            previous = current
            # Move by 60% of the viewport, retaining a generous overlap.
            step = max(0.02, 60 * current.view_size / max(0.001, 100 - current.view_size))
            for attempt in range(6):
                target = min(100.0, previous.scroll_percent + step)
                reader.scroll_to(target, session)
                pause()
                current = reader.snapshot()
                if current.session_key != session:
                    raise RuntimeError("Во время сбора открыт другой чат.")
                try:
                    merge_window(result.messages, current.messages)
                    break
                except HistoryGapError:
                    if attempt == 5:
                        raise
                    step /= 2
            if current.expected_users is not None:
                result.expected_users = current.expected_users
            if (
                abs(current.scroll_percent - previous.scroll_percent) < 0.005
                and current.scroll_percent < 99.95
            ):
                result.warnings.append("Прокрутка перестала продвигаться; полнота не подтверждена.")
                break
        else:
            result.warnings.append("Достигнут лимит шагов; сохранена прочитанная часть.")
        # A later user turn proves earlier commentary is no longer being written,
        # even when that old answer was cancelled and has no action toolbar.
        last_user = max(
            (index for index, message in enumerate(result.messages) if message.role == "user"),
            default=-1,
        )
        for message in result.messages[:last_user]:
            message.complete = True
        if (current and current.generating) or any(
            not message.complete for message in result.messages
        ):
            result.warnings.append(
                "Последний ответ ещё пишется или его окончание не подтверждено; он может быть неполным."
            )
    except KeyboardInterrupt:
        result.warnings.append("Сбор прерван пользователем; сохранена прочитанная часть.")
    except Exception as error:
        # A failed import is a partial history, never a claimed complete one.
        result.warnings.append(str(error))
    finally:
        if original_position >= 0:
            try:
                reader.scroll_to(original_position, session)
            except Exception as error:
                result.warnings.append(f"Не удалось восстановить прокрутку: {error}")
    if result.expected_users is None:
        result.warnings.append(
            "Приложение не сообщило общее число сообщений; полнота не подтверждена."
        )
    elif result.user_count != result.expected_users:
        result.warnings.append(
            f"Прочитано сообщений пользователя: {result.user_count} из {result.expected_users}."
        )
    return result
