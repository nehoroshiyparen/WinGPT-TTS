"""Windows UI Automation adapter. Reading never invokes chat action buttons."""

import re
import time
from dataclasses import dataclass

from comtypes import COMError
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
from pywinauto.timings import TimeoutError as UIATimeoutError
from pywinauto.uia_defines import NoPatternInterfaceError

from wgtts.chat.models import Message, Snapshot

START, END = 0, 1
AUTHOR_LABELS = {
    "Вы сказали:": "user",
    "ChatGPT сказал:": "assistant",
    "You said:": "user",
    "ChatGPT said:": "assistant",
}
FOOTER_BUTTONS = {"Прочитать вслух", "Другие действия", "Read aloud", "More actions"}
REMOVE_BUTTONS = FOOTER_BUTTONS | {
    "Копировать код",
    "Копировать",
    "Добавить в чат",
    "Изменить",
    "Редактировать",
    "Показать ещё",
    "Свернуть",
    "Copy code",
    "Copy",
    "Edit",
    "Show more",
    "Show less",
}
TIME_LABEL = re.compile(r"^\d{1,2}:\d{2}(?:\s*[AP]M)?$", re.I)
JUMP_LABEL = re.compile(r"(?:Перейти к сообщению пользователя|Jump to user message)\s+(\d+)$")


class ReaderError(RuntimeError):
    pass


@dataclass
class Context:
    document: object
    conversation: object
    pattern: object
    session_key: str


def _role(node):
    info = node.element_info
    if info.control_type != "Text":
        return None
    if info.name not in AUTHOR_LABELS or info.element.CurrentAriaRole != "heading":
        return None
    # User-authored headings are not the screen-reader author captions.
    if "sr-only" not in info.class_name.split():
        return None
    return AUTHOR_LABELS[info.name]


def _before(left, right):
    return left.CompareEndpoints(START, right, START) < 0


def _without_controls(span, exclusions):
    cursor = span.Clone()
    chunks = []
    for exclusion in exclusions:
        if exclusion.CompareEndpoints(END, cursor, START) <= 0:
            continue
        if exclusion.CompareEndpoints(START, span, END) >= 0:
            break
        if exclusion.CompareEndpoints(START, cursor, START) > 0:
            prefix = cursor.Clone()
            prefix.MoveEndpointByRange(END, exclusion, START)
            chunks.append(prefix.GetText(-1))
        cursor.MoveEndpointByRange(START, exclusion, END)
        if cursor.CompareEndpoints(START, span, END) >= 0:
            return "".join(chunks)
    chunks.append(cursor.GetText(-1))
    return "".join(chunks)


