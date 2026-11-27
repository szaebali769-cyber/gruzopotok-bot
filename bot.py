# -*- coding: utf-8 -*-
"""
Грузовой поток — бот ВК: путёвки, парк, закрепление машин, ивенты, штрафы.

Один файл, только стандартная библиотека Python 3.8+. Зависимостей нет.
Работает через Bots Long Poll API сообщества (groups.getLongPollServer).

НАСТРОЙКА (заполните три строки ниже или задайте переменные окружения):
    BOT_TOKEN    — токен сообщества, права: «Сообщения» + «Управление»
    GROUP_ID     — ID сообщества (число из vk.com/club<ID>), минус можно не писать
    ADMINS       — VK ID админов через запятую
    SUPERADMINS  — VK ID суперадминов через запятую

Запуск: python bot.py
Все команды начинаются с «!»: !помощь, !анкета Виктор 123456
"""

import calendar
import json
import logging
import os
import random
import re
import signal
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------- настройки

BOT_TOKEN = ""          # сюда токен сообщества
GROUP_ID = 0            # сюда ID сообщества, например 123456789
ADMINS = []             # [123456789, 987654321]
SUPERADMINS = []        # [123456789]

# Переменные окружения имеют приоритет над значениями выше.
BOT_TOKEN = (os.environ.get("BOT_TOKEN") or os.environ.get("TOKEN") or BOT_TOKEN).strip()
GROUP_ID = abs(int(os.environ.get("GROUP_ID") or GROUP_ID or 0))
ADMINS = [int(x) for x in (os.environ.get("ADMINS") or "").replace(" ", "").split(",")
          if x.lstrip("-").isdigit()] or [abs(int(x)) for x in ADMINS]
SUPERADMINS = ([int(x) for x in (os.environ.get("SUPERADMINS") or "").replace(" ", "").split(",")
                if x.lstrip("-").isdigit()] or [abs(int(x)) for x in SUPERADMINS])

logging.basicConfig(
    level=getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S",
)
log = logging.getLogger("cargo-flow")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "db.json")
os.makedirs(DATA_DIR, exist_ok=True)

API_URL = "https://api.vk.com/method/"
API_V = "5.199"
HTTP_TIMEOUT = 20
LP_WAIT = 25                      # ожидание Long Poll-ответа, сек (сервер отдаёт до 90)
MSK_OFFSET = 3 * 3600
USER_AGENT = "cargo-flow-bot/1.0 (+python-urllib)"

PREFIX = "!"                      # префикс команд — обязателен для всех
PENALTY_ODOMETER = 10             # штраф за минус пробега
PENALTY_BURN = 10                 # штраф за сгоревшую путёвку (>24 ч открытой)
EVENT_HISTORY_HOURS = 24          # сколько часов хранить итоги ивента
DEFAULT_PAUSE_HOURS = 3           # пауза выдачи путёвок по умолчанию
TRIP_LIFETIME = 24 * 3600         # путёвка сгорает, если открыта дольше суток
TO_LIMIT_KM = 500                 # порог пробега с последнего ТО
BURN_CHECK_EVERY = 60             # период фоновой проверки, сек

MAX_LOG = 3000                    # записей рейсов у водителя (для статистики)
MAX_HISTORY = 200                 # рейсов в личной истории водителя
MAX_JOURNAL = 1000                # записей в журнале машины
MAX_MSG_LEN = 3800                # длиннее — режем на несколько сообщений (лимит VK 4096)
SEND_PAUSE = 0.05                 # пауза между отправками при рассылке, сек

RETRY_CODES = {1, 6, 9, 10}       # unknown / too many requests / flood / internal
RETRY_DELAY = {1: 1, 6: 1, 9: 5, 10: 2}

_lock = threading.RLock()
LINE = "━━━━━━━━━━━━━━"

CARS_INIT = [
    ("КАМАЗ 54115", "Т740ТТ77"), ("КАМАЗ 54115", "О740ОО77"),
    ("КАМАЗ 54115", "Х740ХХ77"), ("КАМАЗ 54115", "У740УУ77"),
    ("КАМАЗ 54115", "В740ВВ77"), ("КАМАЗ 54115", "С740СС77"),
    ("КАМАЗ 54115", "М740ММ77"), ("КАМАЗ 5490 НЕО", "Е740ЕЕ77"),
    ("КАМАЗ 5490 НЕО", "К740КК77"), ("КАМАЗ 5490 НЕО", "Н740НН77"),
    ("КАМАЗ 5490 НЕО", "Р740РР77"), ("КАМАЗ 5490 НЕО", "А740АА77"),
    ("ЗИЛ 133540", "Х761ХХ77"), ("КАМАЗ 53112 Тент", "О001СТ77"),
    ("КАМАЗ 53112 Тент", "О002СТ77"), ("КАМАЗ 53112 Тент", "О003СТ77"),
    ("Scania R500", "А501АА77"), ("Scania R500", "О501ОО77"),
    ("Scania Topline R480", "А502АА77"), ("Volvo FH", "А601АА77"),
    ("Volvo FH", "О601ОО77"), ("Volvo VNL", "А701АА77"),
    ("Volvo VNL", "О701ОО77"), ("Mercedes-Benz Actros", "А410АА77"),
    ("Renault Magnum", "О077ТК77"), ("УАЗ 3909 Оборона", "В761ВВ77"),
    ("ГАЗ-3302 Тент", "О275ОО77"), ("ГАЗель NEXT", "А275АА77"),
    ("ГАЗ-3309 Рест", "В275ВВ77"),
    ("Mercedes-Benz Sprinter Classic 311 CDI", "С761СС77"),
]

LAT2CYR = str.maketrans("ABEKMHOPCTXY", "АВЕКМНОРСТХУ")

SITE = "https://cargo-flow-mta-province-2-server.ru"

# ---------------------------------------------------------------- тексты

BINDS = (
    "🎮 БИНДЫ ЧАТА ОРГАНИЗАЦИИ\n" + LINE + "\n"
    "Открыть консоль в игре: клавиша F8\n\n"
    "📡 РП-чат организации:\n"
    "bind [ваша_кнопка] chatbox o\n\n"
    "💬 Нон-РП чат организации:\n"
    "bind [ваша_кнопка] chatbox ob\n\n"
    "🚛 Реклама бригады (помогает нам расти):\n"
    'bind [ваша_кнопка] do "Табличка" Грузовой поток, система работы '
    "дальнобойщика: путёвки, пробег, собственный автопарк, рейтинги "
    "и ивенты, ждём вас в кабинах!\n\n"
    "🌐 Ссылка на информацию:\n"
    "bind [ваша_кнопка] b Вся необходимая информация доступна на нашем сайте "
    + SITE + "\n\n"
    "📌 Пример: bind F6 chatbox o"
)

REG_HELP = (
    "📋 КАК ЗАРЕГИСТРИРОВАТЬСЯ\n" + LINE + "\n"
    "Напишите боту одну строку:\n"
    "❗ !анкета ВашНик 123456\n\n"
    "🔹 ВашНик — игровой ник в MTA Province,\n"
    "   ровно так, как он записан в игре.\n"
    "🔹 123456 — номер игрового счёта: ровно 6 цифр,\n"
    "   туда приходят премии. Проверьте перед отправкой!\n\n"
    "Пример: !анкета Виктор 776543\n\n"
    "После регистрации вам будет присвоен личный ID,\n"
    "откроются путёвки и гараж. Список команд: !помощь"
)

