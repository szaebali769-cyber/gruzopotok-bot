# -*- coding: utf-8 -*-
"""
Грузовой поток — бот ВК: путёвки, парк, закрепление машин, ивенты, штрафы.
Long Poll VK API (requests). Токен: BOT_TOKEN. Админы: ADMINS, суперадмины: SUPERADMINS.
Все команды начинаются с префикса "!" (например: !помощь, !анкета Виктор 123456).
"""

import os
import re
import json
import random
import threading
import time
import calendar

import requests

TOKEN = (os.environ.get("BOT_TOKEN") or os.environ.get("TOKEN") or "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
GROUP_ID = int(os.environ.get("GROUP_ID") or 0)
API_URL = "https://api.vk.com/method/"
API_V = "5.199"
MSK_OFFSET = 3 * 3600
PREFIX = "!"                      # префикс команд — обязателен для всех
PENALTY_ODOMETER = 10             # штраф за минус пробега
PENALTY_BURN = 10                 # штраф за сгоревшую путёвку (>24 ч открытой)
EVENT_HISTORY_HOURS = 24          # сколько часов хранить итоги ивента
DEFAULT_PAUSE_HOURS = 3           # пауза выдачи путёвок по умолчанию
TRIP_LIFETIME = 24 * 3600         # путёвка сгорает, если открыта дольше суток
TO_LIMIT_KM = 500                 # порог пробега с последнего ТО
BURN_CHECK_EVERY = 60             # период фоновой проверки, сек


def _env_ids(name):
    out = []
    for chunk in (os.environ.get(name) or "").replace(" ", "").split(","):
        if chunk.isdigit():
            out.append(int(chunk))
    return out

ADMIN_IDS = _env_ids("ADMINS")
SUPER_IDS = _env_ids("SUPERADMINS") or _env_ids("SUPER_ADMIN")

DATA_FILE = os.path.join(DATA_DIR, "db.json")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()
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
    "• !гараж — весь парк: ID, марка, номер, статус\n"
    "• !история Т740ТТ77 — журнал рейсов машины\n"
    "• !за сегодня — пробег бригады за 24 часа\n"
    "• !рейтинг — топ водителей\n"
    "• !водители — кто в бригаде\n"
    "• !ивент — активный ивент и итоги\n"
    "• !бинды — бинды чата организации (F8)\n\n"
    "🛠 Админ-команды (вместо номера/ника можно ID):\n"
    "• !авто МАРКА НОМЕР пробег — добавить авто\n"
    "• !пробег НОМЕР|ID значение — правка одометра\n"
    "• !удалить авто НОМЕР|ID — убрать авто из парка\n"
    "• !профиль Ник|ID — профиль водителя\n"
    "• !ивент Название 26.11-05.12 фонд — создать ивент\n"
    "• !стоп путевки [часы] — пауза выдачи (по умолчанию 3 ч)\n"
    "• !пуск путевки — возобновить выдачу\n"
    "• !то НОМЕР|ID — сброс счётчика ТО после обслуживания\n"
    "• !штраф Ник [баллы] — штраф (по умолчанию 10)\n"
    "• !сброс рейтинга Ник — вернуть рейтинг к 100\n"
    "• !удалить Ник — удалить регистрацию водителя\n"
    "• !объявление текст — рассылка всем в личку\n\n"
    "👑 Только суперадмин:\n"
    "• !закрепить НОМЕР|ID Ник|ID — закрепить авто за водителем\n"
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


def call(method, **params):
    params["access_token"] = TOKEN
    params["v"] = API_V
    try:
        r = requests.post(API_URL + method, data=params, timeout=15)
        data = r.json()
    except Exception as e:
        print("network error in {}: {}".format(method, e))
        return None
    if "error" in data:
        print("VK error in {}: {}".format(method, data["error"].get("error_msg")))
        return None
    return data.get("response")


def norm_plate(s):
    return s.strip().upper().translate(LAT2CYR)


def fmt_dt(ts):
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(ts + MSK_OFFSET)) + " МСК"


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


