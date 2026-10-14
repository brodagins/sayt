"""База потенциальных клиентов — программа запускается командой:

    python3 app.py

После запуска интерфейс открывается в браузере по адресу
http://127.0.0.1:8000 . Программа работает только на этом компьютере:
сайт не виден из интернета, данные никуда не отправляются.
"""

import email.parser
import email.policy
import html
import math
import os
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlparse

import database as db
from csv_tools import HEADER_SYNONYMS, export_csv, import_csv, template_csv

HOST = "127.0.0.1"
FIRST_PORT = 8000
PAGE_SIZE = 50
MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # 30 МБ


def e(value):
    """Экранирование текста для HTML."""
    return html.escape("" if value is None else str(value), quote=True)


# ---------------------------------------------------------------------------
# Оформление страниц
# ---------------------------------------------------------------------------

STYLE = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#667085; --line:#e3e6eb;
        --accent:#2f6fde; --accent-text:#fff; --danger:#c43c3c; --ok:#1f8a4c; --warn:#b26b00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181d; --card:#1f232b; --text:#e6e8ec; --muted:#9aa3b2; --line:#323844;
          --accent:#5b8ff0; --accent-text:#0b1220; --danger:#e06666; --ok:#4cc584; --warn:#e0a040; }
}
* { box-sizing:border-box; }
body { margin:0; font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background:var(--bg); color:var(--text); }
header { background:var(--card); border-bottom:1px solid var(--line); padding:12px 20px;
         display:flex; flex-wrap:wrap; gap:6px 18px; align-items:center; }
header .logo { font-weight:700; margin-right:10px; }
header a { color:var(--text); text-decoration:none; padding:4px 2px; border-bottom:2px solid transparent; }
header a.active { border-bottom-color:var(--accent); }
main { padding:20px; max-width:1500px; margin:0 auto; }
h1 { font-size:22px; margin:0 0 14px; }
h2 { font-size:18px; margin:22px 0 10px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; }
.muted { color:var(--muted); }
.msg { padding:10px 14px; border-radius:8px; margin-bottom:14px; background:var(--card);
       border-left:4px solid var(--ok); }
.msg.error { border-left-color:var(--danger); }
.btn, button { display:inline-block; font:inherit; padding:7px 14px; border-radius:7px;
       border:1px solid var(--line); background:var(--card); color:var(--text); cursor:pointer; text-decoration:none; }
.btn.primary, button.primary { background:var(--accent); border-color:var(--accent); color:var(--accent-text); }
.btn.danger, button.danger { color:var(--danger); border-color:var(--danger); }
input[type=text], input[type=search], select { font:inherit; padding:6px 9px; border-radius:7px;
       border:1px solid var(--line); background:var(--bg); color:var(--text); max-width:100%; }
form.filters { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; background:var(--card); }
th, td { text-align:left; padding:7px 9px; border-bottom:1px solid var(--line); vertical-align:top; }
th { font-size:13px; color:var(--muted); font-weight:600; white-space:nowrap; }
td.nowrap { white-space:nowrap; }
td select { font-size:13px; padding:3px 5px; }
.stats { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }
.stat { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 12px; font-size:14px; }
.stat b { font-size:16px; }
.pager { display:flex; gap:6px; align-items:center; margin-top:12px; flex-wrap:wrap; }
.form-grid { display:grid; grid-template-columns: 200px 1fr; gap:10px 14px; align-items:center; max-width:760px; }
.form-grid input[type=text], .form-grid select { width:100%; }
@media (max-width: 640px) { .form-grid { grid-template-columns: 1fr; } }
.group { border:1px solid var(--line); border-radius:10px; padding:12px; margin-bottom:14px; background:var(--card); }
.group .reasons { color:var(--warn); font-size:14px; margin-bottom:8px; }
.actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
code { background:var(--bg); padding:1px 5px; border-radius:4px; }
a { color:var(--accent); }
"""

NAV = [
    ("/", "Компании"),
    ("/import", "Загрузить CSV"),
    ("/add", "Добавить вручную"),
    ("/duplicates", "Дубликаты"),
    ("/export", "Экспорт"),
    ("/help", "Справка"),
]


def page(title, body, active="", message="", error=""):
    nav = "".join(
        '<a href="%s"%s>%s</a>' % (href, ' class="active"' if href == active else "", e(label))
        for href, label in NAV
    )
    notice = ""
    if message:
        notice += '<div class="msg">%s</div>' % e(message)
    if error:
        notice += '<div class="msg error">%s</div>' % e(error)
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s — База клиентов</title><style>%s</style></head>
<body><header><span class="logo">База клиентов</span>%s</header>
<main>%s<h1>%s</h1>%s</main></body></html>""" % (e(title), STYLE, nav, notice, e(title), body)