HELP = (
    "🚚 ГРУЗОВОЙ ПОТОК — КОМАНДЫ\n" + LINE + "\n"
    "❗ Все команды начинаются с символа «!»\n\n"
    "📋 Регистрация:\n"
    "• !анкета Ник 123456 — игровой ник + 6-значный счёт\n"
    "  (подробная инструкция: !анкета без слов)\n"
    "• !мой id — ваш VK ID и ID водителя в боте\n\n"
    "🛣 Путёвки:\n"
    "• !выписать путевку Т740ТТ77 100 — выезд\n"
    "• !сдать путевку Т740ТТ77 250 — возврат\n"
    "⚠️ Начальный пробег — не меньше одометра авто\n"
    "⚠️ Минус пробега — штраф −" + str(PENALTY_ODOMETER) + " к рейтингу\n"
    "⏰ Открытая больше суток путёвка сгорает (штраф −" + str(PENALTY_BURN) + ")\n\n"
    "📊 Статистика:\n"
    "• !профиль — ваши данные и пробег за сутки\n"
    "• !путевки — история ваших рейсов\n"
    "• !гараж — весь парк: номер, марка, ID, статус\n"
    "• !история Т740ТТ77 — журнал рейсов машины\n"
    "• !за сегодня — пробег бригады за 24 часа\n"
    "• !рейтинг — топ водителей\n"
    "• !водители — кто в бригаде\n"
    "• !ивент — активный ивент и итоги\n"
    "• !бинды — бинды чата организации (F8)\n\n"
    "🛠 Админ-команды (вместо номера можно ID авто,\n"
    "вместо ника — ID водителя):\n"
    "• !авто МАРКА НОМЕР пробег — добавить авто\n"
    "• !пробег НОМЕР|ID значение — правка одометра\n"
    "• !удалить авто НОМЕР|ID — убрать авто из парка\n"
    "• !профиль Ник — профиль водителя\n"
    "• !ивент Название 26.11-05.12 фонд — создать ивент\n"
    "• !стоп путевки [часы] — пауза выдачи (по умолчанию 3 ч)\n"
    "• !пуск путевки — возобновить выдачу\n"
    "• !то НОМЕР|ID — сброс счётчика ТО после обслуживания\n"
    "• !штраф Ник [баллы] — штраф (по умолчанию 10)\n"
    "• !сброс рейтинга Ник — вернуть рейтинг к 100\n"
    "• !удалить Ник — удалить регистрацию водителя\n"
    "• !объявление текст — рассылка всем в личку\n\n"
    "👑 Только суперадмин:\n"
    "• !закрепить НОМЕР|ID Ник — закрепить авто за водителем\n"
    "• !открепить НОМЕР|ID — снять закрепление\n"
    "• !сброс базы ПОДТВЕРЖДАЮ — полная зачистка базы\n\n"
    "🔒 На закреплённое авто путёвку выписывает только его водитель"
)

WELCOME = (
    "👋 Всем привет! Я бот учёта рейсов «Грузовой поток».\n\n"
    "🚚 Чтобы начать работу, зарегистрируйтесь:\n" + LINE + "\n"
    "1️⃣ Ваш игровой ник в MTA Province — ровно как в игре\n"
    "2️⃣ Номер игрового счёта — 6 цифр (для премий)\n\n"
    "Напишите боту:\n"
    "❗ !анкета ВашНик 123456\n"
    "Например: !анкета Виктор 776543\n" + LINE + "\n"
    "После регистрации откроются путёвки и гараж.\n"
    "Весь список команд: !помощь"
)

HINT_NOPREFIX = (
    "🤖 Я отвечаю только на команды со знаком «!» в начале.\n\n"
    "Например:\n"
    "❗ !помощь — все команды\n"
    "❗ !анкета Виктор 776543 — регистрация"
)


# ---------------------------------------------------------------- HTTP и VK API

TOKEN = BOT_TOKEN                 # используется внутри call/lp_call
SUPER_IDS = SUPERADMINS
ADMIN_IDS = ADMINS


def _http_json(url, data=None, timeout=HTTP_TIMEOUT):
    """POST (если data) или GET, ответ — разобранный JSON. Только stdlib."""
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", "replace"))


def call(method, _retries=3, **params):
    """Запрос к VK API с повтором на лимитах/флуде/внутренних ошибках."""
    params["access_token"] = TOKEN
    params["v"] = API_V
    last = "?"
    for attempt in range(_retries):
        try:
            data = _http_json(API_URL + method, params)
        except ValueError:
            last = "invalid json"
            time.sleep(1 + attempt)
            continue
        except OSError as e:
            last = "network: {}".format(e)
            time.sleep(1 + attempt)
            continue
        if isinstance(data, dict) and "error" in data:
            err = data.get("error") or {}
            code = err.get("error_code")
            last = "[{}] {}".format(code, err.get("error_msg"))
            if code in RETRY_CODES and attempt < _retries - 1:
                time.sleep(RETRY_DELAY.get(code, 1) * (attempt + 1))
                continue
            log.warning("VK error in %s: %s", method, last)
            return None
        return data.get("response") if isinstance(data, dict) else None
    log.warning("VK call %s failed: %s", method, last)
    return None


def lp_call(method, **params):
    """Для методов Long Poll: возвращаем весь ответ, чтобы видеть ошибку."""
    params["access_token"] = TOKEN
    params["v"] = API_V
    try:
        return _http_json(API_URL + method, params)
    except (ValueError, OSError) as e:
        log.warning("network error in %s: %s", method, e)
        return {}


def _split_text(text, limit=MAX_MSG_LEN):
    """Режет длинный текст на части по строкам, чтобы влезть в лимит VK."""
    parts = []
    cur = ""
    for line in str(text).split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += "\n" + line
        else:
            parts.append(cur)
            cur = line
    if cur or not parts:
        parts.append(cur)
    return parts


def send(peer_id, text):
    """Отправка сообщения; длинный текст уходит несколькими частями."""
    ok = True
    for chunk in _split_text(text):
        resp = call("messages.send", peer_id=peer_id, message=chunk,
                    random_id=random.randint(1, 2 ** 31 - 1))
        if resp is None:
            ok = False
            log.warning("send error to peer %s", peer_id)
        time.sleep(SEND_PAUSE)
    return ok


# ---------------------------------------------------------------- мелочи


def norm_plate(s):
    return str(s).strip().upper().translate(LAT2CYR)


def fmt_dt(ts):
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(ts + MSK_OFFSET)) + " МСК"


def fmt_date(ts):
    return time.strftime("%d.%m.%Y", time.gmtime(ts + MSK_OFFSET))


def fmt_km(n):
    return "{:,}".format(int(n)).replace(",", " ")


def fmt_left(sec):
    sec = max(0, int(sec))
    d = sec // 86400
    h = (sec % 86400) // 3600
    mi = (sec % 3600) // 60
    if d:
        return "{} д {} ч".format(d, h)
    if h:
        return "{} ч {} мин".format(h, mi)
    return "{} мин".format(mi)


def msk_weekday(ts=None):
    return time.gmtime((ts or time.time()) + MSK_OFFSET).tm_wday


def msk_hour(ts=None):
    return time.gmtime((ts or time.time()) + MSK_OFFSET).tm_hour


# ---------------------------------------------------------------- база


def _new_car(model, plate):
    return {"model": model, "mileage": 0, "status": "стоит", "driver_id": 0,
            "driver_name": "", "assigned": None, "assigned_name": "",
            "service_km": 0, "journal": []}


def _new_driver():
    return {"name": "", "account": "", "id": 0, "total_km": 0, "trips": 0,
            "rating": 100, "penalties": 0, "open_trip": None,
            "history": [], "log": [], "assigned_plate": None}


def _trim(lst, n):
    return lst[-n:] if isinstance(lst, list) else []


def _next_seq(meta, key):
    meta[key] = int(meta.get(key, 0)) + 1
    return int(meta[key])


