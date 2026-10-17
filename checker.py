"""Автоматическая проверка: есть ли у компании сайт.

Как это работает:

1. Если сайт указан в данных, программа открывает его главную страницу
   (как обычный браузер, один запрос) и смотрит, работает ли сайт.
2. Если сайт не указан (или указана только соцсеть / справочник),
   программа ищет сайт через официальный платный API поиска Яндекса
   (Yandex Search API). Его нужно один раз подключить на странице
   «Настройки». Без подключения такие компании остаются «не проверено».

Программа не обходит капчи, защиту сайтов и ограничения поисковиков:
если сайт или поиск не пускает автоматические запросы, это отмечается
в результате проверки.
"""

import base64
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import database as db

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "ClientBase/1.0 (site availability check)")
SITE_TIMEOUT = 12
MAX_PAGE_BYTES = 400000
SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
SETTINGS_PATH = os.path.join(os.path.dirname(db.DEFAULT_DB_PATH), "settings.json")

# Соцсети и мессенджеры: страница там — это не собственный сайт.
SOCIAL_DOMAINS = {
    "vk.com", "vk.ru", "vkontakte.ru", "instagram.com", "t.me", "telegram.me",
    "ok.ru", "facebook.com", "fb.com", "youtube.com", "wa.me", "whatsapp.com",
    "taplink.cc", "taplink.ws", "x.com", "twitter.com", "tiktok.com", "dzen.ru",
    "zen.yandex.ru", "rutube.ru", "linktr.ee", "max.ru",
}
# Справочники, карты, отзовики, маркетплейсы: это не сайт компании.
DIRECTORY_DOMAINS = {
    "2gis.ru", "2gis.com", "2gis.kz", "yandex.ru", "yandex.com", "ya.ru",
    "google.com", "google.ru", "goo.gl", "zoon.ru", "flamp.ru", "avito.ru",
    "hh.ru", "rusprofile.ru", "list-org.com", "spravker.ru", "orgpage.ru",
    "yell.ru", "otzovik.com", "irecommend.ru", "tripadvisor.ru",
    "tripadvisor.com", "restoclub.ru", "yclients.com", "prodoctorov.ru",
    "napopravku.ru", "sbis.ru", "checko.ru", "zachestnyibiznes.ru",
    "nalog.ru", "nalog.gov.ru", "wikipedia.org", "pulscen.ru", "blizko.ru",
    "cataloxy.ru", "firmsonmap.ru", "spr.ru", "jsprav.ru", "gde.ru",
    "uslugio.com", "profi.ru", "youdo.com", "tiu.ru", "satom.ru", "ozon.ru",
    "wildberries.ru", "mapcarta.com", "foursquare.com", "kartaslov.ru",
    "rubrikator.org", "orgsinfo.ru", "biznesrf.ru", "spravochnik.ru",
    "infobel.com", "yp.ru", "big-book-ru.com", "dikidi.net", "booking.com",
    "ostrovok.ru", "101hotels.com", "sravni.ru", "banki.ru", "vsegda-pomogu.ru",
    "medi.ru", "docdoc.ru", "zoon.com", "otzyvru.com", "spravka-region.ru",
    "tutu.ru", "auto.ru", "drom.ru",
    "mail.ru", "rambler.ru", "egrul.ru", "audit-it.ru", "vbankcenter.ru",
}

# Слова, которые есть в названиях многих компаний и не помогают
# понять, что найденный сайт принадлежит именно этой компании.
GENERIC_WORDS = set("""
салон студия магазин центр компания группа клиника кафе ресторан бар сервис
мастерская офис парикмахерская красоты авто автосервис стоматология
стоматологическая медицинский медицинская торговый торговая дом мир строй
плюс групп официальный сайт ип ооо фирма агентство бюро школа детский детская
сеть магазинов студии центра компании мебель цветы ремонт услуги
group studio shop center centre service company beauty auto cafe bar the
official plus pro
""".split())

PARKING_PHRASES = [
    "домен продается", "домен продаётся", "домен припаркован", "этот домен продается",
    "domain is for sale", "this domain is for sale", "buy this domain",
    "domain may be for sale", "parked domain", "domain parking",
    "домен зарегистрирован в reg.ru", "домен зарегистрирован, но сайт",
    "сайт не опубликован", "срок регистрации домена истек", "срок регистрации домена истёк",
    "account suspended", "аккаунт заблокирован", "услуга хостинга приостановлена",
    "this site is not available", "сайт временно заблокирован",
]