def load_db():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or "drivers" not in data:
                raise ValueError
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        data = {}
    if not isinstance(data.get("drivers"), dict):
        data["drivers"] = {}
    if not isinstance(data.get("cars"), dict):
        data["cars"] = {}
        for model, plate in CARS_INIT:
            data["cars"][plate] = _new_car(model, plate)
    if not isinstance(data.get("chat"), dict):
        data["chat"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    for plate, car in data["cars"].items():
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
        if not isinstance(car.get("journal"), list):
            car["journal"] = []
        if not car.get("car_id"):
            data["meta"]["car_seq"] = int(data["meta"].get("car_seq", 0)) + 1
            car["car_id"] = int(data["meta"]["car_seq"])
    for vk, d in data["drivers"].items():
        if not isinstance(d, dict):
            data["drivers"][vk] = _new_driver()
            d = data["drivers"][vk]
        d.setdefault("name", "")
        d.setdefault("account", "")
        d.setdefault("total_km", 0)
        d.setdefault("trips", 0)
        d.setdefault("rating", 100)
        d.setdefault("penalties", 0)
        d.setdefault("open_trip", None)
        d.setdefault("assigned_plate", None)
        for k in ("history", "log"):
            if not isinstance(d.get(k), list):
                d[k] = []
        if not d.get("id"):
            data["meta"]["drv_seq"] = int(data["meta"].get("drv_seq", 0)) + 1
            d["id"] = int(data["meta"]["drv_seq"])
    if data.get("pause") is not None and not isinstance(data["pause"], dict):
        data["pause"] = None
    return data


def _new_car(model, plate):
    return {"model": model, "mileage": 0, "status": "стоит", "driver_id": 0,
            "driver_name": "", "assigned": None, "assigned_name": "",
            "service_km": 0, "journal": []}


def _new_driver():
    return {"name": "", "account": "", "id": 0, "total_km": 0, "trips": 0,
            "rating": 100, "penalties": 0, "open_trip": None,
            "history": [], "log": [], "assigned_plate": None}


def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_driver(db, user_id):
    d = db["drivers"].setdefault(str(user_id), _new_driver())
    if not d.get("id"):
        db["meta"]["drv_seq"] = int(db["meta"].get("drv_seq", 0)) + 1
        d["id"] = int(db["meta"]["drv_seq"])
    return d


def registered(d):
    return bool(d.get("name")) and bool(d.get("account"))


def is_super(uid):
    return uid in SUPER_IDS


def is_admin(uid):
    return is_super(uid) or uid in ADMIN_IDS


def send(peer_id, text):
    resp = call("messages.send", peer_id=peer_id, message=text,
                random_id=random.randint(0, 2 ** 31))
    if resp is None:
        print("send error to peer", peer_id)


def find_driver_by_name(db, nick):
    nick = nick.strip().lower()
    for uid, drv in db["drivers"].items():
        if (drv.get("name") or "").lower() == nick:
            return uid, drv
    return None, None


def find_driver(db, token):
    token = str(token).strip()
    if token.isdigit():
        did = int(token)
        for uid, drv in db["drivers"].items():
            if drv.get("id") == did:
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


# ---------------- Путёвки ----------------

def issue_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not registered(d):
        return REG_HELP
    pause = db.get("pause")
    if pause and pause.get("until", 0) > time.time():
        return ("⏸ ВЫДАЧА ПУТЁВОК ПРИОСТАНОВЛЕНА\n" + LINE + "\n"
                "Идёт контрольная сверка пробегов.\n"
                "🕒 Выдача возобновится: {}\n\n"
                "Уже открытые путёвки остаются рабочими.").format(
            fmt_dt(pause["until"]))
    if d.get("open_trip"):
        return ("❌ У вас уже открыта путёвка на {} ({}).\n\n"
                "Сначала закройте её:\n"
                "!сдать путевку {} <пробег>").format(
            d["open_trip"]["plate"], plate, d["open_trip"]["plate"])
    if car["status"] == "в рейсе" and car["driver_id"] != user_id:
        return ("❌ {} ({}) сейчас в рейсе у {}.\n\n"
                "🚛 Свободные машины — команда: !гараж").format(
            plate, car["model"], car["driver_name"] or "другого водителя")
    if car.get("assigned") and str(car["assigned"]) != str(user_id):
        return ("🔒 {} ({}) закреплён за {}. Выписать путёвку\n"
                "на него может только он.").format(
            plate, car["model"], car["assigned_name"] or "закреплённым водителем")
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
        car["model"], plate, km, fmt_dt(now), d["name"])


