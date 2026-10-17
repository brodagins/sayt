"""Проверки логики «есть ли у компании сайт» без выхода в интернет."""

import base64
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checker  # noqa: E402
import database as db  # noqa: E402

SEARCH_ON = {"yandex_api_key": "key", "yandex_folder_id": "folder"}
SEARCH_OFF = {"yandex_api_key": "", "yandex_folder_id": ""}


def fake_fetch(pages):
    """pages: адрес -> (код, текст) или None (сайт недоступен)."""
    def fetch(url, verify=True):
        for prefix, value in pages.items():
            if url.startswith(prefix):
                if value is None:
                    return {"code": None, "url": url, "text": "", "error": "домен не найден"}
                return {"code": value[0], "url": url, "text": value[1], "error": ""}
        return {"code": None, "url": url, "text": "", "error": "домен не найден"}
    return fetch


def fake_search(results):
    def search(query, settings):
        return results
    return search


GOOD_PAGE = "<html><body><h1>Ромашка — цветы с доставкой</h1>" + "текст " * 20 + \
            "Звоните: +7 (900) 111-22-33</body></html>"


class ClassifyTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(checker.classify_website(""), "none")
        self.assertEqual(checker.classify_website("https://vk.com/romashka"), "social")
        self.assertEqual(checker.classify_website("2gis.ru/ufa/firm/1"), "directory")
        self.assertEqual(checker.classify_website("www.romashka-ufa.ru"), "site")

    def test_skeleton_matches_translit_variants(self):
        company = {"name": "ООО «Ромашка»", "city": "Уфа"}
        self.assertTrue(checker.domain_matches_name("romashka-ufa.ru", company))
        self.assertTrue(checker.domain_matches_name("romaschka.ru", company))
        self.assertFalse(checker.domain_matches_name("tulpan.ru", company))

    def test_generic_words_do_not_match(self):
        company = {"name": "Салон красоты", "city": "Уфа"}
        self.assertFalse(checker.domain_matches_name("salon-krasoty.ru", company))

    def test_phone_on_page(self):
        company = {"phone": "8 900 111-22-33"}
        self.assertTrue(checker.phone_on_page(company, GOOD_PAGE))
        self.assertFalse(checker.phone_on_page(company, "тел. +7 900 999-88-77"))


class CheckSiteTest(unittest.TestCase):
    def test_listed_site_works(self):
        result = checker.check_company({"name": "Ромашка", "website": "romashka.ru"}, SEARCH_OFF,
                                       fetch=fake_fetch({"https://romashka.ru": (200, GOOD_PAGE)}))
        self.assertEqual(result["status"], "сайт есть")

    def test_listed_site_dead(self):
        result = checker.check_company({"name": "Ромашка", "website": "romashka.ru"}, SEARCH_OFF,
                                       fetch=fake_fetch({}))
        self.assertEqual(result["status"], "сайт не открывается")
        self.assertIn("домен не найден", result["note"])

    def test_parked_domain(self):
        page = "<html>Этот домен продается! " + "x" * 100 + "</html>"
        result = checker.check_company({"name": "Ромашка", "website": "romashka.ru"}, SEARCH_OFF,
                                       fetch=fake_fetch({"https://romashka.ru": (200, page)}))
        self.assertEqual(result["status"], "сайт не открывается")

    def test_http_fallback(self):
        result = checker.check_company({"name": "Ромашка", "website": "romashka.ru"}, SEARCH_OFF,
                                       fetch=fake_fetch({"https://": None, "http://romashka.ru": (200, GOOD_PAGE)}))
        self.assertEqual(result["status"], "сайт есть")
        self.assertIn("без https", result["note"])

    def test_no_site_without_search_stays_unchecked(self):
        result = checker.check_company({"name": "Ромашка"}, SEARCH_OFF, fetch=fake_fetch({}))
        self.assertEqual(result["status"], db.DEFAULT_STATUS)
        self.assertIn("Настройках", result["note"])


class SearchTest(unittest.TestCase):
    company = {"name": "Ромашка", "city": "Уфа", "phone": "8 900 111-22-33"}

    def test_found_by_phone(self):
        search = fake_search([
            {"url": "https://2gis.ru/ufa/firm/1", "title": "Ромашка — 2ГИС", "text": ""},
            {"url": "https://cvety-ufa.ru/", "title": "Цветы в Уфе", "text": ""},
        ])
        result = checker.check_company(self.company, SEARCH_ON, search=search,
                                       fetch=fake_fetch({"https://cvety-ufa.ru": (200, GOOD_PAGE)}))
        self.assertEqual(result["status"], "сайт есть")
        self.assertEqual(result["website"], "https://cvety-ufa.ru")

    def test_not_found(self):
        search = fake_search([{"url": "https://2gis.ru/ufa/firm/1", "title": "Ромашка", "text": ""}])
        result = checker.check_company(self.company, SEARCH_ON, search=search, fetch=fake_fetch({}))
        self.assertEqual(result["status"], "сайт не найден")
        self.assertIsNone(result["website"])

    def test_only_social(self):
        search = fake_search([{"url": "https://vk.com/romashka_ufa", "title": "Ромашка | Уфа", "text": ""}])
        result = checker.check_company(self.company, SEARCH_ON, search=search, fetch=fake_fetch({}))
        self.assertEqual(result["status"], "только соцсети")

    def test_weak_match_needs_manual_check(self):
        page = "<html>" + "Другая фирма " * 10 + "</html>"
        search = fake_search([{"url": "https://flowers-shop.ru/", "title": "Ромашка", "text": ""}])
        result = checker.check_company({"name": "Ромашка", "city": "Уфа"}, SEARCH_ON, search=search,
                                       fetch=fake_fetch({"https://flowers-shop.ru": (200, page)}))
        self.assertEqual(result["status"], "нужна повторная проверка")
        self.assertIsNone(result["website"])

    def test_parse_xml(self):
        xml = ("<yandexsearch><response><results><grouping><group><doc>"
               "<url>https://romashka.ru/</url><title>Цветы <hlword>Ромашка</hlword></title>"
               "</doc></group></grouping></results></response></yandexsearch>").encode("utf-8")
        self.assertEqual(checker.parse_search_xml(xml)[0]["title"], "Цветы Ромашка")
        empty = b'<yandexsearch><response><error code="15">nothing</error></response></yandexsearch>'
        self.assertEqual(checker.parse_search_xml(empty), [])
        self.assertTrue(base64)  # формат ответа: rawData в base64


class SaveResultTest(unittest.TestCase):
    def test_save_and_manual_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(os.path.join(tmp, "t.db"))
            cid = db.add_company(conn, {"name": "Ромашка"})
            db.save_check_result(conn, cid, "сайт есть", "найден", website="https://romashka.ru")
            row = db.get_company(conn, cid)
            self.assertEqual(row["website"], "https://romashka.ru")
            self.assertEqual(row["status"], "сайт есть")
            self.assertIn("поиск Яндекса", row["source"])
            db.set_status(conn, cid, "сайт устарел")
            self.assertEqual(db.get_company(conn, cid)["check_note"], "статус указан вручную")
            conn.close()

    def test_old_statuses_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.db")
            conn = db.connect(path)
            conn.execute("INSERT INTO companies (name, status, created_at, updated_at) "
                         "VALUES ('А', 'сайт найден', '', '')")
            conn.commit()
            conn.close()
            conn = db.connect(path)
            self.assertEqual(db.search_companies(conn)[0][0]["status"], "сайт есть")
            conn.close()


if __name__ == "__main__":
    unittest.main()