class SearchError(Exception):
    pass


# ---------------------------------------------------------------------------
# Настройки (ключ для поиска Яндекса)
# ---------------------------------------------------------------------------

def load_settings(path=None):
    path = path or SETTINGS_PATH
    settings = {"yandex_api_key": "", "yandex_folder_id": ""}
    try:
        with open(path, encoding="utf-8") as fh:
            settings.update({k: str(v) for k, v in json.load(fh).items() if k in settings})
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings, path=None):
    path = path or SETTINGS_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)  # ключ видит только владелец компьютера
    except OSError:
        pass


def search_configured(settings):
    return bool(settings.get("yandex_api_key") and settings.get("yandex_folder_id"))


# ---------------------------------------------------------------------------
# Адреса сайтов
# ---------------------------------------------------------------------------

def host_of(url):
    key = db.website_key(url)
    if not key:
        return ""
    host = key.split("/")[0].split(":")[0].split("@")[-1]
    return host[4:] if host.startswith("www.") else host


def _in_domains(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def classify_website(website):
    """'none' — не указан, 'social' — соцсеть, 'directory' — справочник,
    'site' — похоже на собственный сайт."""
    host = host_of(website)
    if not host:
        return "none"
    if _in_domains(host, SOCIAL_DOMAINS):
        return "social"
    if _in_domains(host, DIRECTORY_DOMAINS):
        return "directory"
    return "site"


def full_url(website):
    url = (website or "").strip()
    if not re.match(r"^https?://", url, flags=re.I):
        url = "https://" + re.sub(r"^[a-z][a-z0-9+.-]*://", "", url, flags=re.I)
    return url


# ---------------------------------------------------------------------------
# Загрузка страниц
# ---------------------------------------------------------------------------

def _ssl_context(verify=True):
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ctx = ssl.create_default_context()
    # На Mac список сертификатов лежит здесь. Без него Python, скачанный
    # с python.org, не может открыть ни один https-сайт.
    if os.path.exists("/etc/ssl/cert.pem"):
        try:
            ctx.load_verify_locations("/etc/ssl/cert.pem")
        except (OSError, ssl.SSLError):
            pass
    return ctx


def _decode(body, charset):
    for enc in (charset, "utf-8", "cp1251"):
        if not enc:
            continue
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", "replace")


def _reason_text(exc):
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, socket.gaierror):
        return "домен не найден (адрес не существует или не продлён)"
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason):
        return "сайт не ответил за %d секунд" % SITE_TIMEOUT
    if isinstance(reason, ConnectionRefusedError):
        return "сервер отказал в соединении"
    if isinstance(reason, ssl.SSLError):
        return "ошибка защищённого соединения"
    return "не удалось подключиться (%s)" % str(reason)[:120]


def fetch_page(url, verify=True):
    """Открывает страницу. Возвращает словарь:
    code (код ответа или None), url (итоговый адрес), text, error."""
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ru,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(request, timeout=SITE_TIMEOUT,
                                    context=_ssl_context(verify)) as resp:
            body = resp.read(MAX_PAGE_BYTES)
            return {"code": resp.status, "url": resp.geturl(),
                    "text": _decode(body, resp.headers.get_content_charset()), "error": ""}
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_PAGE_BYTES)
        except Exception:  # noqa: BLE001
            body = b""
        return {"code": exc.code, "url": exc.geturl() or url,
                "text": _decode(body, None), "error": ""}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"code": None, "url": url, "text": "", "error": _reason_text(exc),
                "cert_error": isinstance(reason, ssl.SSLCertVerificationError)}