def _repair(data):
    """Приводит базу к актуальной схеме, ничего не теряя."""
    if not isinstance(data.get("drivers"), dict):
        data["drivers"] = {}
    if not isinstance(data.get("cars"), dict) or not data["cars"]:
        data["cars"] = {}
        for model, plate in CARS_INIT:
            data["cars"][plate] = _new_car(model, plate)
    if not isinstance(data.get("chat"), dict):
        data["chat"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    if data.get("pause") is not None and not isinstance(data["pause"], dict):
        data["pause"] = None

    for plate, car in list(data["cars"].items()):
        if not isinstance(car, dict):
            data["cars"][plate] = _new_car(plate, plate)
            car = data["cars"][plate]
        car.setdefault("model", plate)
        car.setdefault("mileage", 0)
        car.setdefault("status", "стоит")
        car.setdefault("driver_id", 0)
        car.setdefault("driver_name", "")
        car.setdefault("assigned", None)
        car.setdefault("assigned_name", "")
        car.setdefault("service_km", 0)
        car["journal"] = _trim(car.get("journal"), MAX_JOURNAL)
        if not car.get("car_id"):
            car["car_id"] = _next_seq(data["meta"], "car_seq")

    for vk, drv in list(data["drivers"].items()):
        if not isinstance(drv, dict):
            data["drivers"][vk] = _new_driver()
            drv = data["drivers"][vk]
        for key, default in (("name", ""), ("account", ""), ("total_km", 0),
                             ("trips", 0), ("rating", 100), ("penalties", 0),
                             ("open_trip", None), ("assigned_plate", None)):
            drv.setdefault(key, default)
        drv["history"] = _trim(drv.get("history"), MAX_HISTORY)
        drv["log"] = _trim(drv.get("log"), MAX_LOG)
        if not drv.get("id"):
            drv["id"] = _next_seq(data["meta"], "drv_seq")
    return data


def load_db():
    data = {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("корень базы должен быть объектом")
    except FileNotFoundError:
        data = {}
    except (ValueError, json.JSONDecodeError) as e:
        # Битый файл не затираем: сохраняем копию, чтобы можно было восстановить.
        copy = "{}.broken-{}".format(DATA_FILE, int(time.time()))
        try:
            os.replace(DATA_FILE, copy)
            log.error("db.json повреждён (%s), копия: %s", e, copy)
        except OSError as e2:
            log.error("db.json повреждён, копию сохранить не удалось: %s", e2)
        data = {}
    return _repair(data)


def save_db(db):
    """Атомарная запись: временный файл + os.replace."""
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_FILE)


def get_driver(db, user_id):
    d = db["drivers"].setdefault(str(user_id), _new_driver())
    if not d.get("id"):
        d["id"] = _next_seq(db["meta"], "drv_seq")
    return d


def registered(d):
    return bool(d.get("name")) and bool(d.get("account"))


def is_super(uid):
    return uid in SUPER_IDS


def is_admin(uid):
    return is_super(uid) or uid in ADMIN_IDS


def admin_only():
    return ("❌ Команда доступна только админам бригады.\n"
            "Вопросы к руководству: админы решают, дальше бот долбит по цифрам 🚛")


def find_driver_by_name(db, nick):
    nick = str(nick).strip().lower()
    for uid, drv in db["drivers"].items():
        if (drv.get("name") or "").lower() == nick:
            return uid, drv
    return None, None


def find_driver(db, token):
    """Ищет водителя по ID в боте, по VK ID или по нику."""
    token = str(token).strip()
    if token.isdigit():
        num = int(token)
        for uid, drv in db["drivers"].items():
            if drv.get("id") == num:
                return uid, drv
        if token in db["drivers"]:
            return token, db["drivers"][token]
        return None, None
    return find_driver_by_name(db, token)


def find_car(db, token):
    token = str(token).strip()
    if token.isdigit():
        cid = int(token)
        for plate, car in db["cars"].items():
            if car.get("car_id") == cid:
                return plate, car
        return None, None
    plate = norm_plate(token)
    if plate in db["cars"]:
        return plate, db["cars"][plate]
    return None, None


# ---------------------------------------------------------------- путёвки


def issue_trip(db, d, user_id, plate, km):
    if not registered(d):
        return REG_HELP
    car = db["cars"].get(plate)
    if not car:
        return "❌ Авто «{}» не найдено.\n\nВесь парк: !гараж".format(plate)
    pause = db.get("pause")
    if pause and pause.get("until", 0) > time.time():
        return ("⏸ ВЫДАЧА ПУТЁВОК ПРИОСТАНОВЛЕНА\n" + LINE + "\n"
                "Идёт контрольная сверка пробегов.\n"
                "🕒 Выдача возобновится: {}\n\n"
                "Уже открытые путёвки остаются рабочими.").format(
            fmt_dt(pause["until"]))
    if d.get("open_trip"):
        return ("❌ У вас уже открыта путёвка на {}.\n\n"
                "Сначала закройте её:\n"
                "!сдать путевку {} <пробег>").format(
            d["open_trip"].get("plate", "—"), d["open_trip"].get("plate", "—"))
    if car.get("status") == "в рейсе" and str(car.get("driver_id")) != str(user_id):
        return ("❌ {} ({}) сейчас в рейсе у {}.\n\n"
                "🚛 Свободные машины — команда: !гараж").format(
            plate, car["model"], car.get("driver_name") or "другого водителя")
    if car.get("assigned") and str(car["assigned"]) != str(user_id):
        return ("🔒 {} ({}) закреплён за {}. Выписать путёвку\n"
                "на него может только он.").format(
            plate, car["model"], car.get("assigned_name") or "закреплённым водителем")
    if km < car.get("mileage", 0):
        return ("❌ Пробег на выезде ({}) меньше одометра авто ({}).\n\n"
                "📊 Одометр {} — {}\n"
                "Укажите пробег не меньше этого значения.").format(
            fmt_km(km), fmt_km(car["mileage"]), plate, fmt_km(car["mileage"]))

    now = time.time()
    d["open_trip"] = {"plate": plate, "start": km, "ts": now}
    car["status"] = "в рейсе"
    car["driver_id"] = user_id
    car["driver_name"] = d["name"]
    return ("🟢 ПУТЁВКА ВЫДАНА\n" + LINE + "\n"
            "🚛 Авто: {} | {}\n"
            "🛣 Пробег на выезде: {} км\n"
            "🕒 Выдана: {}\n" + LINE + "\n"
            "🩺 Медосмотр пройден.\n"
            "📋 Водитель проинструктирован при выходе в рейс\n"
            "о соблюдении правил ПДД и ВПС.\n\n"
            "📸 Совет: приложите скрин панели F3 (марка, номер,\n"
            "пробег) — так у одометра не будет споров.\n"
            "⏰ Путёвка сгорит, если держать её дольше суток.\n\n"
            "Удачи в рейсе, {}! 🚛💨").format(
        car["model"], plate, fmt_km(km), fmt_dt(now), d["name"])


def close_trip(db, d, user_id, plate, km):
    if not registered(d):
        return REG_HELP
    car = db["cars"].get(plate)
    if not car:
        return "❌ Авто «{}» не найдено.\n\nВесь парк: !гараж".format(plate)
    trip = d.get("open_trip")
    if not trip:
        return ("❌ Открытой путёвки нет.\n\n"
                "Сначала: !выписать путевку НОМЕР пробег")
    if trip.get("plate") != plate:
        return ("❌ У вас открыта путёвка на {}, а не на {}.\n\n"
                "Закройте её: !сдать путевку {} <пробег>").format(
            trip.get("plate"), plate, trip.get("plate"))
    start = int(trip.get("start", 0))
    if km < start:
        d["rating"] = max(0, d.get("rating", 100) - PENALTY_ODOMETER)
        d["penalties"] = d.get("penalties", 0) + 1
        bump_week_penalty(db, d)
        return ("⚠️ ОШИБКА В ПОКАЗАНИЯХ ОДОМЕТРА\n" + LINE + "\n"
                "📉 Возврат: {} км, выезд был: {} км — минус {} км!\n"
                "💰 Штраф: −{} к рейтингу (теперь {})\n" + LINE + "\n"
                "🟢 Путёвка остаётся открытой: {} со {} км.\n"
                "🔁 Проверьте одометр и повторите:\n"
                "!сдать путевку {} <пробег>").format(
            fmt_km(km), fmt_km(start), fmt_km(start - km), PENALTY_ODOMETER,
            d["rating"], plate, fmt_km(start), plate)

    distance = km - start
    now = time.time()
    d["open_trip"] = None
    d["total_km"] = d.get("total_km", 0) + distance
    d["trips"] = d.get("trips", 0) + 1
    d["rating"] = min(100, d.get("rating", 100) + 1)
    d["history"] = _trim(d.get("history"), MAX_HISTORY - 1)
    d["history"].append({"km": distance, "plate": plate, "ts": now,
                         "date": fmt_dt(now)})
    d["log"] = _trim(d.get("log"), MAX_LOG - 1)
    d["log"].append({"ts": now, "plate": plate, "km": distance})

    car["mileage"] = km
    car["status"] = "стоит"
    car["driver_id"] = 0
    car["driver_name"] = ""
    car["service_km"] = car.get("service_km", 0) + distance
    car["journal"] = _trim(car.get("journal"), MAX_JOURNAL - 1)
    car["journal"].append({"ts": now, "name": d["name"], "km": distance,
                           "burned": False})

    out = ("🔴 ПУТЁВКА ЗАКРЫТА\n" + LINE + "\n"
           "🚛 Авто: {} | {}\n"
           "📏 Пройдено: {} км\n"
           "📊 Одометр авто: {} км\n"
           "🕒 Закрыта: {}\n" + LINE + "\n"
           "👤 {}: всего {} км, рейсов {} (+1 к рейтингу ⭐{})").format(
        car["model"], plate, fmt_km(distance), fmt_km(car["mileage"]),
        fmt_dt(now), d["name"], fmt_km(d["total_km"]), d["trips"], d["rating"])
    if car["service_km"] >= TO_LIMIT_KM:
        out += ("\n\n🔧 ПОРА НА ТО: {} {} — с последнего обслуживания\n"
                "прошло {} км. После ремонта: !то {}").format(
            car["model"], plate, fmt_km(car["service_km"]), plate)
    return out


def bump_week_penalty(db, d):
    pw = db["meta"].setdefault("pen_week", {})
    name = d.get("name") or "без имени"
    pw[name] = pw.get(name, 0) + 1


# ---------------------------------------------------------------- фоновые проверки


def check_burned(db, now=None):
    """Гасит просроченные путёвки. Возвращает [(peer_id, текст), ...]."""
    now = now or time.time()
    chat_id = db["chat"].get("chat_id")
    out = []
    chat_lines = []
    for uid, d in db["drivers"].items():
        trip = d.get("open_trip")
        if not trip or now - trip.get("ts", now) < TRIP_LIFETIME:
            continue
        plate = trip.get("plate")
        car = db["cars"].get(plate)
        d["open_trip"] = None
        d["rating"] = max(0, d.get("rating", 100) - PENALTY_BURN)
        d["penalties"] = d.get("penalties", 0) + 1
        bump_week_penalty(db, d)
        if car:
            car["status"] = "стоит"
            car["driver_id"] = 0
            car["driver_name"] = ""
            car["journal"] = _trim(car.get("journal"), MAX_JOURNAL - 1)
            car["journal"].append({"ts": now, "name": d.get("name") or "",
                                   "km": 0, "burned": True})
        msg = ("⏰ ПУТЁВКА СГОРЕЛА\n" + LINE + "\n"
               "🚛 Авто: {} | {}\n"
               "🕒 Выдана: {}\n"
               "💰 Штраф: −{} к рейтингу (теперь ⭐{})\n" + LINE + "\n"
               "Путёвка была открыта больше суток.\n"
               "Выпишите новую: !выписать путевку НОМЕР пробег").format(
            car["model"] if car else "—", plate, fmt_dt(trip.get("ts", now)),
            PENALTY_BURN, d["rating"])
        if str(uid).isdigit():
            out.append((int(uid), msg))
        chat_lines.append("⏰ Путёвка {} у водителя {} сгорела "
                          "(штраф −{} к рейтингу).".format(
                              plate, d.get("name") or "—", PENALTY_BURN))
    if chat_id and chat_lines:
        out.append((int(chat_id), "\n".join(chat_lines)))
    return out


def parse_event_dates(s):
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})$", str(s))
    if not m:
        return None, None
    year = time.gmtime(time.time() + MSK_OFFSET).tm_year
    d1, m1, d2, m2 = map(int, m.groups())
    try:
        start = calendar.timegm((year, m1, d1, 0, 0, 0)) - MSK_OFFSET
        end_year = year + 1 if (m2, d2) < (m1, d1) else year
        end = calendar.timegm((end_year, m2, d2, 23, 59, 0)) - MSK_OFFSET
    except (ValueError, OverflowError):
        return None, None
    if end <= start:
        return None, None
    return start, end