def options(values, selected, empty_label=None):
    out = []
    if empty_label is not None:
        out.append('<option value="">%s</option>' % e(empty_label))
    for value in values:
        out.append('<option value="%s"%s>%s</option>' % (
            e(value), " selected" if value == selected else "", e(value)))
    return "".join(out)


def website_link(website):
    """Ссылка на сайт компании (только http/https)."""
    if not db.website_key(website):
        return e(website)
    url = website.strip()
    if not url.lower().startswith(("http://", "https://")):
        if "://" in url:
            return e(website)
        url = "http://" + url
    return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (e(url), e(website))


def search_link(company):
    """Ссылка на обычный поиск в Яндексе — для ручной проверки сайта."""
    text = " ".join(x for x in (company["name"], company["city"]) if x)
    url = "https://yandex.ru/search/?text=" + quote(text + " официальный сайт")
    return ('<a href="%s" target="_blank" rel="noopener noreferrer" '
            'title="Открыть поиск, чтобы проверить сайт вручную">искать сайт</a>' % e(url))


# ---------------------------------------------------------------------------
# Страницы
# ---------------------------------------------------------------------------

def list_page(conn, params):
    q = params.get("q", "")
    city = params.get("city", "")
    category = params.get("category", "")
    status = params.get("status", "")
    try:
        page_no = max(1, int(params.get("page", "1")))
    except ValueError:
        page_no = 1
    rows, total = db.search_companies(conn, q, city, category, status,
                                      limit=PAGE_SIZE, offset=(page_no - 1) * PAGE_SIZE)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    filters = {"q": q, "city": city, "category": category, "status": status}
    active_filters = {k: v for k, v in filters.items() if v}

    counts = db.status_counts(conn)
    all_total = sum(counts.values())
    stats = '<div class="stats"><div class="stat">Всего компаний: <b>%d</b></div>' % all_total
    for name in db.STATUSES:
        stats += '<a class="stat" style="text-decoration:none;color:inherit" href="/?%s">%s: <b>%d</b></a>' % (
            e(urlencode({"status": name})), e(name), counts.get(name, 0))
    stats += "</div>"

    form = """<form class="filters card" method="get" action="/">
      <input type="search" name="q" value="%s" placeholder="Поиск: название, телефон, адрес…" size="32">
      <select name="city">%s</select>
      <select name="category">%s</select>
      <select name="status">%s</select>
      <button class="primary" type="submit">Найти</button>
      <a class="btn" href="/">Сбросить</a>
      <span style="flex:1"></span>
      <a class="btn" href="/export.csv?%s">Скачать эти записи для Excel</a>
    </form>""" % (
        e(q),
        options(db.distinct_values(conn, "city"), city, "Все города"),
        options(db.distinct_values(conn, "category"), category, "Все категории"),
        options(db.STATUSES, status, "Все статусы"),
        e(urlencode(dict(active_filters, format="excel"))),
    )

    if not rows:
        if all_total == 0:
            table = ('<div class="card">База пока пустая. <a href="/import">Загрузите CSV-файл</a> '
                     'или <a href="/add">добавьте компанию вручную</a>.</div>')
        else:
            table = '<div class="card">По этому запросу ничего не найдено.</div>'
    else:
        back = "/?" + urlencode(dict(active_filters, page=page_no))
        lines = []
        for c in rows:
            lines.append("""<tr>
              <td><a href="/edit?id=%d">%s</a></td><td>%s</td><td>%s</td>
              <td class="nowrap">%s</td><td>%s</td><td>%s<br><span class="muted" style="font-size:13px">%s</span></td>
              <td>%s</td>
              <td><form method="post" action="/status">
                <input type="hidden" name="id" value="%d"><input type="hidden" name="back" value="%s">
                <select name="status" onchange="this.form.submit()">%s</select>
                <noscript><button type="submit">OK</button></noscript></form></td>
            </tr>""" % (
                c["id"], e(c["name"]), e(c["city"]), e(c["category"]), e(c["phone"]),
                e(c["address"]), website_link(c["website"]), search_link(c), e(c["source"]),
                c["id"], e(back), options(db.STATUSES, c["status"]),
            ))
        table = """<div class="table-wrap"><table>
          <tr><th>Название компании</th><th>Город</th><th>Категория бизнеса</th><th>Телефон</th>
          <th>Адрес</th><th>Сайт</th><th>Источник данных</th><th>Статус проверки</th></tr>
          %s</table></div>""" % "".join(lines)

    pager = ""
    if pages > 1:
        def link(n, label):
            return '<a class="btn" href="/?%s">%s</a>' % (e(urlencode(dict(active_filters, page=n))), label)
        pager = '<div class="pager">'
        if page_no > 1:
            pager += link(page_no - 1, "← Назад")
        pager += '<span class="muted">Страница %d из %d (найдено: %d)</span>' % (page_no, pages, total)
        if page_no < pages:
            pager += link(page_no + 1, "Вперёд →")
        pager += "</div>"
    elif rows:
        pager = '<div class="pager muted">Найдено: %d</div>' % total

    return stats + form + table + pager


