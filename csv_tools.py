"""Чтение CSV-файлов с компаниями и выгрузка базы в CSV для Excel."""

import csv
import io
import re

from database import (
    ALL_FIELD_NAMES,
    ALL_FIELDS,
    DEFAULT_STATUS,
    FIELDS,
    DuplicateIndex,
    add_company,
    clean_value,
    normalize_status,
)

# Какие названия столбцов в файле понимает программа.
# Регистр букв, «ё» и лишние пробелы не важны.
HEADER_SYNONYMS = {
    "name": ["название компании", "название", "компания", "наименование",
             "наименование компании", "организация", "name", "company",
             "company name", "title"],
    "city": ["город", "населенный пункт", "city", "town"],
    "category": ["категория бизнеса", "категория", "рубрика", "сфера",
                 "вид деятельности", "отрасль", "category", "rubric", "industry"],
    "phone": ["телефон компании", "телефон", "телефоны", "тел", "phone",
              "phones", "telephone", "tel"],
    "address": ["адрес", "address"],
    "website": ["сайт", "веб-сайт", "вебсайт", "website", "site", "url", "web"],
    "source": ["источник данных", "источник", "source"],
    "status": ["статус проверки", "статус", "status"],
    "check_note": ["результат проверки", "комментарий проверки"],
    "checked_at": ["дата проверки"],
}


def _norm_header(text):
    text = (text or "").replace("﻿", "").lower().replace("ё", "е")
    text = re.sub(r"[\s_]+", " ", text).strip(" .:*")
    return text


_SYNONYM_LOOKUP = {}
for _field, _names in HEADER_SYNONYMS.items():
    for _name in _names:
        _SYNONYM_LOOKUP[_norm_header(_name)] = _field


def decode_bytes(raw):
    """Файлы из Excel бывают в UTF-8 или в старой кодировке Windows-1251."""
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8 (с ошибками)"


def detect_delimiter(text):
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        first = text.splitlines()[0] if text else ""
        counts = {d: first.count(d) for d in (";", ",", "\t")}
        return max(counts, key=counts.get) if max(counts.values()) else ","


def _unwrap(value):
    """Убирает защитные символы, которые добавляет наш же экспорт."""
    value = value or ""
    match = re.fullmatch(r'="(.*)"', value.strip(), flags=re.S)
    if match:
        value = match.group(1).replace('""', '"')
    if len(value) > 1 and value[0] == "'" and value[1] in "=+-@\t\r":
        value = value[1:]
    return value


def read_csv(raw):
    """Читает CSV и сопоставляет его столбцы с полями базы."""
    text, encoding = decode_bytes(raw)
    delimiter = detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return {"encoding": encoding, "delimiter": delimiter, "mapping": {},
                "headers": [], "unknown": [], "rows": []}
    headers = rows[0]
    mapping = {}  # номер столбца -> поле базы
    unknown = []
    for index, header in enumerate(headers):
        field = _SYNONYM_LOOKUP.get(_norm_header(header))
        if field and field not in mapping.values():
            mapping[index] = field
        elif header.strip():
            unknown.append(header.strip())
    records = []
    for row in rows[1:]:
        record = {field: "" for field in ALL_FIELD_NAMES}
        for index, field in mapping.items():
            if index < len(row):
                record[field] = _unwrap(row[index])
        records.append(record)
    return {"encoding": encoding, "delimiter": delimiter, "mapping": mapping,
            "headers": headers, "unknown": unknown, "rows": records}


def import_csv(conn, raw, default_source="", skip_duplicates=True):
    """Загружает компании из CSV в базу. Ничего не придумывает:
    пустые поля остаются пустыми, статус по умолчанию — «не проверено»."""
    parsed = read_csv(raw)
    report = {
        "encoding": parsed["encoding"],
        "delimiter": parsed["delimiter"],
        "mapping": {parsed["headers"][i]: f for i, f in parsed["mapping"].items()},
        "unknown": parsed["unknown"],
        "total": len(parsed["rows"]),
        "added": 0,
        "no_name": [],
        "duplicates": [],
        "status_reset": 0,
    }
    if "name" not in parsed["mapping"].values():
        report["error"] = (
            "В файле не найден столбец с названием компании. Первая строка "
            "файла должна содержать заголовки, например: «Название компании»."
        )
        return report

    index = DuplicateIndex(conn) if skip_duplicates else None
    default_source = clean_value(default_source)
    for line_number, record in enumerate(parsed["rows"], start=2):
        record = {k: clean_value(v) for k, v in record.items()}
        if not record["name"]:
            report["no_name"].append(line_number)
            continue
        if not record["source"]:
            record["source"] = default_source
        if record["status"] and normalize_status(record["status"]) == DEFAULT_STATUS \
                and record["status"].lower() != DEFAULT_STATUS:
            report["status_reset"] += 1
        record["status"] = normalize_status(record["status"])
        if index is not None:
            existing_id, reason = index.find(record)
            if existing_id is not None:
                report["duplicates"].append({
                    "line": line_number, "name": record["name"],
                    "existing_id": existing_id, "reason": reason,
                })
                continue
        new_id = add_company(conn, record, commit=False)
        if index is not None:
            index.add(new_id, record)
        report["added"] += 1
    conn.commit()
    return report


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------

_PHONE_CHARS = re.compile(r"^[+\d\s()\-.,;/]+$")


def _safe_cell(field, value, for_excel):
    value = value or ""
    if field == "phone" and value and _PHONE_CHARS.match(value):
        if not for_excel:
            return value
        # Так Excel покажет телефон как текст, а не превратит
        # «79001234567» в «7,9E+10» и не потеряет «+».
        return '="%s"' % value.replace('"', '""')
    if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        # Защита: Excel не должен выполнять текст из ячейки как формулу.
        return "'" + value
    return value


def export_csv(companies, for_excel=True):
    """Возвращает CSV в байтах.

    for_excel=True: разделитель «;» и метка UTF-8 — так файл правильно
    открывается двойным щелчком в Excel с русскими настройками.
    for_excel=False: обычный CSV с запятой, UTF-8.
    """
    out = io.StringIO(newline="")
    writer = csv.writer(out, delimiter=";" if for_excel else ",", lineterminator="\r\n")
    writer.writerow([label for _, label in ALL_FIELDS])
    for company in companies:
        writer.writerow([_safe_cell(f, company.get(f, ""), for_excel) for f in ALL_FIELD_NAMES])
    data = out.getvalue()
    return ("﻿" + data).encode("utf-8") if for_excel else data.encode("utf-8")


def template_csv():
    """Пустой шаблон: только заголовки, без выдуманных компаний."""
    return ("﻿" + ";".join(label for _, label in FIELDS) + "\r\n").encode("utf-8")