def close_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not registered(d):
        return REG_HELP
    trip = d.get("open_trip")
    if not trip:
        return ("❌ Открытой путёвки нет.\n\n"
                "Сначала: !выписать путевку НОМЕР пробег")
    if trip["plate"] != plate:
        return ("❌ У вас открыта путёвка на {}, а не на {}.\n\n"
                "Закройте её: !сдать путевку {} <пробег>").format(
            trip["plate"], plate, trip["plate"])
    if km < trip["start"]:
        d["rating"] = max(0, d["rating"] - PENALTY_ODOMETER)
        d["penalties"] = d.get("penalties", 0) + 1
        bump_week_penalty(db, d)
        return ("⚠️ ОШИБКА В ПОКАЗАНИЯХ ОДОМЕТРА\n" + LINE + "\n"
                "📉 Возврат: {} км, выезд был: {} км — минус {} км!\n"
                "💰 Штраф: −{} к рейтингу (теперь {})\n" + LINE + "\n"
                "🟢 Путёвка остаётся открытой: {} со {} км.\n"
                "🔁 Проверьте одометр и повторите:\n"
                "!сдать путевку {} <пробег>").format(
            km, trip["start"], trip["start"] - km, PENALTY_ODOMETER,
            d["rating"], plate, trip["start"], plate)
    distance = km - trip["start"]
    now = time.time()
    d["open_trip"] = None
    d["total_km"] += distance
    d["trips"] += 1
    d["rating"] = min(100, d["rating"] + 1)
    d["history"] = (d.get("history") or [])[-49:]
    d["history"].append({"km": distance, "plate": plate, "ts": now,
                         "date": fmt_dt(now)})
    d["log"] = (d.get("log") or [])[-299:]
    d["log"].append({"ts": now, "plate": plate, "km": distance})
    to_warn = False
    if car:
        car["mileage"] = km
        car["status"] = "стоит"
        car["driver_id"] = 0
        car["driver_name"] = ""
        car["service_km"] = car.get("service_km", 0) + distance
        car["journal"] = (car.get("journal") or [])[-199:]
        car["journal"].append({"ts": now, "name": d["name"], "km": distance,
                               "burned": False})
        if car["service_km"] >= TO_LIMIT_KM:
            to_warn = True
    out = ("🔴 ПУТЁВКА ЗАКРЫТА\n" + LINE + "\n"
           "🚛 Авто: {} | {}\n"
           "📏 Пройдено: {} км\n"
           "📊 Одометр авто: {} км\n"
           "🕒 Закрыта: {}\n" + LINE + "\n"
           "👤 {}: всего {} км, рейсов {} (+1 к рейтингу ⭐{})").format(
        car["model"] if car else "—", plate, fmt_km(distance),
        fmt_km(car["mileage"]) if car else "?", fmt_dt(now),
        d["name"], fmt_km(d["total_km"]), d["trips"], d["rating"])
    if to_warn:
        out += ("\n\n🔧 ПОРА НА ТО: {} {} — с последнего обслуживания\n"
                "прошло {} км. После ремонта: !то {}").format(
            car["model"], plate, fmt_km(car["service_km"]), plate)
    return out


def bump_week_penalty(db, d):
    pw = db["meta"].setdefault("pen_week", {})
    pw[d["name"]] = pw.get(d["name"], 0) + 1


# ---------------- Сгорание путёвок ----------------

def check_burned(db, now=None):
    now = now or time.time()
    chat_id = db["chat"].get("chat_id")
    chat_msgs = []
    for uid, d in db["drivers"].items():
        trip = d.get("open_trip")
        if not trip or now - trip["ts"] < TRIP_LIFETIME:
            continue
        plate = trip["plate"]
        car = db["cars"].get(plate)
        d["open_trip"] = None
        d["rating"] = max(0, d["rating"] - PENALTY_BURN)
        d["penalties"] = d.get("penalties", 0) + 1
        bump_week_penalty(db, d)
        if car:
            car["status"] = "стоит"
            car["driver_id"] = 0
            car["driver_name"] = ""
            car["journal"] = (car.get("journal") or [])[-199:]
            car["journal"].append({"ts": now, "name": d["name"], "km": 0,
                                   "burned": True})
        msg = ("⏰ ПУТЁВКА СГОРЕЛА\n" + LINE + "\n"
               "🚛 Авто: {} | {}\n"
               "🕒 Выдана: {}\n"
               "💰 Штраф: −{} к рейтингу (теперь ⭐{})\n" + LINE + "\n"
               "Путёвка была открыта больше суток.\n"
               "Выпишите новую: !выписать путевку НОМЕР пробег").format(
            car["model"] if car else "—", plate, fmt_dt(trip["ts"]),
            PENALTY_BURN, d["rating"])
        send(int(uid), msg)
        if chat_id:
            chat_msgs.append("⏰ Путёвка {} у водителя {} сгорела "
                             "(штраф −{} к рейтингу).".format(
                plate, d["name"], PENALTY_BURN))
    return chat_msgs


# ---------------- Ивенты ----------------

def parse_event_dates(s):
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})$", s)
    if not m:
        return None, None
    y = time.gmtime(time.time() + MSK_OFFSET).tm_year
    d1, m1, d2, m2 = map(int, m.groups())
    try:
        start = calendar.timegm((y, m1, d1, 0, 0, 0)) - MSK_OFFSET
        end = calendar.timegm((y, m2, d2, 23, 59, 0)) - MSK_OFFSET
    except (ValueError, OverflowError):
        return None, None
    if m2 < m1:
        end = calendar.timegm((y + 1, m2, d2, 23, 59, 0)) - MSK_OFFSET
    if end <= start:
        return None, None
    return start, end


def event_km_in_window(db, start, end):
    sums = {}
    for uid, d in db["drivers"].items():
        s = 0
        for rec in d.get("log") or []:
            if start <= rec.get("ts", 0) <= end:
                s += rec.get("km", 0)
        if s:
            sums[d.get("name") or "без имени"] = s
    return sorted(sums.items(), key=lambda x: -x[1])