def event_km_in_window(db, start, end):
    sums = {}
    for d in db["drivers"].values():
        total = 0
        for rec in d.get("log") or []:
            if start <= rec.get("ts", 0) <= end:
                total += rec.get("km", 0)
        if total:
            name = d.get("name") or "без имени"
            sums[name] = sums.get(name, 0) + total
    return sorted(sums.items(), key=lambda x: -x[1])


def maybe_finalize_events(db, now=None):
    """Подводит итоги истёкших ивентов. Возвращает [(peer_id, текст), ...]."""
    now = now or time.time()
    chat_id = db["chat"].get("chat_id")
    out = []
    for ev in db["events"]:
        if ev.get("finalized") or now < ev.get("end", 0):
            continue
        top = event_km_in_window(db, ev["start"], ev["end"])[:3]
        medals = ["🥇", "🥈", "🥉"]
        lines = ["{} {} — {} км".format(medals[i], name, fmt_km(km))
                 for i, (name, km) in enumerate(top)]
        if not lines:
            lines.append("Никто не закрыл рейсов за период 😢")
        ev["finalized"] = True
        ev["fin_ts"] = now
        ev["results"] = top
        if chat_id:
            out.append((int(chat_id),
                        "🏁 ИТОГИ ИВЕНТА «{}»\n{}\n".format(ev["name"], LINE)
                        + "🏆 Топ-3 по пробегу:\n{}\n".format("\n".join(lines))
                        + "💰 Призовой фонд: {} ₽\n".format(fmt_km(ev.get("fund", 0)))
                        + LINE + "\n"
                        + "📌 Итоги доступны {} ч (команда: !ивент), "
                          "потом история очистится.".format(EVENT_HISTORY_HOURS)))
    db["events"] = [e for e in db["events"]
                    if not (e.get("finalized") and
                            now - e.get("fin_ts", now) > EVENT_HISTORY_HOURS * 3600)]
    return out


def maybe_weekly_report(db, now=None):
    """Итоги за прошедшую неделю: понедельник с 10:00 МСК, один раз на неделю."""
    now = now or time.time()
    if msk_weekday(now) != 0 or msk_hour(now) < 10:
        return None
    chat_id = db["chat"].get("chat_id")
    if not chat_id:                 # некуда отправить — попробуем позже
        return None
    prev_week = time.gmtime(now + MSK_OFFSET - 7 * 86400)
    key = "weekly_{}".format(time.strftime("%G-W%V", prev_week))
    if db["meta"].get(key):
        return None

    week_ago = now - 7 * 86400
    sums = {}
    for d in db["drivers"].values():
        total = 0
        for rec in d.get("log") or []:
            if rec.get("ts", 0) >= week_ago:
                total += rec.get("km", 0)
        if total:
            name = d.get("name") or "без имени"
            sums[name] = sums.get(name, 0) + total
    top = sorted(sums.items(), key=lambda x: -x[1])[:5]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["{} {} — {} км".format(medals[i], n, fmt_km(km))
             for i, (n, km) in enumerate(top)] or ["Пробега за неделю нет 😢"]

    pw = db["meta"].get("pen_week") or {}
    plines = ["• {} — {} штраф(ов), рейтинг просел".format(name, cnt)
              for name, cnt in sorted(pw.items(), key=lambda x: -x[1])]

    out = ("📊 НЕДЕЛЬНЫЙ ОТЧЁТ БРИГАДЫ\n" + LINE + "\n"
           "🛣 Топ-5 по пробегу за неделю:\n{}\n\n").format("\n".join(lines))
    if plines:
        out += ("⚠️ Штрафы за неделю:\n{}\n\n").format("\n".join(plines))
    else:
        out += "✅ Штрафов за неделю нет — молодцы!\n\n"
    out += "🚚 Хорошей дороги на следующей неделе!"

    db["meta"][key] = now
    db["meta"]["pen_week"] = {}
    return out


# ---------------------------------------------------------------- команды


def cmd_help(db, uid, args):
    return HELP


