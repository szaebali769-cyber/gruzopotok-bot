# -*- coding: utf-8 -*-
"""
Грузовой поток — бот ВК: путёвки, парк, закрепление машин, водители, ивенты.
Long Poll VK API (requests). Токен: BOT_TOKEN. Админы: ADMINS (через запятую).
Суперадмины: SUPERADMINS (закрепление/открепление машин — только они).
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
CHAT_TITLE = "Грузовой поток | бригада"
MSK_OFFSET = 3 * 3600  # время в сообщениях — московское
PENALTY_ODOMETER = 10        # штраф за минус пробега (ошибка в показаниях одометра)
EVENT_HISTORY_HOURS = 24     # сколько часов хранить итоги завершённого ивента
DEFAULT_PAUSE_HOURS = 3      # пауза выдачи путёвок для контрольной сверки

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

# --- Парк автомобилей: марка, госномер (одометр заводится админом командой !пробег) ---
CARS_INIT = [
    ("КАМАЗ 54115", "Т740ТТ77"),
    ("КАМАЗ 54115", "О740ОО77"),
    ("КАМАЗ 54115", "Х740ХХ77"),
    ("КАМАЗ 54115", "У740УУ77"),
    ("КАМАЗ 54115", "В740ВВ77"),
    ("КАМАЗ 54115", "С740СС77"),
    ("КАМАЗ 54115", "М740ММ77"),
    ("КАМАЗ 5490 НЕО", "Е740ЕЕ77"),
    ("КАМАЗ 5490 НЕО", "К740КК77"),
    ("КАМАЗ 5490 НЕО", "Н740НН77"),
    ("КАМАЗ 5490 НЕО", "Р740РР77"),
    ("КАМАЗ 5490 НЕО", "А740АА77"),
    ("ЗИЛ 133540", "Х761ХХ77"),
    ("КАМАЗ 53112 Тент", "О001СТ77"),
    ("КАМАЗ 53112 Тент", "О002СТ77"),
    ("КАМАЗ 53112 Тент", "О003СТ77"),
    ("Scania R500", "А501АА77"),
    ("Scania R500", "О501ОО77"),
    ("Scania Topline R480", "А502АА77"),
    ("Volvo FH", "А601АА77"),
    ("Volvo FH", "О601ОО77"),
    ("Volvo VNL", "А701АА77"),
    ("Volvo VNL", "О701ОО77"),
    ("Mercedes-Benz Actros", "А410АА77"),
    ("Renault Magnum", "О077ТК77"),
    ("УАЗ 3909 Оборона", "В761ВВ77"),
    ("ГАЗ-3302 Тент", "О275ОО77"),
    ("ГАЗель NEXT", "А275АА77"),
    ("ГАЗ-3309 Рест", "В275ВВ77"),
    ("Mercedes-Benz Sprinter Classic 311 CDI", "С761СС77"),
]

# Похожие латинские буквы -> кириллица, чтобы номера распознавались в любой раскладке
LAT2CYR = str.maketrans("ABEKMHOPCTXY", "АВЕКМНОРСТХУ")


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
    "bind num_3 b Вся необходимая информация доступна на нашем сайте "
    "https://cargo-flow-mta-province-2-server.ru\n\n"
    "📌 Пример: bind F6 chatbox o"
)

HELP = (
    "🚚 ГРУЗОВОЙ ПОТОК — КОМАНДЫ\n" + LINE + "\n"
    "📋 Регистрация:\n"
    "• анкета Ник 123456 — игровой ник + 6-значный счёт\n"
    "• мой id — ваш личный ID в боте\n\n"
    "🛣 Путёвки:\n"
    "• выписать путевку Т740ТТ77 100 — выезд\n"
    "• сдать путевку Т740ТТ77 250 — возврат\n"
    "⚠️ Начальный пробег — не меньше одометра авто\n"
    "⚠️ Минус пробега — штраф −{} к рейтингу\n\n"
    "📊 Статистика:\n"
    "• профиль — ваши данные и пробег за сутки\n"
    "• путевки — история ваших рейсов\n"
    "• гараж — весь парк: ID, марка, номер, статус\n"
    "• за сегодня — пробег бригады за 24 часа\n"
    "• рейтинг — топ водителей\n"
    "• водители — кто в бригаде\n"
    "• ивент — активные ивенты и итоги\n"
    "• бинды — бинды чата организации (F8)\n\n"
    "💬 Чат бригады:\n"
    "• создать чат — бот создаст беседу и пришлёт ссылку\n"
    "• ссылка — прислать ссылку ещё раз\n\n"
    "🛠 Админ-команды (вместо номера можно указывать ID):\n"
    "• !авто МАРКА НОМЕР пробег — добавить авто\n"
    "• !пробег НОМЕР|ID значение — правка одометра\n"
    "• !удалить авто НОМЕР|ID — убрать авто из парка\n"
    "• !профиль Ник|ID — профиль водителя\n"
    "• !ивент Название 26.11-05.12 фонд — создать ивент\n"
    "• !стоп путевки [часы] — пауза выдачи (по умолчанию 3 ч)\n"
    "• !пуск путевки — возобновить выдачу\n"
    "• !штраф [баллы] / !сброс рейтинга / !удалить — ответом на сообщение\n"
    "• !объявление текст — рассылка всем в личку\n\n"
    "👑 Только суперадмин:\n"
    "• !закрепить НОМЕР|ID Ник|ID — закрепить авто за водителем\n"
    "• !открепить НОМЕР|ID — снять закрепление\n\n"
    "🔒 На закреплённое авто путёвку выписывает только его водитель"
).format(PENALTY_ODOMETER)

WELCOME = (
    "👋 Всем привет! Я бот учёта рейсов «Грузовой поток».\n\n"
    "Чтобы получать путёвки, зарегистрируйтесь:\n"
    "📋 анкета Ник 123456\n"
    "(игровой ник + 6-значный номер счёта для премий)\n\n"
    "🚚 Полный список команд: помощь"
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
        cars = {}
        for model, plate in CARS_INIT:
            cars[plate] = {"model": model, "mileage": 0, "status": "стоит",
                           "driver_id": 0, "driver_name": ""}
        data["cars"] = cars
    if not isinstance(data.get("chat"), dict):
        data["chat"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    if not isinstance(data.get("ids"), dict):
        data["ids"] = {}

    ids = data["ids"]
    # статические ID автомобилей
    if not isinstance(ids.get("car"), int):
        ids["car"] = 0
    for plate, car in data["cars"].items():
        if not car.get("cid"):
            ids["car"] += 1
            car["cid"] = ids["car"]
        car.setdefault("assigned_id", 0)
        car.setdefault("assigned_name", "")
    # статические ID водителей
    if not isinstance(ids.get("driver"), int):
        ids["driver"] = 0
    for uid, drv in data["drivers"].items():
        if not drv.get("did"):
            ids["driver"] += 1
            drv["did"] = ids["driver"]
    if data.get("fleet_pause") is not None and not isinstance(data["fleet_pause"], dict):
        data["fleet_pause"] = None
    return data


def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_driver(db, user_id):
    d = db["drivers"].setdefault(str(user_id), {
        "name": "",
        "account": "",
        "did": 0,
        "total_km": 0,
        "trips": 0,
        "rating": 100,
        "open_trip": None,
        "history": [],
        "log": [],
    })
    for k, v in (("history", []), ("log", [])):
        if not isinstance(d.get(k), list):
            d[k] = v
    return d


def registered(d):
    return bool(d.get("name")) and bool(d.get("account"))


def is_super(uid):
    return uid in SUPER_IDS


def is_admin(uid):
    return is_super(uid) or uid in ADMIN_IDS


def send(peer_id, text):
    resp = call("messages.send",
                peer_id=peer_id,
                message=text,
                random_id=random.randint(0, 2 ** 31))
    if resp is None:
        print("send error to peer", peer_id)


def reg_error():
    return ("❌ Путёвки доступны только зарегистрированным водителям.\n\n"
            "📋 Зарегистрируйтесь:\n"
            "анкета Ник 123456\n"
            "(игровой ник + 6-значный номер счёта)")


# ---------------- Поиск по ID / номеру / нику ----------------

def find_driver_by_name(db, nick):
    nick = nick.strip().lower()
    for uid, drv in db["drivers"].items():
        if (drv.get("name") or "").lower() == nick:
            return uid, drv
    return None, None


def find_driver(db, token):
    token = token.strip()
    if token.isdigit():
        did = int(token)
        for uid, drv in db["drivers"].items():
            if drv.get("did") == did:
                return uid, drv
        if token in db["drivers"]:
            return token, db["drivers"][token]
        return None, None
    return find_driver_by_name(db, token)


def find_car(db, token):
    token = token.strip()
    if token.isdigit():
        cid = int(token)
        for plate, car in db["cars"].items():
            if car.get("cid") == cid:
                return plate, car
        return None, None
    plate = norm_plate(token)
    if plate in db["cars"]:
        return plate, db["cars"][plate]
    return None, None


# ---------------- Путёвки ----------------

def issue_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not car:
        return ("❌ Авто с номером {} нет в парке.\n\n"
                "🔍 Проверьте номер — вся техника здесь: гараж").format(plate)
    if not registered(d):
        return reg_error()
    pause = db.get("fleet_pause")
    if pause and pause.get("until", 0) > time.time():
        return ("⛔ ВЫДАЧА ПУТЁВОК ВРЕМЕННО ПРИОСТАНОВЛЕНА\n" + LINE + "\n"
                "⏱ Идёт контрольная сверка пробегов.\n"
                "🕘 Возобновление: {}\n"
                "Уже открытые путёвки работают как обычно.").format(fmt_dt(pause["until"]))
    if d.get("open_trip"):
        prev = db["cars"].get(d["open_trip"]["plate"], {})
        return ("❌ У вас уже открыта путёвка на {} ({}).\n\n"
                "Сначала закройте её:\n"
                "сдать путевку {} <пробег>").format(
            d["open_trip"]["plate"], prev.get("model", ""), d["open_trip"]["plate"])
    if car.get("assigned_id") and car["assigned_id"] != user_id:
        return ("❌ {} ({}) закреплён за {}. Выписать путёвку на него\n"
                "может только закреплённый водитель.").format(
            plate, car["model"], car["assigned_name"] or "водителем")
    if car["status"] == "в рейсе" and car["driver_id"] != user_id:
        return ("❌ {} ({}) сейчас в рейсе у {}.\n\n"
                "🚛 Свободные машины — команда: гараж").format(
            plate, car["model"], car["driver_name"] or "другого водителя")
    if km < car["mileage"]:
        return ("❌ Начальный пробег ({}) меньше одометра авто {} ({} км).\n\n"
                "Пробег на выезде не может быть меньше последнего показания.\n"
                "Укажите число не меньше {}.").format(
            km, plate, fmt_km(car["mileage"]), fmt_km(car["mileage"]))
    now = time.time()
    d["open_trip"] = {"plate": plate, "start": km, "ts": now}
    car["status"] = "в рейсе"
    car["driver_id"] = user_id
    car["driver_name"] = d["name"]
    return ("🟢 ПУТЁВКА ВЫДАНА\n" + LINE + "\n"
            "🚛 Авто: {} / {}\n"
            "🆔 ID авто: {}\n"
            "🛣 Пробег на выезде: {} км\n"
            "🕒 Выдана: {}\n" + LINE + "\n"
            "🩺 Медосмотр пройден. Водитель проинструктирован при выходе в рейс "
            "о соблюдении правил ПДД и ВПС.\n\n"
            "📸 Рекомендация: приложите в чат скрин панели F3 с маркой, номером "
            "и пробегом грузовика — это снимет все споры о пробеге до закрытия путёвки.\n\n"
            "Удачи в рейсе, {}! 🚛💨").format(
        car["model"], plate, car["cid"], km, fmt_dt(now), d["name"])


def close_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not registered(d):
        return reg_error()
    trip = d.get("open_trip")
    if not trip:
        return ("❌ Открытой путёвки нет.\n\n"
                "Сначала: выписать путевку НОМЕР <пробег>")
    if trip["plate"] != plate:
        return ("❌ У вас открыта путёвка на {}, а не на {}.\n\n"
                "Закройте её: сдать путевку {} <пробег>").format(
            trip["plate"], plate, trip["plate"])
    if km < trip["start"]:
        d["rating"] = max(0, d["rating"] - PENALTY_ODOMETER)
        return ("⚠️ ОШИБКА В ПОКАЗАНИЯХ ОДОМЕТРА\n" + LINE + "\n"
                "🚛 Авто: {} / {}\n"
                "🛣 Пробег на выезде: {} км\n"
                "📏 Введено на возврате: {} км\n"
                "📉 Минус: {} км\n"
                "➖ Штраф: −{} к рейтингу (теперь ⭐{})\n" + LINE + "\n"
                "Путёвка осталась ОТКРЫТОЙ. Проверьте одометр и сдайте "
                "с правильным пробегом:\n"
                "сдать путевку {} <пробег>").format(
            car["model"] if car else "—", plate, trip["start"], km,
            trip["start"] - km, PENALTY_ODOMETER, d["rating"], plate)
    distance = km - trip["start"]
    now = time.time()
    d["open_trip"] = None
    d["total_km"] += distance
    d["trips"] += 1
    d["rating"] = min(100, d["rating"] + 1)
    d["history"] = (d.get("history") or [])[-49:]
    d["history"].append({
        "km": distance, "plate": plate, "ts": now,
        "model": car["model"] if car else "",
        "start": trip["start"], "end": km,
        "date": time.strftime("%d.%m.%Y %H:%M", time.gmtime(now + MSK_OFFSET)),
    })
    if car:
        car["mileage"] = km
        car["status"] = "стоит"
        car["driver_id"] = 0
        car["driver_name"] = ""
    return ("🔴 ПУТЁВКА ЗАКРЫТА\n" + LINE + "\n"
            "🚛 Авто: {} / {}\n"
            "📏 Пройдено: {} км\n"
            "📊 Одометр авто (записан в базу парка): {} км\n"
            "🕒 Закрыта: {}\n" + LINE + "\n"
            "👤 {}: всего {} км, рейсов {} (+1 к рейтингу ⭐{})").format(
        car["model"] if car else "—", plate, fmt_km(distance), fmt_km(km),
        fmt_dt(now), d["name"], fmt_km(d["total_km"]), d["trips"], d["rating"])


# ---------------- Тексты статистики ----------------

def history_text(d):
    hist = d.get("history") or []
    if not hist:
        return "📜 Путёвок пока нет. Первая: выписать путевку НОМЕР <пробег>"
    out = "📜 ВАШИ ПУТЁВКИ (последние {})\n".format(min(10, len(hist))) + LINE
    for t in hist[-10:]:
        out += "\n• {} — {} / {} — {} км".format(t.get("date", "—"), t.get("model", ""), t["plate"], fmt_km(t["km"]))
    return out


def profile_text(db, d):
    out = ("👤 ПРОФИЛЬ: {}\n" + LINE + "\n"
           "🆔 ID: {} | 💳 Счёт: {}\n"
           "⭐ Рейтинг: {} | 🚚 Рейсов: {} | 🛣 Всего: {} км").format(
        d["name"], d.get("did", 0), d.get("account", "—"),
        d["rating"], d["trips"], fmt_km(d["total_km"]))
    trip = d.get("open_trip")
    if trip:
        car = db["cars"].get(trip["plate"], {})
        out += "\n🔁 Открытая путёвка: {} / {} с {} км".format(
            car.get("model", ""), trip["plate"], fmt_km(trip["start"]))
    cut = time.time() - 86400
    per24 = {}
    for t in d.get("history") or []:
        if t.get("ts", 0) >= cut:
            per24[t["plate"]] = per24.get(t["plate"], 0) + t["km"]
    if per24:
        out += "\n🚛 Пробег за 24 часа:"
        for plate, km in per24.items():
            out += "\n• {} — {} км".format(plate, fmt_km(km))
    hist = d.get("history") or []
    if hist:
        out += "\n" + LINE + "\nПоследние рейсы:"
        for t in hist[-3:]:
            out += "\n• {} — {} / {} — {} км".format(t.get("date", "—"), t.get("model", ""), t["plate"], fmt_km(t["km"]))
    return out


def garage_text(db):
    out = "🚛 ГАРАЖ — ВЕСЬ ПАРК\n" + LINE
    for plate, car in sorted(db["cars"].items(), key=lambda kv: kv[1].get("cid", 0)):
        if car["status"] == "в рейсе":
            st = "🔵 в рейсе: " + (car.get("driver_name") or "?")
        else:
            st = "🟢 стоит"
        lock = " 🔒" if car.get("assigned_id") else ""
        out += "\n[ID {}] {} | {} — {} км — {}{}".format(
            car.get("cid", 0), car["model"], plate, fmt_km(car["mileage"]), st, lock)
    out += "\n" + LINE + "\n🔒 закреплённые авто: путёвки только у закреплённого водителя"
    return out


def today_text(db):
    cut = time.time() - 86400
    per = {}
    for uid, drv in db["drivers"].items():
        s = sum(t["km"] for t in (drv.get("history") or []) if t.get("ts", 0) >= cut)
        if s:
            per[drv.get("name") or "водитель"] = s
    total_park = sum(c["mileage"] for c in db["cars"].values())
    out = "📊 ЗА ПОСЛЕДНИЕ 24 ЧАСА\n" + LINE
    if per:
        for name, km in sorted(per.items(), key=lambda kv: -kv[1]):
            out += "\n🚛 {}: {} км".format(name, fmt_km(km))
        out += "\n" + LINE + "\nΣ Бригада за 24 часа: {} км".format(fmt_km(sum(per.values())))
    else:
        out += "\n😴 За сутки закрытых путёвок не было."
    out += "\n🛣 Всего пробег парка за всё время: {} км".format(fmt_km(total_park))
    return out


def rating_text(db):
    rows = [(drv.get("name") or "??", drv.get("rating", 0), drv.get("did", 0))
            for drv in db["drivers"].values() if registered(drv)]
    rows.sort(key=lambda r: -r[1])
    if not rows:
        return "⭐ Рейтинг пока пуст — регистрируйтесь: анкета Ник 123456"
    medals = ["🥇", "🥈", "🥉"]
    out = "⭐ РЕЙТИНГ ВОДИТЕЛЕЙ\n" + LINE
    for i, (name, r, did) in enumerate(rows[:10]):
        icon = medals[i] if i < 3 else "{}.".format(i + 1)
        out += "\n{} {} (ID {}) — ⭐{}".format(icon, name, did, r)
    return out


def drivers_text(db):
    rows = [(drv.get("name") or "??", drv.get("did", 0), drv.get("open_trip"))
            for drv in db["drivers"].values() if registered(drv)]
    if not rows:
        return "👥 В бригаде пока никто не зарегистрирован. Первым будьте: анкета Ник 123456"
    rows.sort(key=lambda r: r[1])
    out = "👥 ВОДИТЕЛИ БРИГАДЫ ({} чел.)\n".format(len(rows)) + LINE
    for name, did, trip in rows:
        if trip:
            out += "\n[ID {}] {} — 🔵 в рейсе: {}".format(did, name, trip["plate"])
        else:
            out += "\n[ID {}] {} — 🟢 свободен".format(did, name)
    return out


# ---------------- Ивенты ----------------

def parse_event(body):
    dm = re.search(r"(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})", body)
    if not dm:
        return None
    fm = re.search(r"(\d[\d\s]*)\s*$", body[dm.end():])
    fund = int(fm.group(1).replace(" ", "")) if fm else 0
    name = body[:dm.start()].strip() or "Ивент"
    year = time.gmtime(time.time() + MSK_OFFSET).tm_year

    def ts(dd, mm):
        return calendar.timegm((year, int(mm), int(dd), 0, 0, 0, 0, 0, 0))

    start = ts(dm.group(1), dm.group(2))
    end = ts(dm.group(3), dm.group(4))
    if end < start:
        end = ts(dm.group(3), dm.group(4)) + 365 * 86400
    return {"name": name, "fund": fund, "start_ts": start, "end_ts": end}


def event_km(db, ev, until_ts):
    per = {}
    for uid, drv in db["drivers"].items():
        s = sum(t["km"] for t in (drv.get("history") or [])
                if ev["start_ts"] <= t.get("ts", 0) <= until_ts)
        if s:
            per[drv.get("name") or "??"] = s
    return sorted(per.items(), key=lambda kv: -kv[1])


def event_top_text(db, ev, top_n, header):
    top = event_km(db, ev, ev["end_ts"])[:top_n]
    out = header + "\n" + LINE
    medals = ["🥇", "🥈", "🥉"]
    if not top:
        out += "\n😴 Пока никто не закрывал путёвки в период ивента."
        return out
    for i, (name, km) in enumerate(top):
        icon = medals[i] if i < 3 else "{}.".format(i + 1)
        out += "\n{} {} — {} км".format(icon, name, fmt_km(km))
    return out


def event_results_text(db, ev):
    out = ("🏁 ИВЕНТ ЗАВЕРШЁН — «{}»\n".format(ev["name"]) + LINE + "\n"
           "🏆 Топ-3 по пробегу за ивент:")
    medals = ["🥇", "🥈", "🥉"]
    top = event_km(db, ev, ev["end_ts"])[:3]
    if top:
        for i, (name, km) in enumerate(top):
            out += "\n{} {} — {} км".format(medals[i], name, fmt_km(km))
        if ev.get("fund"):
            out += "\n💰 Призовой фонд: {} (выдаёт админ)".format(fmt_km(ev["fund"]))
    else:
        out += "\n😴 В период ивента никто не закрывал путёвки."
    out += "\n" + LINE + "\n⏱ Итоги видны ещё 24 часа (команда: ивент), потом история очистится."
    return out


def event_cmd_text(db):
    now = time.time()
    active = [e for e in db["events"] if not e.get("announced") and e["end_ts"] > now]
    if active:
        ev = active[0]
        out = ("🏁 ИВЕНТ «{}»\n".format(ev["name"]) + LINE + "\n"
               "📅 Период: {} — {}\n"
               "💰 Призовой фонд: {}\n"
               "⏱ До конца: {}\n" + LINE).format(
            fmt_dt(ev["start_ts"]), fmt_dt(ev["end_ts"]),
            fmt_km(ev["fund"]) if ev.get("fund") else "не указан",
            fmt_left(ev["end_ts"] - now))
        out += "\n🏆 Топ-5 на текущий момент:"
        out += "\n" + event_top_text(db, ev, 5, "") .strip() or ""
        return out
    done = [e for e in db["events"] if e.get("announced") and now - e.get("announced_ts", 0) <= EVENT_HISTORY_HOURS * 3600]
    if done:
        return event_results_text(db, done[-1]) + "\n\n" + "😴 Активных ивентов больше нет. Ждём следующий — совсем скоро! 🏁"
    return "😴 Активных ивентов нет. Ждём следующий — совсем скоро! 🏁"


def check_events(db):
    """Возвращает список сообщений для публикации в чат бригады."""
    now = time.time()
    out = []
    for ev in db["events"]:
        if not ev.get("announced") and now >= ev["end_ts"]:
            ev["announced"] = True
            ev["announced_ts"] = now
            out.append(event_results_text(db, ev))
    db["events"] = [e for e in db["events"]
                    if not (e.get("announced") and now - e.get("announced_ts", 0) > EVENT_HISTORY_HOURS * 3600)]
    return out


# ---------------- Чат бригады ----------------

def export_invite(db):
    chat_id = db["chat"].get("chat_id")
    if not chat_id:
        return None
    resp = call("messages.exportChatInvite", chat_id=chat_id)
    if isinstance(resp, dict) and resp.get("link"):
        db["chat"]["link"] = resp["link"]
        return resp["link"]
    return db["chat"].get("link")


def create_chat(db, user_id):
    if db["chat"].get("chat_id"):
        link = export_invite(db)
        if link:
            return ("💬 Чат бригады уже есть. Ссылка-приглашение:\n{}\n"
                    "(ссылку можно получить снова: ссылка)").format(link)
        return "💬 Чат бригады уже есть, но ссылка недоступна. Попробуйте команду: ссылка"
    created = call("messages.createChat", title=CHAT_TITLE)
    if created is None:
        return ("❌ Не удалось создать беседу через API. Обычно это право ключа: "
                "перевыпустите токен с галочкой «Управление сообществом» и напишите: создать чат")
    chat_id = int(created)
    call("messages.inviteChat", chat_id=chat_id, user_id=user_id)
    db["chat"]["chat_id"] = chat_id
    link = export_invite(db)
    if link:
        return ("✅ Беседа создана, вы добавлены!\n"
                "🔗 Ссылка для коллег (по ней они заходят сами):\n{}\n"
                "(ссылку можно получить снова: ссылка)").format(link)
    return "✅ Беседа создана, вы добавлены! Ссылку можно получить командой: ссылка"


# ---------------- Запуск ----------------

def init_longpoll(gid):
    resp = call("groups.getLongPollServer", group_id=gid)
    if not resp or "server" not in resp:
        raise RuntimeError("groups.getLongPollServer не ответил — проверьте токен и права ключа")
    return resp["server"], resp["key"], resp["ts"]


def handle_text(db, user_id, text, reply_to):
    """Возвращает текст ответа (или None)."""
    m = text.lower().strip()

    if m in ("помощь", "команды"):
        return HELP

    if m == "мой id":
        return ("🆔 Ваш ID: {}\n👤 Ник: {} | Статус: {}".format(
            d_id(db, user_id),
            db["drivers"].get(str(user_id), {}).get("name") or "не зарегистрирован",
            "зарегистрирован" if registered(db["drivers"].get(str(user_id), {})) else "не зарегистрирован"))

    if m.startswith("анкета"):
        found = re.match(r"^анкета\s+(\S+)(?:\s+(\S+))?", text, re.IGNORECASE)
        if not found:
            return "📋 Формат: анкета Ник 123456 (6 цифр — номер игрового счёта)"
        d = get_driver(db, user_id)
        d["name"] = found.group(1)
        acc = found.group(2)
        if acc and acc.isdigit() and len(acc) == 6:
            d["account"] = acc
        elif acc:
            return ("❌ Номер счёта — ровно 6 цифр.\n\n"
                    "📋 Пример: анкета Виктор 123456")
        if not d.get("did"):
            db["ids"]["driver"] += 1
            d["did"] = db["ids"]["driver"]
        if registered(d):
            return ("✅ Вы зарегистрированы: {} | счёт {}\n"
                    "🆔 Ваш ID: {}\n\n"
                    "🚛 Выберите машину: гараж\n"
                    "🛣 В путь: выписать путевку НОМЕР <пробег>").format(
                d["name"], d["account"], d["did"])
        return "📋 Добавьте номер счёта (6 цифр): анкета {} 123456".format(d["name"])

    m2 = re.match(r"^выписать\s+путевку\s+(\S+)\s+(\d+)", text, re.IGNORECASE)
    if m2:
        plate, car = find_car(db, m2.group(1))
        if not car:
            return "❌ Авто «{}» нет в парке. Проверьте номер — вся техника: гараж".format(m2.group(1))
        return issue_trip(db, get_driver(db, user_id), user_id, plate, int(m2.group(2)))

    m2 = re.match(r"^(?:сдать|закрыть)\s+путевку\s+(\S+)\s+(\d+)", text, re.IGNORECASE)
    if m2:
        plate, car = find_car(db, m2.group(1))
        if not car:
            return "❌ Авто «{}» нет в парке. Проверьте номер — вся техника: гараж".format(m2.group(1))
        return close_trip(db, get_driver(db, user_id), user_id, plate, int(m2.group(2)))

    if m in ("путевки", "путёвки"):
        return history_text(get_driver(db, user_id))

    if m == "профиль":
        d = get_driver(db, user_id)
        if not registered(d):
            return reg_error()
        return profile_text(db, d)

    if m == "гараж":
        return garage_text(db)

    if m in ("за сегодня", "за сутки"):
        return today_text(db)

    if m == "рейтинг":
        return rating_text(db)

    if m == "водители":
        return drivers_text(db)

    if m == "ивент":
        return event_cmd_text(db)

    if m == "бинды":
        return BINDS

    if m == "создать чат":
        return create_chat(db, user_id)

    if m == "ссылка":
        link = export_invite(db)
        if link:
            return "🔗 Ссылка-приглашение в беседу бригады:\n{}".format(link)
        return "❌ Беседа ещё не создана. Команда: создать чат"

    # ---------- админ-команды ----------
    if text.startswith("!") and len(text) > 1:
        if not is_admin(user_id):
            return "⛔ Эта команда только для админов."
        body = text[1:].strip()
        low = body.lower()

        if low.startswith("закрепить") or low.startswith("открепить"):
            if not is_super(user_id):
                return "👑 Закрепление машин — только для суперадмина."
            parts = body.split()
            sub = parts[0].lower()
            if sub == "закрепить":
                if len(parts) < 3:
                    return "📋 Формат: !закрепить НОМЕР|ID Ник|ID"
                plate, car = find_car(db, parts[1])
                if not car:
                    return "❌ Авто «{}» не найдено. Вся техника: гараж".format(parts[1])
                uid, drv = find_driver(db, " ".join(parts[2:]))
                if not drv or not registered(drv):
                    return "❌ Водитель «{}» не найден или не зарегистрирован.".format(" ".join(parts[2:]))
                car["assigned_id"] = int(uid)
                car["assigned_name"] = drv["name"]
                extra = ""
                if car["status"] == "в рейсе" and car["driver_id"] != int(uid):
                    extra = "\nℹ️ Сейчас на авто чужая путёвка — закрепление вступит в силу после её закрытия."
                return ("🔧 ЗАКРЕПЛЕНИЕ\n" + LINE + "\n"
                        "🚛 {} / {} (ID {})\n"
                        "👤 Водитель: {} (ID {})\n" + LINE + "\n"
                        "🔒 Теперь путёвку на это авто выписывает только {}. "
                        "Другие — только после открепления.").format(
                    car["model"], plate, car["cid"], drv["name"], drv.get("did", 0), drv["name"]) + extra
            else:
                if len(parts) < 2:
                    return "📋 Формат: !открепить НОМЕР|ID"
                plate, car = find_car(db, parts[1])
                if not car:
                    return "❌ Авто «{}» не найдено. Вся техника: гараж".format(parts[1])
                if not car.get("assigned_id"):
                    return "ℹ️ {} не закреплён ни за кем.".format(plate)
                old = car.get("assigned_name") or "?"
                car["assigned_id"] = 0
                car["assigned_name"] = ""
                return ("🔓 ОТКРЕПЛЕНИЕ\n🚛 {} / {} (ID {})\n"
                        "👤 Снят закрепление с водителя: {}\n"
                        "Теперь авто доступно всем водителям.").format(
                    car["model"], plate, car["cid"], old)

        if low.startswith("стоп путевки"):
            hm = re.search(r"(\d+)", body)
            hours = int(hm.group(1)) if hm else DEFAULT_PAUSE_HOURS
            db["fleet_pause"] = {"until": time.time() + hours * 3600}
            return ("⛔ ВЫДАЧА ПУТЁВОК ПРИОСТАНОВЛЕНА\n" + LINE + "\n"
                    "⏱ Срок: {} ч (контрольная сверка пробегов)\n"
                    "🕘 До: {}\n"
                    "Уже открытые путёвки остаются рабочими.\n"
                    "Возобновить: !пуск путевки").format(hours, fmt_dt(db["fleet_pause"]["until"]))

        if low.startswith("пуск путевки"):
            db["fleet_pause"] = None
            return "✅ Выдача путёвок возобновлена. В путь! 🚛"

        if low.startswith("авто "):
            pm = re.match(r"^авто\s+(.+?)\s+(\S+)\s+(\d+)\s*$", body, re.IGNORECASE)
            if not pm:
                return "📋 Формат: !авто МАРКА НОМЕР пробег (пример: !авто КАМАЗ 54115 Т999ТТ77 0)"
            model = pm.group(1).strip()
            plate = norm_plate(pm.group(2))
            if plate in db["cars"]:
                return "❌ Авто {} уже в парке.".format(plate)
            db["cars"][plate] = {"model": model, "mileage": int(pm.group(3)), "status": "стоит",
                                 "driver_id": 0, "driver_name": "", "assigned_id": 0, "assigned_name": ""}
            db["ids"]["car"] += 1
            db["cars"][plate]["cid"] = db["ids"]["car"]
            return "✅ Добавлено: {} / {} (ID {}) — {} км на одометре.".format(
                model, plate, db["cars"][plate]["cid"], fmt_km(int(pm.group(3))))

        if low.startswith("пробег "):
            parts = body.split()
            if len(parts) < 3:
                return "📋 Формат: !пробег НОМЕР|ID значение"
            plate, car = find_car(db, parts[1])
            if not car:
                return "❌ Авто «{}» не найдено. Вся техника: гараж".format(parts[1])
            old = car["mileage"]
            car["mileage"] = int(parts[2])
            note = ""
            if car["status"] == "в рейсе":
                note = "\n⚠️ Авто сейчас в рейсе — открытая путёвка остаётся активной."
            return "🔧 Одометр {} / {}: {} → {} км".format(car["model"], plate, fmt_km(old), fmt_km(car["mileage"])) + note

        if low.startswith("удалить авто"):
            parts = body.split()
            if len(parts) < 3:
                return "📋 Формат: !удалить авто НОМЕР|ID"
            plate, car = find_car(db, parts[2])
            if not car:
                return "❌ Авто «{}» не найдено. Вся техника: гараж".format(parts[2])
            if car["status"] == "в рейсе":
                return ("❌ {} сейчас в рейсе у {}. Сначала закройте путёвку.".format(
                    plate, car.get("driver_name") or "водителя"))
            del db["cars"][plate]
            return "🗑 Авто {} / {} удалено из парка.".format(car["model"], plate)

        if low.startswith("профиль "):
            uid, drv = find_driver(db, body[8:].strip())
            if not drv:
                return "❌ Водитель «{}» не найден.".format(body[8:].strip())
            if not registered(drv):
                return "❌ Водитель «{}» не зарегистрирован (нет ника или счёта).".format(body[8:].strip())
            return profile_text(db, drv)

        if low.startswith("ивент "):
            ev = parse_event(body[6:])
            if not ev:
                return "📋 Формат: !ивент Название 26.11-05.12 фонд (пример: !ивент Зимний марафон 26.11-05.12 100000)"
            now = time.time()
            act = [e for e in db["events"] if not e.get("announced") and e["end_ts"] > now]
            if act:
                return "❌ Уже идёт ивент «{}» — одновременно активен только один.".format(act[0]["name"])
            db["events"].append(ev)
            return ("🎯 ИВЕНТ СОЗДАН\n" + LINE + "\n"
                    "🏁 «{}»\n"
                    "📅 Период: {} — {}\n"
                    "💰 Призовой фонд: {}\n"
                    "⏱ До конца: {}\n" + LINE + "\n"
                    "По завершении бот сам опубликует топ-3 по пробегу.").format(
                ev["name"], fmt_dt(ev["start_ts"]), fmt_dt(ev["end_ts"]),
                fmt_km(ev["fund"]) if ev["fund"] else "не указан",
                fmt_left(ev["end_ts"] - now))

        if low.startswith("штраф"):
            if not reply_to:
                return "📋 Команду !штраф отправляйте ответом на сообщение нарушителя."
            hm = re.search(r"(\d+)", body)
            pts = int(hm.group(1)) if hm else 10
            drv = get_driver(db, reply_to)
            drv["rating"] = max(0, drv["rating"] - pts)
            return "⚠️ Штраф −{} к рейтингу. У {} теперь ⭐{}".format(pts, drv["name"] or "водитель", drv["rating"])

        if low.startswith("сброс рейтинга"):
            if not reply_to:
                return "📋 Команду !сброс рейтинга отправляйте ответом на сообщение водителя."
            drv = get_driver(db, reply_to)
            drv["rating"] = 100
            drv["total_km"] = 0
            drv["trips"] = 0
            drv["history"] = []
            if drv.get("open_trip"):
                car = db["cars"].get(drv["open_trip"]["plate"])
                if car and car["driver_id"] == int(reply_to):
                    car["status"] = "стоит"
                    car["driver_id"] = 0
                    car["driver_name"] = ""
                drv["open_trip"] = None
            return "♻️ Статистика {} сброшена: рейтинг ⭐100, пробег 0.".format(drv["name"] or "водитель")

        if low == "удалить":
            if not reply_to:
                return "📋 Команду !удалить отправляйте ответом на сообщение водителя."
            uid = str(reply_to)
            drv = db["drivers"].get(uid)
            if not drv:
                return "❌ Этот водитель не зарегистрирован."
            name = drv.get("name") or "?"
            for car in db["cars"].values():
                if car.get("assigned_id") == reply_to:
                    car["assigned_id"] = 0
                    car["assigned_name"] = ""
                if car.get("driver_id") == reply_to:
                    car["status"] = "стоит"
                    car["driver_id"] = 0
                    car["driver_name"] = ""
            del db["drivers"][uid]
            return ("🗑 Регистрация {} полностью удалена: ник, счёт, пробег, статистика.\n"
                    "🔒 Закрепления с его машин сняты.").format(name)

        if low.startswith("объявление"):
            msg = text[len("!объявление"):].strip()
            if not msg:
                return "📋 Формат: !объявление текст рассылки"
            cnt = 0
            for uid, drv in db["drivers"].items():
                if registered(drv):
                    send(int(uid), "📢 ОБЪЯВЛЕНИЕ\n" + LINE + "\n" + msg)
                    cnt += 1
                    time.sleep(0.4)
            return "📢 Объявление отправлено {} водителям.".format(cnt)

        return "🤔 Неизвестная админ-команда. Весь список: помощь"

    return "🤔 Команда не распознана. Полный список: помощь"


def d_id(db, user_id):
    return db["drivers"].get(str(user_id), {}).get("did", 0)


def main():
    if not TOKEN:
        print("ERROR: переменная окружения BOT_TOKEN не задана")
        return

    global GROUP_ID
    if not GROUP_ID:
        info = call("groups.getById")
        if not info:
            print("ERROR: не удалось получить группу — проверьте токен")
            return
        item = (info.get("groups") or info)
        GROUP_ID = int(item[0]["id"])
    print("GROUP_ID =", GROUP_ID)

    call("groups.setLongPollSettings", group_id=GROUP_ID,
         enabled=1, message_new=1, api_version=API_V)

    server, key, ts = init_longpoll(GROUP_ID)
    print("Бот запущен, ожидание сообщений...")
    if SUPER_IDS:
        print("Суперадмины:", SUPER_IDS)
    else:
        print("ВНИМАНИЕ: SUPERADMINS не задан — закрепление машин недоступно. "
              "Узнайте свой VK ID командой «мой id» и задайте переменную SUPERADMINS.")

    while True:
        try:
            r = requests.get(server, params={
                "act": "a_check", "key": key, "ts": ts, "wait": 25,
            }, timeout=35)
            data = r.json()
        except Exception as e:
            print("longpoll error:", e)
            time.sleep(3)
            continue

        if "failed" in data:
            code = data["failed"]
            if code == 1 and "ts" in data:
                ts = data["ts"]
                continue
            if code in (2, 3):
                try:
                    server, key, ts = init_longpoll(GROUP_ID)
                except RuntimeError as e:
                    print("reconnect failed:", e)
                    time.sleep(10)
                continue
            time.sleep(3)
            continue

        ts = data.get("ts", ts)

        for update in data.get("updates", []):
            if update.get("type") != "message_new":
                continue
            msg = update.get("object", {}).get("message", {})
            peer_id = int(msg.get("peer_id") or 0)
            if not peer_id:
                continue

            action = msg.get("action") or {}
            if action.get("type") == "chat_invite_user" and action.get("member_id", 0) < 0:
                send(peer_id, WELCOME)
                continue

            if msg.get("from_id", 0) <= 0:
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            user_id = int(msg["from_id"])
            reply_to = None
            rm = msg.get("reply_message") or {}
            if rm.get("from_id"):
                reply_to = int(rm["from_id"])

            with _lock:
                db = load_db()
                try:
                    reply = handle_text(db, user_id, text, reply_to)
                except Exception as e:
                    print("handler error:", e)
                    reply = "😐 Что-то сломалось в обработке команды. Напишите админу, он посмотрит."
                for ev_msg in check_events(db):
                    chat_id = db["chat"].get("chat_id")
                    if chat_id:
                        send(chat_id, ev_msg)
                save_db(db)
            if reply:
                send(peer_id, reply)


if __name__ == "__main__":
    main()
