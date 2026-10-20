import unittest

from wgtts.chat.tables import Table, tables_for_speech


class TableTests(unittest.TestCase):
    def test_rows_are_read_with_column_names_and_empty_cells(self):
        source = (
            "Перед таблицей.\n\n"
            "| Товар | Цена | Примечание |\n"
            "| :--- | ---: | :---: |\n"
            "| Чай | 50 | |\n"
            "| Кофе | | **Без сахара** |\n\n"
            "После таблицы."
        )
        spoken = tables_for_speech(source)
        self.assertIn("Строка 1. Товар: Чай; Цена: 50; Примечание: пусто.", spoken)
        self.assertIn("Строка 2. Товар: Кофе; Цена: пусто; Примечание: Без сахара.", spoken)
        self.assertTrue(spoken.startswith("Перед таблицей.\n\nТаблица."))
        self.assertTrue(spoken.endswith("Конец таблицы.\n\nПосле таблицы."))

    def test_roundtrip_preserves_multiline_cells_pipes_backslashes_and_html(self):
        table = Table(
            ["Выражение", "Описание"],
            [["a|b", "Первая\nВторая"], ["C:\\tmp\\", "x < y & z > 0"]],
        )
        markdown = table.markdown()
        self.assertIn(r"a\|b", markdown)
        self.assertIn("Первая<br>Вторая", markdown)
        self.assertIn("x &lt; y &amp; z &gt; 0", markdown)
        spoken = tables_for_speech(markdown)
        self.assertIn("Выражение: a|b; Описание: Первая; Вторая.", spoken)
        self.assertIn("Выражение: C:\\tmp\\; Описание: x < y & z > 0.", spoken)

    def test_optional_outer_pipes_and_several_tables(self):
        text = "A | B\n--- | ---\nx | y\n\nМежду.\n\n| Один |\n| --- |\n| Два |"
        spoken = tables_for_speech(text)
        self.assertEqual(spoken.count("Таблица."), 2)
        self.assertIn("Строка 1. A: x; B: y.", spoken)
        self.assertIn("Строка 1. Один: Два.", spoken)
        self.assertIn("\n\nМежду.\n\n", spoken)

    def test_code_blocks_and_non_tables_remain_unchanged(self):
        for source in (
            "```markdown\n| A | B |\n| --- | --- |\n| x | y |\n```",
            "~~~~\n| A | B |\n| --- | --- |\n| x | y |\n~~~~",
            "Текст с | разделителем.\nОбычный текст\n---\nКонец.",
            "| A | B |\n| -- | --- |\n| x | y |",
        ):
            with self.subTest(source=source):
                self.assertEqual(tables_for_speech(source), source)

    def test_blank_header_and_header_only_tables(self):
        spoken = tables_for_speech("| | Цена |\n| --- | --- |\n| Чай | 50 |")
        self.assertIn("Строка 1. Столбец 1: Чай; Цена: 50.", spoken)
        spoken = tables_for_speech("| Товар | Цена |\n| --- | --- |")
        self.assertIn("Столбцы: Товар; Цена.", spoken)

    def test_malformed_rows_are_preserved_instead_of_silently_truncated(self):
        text = "| A | B |\n| --- | --- |\n| x | y |\n| лишняя | ячейка | здесь |"
        self.assertTrue(tables_for_speech(text).endswith("| лишняя | ячейка | здесь |"))


if __name__ == "__main__":
    unittest.main()