def cmd_anketa(db, uid, args):
    if not args:
        return REG_HELP
    account = args[-1]
    name = " ".join(args[:-1]).strip()
    if len(args) < 2 or not account.isdigit() or len(account) != 6:
        return "❌ Нужны ровно два данных: ник и 6-значный счёт.\n\n" + REG_HELP
    if not re.match(r"^[A-Za-zА-Яа-яЁё0-9_\- ]+$", name):
        return "❌ Ник содержит недопустимые символы.\n\n" + REG_HELP
    nick_uid, _ = find_driver_by_name(db, name)
    if nick_uid and nick_uid != str(uid):
        return ("❌ Ник «{}» уже занят другим водителем.\n\n"
                "Укажите свой игровой ник (как в игре).\n\n" + REG_HELP).format(name)

    d = get_driver(db, uid)
    was = registered(d)
    d["name"], d["account"] = name, account
    # ник мог измениться — обновляем подписи на машинах
    for car in db["cars"].values():
        if str(car.get("assigned")) == str(uid):
            car["assigned_name"] = name
        if str(car.get("driver_id")) == str(uid):
            car["driver_name"] = name

    if was:
        return ("✅ АНКЕТА ОБНОВЛЕНА\n" + LINE + "\n"
                "👤 Ник: {}\n"
                "💳 Счёт: {}\n"
                "🆔 Ваш ID в боте: {}\n" + LINE + "\n"
                "Все команды: !помощь").format(name, account, d["id"])
    return ("✅ ДОБРО ПОЖАЛОВАТЬ В БРИГАДУ!\n" + LINE + "\n"
            "👤 Ник: {}\n"
            "💳 Счёт: {}\n"
            "🆔 Ваш ID в боте: {}\n" + LINE + "\n"
            "Что дальше:\n"
            "1️⃣ !гараж — выберите свободную машину\n"
            "2️⃣ !выписать путевку НОМЕР пробег — в рейс!\n"
            "3️⃣ !бинды — настройте чат организации (F8)\n\n"
            "Хорошей дороги, {}! 🚛💨").format(name, account, d["id"], name)


def cmd_my_id(db, uid, args):
    d = get_driver(db, uid)
    if registered(d):
        return ("🆔 ВАШИ ID\n" + LINE + "\n"
                "VK ID: {}\n"
                "ID водителя в боте: {}\n"
                "👤 Ник: {}").format(uid, d["id"], d["name"])
    return "🆔 VK ID: {}\n\nВы ещё не зарегистрированы.\n\n{}".format(uid, REG_HELP)


def cmd_issue(db, uid, args):
    # формат: !выписать путевку НОМЕР пробег (принимаем и «путёвку»)
    if len(args) < 3 or args[0].lower() not in ("путевку", "путёвку"):
        return ("❌ Формат: !выписать путевку НОМЕР пробег\n"
                "Пример: !выписать путевку Т740ТТ77 100\n\n"
                "Номера машин: !гараж")
    if not args[2].isdigit():
        return "❌ Пробег должен быть числом. Пример: !выписать путевку Т740ТТ77 100"
    return issue_trip(db, get_driver(db, uid), uid, norm_plate(args[1]), int(args[2]))


def cmd_close(db, uid, args):
    if len(args) < 3 or args[0].lower() not in ("путевку", "путёвку"):
        return ("❌ Формат: !сдать путевку НОМЕР пробег\n"
                "Пример: !сдать путевку Т740ТТ77 250")
    if not args[2].isdigit():
        return "❌ Пробег должен быть числом. Пример: !сдать путевку Т740ТТ77 250"
    return close_trip(db, get_driver(db, uid), uid, norm_plate(args[1]), int(args[2]))


def cmd_my_trips(db, uid, args):
    d = get_driver(db, uid)
    if not registered(d):
        return REG_HELP
    h = d.get("history") or []
    if not h:
        return ("📭 Рейсов пока нет.\n\n"
                "Первая путёвка: !выписать путевку НОМЕР пробег")
    lines = ["• {} | {} | {} км".format(rec.get("date", "—"),
                                        rec.get("plate", "—"),
                                        fmt_km(rec.get("km", 0)))
             for rec in h[-10:]]
    return "📜 ВАШИ РЕЙСЫ (последние {} из {})\n{}\n{}".format(
        len(lines), len(h), LINE, "\n".join(lines))


def cmd_profile(db, uid, args):
    if args:
        if not is_admin(uid):
            return "❌ Профиль другого водителя может смотреть только админ."
        query = " ".join(args)
        tuid, td = find_driver(db, query)
        if not td:
            return "❌ Водитель «{}» не найден. Список: !водители".format(query)
        d, label = td, td.get("name") or "—"
    else:
        d, label = get_driver(db, uid), None
        if not registered(d):
            return REG_HELP

    lines = ["👤 ПРОФИЛЬ: {}".format(label if label else d.get("name") or "—")]
    if label:
        lines.append("🆔 ID водителя: {}".format(d.get("id")))
    lines += [LINE,
              "💳 Счёт: {}".format(d.get("account") or "—"),
              "⭐ Рейтинг: {}".format(d.get("rating", 100)),
              "🛣 Всего км: {}".format(fmt_km(d.get("total_km", 0))),
              "🚚 Рейсов: {}".format(d.get("trips", 0)),
              "⚠️ Штрафов: {}".format(d.get("penalties", 0))]
    trip = d.get("open_trip")
    if trip:
        left = TRIP_LIFETIME - (time.time() - trip.get("ts", time.time()))
        lines.append("🟢 Открыта путёвка: {} (сгорит через {})".format(
            trip.get("plate", "—"), fmt_left(left)))
    assigned = d.get("assigned_plate")
    if assigned:
        lines.append("🔒 Закреплено авто: {}".format(assigned))

    day_ago = time.time() - 86400
    day = {}
    for rec in d.get("log") or []:
        if rec.get("ts", 0) >= day_ago:
            p = rec.get("plate", "—")
            day[p] = day.get(p, 0) + rec.get("km", 0)
    if day:
        lines.append(LINE)
        lines.append("📅 За сутки:")
        for p, km in sorted(day.items()):
            lines.append("• {} — {} км".format(p, fmt_km(km)))
    return "\n".join(lines)


def cmd_garage(db, uid, args):
    if not db["cars"]:
        return "Парк пуст. Админ может добавить авто: !авто МАРКА НОМЕР пробег"
    lines = ["🚚 ПАРК АВТОМОБИЛЕЙ ({}):".format(len(db["cars"])), LINE]
    for plate in sorted(db["cars"]):
        c = db["cars"][plate]
        status = "🟢 стоит"
        if c.get("status") == "в рейсе":
            status = "🔵 в рейсе ({})".format(c.get("driver_name") or "—")
        lock = ""
        if c.get("assigned"):
            lock = " 🔒 закреплён за {}".format(c.get("assigned_name") or "—")
        lines.append("• {} | {} | ID {} | {} км | {}{}".format(
            plate, c.get("model", "—"), c.get("car_id", "—"),
            fmt_km(c.get("mileage", 0)), status, lock))
    return "\n".join(lines)


def cmd_history(db, uid, args):
    if not args:
        return "❌ Формат: !история НОМЕР (например, !история Т740ТТ77)"
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    j = car.get("journal") or []
    if not j:
        return ("📭 По {} рейсов ещё не было.\n\n"
                "Журнал заполнится после закрытых путёвок.").format(plate)
    lines = ["📜 ИСТОРИЯ: {} | {}\n".format(car.get("model", "—"), plate)]
    for rec in j[-10:]:
        if rec.get("burned"):
            lines.append("• {} | {} | путёвка сгорела ⏰".format(
                fmt_dt(rec.get("ts", time.time())), rec.get("name") or "—"))
        else:
            lines.append("• {} | {} | {} км".format(
                fmt_dt(rec.get("ts", time.time())), rec.get("name") or "—",
                fmt_km(rec.get("km", 0))))
    return "\n".join(lines) + "\n\nВсего записей: {}".format(len(j))


def cmd_today(db, uid, args):
    day_ago = time.time() - 86400
    total_b = 0
    rows = []
    for d in db["drivers"].values():
        total = 0
        for rec in d.get("log") or []:
            if rec.get("ts", 0) >= day_ago:
                total += rec.get("km", 0)
        if total:
            total_b += total
            rows.append((total, d.get("name") or "—"))
    park = sum(c.get("mileage", 0) for c in db["cars"].values())
    out = "📅 ПРОБЕГ ЗА СУТКИ\n" + LINE + "\n"
    if rows:
        out += "\n".join("• {} — {} км".format(n, fmt_km(km))
                         for km, n in sorted(rows, reverse=True))
    else:
        out += "За сутки рейсов не было 😴"
    out += ("\n" + LINE + "\n"
            "👥 Бригада: {} км\n"
            "🚚 Парк всего: {} км").format(fmt_km(total_b), fmt_km(park))
    return out