def check_site(website, fetch=fetch_page):
    """Проверяет, работает ли сайт. Возвращает (работает: bool, пояснение, итоговый адрес, текст)."""
    url = full_url(website)
    result = fetch(url)
    notes = []
    if result["code"] is None and result.get("cert_error"):
        # Сертификат просрочен или неверный: сайт может работать, просто
        # браузер покажет предупреждение. Смотрим страницу без проверки
        # сертификата — данные мы никуда не отправляем.
        result = fetch(url, verify=False)
        if result["code"] is not None:
            notes.append("сертификат безопасности недействителен — браузер показывает предупреждение")
    if result["code"] is None and url.startswith("https://"):
        http_result = fetch("http://" + url[len("https://"):])
        if http_result["code"] is not None:
            result = http_result
            notes.append("работает только без https")
    code = result["code"]
    if code is None:
        return False, result["error"], url, ""
    text_lower = result["text"].lower()
    if 200 <= code < 400:
        for phrase in PARKING_PHRASES:
            if phrase in text_lower:
                return False, "на адресе нет сайта компании: страница-заглушка («%s»)" % phrase, result["url"], ""
        if len(re.sub(r"<[^>]+>|\s", "", result["text"])) < 50:
            notes.append("главная страница почти пустая")
        return True, "; ".join(["сайт открывается"] + notes), result["url"], result["text"]
    if code in (401, 403, 429):
        return True, "сайт отвечает, но не пускает автоматическую проверку (код %d)" % code, result["url"], ""
    if code in (404, 410):
        return False, "главная страница не найдена (код %d)" % code, result["url"], ""
    return False, "сайт выдаёт ошибку (код %d)" % code, result["url"], ""


# ---------------------------------------------------------------------------
# Поиск через Yandex Search API
# ---------------------------------------------------------------------------

def yandex_search(query, settings, timeout=30):
    """Возвращает список результатов [{'url', 'title', 'text'}] (до 10 штук)."""
    body = json.dumps({
        "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query},
        "folderId": settings["yandex_folder_id"].strip(),
        "responseFormat": "FORMAT_XML",
    }).encode("utf-8")
    request = urllib.request.Request(SEARCH_URL, data=body, method="POST", headers={
        "Authorization": "Api-Key " + settings["yandex_api_key"].strip(),
        "Content-Type": "application/json",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            if exc.code in (401, 403):
                raise SearchError("Яндекс не принял ключ: проверьте API-ключ, идентификатор каталога "
                                  "и роль search-api.webSearch.user. %s" % detail)
            raise SearchError("Поиск Яндекса вернул ошибку %d. %s" % (exc.code, detail))
        except ssl.SSLCertVerificationError:
            raise SearchError("Не удалось проверить сертификат Яндекса. Если Python установлен "
                              "с python.org, запустите файл «Install Certificates.command» "
                              "в папке «Программы → Python 3».")
        except (urllib.error.URLError, OSError) as exc:
            raise SearchError("Нет связи с поиском Яндекса: %s" % _reason_text(exc))
    return parse_search_xml(base64.b64decode(payload.get("rawData", "")))


def _element_text(element):
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def parse_search_xml(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise SearchError("Не удалось прочитать ответ поиска Яндекса.")
    error = root.find(".//error")
    if error is not None:
        if error.get("code") == "15":  # «по вашему запросу ничего не нашлось»
            return []
        raise SearchError("Поиск Яндекса: %s" % _element_text(error))
    results = []
    for doc in root.iter("doc"):
        url = (doc.findtext("url") or "").strip()
        if url:
            results.append({
                "url": url,
                "title": _element_text(doc.find("title")),
                "text": _element_text(doc.find("passages")) or _element_text(doc.find("headline")),
            })
    return results


# ---------------------------------------------------------------------------
# Сопоставление найденного сайта с компанией
# ---------------------------------------------------------------------------

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}
_SKELETON_RULES = [
    ("shch", "s"), ("sch", "s"), ("sh", "s"), ("ch", "c"), ("zh", "z"), ("kh", "h"),
    ("ts", "c"), ("tz", "c"), ("yu", "u"), ("ju", "u"), ("iu", "u"), ("ya", "a"),
    ("ja", "a"), ("ia", "a"), ("yo", "o"), ("x", "ks"), ("w", "v"), ("j", "i"),
    ("y", "i"), ("q", "k"),
]


def skeleton(text):
    """Упрощённое латинское написание: «Ромашка», «romashka» и «romaschka»
    превращаются в одно и то же, чтобы сравнивать название с адресом сайта."""
    text = "".join(_TRANSLIT.get(ch, ch) for ch in (text or "").lower())
    text = re.sub(r"[^a-z0-9]", "", text)
    for old, new in _SKELETON_RULES:
        text = text.replace(old, new)
    return re.sub(r"(.)\1+", r"\1", text)


def name_words(company):
    city_words = set(re.findall(r"\w+", (company.get("city") or "").lower().replace("ё", "е")))
    words = []
    for word in re.findall(r"\w+", (company.get("name") or "").lower().replace("ё", "е")):
        if word in GENERIC_WORDS or word in city_words or word.isdigit():
            continue
        if len(skeleton(word)) >= 4:
            words.append(word)
    return words


def domain_matches_name(host, company):
    label = skeleton(host.rsplit(".", 1)[0])
    words = name_words(company)
    if not words:
        return False
    joined = skeleton("".join(words))
    if len(joined) >= 4 and joined in label:
        return True
    return any(skeleton(w) in label for w in words)


def _norm_text(text):
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower().replace("ё", "е")).split())