def maybe_finalize_events(db, now=None):
    now = now or time.time()
    chat_id = db["chat"].get("chat_id")
    msgs = []
    for ev in db["events"]:
        if ev.get("finalized"):
            continue
        if now >= ev["end"]:
            top = event_km_in_window(db, ev["start"], ev["end"])[:3]
            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, (name, km) in enumerate(top):
                lines.append("{} {} — {} км".format(medals[i], name, fmt_km(km)))
            if not lines:
                lines.append("Никто не закрыл рейсов за период 😢")
            ev["finalized"] = True
            ev["fin_ts"] = now
            ev["results"] = top
            msg = ("🏁 ИТОГИ ИВЕНТА «{}»\n" + LINE + "\n"
                   "🏆 Топ-3 по пробегу:\n{}\n"
                   "💰 Призовой фонд: {} ₽\n" + LINE + "\n"
                   "📌 Итоги доступны 24 часа (команда: !ивент),\n"
                   "потом история ивента очистится.").format(
                ev["name"], "\n".join(lines), fmt_km(ev["fund"]))
            if chat_id:
                msgs.append(msg)
    db["events"] = [e for e in db["events"]
                    if not (e.get("finalized") and
                            now - e.get("fin_ts", now) > EVENT_HISTORY_HOURS * 3600)]
    return msgs


# ---------------- Недельный отчёт ----------------

def maybe_weekly_report(db, now=None):
    now = now or time.time()
    if msk_weekday(now) != 6 or msk_hour(now) < 12:
        return None
    key = "weekly_{}".format(time.strftime("%G-W%V", time.gmtime(now + MSK_OFFSET)))
    if db["meta"].get(key):
        return None
    week_ago = now - 7 * 86400
    sums = {}
    for uid, d in db["drivers"].items():
        s = 0
        for rec in d.get("log") or []:
            if rec.get("ts", 0) >= week_ago:
                s += rec.get("km", 0)
        if s:
            sums[d.get("name") or "без имени"] = s
    top = sorted(sums.items(), key=lambda x: -x[1])[:5]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = ["{} {} — {} км".format(medals[i], n, fmt_km(km))
             for i, (n, km) in enumerate(top)] or ["Пробега за неделю нет 😢"]
    pw = db["meta"].get("pen_week") or {}
    plines = []
    for name, cnt in sorted(pw.items(), key=lambda x: -x[1]):
        plines.append("• {} — {} штраф(ов), рейтинг просел".format(name, cnt))
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


# ---------------- Команды ----------------

def cmd_help(db, uid, args):
    return HELP


def cmd_anketa(db, uid, args):
    if not args:
        return REG_HELP
    account = args[-1]
    name = " ".join(args[:-1]).strip()
    if len(args) < 2 or not account.isdigit() or len(account) != 6:
        return ("❌ Нужны ровно два данных: ник и 6-значный счёт.\n\n" + REG_HELP)
    if not re.match(r"^[A-Za-zА-Яа-яЁё0-9_\- ]+$", name):
        return ("❌ Ник содержит недопустимые символы.\n\n" + REG_HELP)
    nick_uid, _ = find_driver_by_name(db, name)
    if nick_uid and nick_uid != str(uid):
        return ("❌ Ник «{}» уже занят другим водителем.\n\n"
                "Укажите свой игровой ник (как в игре).\n\n" + REG_HELP).format(name)
    d = get_driver(db, uid)
    was = registered(d)
    d["name"], d["account"] = name, account
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
    return ("🆔 VK ID: {}\n\n"
            "Вы ещё не зарегистрированы.\n\n" + REG_HELP).format(uid)


def cmd_issue(db, uid, args):
    if len(args) < 3 or args[0].lower() != "путевку":
        return ("❌ Формат: !выписать путевку НОМЕР пробег\n"
                "Пример: !выписать путевку Т740ТТ77 100\n\n"
                "Номера машин: !гараж")
    plate = norm_plate(args[1])
    if not args[2].isdigit():
        return "❌ Пробег должен быть числом. Пример: !выписать путевку Т740ТТ77 100"
    d = get_driver(db, uid)
    return issue_trip(db, d, uid, plate, int(args[2]))


def cmd_close(db, uid, args):
    if len(args) < 3 or args[0].lower() != "путевку":
        return ("❌ Формат: !сдать путевку НОМЕР пробег\n"
                "Пример: !сдать путевку Т740ТТ77 250")
    plate = norm_plate(args[1])
    if not args[2].isdigit():
        return "❌ Пробег должен быть числом. Пример: !сдать путевку Т740ТТ77 250"
    d = get_driver(db, uid)
    return close_trip(db, d, uid, plate, int(args[2]))