def company_form(company, action, submit_label):
    fields = ""
    for name, label in db.FIELDS:
        if name == "status":
            control = '<select name="status" id="status">%s</select>' % options(db.STATUSES, company.get("status") or db.DEFAULT_STATUS)
        else:
            control = '<input type="text" name="%s" id="%s" value="%s"%s>' % (
                name, name, e(company.get(name, "")), " required" if name == "name" else "")
        fields += "<label for='%s'>%s%s</label>%s" % (name, e(label), " *" if name == "name" else "", control)
    hidden = '<input type="hidden" name="id" value="%d">' % company["id"] if company.get("id") else ""
    return """<form class="card" method="post" action="%s">%s
      <div class="form-grid">%s</div>
      <p class="muted">* — обязательное поле. Если сайт неизвестен, оставьте поле пустым и статус «не проверено».</p>
      <div class="actions"><button class="primary" type="submit">%s</button>
      <a class="btn" href="/">Отмена</a></div></form>""" % (action, hidden, fields, e(submit_label))


def import_page():
    columns = "".join("<li><b>%s</b> — также понимаются: %s</li>" % (
        e(label), e(", ".join(n for n in HEADER_SYNONYMS[name] if n != label.lower())))
        for name, label in db.FIELDS)
    return """
    <form class="card" method="post" action="/import" enctype="multipart/form-data">
      <div class="form-grid">
        <label>Файл CSV *</label><input type="file" name="file" accept=".csv,text/csv" required>
        <label>Откуда эти данные?</label>
        <input type="text" name="source" placeholder="например: выгрузка из справочника, 14.10.2026">
        <label></label>
        <label><input type="checkbox" name="skip_duplicates" value="1" checked>
          Не добавлять компании, которые уже есть в базе</label>
      </div>
      <p class="muted">«Откуда эти данные» запишется в поле «Источник данных» для строк, где источник не указан в самом файле.</p>
      <div class="actions"><button class="primary" type="submit">Загрузить</button></div>
    </form>
    <div class="card">
      <h2 style="margin-top:0">Каким должен быть файл</h2>
      <p>Первая строка — заголовки столбцов. Обязателен только столбец с названием компании.
         Остальные столбцы можно не заполнять. Порядок столбцов не важен.</p>
      <ul>%s</ul>
      <p>Подходит файл, сохранённый из Excel как «CSV UTF-8» или просто «CSV».
         <a href="/template.csv">Скачать пустой шаблон</a> (только заголовки, без данных).</p>
      <p class="muted">Если сайт в файле не указан, статус будет «не проверено» — это не значит, что сайта нет.</p>
    </div>""" % columns


