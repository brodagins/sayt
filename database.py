"""Хранение компаний в базе данных SQLite.

Вся база — это один файл data/clients.db. Его можно копировать
для резервной копии.
"""

import datetime
import os
import re
import sqlite3

# Поля компании: внутреннее имя -> подпись на русском.
FIELDS = [
    ("name", "Название компании"),
    ("city", "Город"),
    ("category", "Категория бизнеса"),
    ("phone", "Телефон компании"),
    ("address", "Адрес"),
    ("website", "Сайт"),
    ("source", "Источник данных"),
    ("status", "Статус проверки"),
]
FIELD_NAMES = [name for name, _ in FIELDS]
LABELS = dict(FIELDS)

# Статус по умолчанию: сайт ещё не проверяли.
DEFAULT_STATUS = "не проверено"
STATUSES = [
    DEFAULT_STATUS,
    "сайт есть",
    "сайт не найден",
    "только соцсети",
    "сайт не открывается",
    "сайт устарел",
    "нужна повторная проверка",
]
# Старые и другие написания статусов (например, из загружаемых таблиц).
STATUS_ALIASES = {
    "сайт найден": "сайт есть",
    "есть сайт": "сайт есть",
    "есть": "сайт есть",
    "сайт не найден (проверено вручную)": "сайт не найден",
    "нет сайта": "сайт не найден",
    "сайта нет": "сайт не найден",
    "нет": "сайт не найден",
}
# Эти статусы автоматическая проверка может заменить.
# «сайт устарел» — оценка человека, её проверка не трогает, если сайт работает.
AUTO_STATUSES = {DEFAULT_STATUS, "сайт есть", "сайт не найден", "только соцсети",
                 "сайт не открывается", "нужна повторная проверка"}

# Поля с результатом автоматической проверки (заполняет программа).
CHECK_FIELDS = [
    ("check_note", "Результат проверки"),
    ("checked_at", "Дата проверки"),
]
ALL_FIELDS = FIELDS + CHECK_FIELDS
ALL_FIELD_NAMES = [name for name, _ in ALL_FIELDS]
LABELS.update(dict(CHECK_FIELDS))

MAX_VALUE_LENGTH = 1000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "clients.db")


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _lower(value):
    return value.lower().replace("ё", "е") if isinstance(value, str) else value


def connect(path=None):
    path = path or DEFAULT_DB_PATH
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Встроенная в SQLite функция lower() не понимает русские буквы,
    # поэтому регистрируем свою.
    conn.create_function("pylower", 1, _lower)
    init_db(conn)
    return conn


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'не проверено',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        -- Пары записей, которые пользователь отметил как «разные компании»,
        -- чтобы не предлагать их снова как дубликаты.
        CREATE TABLE IF NOT EXISTS not_duplicates (
            id_a INTEGER NOT NULL,
            id_b INTEGER NOT NULL,
            PRIMARY KEY (id_a, id_b)
        );
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(companies)")}
    for name, _ in CHECK_FIELDS:
        if name not in columns:
            conn.execute("ALTER TABLE companies ADD COLUMN %s TEXT NOT NULL DEFAULT ''" % name)
    for old, new in (("сайт найден", "сайт есть"),
                     ("сайт не найден (проверено вручную)", "сайт не найден")):
        conn.execute("UPDATE companies SET status = ? WHERE status = ?", (new, old))
    conn.commit()


# ---------------------------------------------------------------------------
# Очистка значений
# ---------------------------------------------------------------------------

def clean_value(value):
    if value is None:
        return ""
    value = " ".join(str(value).split())
    return value[:MAX_VALUE_LENGTH]


def normalize_status(value):
    """Возвращает статус из списка STATUSES или статус по умолчанию."""
    wanted = _lower(clean_value(value))
    for status in STATUSES:
        if _lower(status) == wanted:
            return status
    return STATUS_ALIASES.get(wanted, DEFAULT_STATUS)


def clean_company(data):
    company = {field: clean_value(data.get(field, "")) for field in ALL_FIELD_NAMES}
    company["status"] = normalize_status(company["status"])
    return company


# ---------------------------------------------------------------------------
# Добавление, изменение, удаление
# ---------------------------------------------------------------------------

def add_company(conn, data, commit=True):
    company = clean_company(data)
    stamp = now()
    cur = conn.execute(
        "INSERT INTO companies (%s, created_at, updated_at) VALUES (%s, ?, ?)"
        % (", ".join(ALL_FIELD_NAMES), ", ".join("?" for _ in ALL_FIELD_NAMES)),
        [company[f] for f in ALL_FIELD_NAMES] + [stamp, stamp],
    )
    if commit:
        conn.commit()
    return cur.lastrowid