def cmd_my_trips(db, uid, args):
    d = get_driver(db, uid)
    if not registered(d):
        return REG_HELP
    h = d.get("history") or []
    if not h:
        return ("📭 Рейсов пока нет.\n\n"
                "Первая путёвка: !выписать путевку НОМЕР пробег")
    lines = []
    for rec in h[-10:]:
        lines.append("• {} | {} | {} км".format(rec["date"], rec["plate"], rec["km"]))
    return ("📜 ВАШИ РЕЙСЫ (последние 10 из {})\n" + LINE + "\n{}").format(
        len(h), "\n".join(lines))


def cmd_profile(db, uid, args, admin=False):
    if args:
        if not is_admin(uid):
            return "❌ Профиль другого водителя может смотреть только админ."
        tuid, td = find_driver(db, " ".join(args))
        if not td:
            return "❌ Водитель «{}» не найден. Список: !водители".format(" ".join(args))
        d, label = td, td.get("name") or "—"
    else:
        d, label = get_driver(db, uid), None
        if not registered(d):
            return REG_HELP
    trip = d.get("open_trip")
    tline = ""
    if trip:
        left = TRIP_LIFETIME - (time.time() - trip["ts"])
        tline = "🟢 Открыта путёвка: {} (сгорит через {})".format(
            trip["plate"], fmt_left(left))
    lines = ["👤 ПРОФИЛЬ: {}".format(label if label else d.get("name") or "—")]
    if label:
        lines.append("🆔 ID водителя: {}".format(d.get("id")))
    lines += [LINE,
              "💳 Счёт: {}".format(d.get("account") or "—"),
              "⭐ Рейтинг: {}".format(d.get("rating", 100)),
              "🛣 Всего км: {}".format(fmt_km(d.get("total_km", 0))),
              "🚚 Рейсов: {}".format(d.get("trips", 0)),
              "⚠️ Штрафов: {}".format(d.get("penalties", 0))]
    if tline:
        lines.append(tline)
    day_ago = time.time() - 86400
    day = {}
    for rec in d.get("log") or []:
        if rec.get("ts", 0) >= day_ago:
            day[rec["plate"]] = day.get(rec["plate"], 0) + rec["km"]
    if day:
        lines.append(LINE)
        lines.append("📅 За сутки:")
        for p, km in day.items():
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
            status = "🔵 в рейсе ({})".format(c["driver_name"] or "—")
        lock = ""
        if c.get("assigned"):
            lock = " 🔒 закреплён за {}".format(c.get("assigned_name") or "—")
        lines.append("• {} | {} | {} км | {}{}".format(
            c["car_id"], c["model"], fmt_km(c["mileage"]), status, lock))
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
    lines = ["📜 ИСТОРИЯ: {} | {}\n".format(car["model"], plate)]
    for rec in j[-10:]:
        if rec.get("burned"):
            lines.append("• {} | {} | путёвка сгорела ⏰".format(
                fmt_dt(rec["ts"]), rec.get("name") or "—"))
        else:
            lines.append("• {} | {} | {} км".format(
                fmt_dt(rec["ts"]), rec.get("name") or "—", fmt_km(rec.get("km", 0))))
    return "\n".join(lines) + "\n\nВсего записей: {}".format(len(j))


def cmd_today(db, uid, args):
    day_ago = time.time() - 86400
    total_b = 0
    total_p = 0
    lines = []
    for uid2, d in db["drivers"].items():
        s = 0
        for rec in d.get("log") or []:
            if rec.get("ts", 0) >= day_ago:
                s += rec["km"]
        if s:
            total_b += s
            lines.append("• {} — {} км".format(d.get("name") or "—", fmt_km(s)))
    park = sum(c.get("mileage", 0) for c in db["cars"].values())
    out = ("📅 ПРОБЕГ ЗА СУТКИ\n" + LINE + "\n")
    out += "\n".join(lines) if lines else "За сутки рейсов не было 😴"
    out += ("\n" + LINE + "\n"
            "👥 Бригада: {} км\n"
            "🚚 Парк всего: {} км").format(fmt_km(total_b), fmt_km(park))
    return out