def name_in_text(company, text):
    name = _norm_text(re.sub(r"\b(ооо|ип|зао|оао|пао|ао)\b", " ", (company.get("name") or "").lower()))
    return len(name) >= 4 and (" " + name + " ") in (" " + _norm_text(text) + " ")


def phone_on_page(company, text):
    keys = db.phone_keys(company.get("phone"))
    if not keys or not text:
        return False
    numbers = [re.sub(r"\D", "", m) for m in re.findall(r"\+?\d[\d\s\-() ]{5,}\d", text)]
    for key in keys:
        tail = key[-10:]
        for number in numbers:
            if number.endswith(tail) or (len(number) >= 7 and tail.endswith(number[-7:])):
                return True
    return False


def search_query(company):
    parts = [company.get("name", ""), company.get("city", "")]
    return " ".join(p for p in parts if p).strip()


def find_site_by_search(company, settings, search=yandex_search, fetch=fetch_page):
    """Ищет сайт компании в поиске. Возвращает (статус, пояснение, адрес сайта или '')."""
    query = search_query(company)
    results = search(query, settings)
    social = None
    weak = None
    checked = 0
    for item in results:
        host = host_of(item["url"])
        if not host or _in_domains(host, DIRECTORY_DOMAINS):
            continue
        if _in_domains(host, SOCIAL_DOMAINS):
            if social is None and name_in_text(company, item["title"] + " " + item["text"]):
                social = item["url"]
            continue
        if checked >= 4:
            continue
        domain_match = domain_matches_name(host, company)
        title_match = name_in_text(company, item["title"])
        if not (domain_match or title_match or db.phone_keys(company.get("phone"))):
            continue
        checked += 1
        works, _, final_url, text = check_site(item["url"], fetch=fetch)
        if not works:
            continue
        parts = urllib.parse.urlsplit(final_url)
        home = "%s://%s" % (parts.scheme, parts.netloc)
        if phone_on_page(company, text):
            return ("сайт есть", "найден через поиск Яндекса: %s (на сайте указан тот же телефон)" % home, home)
        if domain_match and (title_match or name_in_text(company, text)):
            return ("сайт есть", "найден через поиск Яндекса: %s (название совпадает с адресом сайта)" % home, home)
        if weak is None and (domain_match or title_match):
            weak = home
    if weak:
        return ("нужна повторная проверка",
                "возможно, это сайт компании: %s — совпало только название, проверьте вручную" % weak, "")
    if social:
        return ("только соцсети",
                "собственный сайт в поиске Яндекса не найден, есть страница: %s" % social, "")
    return ("сайт не найден",
            "в первых результатах поиска Яндекса по запросу «%s» сайта компании нет" % query, "")


# ---------------------------------------------------------------------------
# Общая проверка одной компании
# ---------------------------------------------------------------------------

def check_company(company, settings, search=yandex_search, fetch=fetch_page):
    """Возвращает {'status', 'note', 'website'} ('website' — новый адрес или None)."""
    website = company.get("website") or ""
    kind = classify_website(website)
    if kind == "site":
        works, note, _, _ = check_site(website, fetch=fetch)
        if works:
            return {"status": "сайт есть", "note": note, "website": None}
        return {"status": "сайт не открывается", "note": note, "website": None}

    prefix = {
        "social": "в данных указана только соцсеть",
        "directory": "в данных указана ссылка на справочник, а не на сайт компании",
        "none": "сайт в данных не указан",
    }[kind]
    if not search_configured(settings):
        return {"status": db.DEFAULT_STATUS,
                "note": prefix + "; чтобы искать сайт автоматически, подключите поиск Яндекса в «Настройках»",
                "website": None}
    if not company.get("name"):
        return {"status": db.DEFAULT_STATUS, "note": prefix + "; нет названия для поиска", "website": None}
    status, note, found = find_site_by_search(company, settings, search=search, fetch=fetch)
    if status == "сайт не найден" and kind == "social":
        status, note = "только соцсети", note + "; в данных есть страница в соцсети"
    return {"status": status, "note": note, "website": found or None}