class ChatReader:
    def connect(self) -> Context:
        try:
            window = Desktop(backend="uia").window(title="ChatGPT", control_type="Window")
            document = window.child_window(
                auto_id="RootWebArea", control_type="Document"
            ).wrapper_object()
            candidates = document.descendants(control_type="Group")
            conversation = next(
                (
                    node
                    for node in candidates
                    if node.window_text() in ("Разговор", "Conversation")
                    and node.element_info.element.CurrentAriaRole == "region"
                ),
                None,
            )
            if conversation is None:
                raise ReaderError("Область переписки не найдена. Открой чат в приложении ChatGPT.")
            session = f"{window.wrapper_object().handle}:{document.window_text()}:{conversation.element_info.runtime_id}"
            return Context(document, conversation, document.iface_text, session)
        except (ElementNotFoundError, UIATimeoutError) as error:
            raise ReaderError("Окно ChatGPT недоступно. Открой приложение и нужный чат.") from error
        except COMError as error:
            raise ReaderError("Интерфейс обновляется; повтори чтение через секунду.") from error

    def position(self, context=None):
        context = context or self.connect()
        scroll = context.conversation.iface_scroll
        return float(scroll.CurrentVerticalScrollPercent), float(scroll.CurrentVerticalViewSize)

    def scroll_to(self, percent, session_key=None):
        context = self.connect()
        if session_key is not None and context.session_key != session_key:
            raise ReaderError("Во время сбора открыт другой чат; сбор остановлен.")
        scroll = context.conversation.iface_scroll
        if scroll.CurrentVerticallyScrollable:
            scroll.SetScrollPercent(-1, max(0.0, min(100.0, percent)))

    def snapshot(self) -> Snapshot:
        last_error = None
        for _ in range(3):
            try:
                return self._snapshot()
            except COMError as error:
                last_error = error
                time.sleep(0.15)
            except NoPatternInterfaceError as error:
                raise ReaderError(
                    "Эта версия интерфейса не предоставляет необходимые TextPattern/ScrollPattern."
                ) from error
        raise ReaderError("Дерево чата меняется во время чтения. Повтори попытку.") from last_error

    def _snapshot(self):
        context = self.connect()
        nodes = context.conversation.descendants()
        pattern = context.pattern
        headings = []
        for index, node in enumerate(nodes):
            node.element_info.set_cache_strategy(cached=True)
            role = _role(node)
            if role:
                headings.append((index, node, role))
        percent, view = self.position(context)
        all_buttons = context.document.descendants(control_type="Button")
        button_names = [button.window_text() for button in all_buttons]
        expected = [
            int(match.group(1)) for name in button_names if (match := JUMP_LABEL.fullmatch(name))
        ]
        generating = any(
            name
            in (
                "Остановить",
                "Остановить ответ",
                "Остановить генерацию",
                "Stop",
                "Stop generating",
                "Stop response",
            )
            for name in button_names
        )
        conversation_range = pattern.RangeFromChild(context.conversation.element_info.element)
        messages = []
        for position, (node_index, heading, role) in enumerate(headings):
            next_index = headings[position + 1][0] if position + 1 < len(headings) else len(nodes)
            block = nodes[node_index + 1 : next_index]
            head_range = pattern.RangeFromChild(heading.element_info.element)
            span = head_range.Clone()
            span.MoveEndpointByRange(START, head_range, END)
            if position + 1 < len(headings):
                end_range = pattern.RangeFromChild(headings[position + 1][1].element_info.element)
                span.MoveEndpointByRange(END, end_range, START)
            else:
                span.MoveEndpointByRange(END, conversation_range, END)

            # In-progress commentary is wrapped separately from tool output.
            parent = heading.parent()
            grouped_draft = (
                parent.element_info.runtime_id != context.conversation.element_info.runtime_id
                and "flex-col" in parent.element_info.class_name.split()
            )
            if grouped_draft:
                parent_range = pattern.RangeFromChild(parent.element_info.element)
                span.MoveEndpointByRange(END, parent_range, END)

            footers = [
                node
                for node in block
                if node.element_info.control_type == "Button"
                and node.window_text() in FOOTER_BUTTONS
            ]
            complete = role == "user" or bool(footers)
            for node in block:
                name = node.window_text()
                sentinel = (
                    name in ("Последний ответ", "Last reply")
                    and node.element_info.element.CurrentAriaRole == "heading"
                )
                if (
                    node.element_info.control_type == "Button" and name in FOOTER_BUTTONS
                ) or sentinel:
                    boundary = pattern.RangeFromChild(node.element_info.element)
                    if _before(boundary, span) or boundary.CompareEndpoints(START, span, END) >= 0:
                        continue
                    span.MoveEndpointByRange(END, boundary, START)
                    break
            if role == "user":
                # The app emits a separate time label after the user content.
                times = [
                    node
                    for node in block
                    if node.element_info.control_type == "Text"
                    and TIME_LABEL.fullmatch(node.window_text())
                    and node.parent().element_info.runtime_id
                    == context.conversation.element_info.runtime_id
                ]
                if times:
                    boundary = pattern.RangeFromChild(times[-1].element_info.element)
                    if (
                        boundary.CompareEndpoints(START, span, START) > 0
                        and boundary.CompareEndpoints(START, span, END) < 0
                    ):
                        span.MoveEndpointByRange(END, boundary, START)
            exclusions = []
            for node in block:
                if (
                    node.element_info.control_type == "Button"
                    and node.window_text() in REMOVE_BUTTONS
                ):
                    exclusions.append(pattern.RangeFromChild(node.element_info.element))
            text = (
                _without_controls(span, exclusions)
                .replace("\ufffc", "")
                .replace("\r\n", "\n")
                .strip()
            )
            if text:
                messages.append(
                    Message(
                        role,
                        text,
                        complete and not grouped_draft,
                        str(heading.element_info.runtime_id),
                    )
                )
        generating = generating or bool(
            messages and messages[-1].role == "assistant" and not messages[-1].complete
        )
        return Snapshot(
            context.document.window_text(),
            context.session_key,
            messages,
            percent,
            view,
            max(expected) if expected else None,
            generating,
        )