def import_report(report, filename):
    if report.get("error"):
        return '<div class="msg error">%s</div><a class="btn" href="/import">Попробовать другой файл</a>' % e(report["error"])
    mapping = "".join("<li>«%s» → %s</li>" % (e(h), e(db.LABELS[f])) for h, f in report["mapping"].items())
    unknown = ""
    if report["unknown"]:
        unknown = "<p>Эти столбцы не распознаны и пропущены: %s</p>" % e(", ".join("«%s»" % u for u in report["unknown"]))
    delim = {";": "точка с запятой", ",": "запятая", "\t": "табуляция"}.get(report["delimiter"], report["delimiter"])
    body = """<div class="card">
      <p><b>Файл:</b> %s<br><b>Строк с данными:</b> %d<br>
      <b>Добавлено компаний:</b> %d<br>
      <b>Пропущено как дубликаты:</b> %d<br>
      <b>Пропущено строк без названия:</b> %d</p>
      <p class="muted">Кодировка: %s, разделитель: %s.</p>
      <p>Распознанные столбцы:</p><ul>%s</ul>%s""" % (
        e(filename), report["total"], report["added"], len(report["duplicates"]),
        len(report["no_name"]), e(report["encoding"]), e(delim), mapping, unknown)
    if report["status_reset"]:
        body += ("<p>У %d строк статус не совпал ни с одним из списка, им поставлен статус «%s».</p>"
                 % (report["status_reset"], e(db.DEFAULT_STATUS)))
    if report["no_name"]:
        shown = ", ".join(str(n) for n in report["no_name"][:50])
        body += "<p>Строки без названия (номер строки в файле): %s%s</p>" % (
            shown, "…" if len(report["no_name"]) > 50 else "")
    body += '<div class="actions"><a class="btn primary" href="/">Открыть базу</a>' \
            '<a class="btn" href="/import">Загрузить ещё файл</a></div></div>'
    if report["duplicates"]:
        rows = "".join(
            '<tr><td>%d</td><td>%s</td><td>%s</td><td><a href="/edit?id=%d">запись №%d</a></td></tr>' % (
                d["line"], e(d["name"]), e(d["reason"]), d["existing_id"], d["existing_id"])
            for d in report["duplicates"][:300])
        more = "" if len(report["duplicates"]) <= 300 else '<p class="muted">Показаны первые 300.</p>'
        body += """<h2>Пропущенные дубликаты</h2><div class="table-wrap"><table>
          <tr><th>Строка файла</th><th>Название</th><th>Причина</th><th>Уже есть в базе</th></tr>%s</table></div>%s""" % (rows, more)
    return body


def duplicates_page(conn):
    groups = db.find_duplicate_groups(conn)
    if not groups:
        return '<div class="card">Дубликатов не найдено.</div>'
    intro = """<div class="card">Найдено групп похожих записей: <b>%d</b>.
      В каждой группе отметьте запись, которую нужно оставить (по умолчанию выбрана самая заполненная), и нажмите кнопку.
      <ul>
        <li><b>Объединить</b> — пустые поля выбранной записи заполнятся данными из остальных, источники сложатся, остальные записи удалятся.</li>
        <li><b>Удалить остальные</b> — останется только выбранная запись, без переноса данных.</li>
        <li><b>Это разные компании</b> — группа больше не будет показываться.</li>
      </ul></div>""" % len(groups)
    blocks = []
    for group in groups:
        ids = ",".join(str(r["id"]) for r in group["records"])
        rows = "".join("""<tr><td><input type="radio" name="keep" value="%d"%s></td>
            <td><a href="/edit?id=%d">%s</a></td><td>%s</td><td>%s</td><td class="nowrap">%s</td>
            <td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>""" % (
                r["id"], " checked" if r["id"] == group["best_id"] else "", r["id"], e(r["name"]),
                e(r["city"]), e(r["category"]), e(r["phone"]), e(r["address"]),
                website_link(r["website"]), e(r["source"]), e(r["status"]))
            for r in group["records"])
        blocks.append("""<form class="group" method="post" action="/duplicates">
          <div class="reasons">Почему похожи: %s</div>
          <input type="hidden" name="ids" value="%s">
          <div class="table-wrap"><table><tr><th>Оставить</th><th>Название</th><th>Город</th><th>Категория</th>
          <th>Телефон</th><th>Адрес</th><th>Сайт</th><th>Источник</th><th>Статус</th></tr>%s</table></div>
          <div class="actions">
            <button class="primary" name="action" value="merge">Объединить</button>
            <button name="action" value="delete" onclick="return confirm('Удалить все записи группы, кроме выбранной?')">Удалить остальные</button>
            <button name="action" value="ignore">Это разные компании</button>
          </div></form>""" % (e(", ".join(group["reasons"])), ids, rows))
    return intro + "".join(blocks)