def update_company(conn, company_id, data, fields=None):
    """Сохраняет поля компании. Если статус поменял человек,
    в «Результат проверки» записывается «указано вручную»."""
    fields = fields or FIELD_NAMES
    company = clean_company(data)
    old = get_company(conn, company_id)
    if old and "status" in fields and "check_note" not in fields and company["status"] != old["status"]:
        fields = list(fields) + ["check_note", "checked_at"]
        company["check_note"], company["checked_at"] = "статус указан вручную", now()
    conn.execute(
        "UPDATE companies SET %s, updated_at = ? WHERE id = ?"
        % ", ".join("%s = ?" % f for f in fields),
        [company[f] for f in fields] + [now(), company_id],
    )
    conn.commit()


def set_status(conn, company_id, status):
    conn.execute(
        "UPDATE companies SET status = ?, check_note = ?, checked_at = ?, updated_at = ? WHERE id = ?",
        (normalize_status(status), "статус указан вручную", now(), now(), company_id),
    )
    conn.commit()


def save_check_result(conn, company_id, status, note, website=None, commit=True):
    """Записывает результат автоматической проверки."""
    stamp = now()
    if website:
        cur = conn.execute("UPDATE companies SET website = ? WHERE id = ? AND website = ''",
                           (clean_value(website), company_id))
    if website and cur.rowcount:
        conn.execute(
            "UPDATE companies SET source = CASE WHEN source = '' THEN ? ELSE source || '; ' || ? END "
            "WHERE id = ?", ("сайт: поиск Яндекса", "сайт: поиск Яндекса", company_id))
    conn.execute(
        "UPDATE companies SET status = ?, check_note = ?, checked_at = ?, updated_at = ? WHERE id = ?",
        (normalize_status(status), clean_value(note), stamp, stamp, company_id),
    )
    if commit:
        conn.commit()


def delete_companies(conn, ids):
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    marks = ", ".join("?" for _ in ids)
    cur = conn.execute("DELETE FROM companies WHERE id IN (%s)" % marks, ids)
    conn.execute(
        "DELETE FROM not_duplicates WHERE id_a IN (%s) OR id_b IN (%s)" % (marks, marks),
        ids + ids,
    )
    conn.commit()
    return cur.rowcount


def delete_all(conn):
    conn.execute("DELETE FROM companies")
    conn.execute("DELETE FROM not_duplicates")
    conn.commit()


def get_company(conn, company_id):
    row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Поиск и фильтры
# ---------------------------------------------------------------------------