def cmd_rating(db, uid, args):
    regs = [(d.get("rating", 100), d.get("name") or "—", d.get("total_km", 0))
            for d in db["drivers"].values() if registered(d)]
    if not regs:
        return "В бригаде пока нет зарегистрированных водителей."
    regs.sort(reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = ["⭐ РЕЙТИНГ ВОДИТЕЛЕЙ", LINE]
    for i, (r, n, km) in enumerate(regs[:10]):
        mark = medals[i] if i < 3 else "{}.".format(i + 1)
        lines.append("{} {} — ⭐{} | {} км".format(mark, n, r, fmt_km(km)))
    return "\n".join(lines)


def cmd_drivers(db, uid, args):
    regs = [(d.get("id"), d.get("name"), d.get("rating", 100), d.get("open_trip"))
            for d in db["drivers"].values() if registered(d)]
    if not regs:
        return "В бригаде пока нет зарегистрированных водителей.\n\n" + REG_HELP
    lines = ["👥 ВОДИТЕЛИ БРИГАДЫ ({})".format(len(regs)), LINE]
    for did, name, rating, trip in sorted(regs, key=lambda x: x[0]):
        mark = "🟢" if trip else ""
        lines.append("• {} | {} | ⭐{} {}".format(did, name, rating, mark))
    return "\n".join(lines)


def cmd_event_view(db, uid, args):
    db["events"] = db.get("events") or []
    now = time.time()
    maybe_finalize_events(db, now)
    for ev in db["events"]:
        if not ev.get("finalized") and now < ev["end"]:
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
                fmt_km(ev["fund"]), fmt_left(ev["end"] - now), "\n".join(lines))
    for ev in db["events"]:
        if ev.get("finalized"):
            top = ev.get("results") or []
            medals = ["🥇", "🥈", "🥉"]
            lines = ["{} {} — {} км".format(medals[i], n, fmt_km(km))
                     for i, (n, km) in enumerate(top)] or ["Итогов нет."]
            return ("🏁 ИТОГИ ИВЕНТА «{}»\n" + LINE + "\n"
                    "🏆 Топ-3:\n{}\n"
                    "💰 Призовой фонд: {} ₽\n" + LINE + "\n"
                    "⏰ Итоги хранятся ещё {}. Потом история\n"
                    "очистится — активных ивентов не будет.").format(
                ev["name"], "\n".join(lines), fmt_km(ev["fund"]),
                fmt_left(EVENT_HISTORY_HOURS * 3600 - (now - ev.get("fin_ts", now))))
    return "😴 Активных ивентов нет. Ждём следующий — совсем скоро! 🏁"


def fmt_date(ts):
    return time.strftime("%d.%m.%Y", time.gmtime(ts + MSK_OFFSET))


def cmd_event_create(db, uid, args):
    if not is_admin(uid):
        return "❌ Команда только для админов."
    if len(args) < 3:
        return ("❌ Формат: !ивент Название 26.11-05.12 100000\n"
                "Пример: !ивент Зимний марафон 26.11-05.12 100000")
    fund = args[-1].replace(" ", "")
    if not fund.isdigit():
        return "❌ Призовой фонд должен быть числом. Пример: !ивент Название 26.11-05.12 100000"
    start, end = parse_event_dates(args[-2])
    if start is None:
        return ("❌ Не понял сроки. Формат дат: 26.11-05.12\n"
                "Пример: !ивент Зимний марафон 26.11-05.12 100000")
    active = [e for e in db["events"] if not e.get("finalized")]
    if active:
        return "❌ Уже идёт ивент «{}» — сначала дождитесь его завершения.".format(
            active[0]["name"])
    name = " ".join(args[:-2])
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


# ---------------- Админ-команды ----------------

