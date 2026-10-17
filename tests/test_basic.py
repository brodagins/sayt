"""Автоматические проверки. Запуск: python3 -m unittest discover tests"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402
from csv_tools import export_csv, import_csv, read_csv  # noqa: E402

HEADER = "Название компании;Город;Категория бизнеса;Телефон компании;Адрес;Сайт\r\n"


class BaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(os.path.join(self.tmp.name, "test.db"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class ImportTest(BaseTest):
    def test_import_cp1251_and_defaults(self):
        raw = (HEADER + "Компания 1;Город;Кафе;;;\r\n").encode("cp1251")
        report = import_csv(self.conn, raw, default_source="тест")
        self.assertEqual(report["added"], 1)
        row = db.search_companies(self.conn)[0][0]
        self.assertEqual(row["status"], db.DEFAULT_STATUS)  # нет сайта -> «не проверено»
        self.assertEqual(row["website"], "")
        self.assertEqual(row["source"], "тест")

    def test_comma_delimiter_and_synonyms(self):
        raw = "Компания,Телефон,Статус\nКомпания 1,123456,сайт найден\n".encode("utf-8")  # старое название
        parsed = read_csv(raw)
        self.assertEqual(parsed["delimiter"], ",")
        self.assertEqual(parsed["rows"][0]["name"], "Компания 1")
        import_csv(self.conn, raw)
        self.assertEqual(db.search_companies(self.conn)[0][0]["status"], "сайт есть")

    def test_unknown_status_becomes_unchecked(self):
        raw = "Название;Статус\nКомпания 1;что-то непонятное\n".encode("utf-8")
        import_csv(self.conn, raw)
        self.assertEqual(db.search_companies(self.conn)[0][0]["status"], db.DEFAULT_STATUS)

    def test_missing_name_column(self):
        report = import_csv(self.conn, "Город;Телефон\nГород;1\n".encode("utf-8"))
        self.assertIn("error", report)

    def test_skips_duplicates_inside_file(self):
        raw = (HEADER + "А;Г;;8 (900) 111-22-33;;\r\nБ;Г;;+7 900 111 22 33;;\r\n").encode("utf-8")
        report = import_csv(self.conn, raw)
        self.assertEqual(report["added"], 1)
        self.assertEqual(len(report["duplicates"]), 1)


class DuplicatesTest(BaseTest):
    def test_keys(self):
        self.assertEqual(db.phone_keys("8 (900) 111-22-33"), ["79001112233"])
        self.assertEqual(db.website_key("https://www.Site.ru/"), "site.ru")
        self.assertEqual(
            db.name_key({"name": "ООО «Ромашка»", "city": "г. Уфа", "address": "ул. Ленина, д. 5"}),
            db.name_key({"name": "Ромашка", "city": "Уфа", "address": "улица Ленина 5"}),
        )

    def test_find_merge_and_ignore(self):
        a = db.add_company(self.conn, {"name": "Ромашка", "phone": "89001112233", "source": "файл 1"})
        b = db.add_company(self.conn, {"name": "Ромашка", "phone": "+79001112233",
                                       "address": "Ленина 5", "source": "файл 2"})
        db.add_company(self.conn, {"name": "Другая", "phone": "1234567"})
        groups = db.find_duplicate_groups(self.conn)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["best_id"], b)

        db.mark_not_duplicates(self.conn, [a, b])
        self.assertEqual(db.find_duplicate_groups(self.conn), [])

        db.merge_companies(self.conn, a, [a, b])
        merged = db.get_company(self.conn, a)
        self.assertEqual(merged["address"], "Ленина 5")
        self.assertEqual(merged["source"], "файл 1; файл 2")
        self.assertIsNone(db.get_company(self.conn, b))


class ExportTest(BaseTest):
    def test_round_trip(self):
        db.add_company(self.conn, {"name": "=HYPERLINK(1)", "phone": "+7 900 111-22-33"})
        data = export_csv(db.search_companies(self.conn)[0])
        self.assertTrue(data.startswith("﻿".encode("utf-8")))
        text = data.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK(1)", text)
        self.assertIn('"=""+7 900 111-22-33"""', text)

        other = db.connect(os.path.join(self.tmp.name, "other.db"))
        import_csv(other, data)
        row = db.search_companies(other)[0][0]
        self.assertEqual(row["name"], "=HYPERLINK(1)")
        self.assertEqual(row["phone"], "+7 900 111-22-33")
        other.close()

    def test_cyrillic_search_ignores_case(self):
        db.add_company(self.conn, {"name": "Ёлка"})
        self.assertEqual(db.search_companies(self.conn, q="ЕЛК")[1], 1)


if __name__ == "__main__":
    unittest.main()