def _like_pattern(text):
    text = _lower(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + text + "%"


def _filters_sql(q="", city="", category="", status=""):
    where, params = [], []
    q = clean_value(q)
    if q:
        parts = []
        for field in ("name", "city", "category", "phone", "address", "website", "source"):
            parts.append("pylower(%s) LIKE ? ESCAPE '\\'" % field)
            params.append(_like_pattern(q))
        where.append("(" + " OR ".join(parts) + ")")
    for field, value in (("city", city), ("category", category), ("status", status)):
        value = clean_value(value)
        if value:
            where.append("%s = ?" % field)
            params.append(value)
    sql = (" WHERE " + " AND ".join(where)) if where else ""
    return sql, params


def search_companies(conn, q="", city="", category="", status="", limit=None, offset=0):
    where, params = _filters_sql(q, city, category, status)
    total = conn.execute("SELECT COUNT(*) FROM companies" + where, params).fetchone()[0]
    sql = "SELECT * FROM companies" + where + " ORDER BY pylower(name), id"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = params + [int(limit), int(offset)]
    rows = [dict(r) for r in conn.execute(sql, params)]
    return rows, total


def distinct_values(conn, field):
    if field not in FIELD_NAMES:
        raise ValueError(field)
    rows = conn.execute(
        "SELECT DISTINCT %s FROM companies WHERE %s != '' ORDER BY pylower(%s)"
        % (field, field, field)
    )
    return [r[0] for r in rows]


def status_counts(conn):
    counts = {status: 0 for status in STATUSES}
    for row in conn.execute("SELECT status, COUNT(*) FROM companies GROUP BY status"):
        counts[row[0]] = row[1]
    return counts


# ---------------------------------------------------------------------------
# Дубликаты
# ---------------------------------------------------------------------------

_LEGAL_FORMS = re.compile(r"\b(ооо|оао|зао|пао|ао|ип|нко|ано|llc|ltd|inc)\b")
_ADDRESS_WORDS = re.compile(
    r"\b(г|город|ул|улица|д|дом|пр|пр-т|проспект|пер|переулок|корп|корпус|стр|строение|"
    r"оф|офис|кв|пом|помещение|россия|рф)\b"
)


def _text_key(value, drop=None):
    value = _lower(value or "")
    value = re.sub(r"[«»\"'“”„`]", " ", value)
    if drop is not None:
        value = drop.sub(" ", value)
    value = re.sub(r"[^\w]+", " ", value)
    if drop is not None:
        value = drop.sub(" ", value)
    return " ".join(value.split())


def phone_keys(phone):
    """Телефоны в виде одних цифр: «8 (900) 123-45-67» -> «79001234567».

    В одном поле может быть несколько телефонов через запятую.
    """
    keys = []
    for part in re.split(r"[,;/]|\sи\s", phone or ""):
        digits = re.sub(r"\D", "", part)
        if len(digits) == 11 and digits[0] == "8":
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits
        if len(digits) >= 6 and digits not in keys:
            keys.append(digits)
    return keys


def website_key(website):
    """Адрес сайта без «http://», «www.» и косой черты в конце."""
    value = _lower((website or "").strip())
    value = re.sub(r"^[a-z][a-z0-9+.-]*://", "", value)
    value = value.split("?")[0].split("#")[0]
    if value.startswith("www."):
        value = value[4:]
    value = value.rstrip("/")
    return value if "." in value and " " not in value else ""


def name_key(company):
    """Название + город + адрес, без «ООО», кавычек и знаков препинания."""
    name = _text_key(company.get("name"), _LEGAL_FORMS)
    if not name:
        return ""
    city = _text_key(company.get("city"), _ADDRESS_WORDS)
    address = _text_key(company.get("address"), _ADDRESS_WORDS)
    return "|".join((name, city, address))


def company_keys(company):
    """Признаки, по которым две записи считаются одной компанией."""
    keys = [("phone", k) for k in phone_keys(company.get("phone"))]
    site = website_key(company.get("website"))
    if site:
        keys.append(("website", site))
    name = name_key(company)
    if name:
        keys.append(("name", name))
    return keys


REASON_LABELS = {
    "phone": "одинаковый телефон",
    "website": "одинаковый сайт",
    "name": "одинаковые название, город и адрес",
}


def filled_fields(company):
    return sum(1 for f in FIELD_NAMES if company.get(f) and f != "status")


def find_duplicate_groups(conn):
    """Группы записей, похожих на одну и ту же компанию."""
    companies = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM companies")}
    ignored = set()
    for row in conn.execute("SELECT id_a, id_b FROM not_duplicates"):
        ignored.add((row[0], row[1]))

    by_key = {}
    for cid, company in companies.items():
        for key in company_keys(company):
            by_key.setdefault(key, []).append(cid)

    parent = {cid: cid for cid in companies}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pair_reasons = []
    for (kind, _), ids in by_key.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = min(ids[i], ids[j]), max(ids[i], ids[j])
                if (a, b) in ignored:
                    continue
                parent[find(a)] = find(b)
                pair_reasons.append((a, kind))

    groups = {}
    for cid in companies:
        groups.setdefault(find(cid), []).append(cid)
    reasons = {}
    for cid, kind in pair_reasons:
        reasons.setdefault(find(cid), set()).add(kind)

    result = []
    for root, ids in groups.items():
        if len(ids) < 2:
            continue
        records = sorted((companies[i] for i in ids), key=lambda c: c["id"])
        best = max(records, key=lambda c: (filled_fields(c), -c["id"]))
        result.append({
            "records": records,
            "best_id": best["id"],
            "reasons": [REASON_LABELS[k] for k in ("phone", "website", "name")
                        if k in reasons.get(root, set())],
        })
    result.sort(key=lambda g: g["records"][0]["id"])
    return result


def mark_not_duplicates(conn, ids):
    ids = sorted(int(i) for i in ids)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            conn.execute(
                "INSERT OR IGNORE INTO not_duplicates (id_a, id_b) VALUES (?, ?)",
                (ids[i], ids[j]),
            )
    conn.commit()


def merge_companies(conn, keep_id, ids):
    """Оставляет одну запись, дополняет её пустые поля данными остальных,
    а остальные удаляет. Источники данных объединяются через «; »."""
    keep_id = int(keep_id)
    others = [int(i) for i in ids if int(i) != keep_id]
    keep = get_company(conn, keep_id)
    if keep is None:
        return 0
    other_rows = [c for c in (get_company(conn, i) for i in others) if c]
    sources = [s.strip() for s in keep["source"].split(";") if s.strip()]
    for other in other_rows:
        for field in FIELD_NAMES:
            if field in ("source", "status"):
                continue
            if not keep[field] and other[field]:
                keep[field] = other[field]
        for s in other["source"].split(";"):
            if s.strip() and s.strip() not in sources:
                sources.append(s.strip())
        if keep["status"] == DEFAULT_STATUS and other["status"] != DEFAULT_STATUS:
            keep["status"] = other["status"]
            keep["check_note"], keep["checked_at"] = other["check_note"], other["checked_at"]
    keep["source"] = "; ".join(sources)
    update_company(conn, keep_id, keep, fields=ALL_FIELD_NAMES)
    return delete_companies(conn, [c["id"] for c in other_rows])


class DuplicateIndex:
    """Быстрая проверка при загрузке: есть ли такая компания в базе."""

    def __init__(self, conn):
        self.keys = {}
        for row in conn.execute("SELECT * FROM companies"):
            self.add(row["id"], dict(row))

    def add(self, company_id, company):
        for key in company_keys(company):
            self.keys.setdefault(key, company_id)

    def find(self, company):
        for key in company_keys(company):
            if key in self.keys:
                return self.keys[key], REASON_LABELS[key[0]]
        return None, None