def cmd_add_car(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if len(args) < 3:
        return ("❌ Формат: !авто МАРКА НОМЕР пробег\n"
                "Пример: !авто КАМАЗ 6520 Т940ТА77 150000")
    km = args[-1]
    if not km.isdigit():
        return "❌ Пробег должен быть числом. Пример: !авто КАМАЗ Т940ТА77 150000"
    plate = norm_plate(args[-2])
    if plate in db["cars"]:
        return "❌ {} уже есть в парке.".format(plate)
    model = " ".join(args[:-2])
    db["cars"][plate] = _new_car(model, plate)
    db["cars"][plate]["mileage"] = int(km)
    db["meta"]["car_seq"] = int(db["meta"].get("car_seq", 0)) + 1
    db["cars"][plate]["car_id"] = int(db["meta"]["car_seq"])
    return ("✅ АВТО ДОБАВЛЕНО\n" + LINE + "\n"
            "🚛 {} | {}\n"
            "📊 Одометр: {} км\n"
            "🆔 ID авто: {}").format(model, plate, fmt_km(int(km)),
                                    db["cars"][plate]["car_id"])


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
    return "✅ Одометр {} ({}): {} км".format(plate, car["model"], fmt_km(car["mileage"]))


def cmd_del_car(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return "❌ Формат: !удалить авто НОМЕР|ID"
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    if car.get("status") == "в рейсе":
        return ("❌ {} сейчас в рейсе у {} — сначала пусть закроет путёвку.").format(
            plate, car.get("driver_name") or "водителем")
    del db["cars"][plate]
    return "✅ {} удалён из парка.".format(plate)


def cmd_stop(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    hours = DEFAULT_PAUSE_HOURS
    if args and args[0].isdigit():
        hours = int(args[0])
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
    if not db.get("pause"):
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
        car["model"], plate, TO_LIMIT_KM)


def cmd_penalty(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return ("❌ Формат: !штраф Ник [баллы]\n"
                "Пример: !штраф Виктор 15 (по умолчанию 10)")
    tuid, td = find_driver(db, args[0])
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(args[0])
    pts = 10
    if len(args) > 1 and args[1].lstrip("-").isdigit():
        pts = abs(int(args[1]))
    td["rating"] = max(0, td.get("rating", 100) - pts)
    td["penalties"] = td.get("penalties", 0) + 1
    bump_week_penalty(db, td)
    return ("⚠️ ШТРАФ\n"
            "👤 {}: −{} к рейтингу (теперь ⭐{}).\n"
            "Работайте аккуратнее! 🚚").format(td["name"], pts, td["rating"])


def cmd_reset_rating(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return "❌ Формат: !сброс рейтинга Ник"
    tuid, td = find_driver(db, " ".join(args))
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(" ".join(args))
    td["rating"] = 100
    return "✅ Рейтинг {} сброшен до 100.".format(td["name"])


def cmd_del_driver(db, uid, args):
    if not is_admin(uid):
        return admin_only()
    if not args:
        return "❌ Формат: !удалить Ник — регистрация и статистика обнулятся"
    tuid, td = find_driver(db, " ".join(args))
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(" ".join(args))
    if td.get("open_trip"):
        car = db["cars"].get(td["open_trip"]["plate"])
        if car:
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
            "Для повторной регистрации: !анкета Ник 123456").format(" ".join(args))


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
    chat_id = db["chat"].get("chat_id")
    delivered, failed = [], []
    for vk, d in db["drivers"].items():
        if not registered(d):
            continue
        msg = ("📢 ОБЪЯВЛЕНИЕ\n" + LINE + "\n{}\n" + LINE + "\n"
               "С уважением, руководство «Грузовой поток» 🚚").format(text)
        try:
            resp = call("messages.send", peer_id=int(vk), message=msg,
                        random_id=random.randint(0, 2 ** 31))
        except Exception:
            resp = None
        if resp is None:
            failed.append(d["name"])
        else:
            delivered.append(d["name"])
    out = ("📢 Объявление разослано в личку.\n"
           "✅ Доставлено: {} из {}.").format(len(delivered),
                                             len(delivered) + len(failed))
    if delivered:
        out += "\nПолучили: {}".format(", ".join(delivered))
    if failed:
        out += ("\n❌ Не дошло (продублируйте лично): {}").format(
            ", ".join(failed))
    return out


def admin_only():
    return ("❌ Команда доступна только админам бригады.\n"
            "Вопросы к руководству: админы решают, дальше бот долбит по цифрам 🚛")


# ---------------- Суперадмин ----------------

def cmd_assign(db, uid, args):
    if not is_super(uid):
        return ("❌ Закреплять машины может только суперадмин —\n"
                "закрепление идёт от ролей в игре.")
    if len(args) < 2:
        return ("❌ Формат: !закрепить НОМЕР|ID Ник|ID\n"
                "Пример: !закрепить Т740ТТ77 Виктор")
    plate, car = find_car(db, args[0])
    if not car:
        return "❌ Авто «{}» не найдено. Весь парк: !гараж".format(args[0])
    if car.get("status") == "в рейсе":
        return "❌ {} сейчас в рейсе — дождитесь закрытия путёвки.".format(plate)
    tuid, td = find_driver(db, args[1])
    if not td:
        return "❌ Водитель «{}» не найден. Список: !водители".format(args[1])
    if not registered(td):
        return "❌ {} ещё не зарегистрирован: !анкета Ник 123456".format(args[1])
    for p, c in db["cars"].items():
        if c.get("assigned") and str(c["assigned"]) == str(tuid):
            c["assigned"], c["assigned_name"] = None, ""
    car["assigned"] = str(tuid)
    car["assigned_name"] = td["name"]
    td["assigned_plate"] = plate
    return ("🔒 АВТО ЗАКРЕПЛЕНО\n" + LINE + "\n"
            "🚛 {} | {}\n"
            "👤 Водитель: {}\n" + LINE + "\n"
            "На это авто путёвку выписывает только он.")


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
    d_uid, td = find_driver_by_name(db, car.get("assigned_name") or "")
    if td:
        td["assigned_plate"] = None
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
    db["drivers"], db["cars"], db["chat"], db["events"], db["meta"] = {}, {}, {}, {}, {}
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


# ---------------- Диспетчер ----------------

def handle(db, user_id, text, peer_id=None):
    raw = (text or "").strip()
    if not raw:
        return None
    if not raw.startswith(PREFIX):
        if raw.lower() in ("начать", "start"):
            return WELCOME
        if peer_id and peer_id == user_id:  # личка: мягкая подсказка
            return HINT_NOPREFIX
        return None  # в чате молчим
    body = raw[len(PREFIX):].strip()
    if not body:
        return None
    parts = body.split()
    cmd = parts[0].lower()
    args = parts[1:]
    db["chat"] = db.get("chat") or {}
    if peer_id and peer_id > 2_000_000_000 and db["chat"].get("chat_id") != peer_id:
        db["chat"]["chat_id"] = peer_id  # запоминаем чат бригады для объявлений

    if cmd in ("помощь", "help"):
        return cmd_help(db, user_id, args)
    if cmd == "анкета":
        return cmd_anketa(db, user_id, args)
    if cmd == "мой" and args[:1] == ["id"]:
        return cmd_my_id(db, user_id, args[1:])
    if cmd == "выписать":
        return cmd_issue(db, user_id, args)
    if cmd == "сдать":
        return cmd_close(db, user_id, args)
    if cmd == "путевки":
        return cmd_my_trips(db, user_id, args)
    if cmd == "профиль":
        return cmd_profile(db, user_id, args)
    if cmd == "гараж":
        return cmd_garage(db, user_id, args)
    if cmd == "история":
        return cmd_history(db, user_id, args)
    if cmd == "за" and args[:1] == ["сегодня"]:
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
    # --- админ ---
    if cmd == "авто":
        return cmd_add_car(db, user_id, args)
    if cmd == "пробег":
        return cmd_set_mileage(db, user_id, args)
    if cmd == "удалить" and args[:1] == ["авто"]:
        return cmd_del_car(db, user_id, args[1:])
    if cmd == "стоп" and args[:1] == ["путевки"]:
        return cmd_stop(db, user_id, args[1:])
    if cmd == "пуск" and args[:1] == ["путевки"]:
        return cmd_start(db, user_id, args[1:])
    if cmd == "то":
        return cmd_to(db, user_id, args)
    if cmd == "штраф":
        return cmd_penalty(db, user_id, args)
    if cmd == "сброс" and args[:1] == ["рейтинга"]:
        return cmd_reset_rating(db, user_id, args[1:])
    if cmd == "удалить":
        return cmd_del_driver(db, user_id, args)
    if cmd == "объявление":
        return cmd_announce(db, user_id, args)
    # --- суперадмин ---
    if cmd == "закрепить":
        return cmd_assign(db, user_id, args)
    if cmd == "открепить":
        return cmd_unassign(db, user_id, args)
    if cmd == "сброс" and args[:1] == ["базы"]:
        return cmd_db_reset(db, user_id, args[1:])
    return ("❌ Не знаю такой команды: «{}»\n\n"
            "Все команды: !помощь").format(cmd)


# ---------------- Фоновые задачи ----------------

def bg_loop(db):
    while True:
        try:
            with _lock:
                now = time.time()
                for m in check_burned(db, now):
                    chat_id = db["chat"].get("chat_id")
                    if chat_id:
                        send(chat_id, m)
                for m in maybe_finalize_events(db, now):
                    chat_id = db["chat"].get("chat_id")
                    if chat_id:
                        send(chat_id, m)
                rep = maybe_weekly_report(db, now)
                if rep:
                    chat_id = db["chat"].get("chat_id")
                    if chat_id:
                        send(chat_id, rep)
                save_db(db)
        except Exception as e:
            print("bg error:", e)
        time.sleep(BURN_CHECK_EVERY)


def main():
    db = load_db()
    threading.Thread(target=bg_loop, args=(db,), daemon=True).start()
    lp = call("messages.getLongPollServer", lp_version=1, need_pts=0)
    if not lp:
        print("no long poll server"); return
    key, server, ts = lp.get("key"), lp.get("server"), lp.get("ts")
    while True:
        try:
            url = "https://{}?act=a_check&key={}&ts={}&wait=25&mode=2&version=1".format(
                server, key, ts)
            r = requests.get(url, timeout=35).json()
        except Exception as e:
            print("lp error:", e)
            time.sleep(3)
            continue
        if r.get("failed"):
            if r.get("failed") == 1:
                ts = r.get("ts", ts)
            else:
                lp = call("messages.getLongPollServer", lp_version=1, need_pts=0)
                if lp:
                    key, server, ts = lp.get("key"), lp.get("server"), lp.get("ts")
            continue
        ts = r.get("ts", ts)
        for upd in r.get("updates", []):
            if upd[0] != 4:
                continue
            peer_id = upd[3]
            text = upd[5]
            if peer_id > 2_000_000_000:
                att = upd[6] if len(upd) > 6 and isinstance(upd[6], dict) else {}
                user_id = att.get("from")
            else:
                user_id = peer_id
            if not isinstance(user_id, int):
                continue
            with _lock:
                reply = handle(db, user_id, text, peer_id)
                if reply:
                    send(peer_id, reply)
                save_db(db)


if __name__ == "__main__":
    main()
