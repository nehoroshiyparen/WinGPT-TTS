"""Exercise UIA table extraction with real text-range boundary semantics."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pywinauto.findwindows import ElementAmbiguousError
from pywinauto.uia_defines import NoPatternInterfaceError

from wgtts.chat.reader import ChatReader, ReaderError, _message_text, _read_table


class AccessibleNode:
    def __init__(self, control, name="", *, role="", visible=True, children=()):
        self.element_info = SimpleNamespace(
            control_type=control,
            automation_id="RootWebArea" if control == "Document" else "",
            runtime_id=[id(self)],
            element=SimpleNamespace(CurrentAriaRole=role),
        )
        self.name, self.visible = name, visible
        self._parent = None
        self._children = list(children)
        self.iface_text = object()
        self.handle = 123
        for child in self._children:
            child._parent = self

    def parent(self):
        return self._parent

    def window_text(self):
        return self.name

    def is_visible(self):
        return self.visible

    def descendants(self, *, control_type):
        nodes = []
        for child in self._children:
            if child.element_info.control_type == control_type:
                nodes.append(child)
            nodes.extend(child.descendants(control_type=control_type))
        return nodes


class ReaderConnectionTests(unittest.TestCase):
    def connect(self, *documents):
        window = AccessibleNode("Window", "ChatGPT", children=documents)
        desktop = Mock()
        desktop.window.return_value.wrapper_object.return_value = window
        with patch("wgtts.chat.reader.Desktop", return_value=desktop):
            return ChatReader().connect()

    def conversation(self):
        return AccessibleNode("Group", "Разговор", role="region")

    def test_three_root_web_areas_select_chat_regardless_of_order(self):
        conversation = self.conversation()
        chat = AccessibleNode("Document", "My chat", children=[conversation])
        previews = [AccessibleNode("Document", name) for name in ["Player", "Design"]]
        first = self.connect(previews[0], chat, previews[1])
        second = self.connect(previews[1], previews[0], chat)
        self.assertIs(first.document, chat)
        self.assertIs(first.conversation, conversation)
        self.assertIs(first.pattern, chat.iface_text)
        self.assertEqual(first.session_key, second.session_key)

    def test_embedded_preview_cannot_supply_a_second_conversation(self):
        preview = AccessibleNode("Document", "Preview", children=[self.conversation()])
        conversation = self.conversation()
        chat = AccessibleNode("Document", "Chat", children=[preview, conversation])
        self.assertIs(self.connect(chat).conversation, conversation)

    def test_chat_must_own_the_conversation_instead_of_reading_a_nested_preview(self):
        preview = AccessibleNode("Document", "Preview", children=[self.conversation()])
        chat = AccessibleNode("Document", "Chat", children=[preview])
        with self.assertRaisesRegex(ReaderError, "Область переписки не найдена"):
            self.connect(chat)

    def test_hidden_document_and_similarly_named_heading_are_not_chats(self):
        hidden = AccessibleNode(
            "Document", "Old chat", visible=False, children=[self.conversation()]
        )
        heading = AccessibleNode("Group", "Разговор", role="heading")
        panel = AccessibleNode("Document", "Panel", children=[heading])
        chat = AccessibleNode("Document", "Current", children=[self.conversation()])
        self.assertIs(self.connect(hidden, panel, chat).document, chat)

    def test_two_real_chat_regions_report_a_readable_error(self):
        first = AccessibleNode("Document", "First", children=[self.conversation()])
        second = AccessibleNode("Document", "Second", children=[self.conversation()])
        with self.assertRaisesRegex(ReaderError, "несколько областей переписки"):
            self.connect(first, second)

    def test_ambiguous_app_windows_do_not_leak_pywinauto_traceback(self):
        desktop = Mock()
        desktop.window.return_value.wrapper_object.side_effect = ElementAmbiguousError()
        with patch("wgtts.chat.reader.Desktop", return_value=desktop):
            with self.assertRaisesRegex(ReaderError, "несколько окон ChatGPT"):
                ChatReader().connect()


class TextRange:
    def __init__(self, source, start, end):
        self.source, self.start, self.end = source, start, end

    def Clone(self):
        return TextRange(self.source, self.start, self.end)

    def CompareEndpoints(self, endpoint, other, other_endpoint):
        left = self.start if endpoint == 0 else self.end
        right = other.start if other_endpoint == 0 else other.end
        return (left > right) - (left < right)

    def MoveEndpointByRange(self, endpoint, other, other_endpoint):
        value = other.start if other_endpoint == 0 else other.end
        if endpoint == 0:
            self.start = value
            self.end = max(self.end, value)
        else:
            self.end = value
            self.start = min(self.start, value)

    def GetText(self, limit):
        return self.source[self.start : self.end]


class Node:
    def __init__(self, role, start, end, *, control="Group", name="", children=()):
        self.start, self.end = start, end
        self.element_info = SimpleNamespace(control_type=control, name=name, element=self)
        self.CurrentAriaRole = role
        self._children = list(children)
        self.grid = None

    @property
    def iface_grid(self):
        if self.grid is None:
            raise NoPatternInterfaceError()
        return self.grid

    def children(self):
        return self._children

    def descendants(self):
        return [node for child in self._children for node in [child, *child.descendants()]]

    def window_text(self):
        return self.element_info.name


class Document:
    def __init__(self):
        self.source = ""

    def cell(self, text, role="cell", *, children=()):
        start = len(self.source)
        self.source += text
        return Node(role, start, len(self.source), children=children)

    def table(self, rows, *, headers=True):
        start = len(self.source)
        nodes = []
        for index, values in enumerate(rows):
            cells = [
                self.cell(value, "columnheader" if headers and index == 0 else "cell")
                for value in values
            ]
            nodes.append(Node("row", cells[0].start, cells[-1].end, children=cells))
        return Node("table", start, len(self.source), control="Table", children=nodes)

    def RangeFromChild(self, node):
        return TextRange(self.source, node.start, node.end)

    def range(self, start=0, end=None):
        return TextRange(self.source, start, len(self.source) if end is None else end)


class ReaderTableTests(unittest.TestCase):
    def test_table_replaces_flat_text_once_and_keeps_prose_and_empty_cells(self):
        document = Document()
        document.source = "До таблицы.\n"
        table = document.table(
            [["Товар", "Цена", "Примечание"], ["Чай", "50", ""], ["Кофе", "80", "Первая\nВторая"]]
        )
        document.source += "\nПосле таблицы."
        text = _message_text(document.range(), [table, *table.descendants()], document)
        self.assertIn("| Товар | Цена | Примечание |", text)
        self.assertIn("| Чай | 50 |  |", text)
        self.assertIn("| Кофе | 80 | Первая<br>Вторая |", text)
        self.assertEqual(text.count("Товар"), 1)
        self.assertTrue(text.startswith("До таблицы."))
        self.assertTrue(text.endswith("После таблицы."))

    def test_buttons_inside_cells_are_removed_but_literal_button_names_stay(self):
        document = Document()
        table = document.table([["Товар", "Действие"], ["ЧайКопировать", "Копировать"]])
        cell = table.children()[1].children()[0]
        button = Node(
            "button", cell.start + len("Чай"), cell.end, control="Button", name="Копировать"
        )
        cell._children.append(button)
        text = _message_text(document.range(), [table, *table.descendants()], document)
        self.assertIn("| Чай | Копировать |", text)
        self.assertNotIn("ЧайКопировать", text)

    def test_headerless_table_keeps_first_data_row(self):
        document = Document()
        table = document.table([["Чай", "50"], ["Кофе", "80"]], headers=False)
        parsed = _read_table(table, document, [])
        self.assertEqual(parsed.headers, ["Столбец 1", "Столбец 2"])
        self.assertEqual(parsed.rows, [["Чай", "50"], ["Кофе", "80"]])

    def test_grid_pattern_uses_coordinates_and_combines_multiple_header_rows(self):
        document = Document()
        header = document.cell("Цена", "columnheader")
        cells = [
            [header, header],
            [document.cell("Мин.", "columnheader"), document.cell("Макс.", "columnheader")],
            [document.cell("10"), document.cell("20")],
        ]
        table = Node("table", 0, len(document.source), control="Table")
        table.grid = SimpleNamespace(
            CurrentRowCount=3, CurrentColumnCount=2, GetItem=lambda row, column: cells[row][column]
        )
        with (
            patch("wgtts.chat.reader.UIAElementInfo", side_effect=lambda node: node),
            patch("wgtts.chat.reader.UIAWrapper", side_effect=lambda node: node),
        ):
            parsed = _read_table(table, document, [])
        self.assertEqual(parsed.headers, ["Цена / Мин.", "Цена / Макс."])
        self.assertEqual(parsed.rows, [["10", "20"]])

    def test_table_outside_message_bounds_is_ignored(self):
        document = Document()
        document.source = "Только этот ответ."
        end = len(document.source)
        table = document.table([["Чужая", "Таблица"], ["1", "2"]])
        text = _message_text(document.range(end=end), [table, *table.descendants()], document)
        self.assertEqual(text, "Только этот ответ.")

    def test_unavailable_table_structure_keeps_original_text(self):
        document = Document()
        document.source = "Содержимое без структуры"
        table = Node("table", 0, len(document.source), control="Table")
        self.assertEqual(_message_text(document.range(), [table], document), document.source)

    def test_two_tables_stay_in_message_order(self):
        document = Document()
        first = document.table([["Первый", "Номер"], ["А", "1"]])
        document.source += "\nМежду таблицами.\n"
        second = document.table([["Второй", "Номер"], ["Б", "2"]])
        # The replacement algorithm must also work with unsorted ranges.
        text = _message_text(document.range(), [second, first], document)
        self.assertLess(text.index("| Первый"), text.index("Между таблицами."))
        self.assertLess(text.index("Между таблицами."), text.index("| Второй"))
        self.assertEqual(text.count("| --- | --- |"), 2)


if __name__ == "__main__":
    unittest.main()