def cmd_rating(db, uid, args):
    regs = [(d.get("rating", 100), d.get("name") or "—", d.get("total_km", 0))
            for d in db["drivers"].values() if registered(d)]
    if not regs:
        return "В бригаде пока нет зарегистрированных водителей."
    regs.sort(key=lambda x: (-x[0], -x[2], x[1]))
    medals = ["🥇", "🥈", "🥉"]
    lines = ["⭐ РЕЙТИНГ ВОДИТЕЛЕЙ", LINE]
    for i, (r, n, km) in enumerate(regs[:10]):
        mark = medals[i] if i < 3 else "{}. ".format(i + 1)
        lines.append("{} {} — ⭐{} | {} км".format(mark, n, r, fmt_km(km)))
    return "\n".join(lines)


def cmd_drivers(db, uid, args):
    regs = [(d.get("id") or 0, d.get("name"), d.get("rating", 100),
             d.get("open_trip"))
            for d in db["drivers"].values() if registered(d)]
    if not regs:
        return "В бригаде пока нет зарегистрированных водителей.\n\n" + REG_HELP
    lines = ["👥 ВОДИТЕЛИ БРИГАДЫ ({})".format(len(regs)), LINE]
    for did, name, rating, trip in sorted(regs, key=lambda x: x[0]):
        mark = "🟢 в рейсе" if trip else ""
        lines.append("• {} | {} | ⭐{} {}".format(did, name, rating, mark).rstrip())
    return "\n".join(lines)


def cmd_event_view(db, uid, args):
    now = time.time()
    maybe_finalize_events(db, now)
    for ev in db["events"]:
        if not ev.get("finalized") and now < ev.get("end", 0):
            top = event_km_in_window(db, ev["start"], ev["end"])[:5]
            medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
            lines = ["{} {} — {} км".format(medals[i], n, fmt_km(km))
                     for i, (n, km) in enumerate(top)]
            if not lines:
                lines.append("Пока ни одного закрытого рейса — гоните колонну! 🚛")
            return ("🏁 ИВЕНТ «{}»\n" + LINE + "\n"
                    "📅 Период: {} — {}\n"
                    "💰 Призовой фонд: {} ₽\n"
                    "⏳ До конца: {}\n" + LINE + "\n"
                    "🏆 Текущая пятёрка:\n{}\n\n"
                    "Пробег учитывается по закрытым путёвкам.").format(
                ev["name"], fmt_date(ev["start"]), fmt_date(ev["end"]),
                fmt_km(ev.get("fund", 0)), fmt_left(ev["end"] - now),
                "\n".join(lines))
    for ev in db["events"]:
        if ev.get("finalized"):
            top = (ev.get("results") or [])[:3]
            medals = ["🥇", "🥈", "🥉"]
            lines = ["{} {} — {} км".format(medals[i], n, fmt_km(km))
                     for i, (n, km) in enumerate(top)] or ["Итогов нет."]
            return ("🏁 ИТОГИ ИВЕНТА «{}»\n" + LINE + "\n"
                    "🏆 Топ-3:\n{}\n"
                    "💰 Призовой фонд: {} ₽\n" + LINE + "\n"
                    "⏰ Итоги хранятся ещё {}. Потом история\n"
                    "очистится — активных ивентов не будет.").format(
                ev["name"], "\n".join(lines), fmt_km(ev.get("fund", 0)),
                fmt_left(EVENT_HISTORY_HOURS * 3600 -
                         (now - ev.get("fin_ts", now))))
    return "😴 Активных ивентов нет. Ждём следующий — совсем скоро! 🏁"


def cmd_event_create(db, uid, args):
    if not is_admin(uid):
        return "❌ Команда только для админов."
    if len(args) < 3:
        return ("❌ Формат: !ивент Название 26.11-05.12 100000\n"
                "Пример: !ивент Зимний марафон 26.11-05.12 100000")
    fund = args[-1].replace(" ", "")
    if not fund.isdigit():
        return ("❌ Призовой фонд должен быть числом.\n"
                "Пример: !ивент Название 26.11-05.12 100000")
    start, end = parse_event_dates(args[-2])
    if start is None:
        return ("❌ Не понял сроки. Формат дат: 26.11-05.12\n"
                "Пример: !ивент Зимний марафон 26.11-05.12 100000")
    name = " ".join(args[:-2]).strip()
    if not name:
        return "❌ Укажите название ивента перед датами."
    now = time.time()
    active = [e for e in db["events"]
              if not e.get("finalized") and e.get("end", 0) >= now]
    if active:
        return "❌ Уже идёт ивент «{}» — сначала дождитесь его завершения.".format(
            active[0]["name"])
    db["events"].append({"name": name, "start": start, "end": end,
                         "fund": int(fund), "finalized": False})
    return ("🎯 ИВЕНТ СОЗДАН\n" + LINE + "\n"
            "🏁 Название: {}\n"
            "📅 Период: {} — {}\n"
            "💰 Призовой фонд: {} ₽\n" + LINE + "\n"
            "Топ-3 по пробегу за период получат приз!\n"
            "Актуальная пятёрка: !ивент").format(
        name, fmt_date(start), fmt_date(end), fmt_km(int(fund)))


def cmd_binds(db, uid, args):
    return BINDS


# ---------------------------------------------------------------- админ