def export_page(conn):
    total = sum(db.status_counts(conn).values())
    return """<div class="card">
      <p>В базе компаний: <b>%d</b>.</p>
      <div class="actions">
        <a class="btn primary" href="/export.csv?format=excel">Скачать всю базу для Excel</a>
        <a class="btn" href="/export.csv?format=csv">Скачать обычный CSV (UTF-8, запятая)</a>
      </div>
      <p class="muted">Файл «для Excel» открывается двойным щелчком в Excel с русскими настройками.
        Если в Excel все данные оказались в одном столбце — скачайте второй вариант и откройте его
        через меню Excel «Файл → Импорт».
        Чтобы скачать только часть базы, задайте фильтры на странице «Компании»
        и нажмите «Скачать эти записи для Excel».</p>
    </div>
    <div class="card">
      <h2 style="margin-top:0">Резервная копия</h2>
      <p>Вся база хранится в одном файле: <code>%s</code>.
         Чтобы сделать копию, закройте программу и скопируйте этот файл в надёжное место.</p>
    </div>
    <div class="card">
      <h2 style="margin-top:0">Очистить базу</h2>
      <p>Удаляет все компании без возможности восстановления. Перед этим лучше скачать копию.</p>
      <form method="post" action="/delete-all" onsubmit="return confirm('Точно удалить ВСЕ компании из базы?')">
        <label><input type="checkbox" name="confirm" value="yes" required> Я понимаю, что все данные будут удалены</label>
        <div class="actions"><button class="danger" type="submit">Удалить все компании</button></div>
      </form>
    </div>""" % (total, e(db.DEFAULT_DB_PATH))


def help_page():
    return """<div class="card">
    <h2 style="margin-top:0">Как пользоваться</h2>
    <ol>
      <li><b>Загрузить CSV</b> — добавьте компании из таблицы. Первая строка таблицы — заголовки столбцов.</li>
      <li><b>Компании</b> — поиск, фильтры по городу, категории и статусу. Статус можно менять прямо в таблице.
          Щелчок по названию открывает карточку компании для правки или удаления.</li>
      <li>Ссылка «искать сайт» открывает обычный поиск в Яндексе — проверьте результат сами
          и поставьте нужный статус.</li>
      <li><b>Дубликаты</b> — программа находит записи с одинаковым телефоном, сайтом
          или одинаковыми названием, городом и адресом. Решение — объединить, удалить или оставить — принимаете вы.</li>
      <li><b>Экспорт</b> — скачать базу в CSV для Excel.</li>
    </ol>
    <h2>Статусы проверки</h2>
    <ul>
      <li><b>не проверено</b> — сайт ещё никто не проверял. Ставится автоматически для всех новых записей,
          в том числе если в данных нет сайта: отсутствие сайта в таблице не доказывает, что сайта нет.</li>
      <li><b>сайт найден</b> — вы проверили и нашли сайт.</li>
      <li><b>сайт не найден (проверено вручную)</b> — вы проверили и сайта не нашли.</li>
      <li><b>сайт устарел</b> — сайт есть, но старый или неудобный (тоже повод предложить новый).</li>
      <li><b>нужна повторная проверка</b> — есть сомнения, нужно перепроверить позже.</li>
    </ul>
    <h2>Важно</h2>
    <ul>
      <li>Программа не собирает данные с сайтов сама и ничего не придумывает: в базе только то, что вы загрузили или ввели.</li>
      <li>Используйте только данные, которые получены законно и с соблюдением правил источника.</li>
      <li>Чтобы остановить программу, закройте окно «Терминал», в котором она запущена.</li>
    </ul></div>"""


# ---------------------------------------------------------------------------
# Обработка запросов
# ---------------------------------------------------------------------------

def parse_multipart(content_type, body):
    """Разбирает форму с файлом. Возвращает (поля, файлы)."""
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename is not None:
            try:
                filename = filename.encode("utf-8", "surrogateescape").decode("utf-8")
            except UnicodeError:
                filename = "файл.csv"
            files[name] = (os.path.basename(filename), payload)
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "ClientBase/1.0"

    # --- вспомогательные методы ---

    def log_message(self, fmt, *args):
        pass  # не засоряем окно Терминала служебными сообщениями

    def send_html(self, content, status=200):
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, data, filename, content_type="text/csv; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition",
                         "attachment; filename=\"clients.csv\"; filename*=UTF-8''%s" % quote(filename))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location, message="", error=""):
        extra = {}
        if message:
            extra["msg"] = message
        if error:
            extra["err"] = error
        if extra:
            location += ("&" if "?" in location else "?") + urlencode(extra)
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def query(self):
        parsed = urlparse(self.path)
        return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}

    def host_allowed(self):
        """Защита: принимаем запросы только от этой программы на этом компьютере."""
        port = self.server.server_address[1]
        allowed = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if self.headers.get("Host", "") not in allowed:
            return False
        if self.command == "POST":
            origin = self.headers.get("Origin")
            if origin and origin != "null" and urlparse(origin).netloc not in allowed:
                return False
            if origin == "null":
                return False
        return True

    def read_form(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("Файл слишком большой (больше 30 МБ). Разделите его на части.")
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            return parse_multipart(content_type, body)
        fields = {k: v for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}
        return fields, {}

    @staticmethod
    def first(fields, name, default=""):
        value = fields.get(name, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value

    # --- GET ---

    def do_GET(self):
        if not self.host_allowed():
            return self.send_html("Доступ запрещён", 403)
        path, params = self.query()
        msg, err = params.get("msg", ""), params.get("err", "")
        conn = db.connect()
        try:
            if path == "/":
                self.send_html(page("Компании", list_page(conn, params), "/", msg, err))
            elif path == "/import":
                self.send_html(page("Загрузить CSV", import_page(), "/import", msg, err))
            elif path == "/add":
                self.send_html(page("Добавить компанию", company_form({}, "/add", "Добавить"), "/add", msg, err))
            elif path == "/edit":
                company = db.get_company(conn, _int(params.get("id")))
                if not company:
                    return self.redirect("/", error="Компания не найдена.")
                body = company_form(company, "/edit", "Сохранить")
                body += """<form class="card" method="post" action="/delete"
                    onsubmit="return confirm('Удалить эту компанию?')">
                    <input type="hidden" name="id" value="%d">
                    <span class="muted">Добавлена: %s. Изменена: %s.</span>
                    <div class="actions"><button class="danger" type="submit">Удалить компанию</button></div>
                    </form>""" % (company["id"], e(company["created_at"]), e(company["updated_at"]))
                self.send_html(page(company["name"] or "Компания", body, "", msg, err))
            elif path == "/duplicates":
                self.send_html(page("Дубликаты", duplicates_page(conn), "/duplicates", msg, err))
            elif path == "/export":
                self.send_html(page("Экспорт", export_page(conn), "/export", msg, err))
            elif path == "/export.csv":
                rows, _ = db.search_companies(conn, params.get("q", ""), params.get("city", ""),
                                              params.get("category", ""), params.get("status", ""))
                for_excel = params.get("format", "excel") != "csv"
                name = "база_клиентов_%s%s.csv" % (db.now()[:10], "" if for_excel else "_utf8")
                self.send_file(export_csv(rows, for_excel), name)
            elif path == "/template.csv":
                self.send_file(template_csv(), "шаблон_базы_клиентов.csv")
            elif path == "/help":
                self.send_html(page("Справка", help_page(), "/help", msg, err))
            else:
                self.send_html(page("Страница не найдена", '<a href="/">На главную</a>'), 404)
        finally:
            conn.close()

    # --- POST ---

    def do_POST(self):
        if not self.host_allowed():
            return self.send_html("Доступ запрещён", 403)
        path, _ = self.query()
        try:
            fields, files = self.read_form()
        except ValueError as exc:
            return self.redirect("/import", error=str(exc))
        f = lambda name, default="": self.first(fields, name, default)  # noqa: E731
        conn = db.connect()
        try:
            if path == "/import":
                if "file" not in files or not files["file"][1]:
                    return self.redirect("/import", error="Выберите CSV-файл.")
                filename, raw = files["file"]
                source = f("source").strip() or "файл %s" % filename
                report = import_csv(conn, raw, default_source=source,
                                    skip_duplicates=f("skip_duplicates") == "1")
                self.send_html(page("Результат загрузки", import_report(report, filename), "/import"))
            elif path == "/add":
                data = {name: f(name) for name in db.FIELD_NAMES}
                if not db.clean_value(data["name"]):
                    return self.redirect("/add", error="Укажите название компании.")
                existing_id, reason = db.DuplicateIndex(conn).find(db.clean_company(data))
                new_id = db.add_company(conn, data)
                message = "Компания добавлена."
                if existing_id:
                    message += " Внимание: похожая запись уже есть (%s), см. «Дубликаты»." % reason
                self.redirect("/edit?id=%d" % new_id, message=message)
            elif path == "/edit":
                company_id = _int(f("id"))
                data = {name: f(name) for name in db.FIELD_NAMES}
                if not db.clean_value(data["name"]):
                    return self.redirect("/edit?id=%d" % company_id, error="Название не может быть пустым.")
                db.update_company(conn, company_id, data)
                self.redirect("/edit?id=%d" % company_id, message="Изменения сохранены.")
            elif path == "/status":
                db.set_status(conn, _int(f("id")), f("status"))
                back = f("back", "/")
                if not back.startswith("/") or back.startswith("//"):
                    back = "/"
                self.redirect(back)
            elif path == "/delete":
                db.delete_companies(conn, [_int(f("id"))])
                self.redirect("/", message="Компания удалена.")
            elif path == "/delete-all":
                if f("confirm") != "yes":
                    return self.redirect("/export", error="Поставьте галочку подтверждения.")
                db.delete_all(conn)
                self.redirect("/", message="Все компании удалены.")
            elif path == "/duplicates":
                ids = [_int(x) for x in f("ids").split(",") if x.strip()]
                keep = _int(f("keep"))
                action = f("action")
                if action == "ignore":
                    db.mark_not_duplicates(conn, ids)
                    return self.redirect("/duplicates", message="Записи отмечены как разные компании.")
                if keep not in ids:
                    return self.redirect("/duplicates", error="Отметьте запись, которую нужно оставить.")
                if action == "merge":
                    removed = db.merge_companies(conn, keep, ids)
                    self.redirect("/duplicates", message="Записи объединены, удалено лишних: %d." % removed)
                elif action == "delete":
                    removed = db.delete_companies(conn, [i for i in ids if i != keep])
                    self.redirect("/duplicates", message="Удалено записей: %d." % removed)
                else:
                    self.redirect("/duplicates")
            else:
                self.send_html(page("Страница не найдена", '<a href="/">На главную</a>'), 404)
        finally:
            conn.close()


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def find_free_port():
    for port in range(FIRST_PORT, FIRST_PORT + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise SystemExit("Не удалось найти свободный порт. Закройте другие копии программы.")


def main():
    db.connect().close()  # создаём файл базы, если его ещё нет
    port = find_free_port()
    server = ThreadingHTTPServer((HOST, port), Handler)
    url = "http://%s:%d/" % (HOST, port)
    print("=" * 60)
    print("Программа «База клиентов» запущена.")
    print("Откройте в браузере: %s" % url)
    print("База данных: %s" % db.DEFAULT_DB_PATH)
    print("Чтобы остановить программу, закройте это окно")
    print("или нажмите Control + C.")
    print("=" * 60)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nПрограмма остановлена.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