def cmd_add_car(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if len(args) < 3:
        return ("❌ Формат: !авто МАРКА НОМЕР пробег\n"
                "Пример: !авто КАМАЗ 6520 Т940ТА77 150000")
    if not args[-1].isdigit():
        return "❌ Пробег должен быть числом. Пример: !авто КАМАЗ Т940ТА77 150000"
    plate = norm_plate(args[-2])
    if plate in db["cars"]:
        return "❌ {} уже есть в парке.".format(plate)
    model = " ".join(args[:-2]) or plate
    car = _new_car(model, plate)
    car["mileage"] = int(args[-1])
    car["car_id"] = _next_seq(db["meta"], "car_seq")
    db["cars"][plate] = car
    return ("✅ АВТО ДОБАВЛЕНО\n" + LINE + "\n"
            "🚛 {} | {}\n"
            "📊 Одометр: {} км\n"
            "🆔 ID авто: {}").format(model, plate, fmt_km(car["mileage"]),
                                    car["car_id"])


def cmd_set_mileage(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if len(args) < 2:
        return "❌ Формат: !пробег НОМЕР|ID значение (например: !пробег Т740ТТ77 450000)"
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    if not args[1].isdigit():
        return "❌ Пробег должен быть числом."
    car["mileage"] = int(args[1])
    car["service_km"] = 0
    return "✅ Одометр {} ({}): {} км, счётчик ТО обнулён.".format(
        plate, car.get("model", "—"), fmt_km(car["mileage"]))


def cmd_del_car(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return "❌ Формат: !удалить авто НОМЕР|ID"
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    if car.get("status") == "в рейсе":
        return "❌ {} сейчас в рейсе у {} — сначала пусть закроет путёвку.".format(
            plate, car.get("driver_name") or "водителем")
    if car.get("assigned"):
        return "❌ {} закреплён за {} — сначала !открепить {}.".format(
            plate, car.get("assigned_name") or "водителем", plate)
    del db["cars"][plate]
    return "✅ {} удалён из парка.".format(plate)


def cmd_stop(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    hours = DEFAULT_PAUSE_HOURS
    if args and args[0].isdigit():
        hours = max(1, min(168, int(args[0])))
    until = time.time() + hours * 3600
    db["pause"] = {"until": until, "by": uid}
    return ("⏸ ВЫДАЧА ПУТЁВОК ПРИОСТАНОВЛЕНА\n" + LINE + "\n"
            "Причина: контрольная сверка пробегов.\n"
            "🕒 Возобновление: {} (через {})\n\n"
            "Уже открытые путёвки — рабочие, их можно закрывать.").format(
        fmt_dt(until), fmt_left(hours * 3600))


def cmd_start(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    pause = db.get("pause")
    if not pause or pause.get("until", 0) <= time.time():
        db["pause"] = None
        return "ℹ️ Выдача путёвок и так работает."
    db["pause"] = None
    return "▶️ ВЫДАЧА ПУТЁВОК ВОЗОБНОВЛЕНА. Всем удачных рейсов! 🚛"


def cmd_to(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return "❌ Формат: !то НОМЕР|ID (после обслуживания авто)"
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    car["service_km"] = 0
    return ("✅ ТО ЗАФИКСИРОВАНО\n"
            "🚛 {} | {} — счётчик обнулён, следующий ТО\nчерез {} км.").format(
        car.get("model", "—"), plate, TO_LIMIT_KM)


def cmd_penalty(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return ("❌ Формат: !штраф Ник [баллы]\n"
                "Пример: !штраф Виктор 15 (по умолчанию 10)")
    query = args[0]
    tuid, td = find_driver(db, query)
    if not td and len(args) > 1:      # ник из двух слов: «Виктор Петров 15»
        query = " ".join(args[:-1])
        tuid, td = find_driver(db, query)
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(query)
    pts = 10
    if len(args) > 1 and args[-1].lstrip("-").isdigit():
        pts = max(1, abs(int(args[-1])))
    td["rating"] = max(0, td.get("rating", 100) - pts)
    td["penalties"] = td.get("penalties", 0) + 1
    bump_week_penalty(db, td)
    return ("⚠️ ШТРАФ\n"
            "👤 {}: −{} к рейтингу (теперь ⭐{}).\n"
            "Работайте аккуратнее! 🚚").format(td.get("name") or "—", pts,
                                               td["rating"])


def cmd_reset_rating(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return "❌ Формат: !сброс рейтинга Ник"
    query = " ".join(args)
    tuid, td = find_driver(db, query)
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(query)
    td["rating"] = 100
    return "✅ Рейтинг {} сброшен до 100.".format(td.get("name") or "—")


def cmd_del_driver(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return "❌ Формат: !удалить Ник — регистрация и статистика обнулятся"
    query = " ".join(args)
    tuid, td = find_driver(db, query)
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(query)
    name = td.get("name") or query
    if td.get("open_trip"):
        car = db["cars"].get(td["open_trip"].get("plate"))
        if car and str(car.get("driver_id")) == str(tuid):
            car["status"] = "стоит"
            car["driver_id"] = 0
            car["driver_name"] = ""
        td["open_trip"] = None
    for car in db["cars"].values():
        if car.get("assigned") and str(car["assigned"]) == str(tuid):
            car["assigned"] = None
            car["assigned_name"] = ""
    db["drivers"][tuid] = _new_driver()
    return ("✅ Регистрация и статистика {} обнулены.\n"
            "Для повторной регистрации: !анкета Ник 123456").format(name)


def cmd_announce(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    text = " ".join(args).strip()
    if not text:
        return ("📢 КАК ОТПРАВИТЬ ОБЪЯВЛЕНИЕ\n" + LINE + "\n"
                "Напишите после команды сам текст объявления:\n\n"
                "❗ !объявление Завтра в 20:00 общий вывоз колонной,\n"
                "сбор на базе. Участие обязательно!\n\n"
                "Что произойдёт:\n"
                "🔹 Бот разошлёт текст всем зарегистрированным водителям в личку\n"
                "🔹 Вы получите отчёт: кому доставлено, кому не дошло\n"
                "🔹 Текст не надо копировать — просто напишите его одной строкой")
    targets = [(int(vk), d.get("name") or str(vk))
               for vk, d in db["drivers"].items()
               if registered(d) and str(vk).isdigit()]
    # рассылка уходит после ответа, чтобы не держать блокировку базы
    return (("📢 РАССЫЛКА ЗАПУЩЕНА\n" + LINE + "\n"
             "Текст: {}\n" + LINE + "\n"
             "Отправляю {} водителям — отчёт придёт сразу после.").format(
        text, len(targets)),
            [("announce", targets, text)])


def do_announce(targets, text):
    delivered, failed = [], []
    for vk, name in targets:
        msg = ("📢 ОБЪЯВЛЕНИЕ\n" + LINE + "\n{}\n" + LINE + "\n"
               "С уважением, руководство «Грузовой поток» 🚚").format(text)
        if send(vk, msg):
            delivered.append(name)
        else:
            failed.append(name)
    out = ("📢 Объявление разослано в личку.\n"
           "✅ Доставлено: {} из {}.").format(len(delivered), len(targets))
    if delivered:
        out += "\nПолучили: {}".format(", ".join(delivered))
    if failed:
        out += "\n❌ Не дошло (продублируйте лично): {}".format(", ".join(failed))
    return out


# ---------------------------------------------------------------- суперадмин


def cmd_assign(db, uid, args):
    if not is_super(uid):
        return ("❌ Закреплять машины может только суперадмин —\n"
                "закрепление идёт от ролей в игре.")
    if len(args) < 2:
        return ("❌ Формат: !закрепить НОМЕР|ID Ник\n"
                "Пример: !закрепить Т740ТТ77 Виктор")
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    if car.get("status") == "в рейсе":
        return "❌ {} сейчас в рейсе — дождитесь закрытия путёвки.".format(plate)
    query = " ".join(args[1:])
    tuid, td = find_driver(db, query)
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(query)
    if not registered(td):
        return "❌ {} ещё не зарегистрирован: !анкета Ник 123456".format(query)
    # снимаем прежние закрепления этого водителя и этой машины
    for other in db["cars"].values():
        if other.get("assigned") and str(other["assigned"]) == str(tuid):
            other["assigned"], other["assigned_name"] = None, ""
    if car.get("assigned"):
        old = db["drivers"].get(str(car["assigned"]))
        if old:
            old["assigned_plate"] = None
    car["assigned"] = str(tuid)
    car["assigned_name"] = td.get("name") or ""
    td["assigned_plate"] = plate
    return ("🔒 АВТО ЗАКРЕПЛЕНО\n" + LINE + "\n"
            "🚛 {} | {}\n"
            "👤 Водитель: {}\n" + LINE + "\n"
            "На это авто путёвку выписывает только он.").format(
        car.get("model", "—"), plate, td.get("name"))


def cmd_unassign(db, uid, args):
    if not is_super(uid):
        return ("❌ Откреплять машины может только суперадмин —\n"
                "закрепление идёт от ролей в игре.")
    if not args:
        return "❌ Формат: !открепить НОМЕР|ID"
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    if not car.get("assigned"):
        return "ℹ️ {} и так не закреплён.".format(plate)
    old = db["drivers"].get(str(car["assigned"]))
    if old:
        old["assigned_plate"] = None
    car["assigned"], car["assigned_name"] = None, ""
    return "🔓 {} откреплён — снова в свободном пользовании.".format(plate)


def cmd_db_reset(db, uid, args):
    if not is_super(uid):
        return "❌ Полный сброс базы — только для суперадмина."
    if " ".join(args).upper() != "ПОДТВЕРЖДАЮ":
        return ("⚠️ ПОЛНЫЙ СБРОС БАЗЫ\n" + LINE + "\n"
                "Будут удалены:\n"
                "• все регистрации водителей (ники, счёты, ID-привязки)\n"
                "• вся статистика: пробеги, рейсы, штрафы, рейтинги\n"
                "• одометры всех машин (парк пересоздастся с нуля)\n"
                "• журнал рейсов, ивенты, закрепления\n\n"
                "Это действие необратимо!\n\n"
                "❗ Для подтверждения отправьте ровно:\n"
                "!сброс базы ПОДТВЕРЖДАЮ")
    chat_id = db["chat"].get("chat_id")
    db.clear()
    db.update({"drivers": {}, "cars": {}, "chat": {}, "events": [],
               "meta": {}, "pause": None})
    if chat_id:
        db["chat"]["chat_id"] = chat_id
    for model, plate in CARS_INIT:
        db["cars"][plate] = _new_car(model, plate)
    return ("✅ БАЗА ПОЛНОСТЬЮ СБРОШЕНА\n" + LINE + "\n"
            "• регистрации удалены\n"
            "• статистика и штрафы обнулены\n"
            "• одометры машин сброшены (парк пересобран)\n"
            "• журналы и ивенты очищены\n" + LINE + "\n"
            "Парку — чистый старт. Регистрируйте бригаду заново: !анкета Ник 123456")


# ---------------------------------------------------------------- диспетчер


def handle(db, user_id, text, peer_id=None):
    """
    Возвращает:
        строка                        — ответ в чат
        (строка, [отложенные дела])   — ответ + действия после ответа
        None                          — молчим
    Отложенное дело: ("announce", [(vk, имя), ...], текст)
    """
    raw = (text or "").strip()

    if peer_id and peer_id > 2_000_000_000:
        db["chat"] = db.get("chat") or {}
        if db["chat"].get("chat_id") != peer_id:
            db["chat"]["chat_id"] = peer_id    # запоминаем беседу бригады

    if not raw:
        return None
    if not raw.startswith(PREFIX):
        if raw.lower() in ("начать", "start"):
            return WELCOME if peer_id == user_id else None
        if peer_id and peer_id == user_id:     # личка: мягкая подсказка
            return HINT_NOPREFIX
        return None                            # в беседе без «!» молчим

    body = raw[len(PREFIX):].strip()
    if not body:
        return None
    parts = body.split()
    cmd = parts[0].lower()
    arg = [a.lower() for a in parts[1:]]       # для сравнения служебных слов
    args = parts[1:]

    # --- общее
    if cmd in ("помощь", "help", "хелп"):
        return cmd_help(db, user_id, args)
    if cmd == "анкета":
        return cmd_anketa(db, user_id, args)
    if cmd == "мой" and arg[:1] == ["id"]:
        return cmd_my_id(db, user_id, args[1:])
    if cmd == "выписать":
        return cmd_issue(db, user_id, args)
    if cmd == "сдать":
        return cmd_close(db, user_id, args)
    if cmd in ("путевки", "путёвки") and not args:
        return cmd_my_trips(db, user_id, args)
    if cmd == "профиль":
        return cmd_profile(db, user_id, args)
    if cmd == "гараж":
        return cmd_garage(db, user_id, args)
    if cmd == "история":
        return cmd_history(db, user_id, args)
    if cmd == "за" and arg[:1] == ["сегодня"]:
        return cmd_today(db, user_id, args[1:])
    if cmd == "рейтинг":
        return cmd_rating(db, user_id, args)
    if cmd == "водители":
        return cmd_drivers(db, user_id, args)
    if cmd == "ивент":
        if args and is_admin(user_id):
            return cmd_event_create(db, user_id, args)
        return cmd_event_view(db, user_id, args)
    if cmd == "бинды":
        return cmd_binds(db, user_id, args)

    # --- админ
    if cmd == "авто":
        return cmd_add_car(db, user_id, args)
    if cmd == "пробег":
        return cmd_set_mileage(db, user_id, args)
    if cmd == "удалить" and arg[:1] == ["авто"]:
        return cmd_del_car(db, user_id, args[1:])
    if cmd == "стоп" and arg[:1] and arg[0].startswith("путев"):
        return cmd_stop(db, user_id, args[1:])
    if cmd == "пуск" and arg[:1] and arg[0].startswith("путев"):
        return cmd_start(db, user_id, args[1:])
    if cmd == "то":
        return cmd_to(db, user_id, args)
    if cmd == "штраф":
        return cmd_penalty(db, user_id, args)
    if cmd == "сброс" and arg[:1] == ["рейтинга"]:
        return cmd_reset_rating(db, user_id, args[1:])
    if cmd == "сброс" and arg[:1] == ["базы"]:
        return cmd_db_reset(db, user_id, args[1:])
    if cmd == "удалить":
        return cmd_del_driver(db, user_id, args)
    if cmd == "объявление":
        return cmd_announce(db, user_id, args)

    # --- суперадмин
    if cmd == "закрепить":
        return cmd_assign(db, user_id, args)
    if cmd == "открепить":
        return cmd_unassign(db, user_id, args)

    return "❌ Не знаю такой команды: «{}»\n\nВсе команды: !помощь".format(cmd)


def process_update(db, upd):
    obj = upd.get("object") or {}
    message = obj.get("message") or obj
    sender = message.get("from_id")
    peer_id = message.get("peer_id")
    text = message.get("text") or ""
    if not isinstance(sender, int) or not isinstance(peer_id, int):
        return
    if sender <= 0:
        return                      # исходящие и системные сообщения

    deferred = []
    with _lock:
        result = handle(db, sender, text, peer_id)
        if isinstance(result, tuple):
            reply, deferred = result
        else:
            reply = result
        if reply:
            send(peer_id, reply)
        save_db(db)

    # тяжёлые действия — вне блокировки, чтобы бот не «замирал»
    for item in deferred or []:
        if item and item[0] == "announce":
            send(peer_id, do_announce(item[1], item[2]))


# ---------------------------------------------------------------- фоновый поток


def bg_loop(db, stop_event):
    while not stop_event.is_set():
        try:
            with _lock:
                now = time.time()
                msgs = check_burned(db, now)
                msgs += maybe_finalize_events(db, now)
                report = maybe_weekly_report(db, now)
                if report:
                    chat_id = db["chat"].get("chat_id")
                    if chat_id:
                        msgs.append((int(chat_id), report))
                save_db(db)
            for peer, text in msgs:
                send(peer, text)
        except Exception as e:
            log.error("bg error: %s", e)
            traceback.print_exc()
        stop_event.wait(BURN_CHECK_EVERY)


# ---------------------------------------------------------------- Long Poll


def lp_server():
    params = {"lp_version": 3, "need_pts": 0}
    if GROUP_ID:
        params["group_id"] = GROUP_ID
    data = lp_call("groups.getLongPollServer", **params)
    if "error" in data:
        err = data.get("error") or {}
        log.error("LP init error: [%s] %s", err.get("error_code"),
                  err.get("error_msg"))
        return None
    return data.get("response")


def ensure_lp_settings():
    """Включает Bots Long Poll и событие message_new для сообщества."""
    if not GROUP_ID:
        return
    data = lp_call("groups.setLongPollSettings", group_id=GROUP_ID,
                   enabled=1, api_version=API_V, message_new=1)
    if "error" in data:
        err = data.get("error") or {}
        log.warning("setLongPollSettings: [%s] %s — включите Long Poll вручную "
                    "в Управление → Работа с API → Long Poll API",
                    err.get("error_code"), err.get("error_msg"))


def main():
    if not TOKEN:
        log.error("BOT_TOKEN не задан — впишите токен в начале bot.py "
                  "или задайте переменную окружения")
        return 1

    db = load_db()
    save_db(db)
    log.info("база: %s (водителей: %d, машин: %d)",
             DATA_FILE, len(db["drivers"]), len(db["cars"]))
    if GROUP_ID:
        log.info("сообщество: %s", GROUP_ID)
    else:
        log.warning("GROUP_ID не задан — Bots Long Poll может не подключиться")

    ensure_lp_settings()

    stop_event = threading.Event()

    def _stop(signum, frame):
        log.info("остановка по сигналу %s", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass

    threading.Thread(target=bg_loop, args=(db, stop_event), daemon=True).start()

    lp = lp_server()
    if not lp:
        log.error("не удалось получить Long Poll сервер")
        return 1

    server, key, ts = lp.get("server"), lp.get("key"), lp.get("ts")
    log.info("Long Poll подключён: %s", server)

    while not stop_event.is_set():
        try:
            url = "{}?act=a_check&key={}&ts={}&wait={}".format(
                server, key, ts, LP_WAIT)
            r = _http_json(url, timeout=LP_WAIT + 15)
        except ValueError:
            log.warning("Long Poll: некорректный ответ, переподключаюсь")
            lp = lp_server() or {}
            server, key, ts = lp.get("server"), lp.get("key"), lp.get("ts")
            time.sleep(2)
            continue
        except OSError as e:
            log.warning("Long Poll: %s", e)
            time.sleep(3)
            continue

        if r.get("failed"):
            code = r.get("failed")
            if code == 1:
                ts = r.get("ts", ts)
            else:                     # 2 — ключ истёк, 3 — нужны новые key и ts
                lp = lp_server()
                if not lp:
                    time.sleep(5)
                    continue
                server, key, ts = lp.get("server"), lp.get("key"), lp.get("ts")
            continue

        ts = r.get("ts", ts)
        for upd in r.get("updates") or []:
            if not isinstance(upd, dict) or upd.get("type") != "message_new":
                continue
            try:
                process_update(db, upd)
            except Exception as e:
                log.error("update error: %s", e)
                traceback.print_exc()

    with _lock:
        save_db(db)
    log.info("бот остановлен, база сохранена")
    return 0


if __name__ == "__main__":
    sys.exit(main())
