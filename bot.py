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

import requests

TOKEN = (os.environ.get("BOT_TOKEN") or os.environ.get("TOKEN") or "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
GROUP_ID = int(os.environ.get("GROUP_ID") or 0)
API_URL = "https://api.vk.com/method/"
API_V = "5.199"
CHAT_TITLE = "Грузовой поток | бригада"
MSK_OFFSET = 3 * 3600  # время в сообщениях — московское
PENALTY_ODOMETER = 10     # штраф за минус пробега (ошибка в показаниях одометра)
PENALTY_BURN = 10         # штраф за сгоревшую (открытую больше суток) путёвку
EVENT_HISTORY_HOURS = 24  # сколько часов хранить итоги завершённого ивента
DEFAULT_PAUSE_HOURS = 3   # пауза выдачи путёвок для контрольной сверки
TRIP_LIFETIME = 24 * 3600 # путёвка сгорает, если открыта дольше суток
TO_LIMIT_KM = 500         # порог пробега с последнего ТО
BURN_CHECK_EVERY = 60     # период проверки сгоревших путёвок, сек


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


def msk_date(ts=None):
    return time.strftime("%Y-%m-%d", time.gmtime((ts or time.time()) + MSK_OFFSET))


def msk_weekday(ts=None):
    return time.gmtime((ts or time.time()) + MSK_OFFSET).tm_wday


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
    "⚠️ Минус пробега — штраф −{} к рейтингу\n"
    "⏰ Открытая больше суток путёвка сгорает (штраф −{})\n\n"
    "📊 Статистика:\n"
    "• профиль — ваши данные и пробег за сутки\n"
    "• путевки — история ваших рейсов\n"
    "• гараж — весь парк: ID, марка, номер, статус\n"
    "• история Т740ТТ77 — журнал рейсов машины\n"
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
    "• !то НОМЕР|ID — сброс счётчика ТО после обслуживания\n"
    "• !штраф [баллы] / !сброс рейтинга / !удалить — ответом на сообщение\n"
    "• !объявление текст — рассылка всем в личку\n\n"
    "👑 Только суперадмин:\n"
    "• !закрепить НОМЕР|ID Ник|ID — закрепить авто за водителем\n"
    "• !открепить НОМЕР|ID — снять закрепление\n\n"
    "🔒 На закреплённое авто путёвку выписывает только его водитель"
).format(PENALTY_ODOMETER, PENALTY_BURN)

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
                           "driver_id": 0, "driver_name": "", "assigned": None,
                           "service_km": 0, "journal": []}
        data["cars"] = cars
    if not isinstance(data.get("chat"), dict):
        data["chat"] = {}
    if not isinstance(data.get("events"), list):
        data["events"] = []
    if not isinstance(data.get("meta"), dict):
        data["meta"] = {}
    # статические ID: присваиваем тем, у кого их ещё нет
    for plate, car in data["cars"].items():
        if not isinstance(car, dict):
            continue
        car.setdefault("model", plate)
        car.setdefault("mileage", 0)
        car.setdefault("status", "стоит")
        car.setdefault("driver_id", 0)
        car.setdefault("driver_name", "")
        car.setdefault("assigned", None)
        car.setdefault("service_km", 0)
        if not isinstance(car.get("journal"), list):
            car["journal"] = []
        if not car.get("car_id"):
            data["meta"]["car_seq"] = int(data["meta"].get("car_seq", 0)) + 1
            car["car_id"] = int(data["meta"]["car_seq"])
    for vk, d in data["drivers"].items():
        for k, v in (("history", []), ("log", []), ("penalties", 0)):
            if not isinstance(d.get(k), (list, int)) or d.get(k) is None:
                d[k] = v
        if not isinstance(d.get("history"), list):
            d["history"] = []
        if not isinstance(d.get("log"), list):
            d["log"] = []
        if not d.get("penalties"):
            d["penalties"] = 0
        if not d.get("id"):
            data["meta"]["drv_seq"] = int(data["meta"].get("drv_seq", 0)) + 1
            d["id"] = int(data["meta"]["drv_seq"])
    return data


def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_driver(db, user_id):
    d = db["drivers"].setdefault(str(user_id), {
        "name": "", "account": "", "id": 0, "total_km": 0, "trips": 0,
        "rating": 100, "penalties": 0, "open_trip": None,
        "history": [], "log": [], "assigned_plate": None,
    })
    for k, v in (("history", []), ("log", [])):
        if not isinstance(d.get(k), list):
            d[k] = v
    if not d.get("id"):
        db["meta"]["drv_seq"] = int(db["meta"].get("drv_seq", 0)) + 1
        d["id"] = int(db["meta"]["drv_seq"])
    return d


def registered(d):
    return bool(d.get("name")) and bool(d.get("account"))


def send(peer_id, text):
    resp = call("messages.send",
                peer_id=peer_id,
                message=text,
                random_id=random.randint(0, 2 ** 31))
    if resp is None:
        print("send error to peer", peer_id)


def chat_send(db, text):
    chat_id = db["chat"].get("chat_id")
    if chat_id:
        send(int(chat_id), text)
        return True
    return False


def find_car(db, token):
    """Ищет авто по номеру или ID."""
    plate = norm_plate(token)
    if plate in db["cars"]:
        return plate, db["cars"][plate]
    try:
        cid = int(token)
    except ValueError:
        return None, None
    for plate, car in db["cars"].items():
        if car.get("car_id") == cid:
            return plate, car
    return None, None


def find_driver_by_name(db, nick):
    nick = nick.lower()
    for vk, d in db["drivers"].items():
        if (d.get("name") or "").lower() == nick:
            return vk, d
    return None, None


def find_driver_by_id(db, did):
    for vk, d in db["drivers"].items():
        if d.get("id") == did:
            return vk, d
    return None, None


def rating_down(db, d, amount):
    d["rating"] = max(0, int(d.get("rating", 100)) - amount)
    d["penalties"] = int(d.get("penalties", 0)) + 1
    wf = db["meta"].setdefault("weekly_fines", {})
    wf[d.get("name") or "водитель"] = int(wf.get(d.get("name") or "водитель", 0)) + amount


# ---------------- Команды ----------------

def issue_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not registered(d):
        return ("❌ Путёвки доступны только зарегистрированным водителям.\n"
                "📋 Регистрация: анкета Ник 123456\n"
                "(игровой ник + 6-значный номер счёта)")
    if d.get("open_trip"):
        old = db["cars"].get(d["open_trip"]["plate"], {})
        return ("⚠️ У вас уже открыта путёвка на {} ({}).\n"
                "Сначала закройте её: сдать путевку {} <пробег>").format(
            d["open_trip"]["plate"], old.get("model", ""), d["open_trip"]["plate"])
    if not car:
        return ("❌ Авто с номером {} нет в парке.\n"
                "🚛 Проверьте номер — команда: гараж").format(plate)
    if car["status"] == "в рейсе" and car["driver_id"] != user_id:
        return ("❌ {} ({}) сейчас в рейсе у {}.\n"
                "Выберите другое авто: гараж").format(
            plate, car["model"], car["driver_name"] or "другого водителя")
    if car.get("assigned") and str(car["assigned"]) != str(user_id):
        return ("🔒 {} ({}) закреплён за {}. Путёвку на него выписывает "
                "только закреплённый водитель.").format(
            plate, car["model"], car.get("driver_name") or "другим водителем")
    if km < car["mileage"]:
        return ("❌ Начальный пробег не может быть меньше одометра авто.\n"
                "🚛 {} | {}: одометр {} км\n"
                "Укажите пробег {} или больше.").format(
            car["model"], plate, fmt_km(car["mileage"]), fmt_km(car["mileage"]))
    now = time.time()
    d["open_trip"] = {"plate": plate, "start_km": km, "start_ts": now}
    car["status"] = "в рейсе"
    car["driver_id"] = user_id
    car["driver_name"] = d["name"]
    return (
        "🟢 ПУТЁВКА ВЫДАНА\n" + LINE + "\n"
        "🚛 Авто: {} | {}\n"
        "🛣 Пробег на выезде: {} км\n"
        "🕒 Выдана: {}\n" + LINE + "\n"
        "🩺 Медосмотр пройден.\n"
        "📋 Водитель проинструктирован при выходе в рейс\n"
        "о соблюдении правил ПДД и ВПС.\n\n"
        "📸 Совет: приложите скрин панели F3 (марка, номер, пробег) —\n"
        "исключит споры о пробеге при закрытии путёвки.\n\n"
        "Удачи в рейсе, {}! 🚛💨"
    ).format(car["model"], plate, fmt_km(km), fmt_dt(now), d["name"])


def close_trip(db, d, user_id, plate, end_km):
    trip = d.get("open_trip")
    if not registered(d):
        return ("❌ Сначала зарегистрируйтесь: анкета Ник 123456")
    if not trip:
        return ("❌ У вас нет открытой путёвки.\n"
                "🛣 Открыть: выписать путевку НОМЕР <пробег>")
    if plate != trip["plate"]:
        car_open = db["cars"].get(trip["plate"], {})
        return ("❌ У вас открыта путёвка на {} ({}).\n"
                "Закройте её: сдать путевку {} <пробег>").format(
            trip["plate"], car_open.get("model", ""), trip["plate"])
    car = db["cars"].get(plate)
    if not car:
        return "❌ Авто с номером {} уже нет в парке.".format(plate)
    if end_km < trip["start_km"]:
        rating_down(db, d, PENALTY_ODOMETER)
        return (
            "❌ ОШИБКА В ПОКАЗАНИЯХ ОДОМЕТРА\n" + LINE + "\n"
            "🚛 Авто: {} | {}\n"
            "🛣 Пробег на выезде: {} км\n"
            "📉 Указано на возврате: {} км (минус {} км!)\n" + LINE + "\n"
            "⚠️ Штраф: −{} к рейтингу (⭐{})\n\n"
            "Путёвка осталась открытой. Проверьте одометр и сдайте\n"
            "с правильным пробегом: сдать путевку {} <пробег>"
        ).format(car["model"], plate, fmt_km(trip["start_km"]),
                 fmt_km(end_km), fmt_km(trip["start_km"] - end_km),
                 PENALTY_ODOMETER, d["rating"], plate)
    now = time.time()
    km = end_km - trip["start_km"]
    d["total_km"] = int(d.get("total_km", 0)) + km
    d["trips"] = int(d.get("trips", 0)) + 1
    d["rating"] = min(100, int(d.get("rating", 100)) + 1)
    d["open_trip"] = None
    old_mileage = car["mileage"]
    car["mileage"] = end_km
    car["service_km"] = int(car.get("service_km", 0)) + km
    car["status"] = "стоит"
    car["driver_id"] = 0
    car["driver_name"] = ""
    entry = {"date": fmt_dt(now), "ts": now, "km": km,
             "start": trip["start_km"], "end": end_km}
    d["history"].append(dict(entry, plate=plate, model=car["model"]))
    if len(d["history"]) > 50:
        d["history"] = d["history"][-50:]
    car["journal"].append({"ts": now, "driver": d["name"], "km": km,
                           "start": trip["start_km"], "end": end_km})
    if len(car["journal"]) > 100:
        car["journal"] = car["journal"][-100:]
    reply = (
        "🔴 ПУТЁВКА ЗАКРЫТА\n" + LINE + "\n"
        "🚛 Авто: {} | {}\n"
        "📏 Пройдено: {} км\n"
        "📊 Одометр авто: {} км (было {})\n"
        "🕒 Закрыта: {}\n" + LINE + "\n"
        "👤 {}: всего {} км, рейсов {} (+1 к рейтингу ⭐{})"
    ).format(car["model"], plate, fmt_km(km), fmt_km(end_km), fmt_km(old_mileage),
             fmt_dt(now), d["name"], fmt_km(d["total_km"]), d["trips"], d["rating"])
    if car["service_km"] >= TO_LIMIT_KM:
        reply += (
            "\n\n🔧 ВНИМАНИЕ — ПОРА НА ТО\n"
            "{} | {} прошёл {} км с последнего ТО.\n"
            "После обслуживания админ сбросит счётчик командой: !то {}"
        ).format(car["model"], plate, fmt_km(car["service_km"]), plate)
    return reply


def burn_trips(db):
    """Сжигает путёвки, открытые дольше суток. Возвращает список сообщений."""
    msgs = []
    now = time.time()
    for vk, d in db["drivers"].items():
        trip = d.get("open_trip")
        if not trip or now - trip["start_ts"] <= TRIP_LIFETIME:
            continue
        plate = trip["plate"]
        car = db["cars"].get(plate)
        rating_down(db, d, PENALTY_BURN)
        d["open_trip"] = None
        if car:
            car["status"] = "стоит"
            car["driver_id"] = 0
            car["driver_name"] = ""
            car["journal"].append({"ts": now, "driver": d.get("name") or "водитель",
                                   "km": 0, "burned": True})
        text = (
            "⏰ ПУТЁВКА СГОРЕЛА\n" + LINE + "\n"
            "🚛 Авто: {} | {}\n"
            "👤 Водитель: {}\n" + LINE + "\n"
            "⚠️ Путёвка была открыта больше суток и автоматически сгорела.\n"
            "📉 Штраф: −{} к рейтингу (⭐{})\n\n"
            "Машина свободна. Новая путёвка: выписать путевку {} <пробег>"
        ).format((car or {}).get("model", ""), plate, d.get("name") or "водитель",
                 PENALTY_BURN, d["rating"], plate)
        msgs.append((int(vk), int(db["chat"].get("chat_id") or 0), text))
    return msgs


def fmt_garage(db):
    lines = ["🚛 ГАРАЖ ПАРКА", LINE]
    for plate in sorted(db["cars"], key=lambda p: db["cars"][p].get("car_id", 0)):
        car = db["cars"][plate]
        lock = "🔒 " if car.get("assigned") else ""
        if car["status"] == "в рейсе":
            status = "🔵 в рейсе ({})".format(car.get("driver_name") or "?")
        else:
            status = "🟢 стоит"
        lines.append("{}. {}{} | {} — {} км — {}".format(
            car.get("car_id", "?"), lock, car["model"], plate,
            fmt_km(car["mileage"]), status))
    lines.append(LINE)
    lines.append("Всего машин: {}".format(len(db["cars"])))
    return "\n".join(lines)


def daily_stats(db, now):
    """Пробег водителей за последние 24 часа."""
    per_driver = {}
    total = 0
    for vk, d in db["drivers"].items():
        rows = {}
        for h in d.get("history", []):
            if now - h["ts"] <= 86400:
                key = "{} {}".format(h.get("model", ""), h.get("plate", ""))
                rows[key] = rows.get(key, 0) + h["km"]
                total += h["km"]
        if rows:
            per_driver[d.get("name") or "водитель"] = rows
    return per_driver, total


def cmd_today(db):
    now = time.time()
    per_driver, total = daily_stats(db, now)
    fleet = sum(c["mileage"] for c in db["cars"].values())
    out = ["📈 ПРОБЕГ ЗА СУТКИ", LINE]
    if not per_driver:
        out.append("За последние 24 часа рейсов нет.")
    else:
        for name, rows in per_driver.items():
            s = sum(rows.values())
            out.append("👤 {} — {} км".format(name, fmt_km(s)))
            for key, km in rows.items():
                out.append("   • {} — {} км".format(key, fmt_km(km)))
    out.append(LINE)
    out.append("Итог за 24 ч: {} км".format(fmt_km(total)))
    out.append("Весь парк за всё время: {} км".format(fmt_km(fleet)))
    return "\n".join(out)


def cmd_history(db, token):
    plate, car = find_car(db, token)
    if not car:
        return ("❌ Авто «{}» нет в парке. Проверьте номер: гараж").format(token)
    out = ["📜 ИСТОРИЯ АВТО — {} | {}".format(car["model"], plate), LINE]
    j = car.get("journal", [])
    if not j:
        out.append("Рейсов ещё не было — машина ждёт первого водителя.")
    else:
        for e in j[-10:]:
            if e.get("burned"):
                out.append("• {} — {} — ⏰ путёвка сгорела".format(
                    time.strftime("%d.%m %H:%M", time.gmtime(e["ts"] + MSK_OFFSET)),
                    e.get("driver", "?")))
            else:
                out.append("• {} — {} — {} км ({} → {})".format(
                    time.strftime("%d.%m %H:%M", time.gmtime(e["ts"] + MSK_OFFSET)),
                    e.get("driver", "?"), fmt_km(e["km"]),
                    fmt_km(e.get("start", 0)), fmt_km(e.get("end", 0))))
        out.append(LINE)
        out.append("Показаны последние {}, всего записей: {}".format(
            min(10, len(j)), len(j)))
    return "\n".join(out)


def cmd_profile(db, d, user_id):
    out = ["👤 ПРОФИЛЬ — {}".format(d.get("name") or "не указано"), LINE]
    out.append("🆔 ID: {}".format(d.get("id", "?")))
    out.append("💳 Игровой счёт: {}".format(d.get("account") or "не указан"))
    out.append("⭐ Рейтинг: {}".format(d.get("rating", 100)))
    out.append("🚚 Рейсов: {}".format(d.get("trips", 0)))
    out.append("🛣 Всего км: {}".format(fmt_km(d.get("total_km", 0))))
    if d.get("assigned_plate"):
        car = db["cars"].get(d["assigned_plate"], {})
        out.append("🔒 Закреплённое авто: {} | {}".format(
            car.get("model", ""), d["assigned_plate"]))
    trip = d.get("open_trip")
    if trip:
        left = TRIP_LIFETIME - (time.time() - trip["start_ts"])
        car = db["cars"].get(trip["plate"], {})
        out.append("🟢 Открытая путёвка: {} | {} (выезд {} км)".format(
            car.get("model", ""), trip["plate"], fmt_km(trip["start_km"])))
        out.append("⏰ Сгорит через: {}".format(fmt_left(left)))
    now = time.time()
    rows = {}
    for h in d.get("history", []):
        if now - h["ts"] <= 86400:
            key = "{} {}".format(h.get("model", ""), h.get("plate", ""))
            rows[key] = rows.get(key, 0) + h["km"]
    if rows:
        out.append(LINE)
        out.append("📊 За сутки по машинам:")
        for key, km in rows.items():
            out.append("• {} — {} км".format(key, fmt_km(km)))
    hist = d.get("history", [])
    if hist:
        out.append(LINE)
        out.append("Последние рейсы:")
        for t in hist[-3:]:
            out.append("• {} — {} | {} — {} км".format(
                t["date"], t.get("model", ""), t.get("plate", ""), fmt_km(t["km"])))
    return "\n".join(out)


def cmd_drivers(db):
    regs = [(int(vk), d) for vk, d in db["drivers"].items() if registered(d)]
    regs.sort(key=lambda x: x[1].get("id", 0))
    out = ["👥 ВОДИТЕЛИ БРИГАДЫ", LINE]
    if not regs:
        out.append("Пока никого. Будьте первым: анкета Ник 123456")
    for vk, d in regs:
        lock = " 🔒" if d.get("assigned_plate") else ""
        out.append("{}. {} (ID {}) — ⭐{} — {} км{}".format(
            d.get("id", "?"), d["name"], d.get("id", "?"), d.get("rating", 100),
            fmt_km(d.get("total_km", 0)), lock))
    return "\n".join(out)


def cmd_rating(db):
    regs = [d for d in db["drivers"].values() if registered(d)]
    regs.sort(key=lambda d: (d.get("rating", 0), d.get("total_km", 0)), reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    out = ["🏆 РЕЙТИНГ ВОДИТЕЛЕЙ", LINE]
    if not regs:
        out.append("Пока пусто — зарегистрируйтесь первым!")
    for i, d in enumerate(regs):
        mark = medals[i] if i < 3 else "{}.".format(i + 1)
        out.append("{} {} — ⭐{} — {} км".format(
            mark, d["name"], d.get("rating", 100), fmt_km(d.get("total_km", 0))))
    return "\n".join(out)


# ---------------- Ивенты ----------------

def parse_event_args(rest):
    m = re.search(r"(\d{2}\.\d{2})\s*-\s*(\d{2}\.\d{2})", rest)
    if not m:
        return None
    fund = re.search(r"(\d[\d\s]*)$", rest[m.end():].strip())
    if not fund:
        return None
    fund_val = int(fund.group(1).replace(" ", ""))
    name = rest[:m.start()].strip()
    if not name:
        return None
    year = time.gmtime(time.time() + MSK_OFFSET).tm_year
    try:
        start = time.mktime(time.strptime(
            "{}.{}".format(m.group(1), year), "%d.%m.%Y")) - MSK_OFFSET
        end = time.mktime(time.strptime(
            "{}.{}".format(m.group(2), year), "%d.%m.%Y")) - MSK_OFFSET + 86399
    except ValueError:
        return None
    if end <= start:
        year += 1
        end = time.mktime(time.strptime(
            "{}.{}".format(m.group(2), year), "%d.%m.%Y")) - MSK_OFFSET + 86399
    return name, start, end, fund_val


def event_top(db, start_ts, end_ts, top=5):
    scores = {}
    for d in db["drivers"].values():
        km = sum(h["km"] for h in d.get("history", [])
                 if start_ts <= h["ts"] <= end_ts and not h.get("burned"))
        if km:
            scores[d.get("name") or "водитель"] = km
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top]


def cmd_event_view(db, now):
    for ev in db["events"]:
        if now < ev["end_ts"] and not ev.get("announced"):
            left = ev["end_ts"] - now
            top = event_top(db, ev["start_ts"], ev["end_ts"], 5)
            out = ["🏁 ИВЕНТ «{}»".format(ev["name"]), LINE,
                   "📅 Период: {} — {}".format(
                       time.strftime("%d.%m", time.gmtime(ev["start_ts"] + MSK_OFFSET)),
                       time.strftime("%d.%m", time.gmtime(ev["end_ts"] + MSK_OFFSET))),
                   "💰 Призовой фонд: {}".format(fmt_km(ev["fund"])),
                   "⏳ До конца: {}".format(fmt_left(left)), LINE]
            if top:
                out.append("Текущая пятёрка по пробегу:")
                medals = ["🥇", "🥈", "🥉"]
                for i, (name, km) in enumerate(top):
                    out.append("{} {} — {} км".format(
                        medals[i] if i < 3 else "{}.".format(i + 1), name, fmt_km(km)))
            else:
                out.append("Рейсов в периоде пока нет — всё в ваших руках!")
            return "\n".join(out)
    # итоги свежезавершённых
    for ev in db["events"]:
        if ev.get("announced") and now - ev["results_ts"] <= EVENT_HISTORY_HOURS * 3600:
            return ev.get("results_text", "🏁 Ивент завершён.")
    return "😴 Активных ивентов нет. Ждём следующий — совсем скоро! 🏁"


def finalize_events(db, now):
    """Публикует итоги завершившихся ивентов и чистит старые. Возвращает сообщения."""
    msgs = []
    for ev in db["events"]:
        if not ev.get("announced") and now >= ev["end_ts"]:
            top = event_top(db, ev["start_ts"], ev["end_ts"], 3)
            medals = ["🥇", "🥈", "🥉"]
            lines = ["🏁 ИВЕНТ «{}» ЗАВЕРШЁН".format(ev["name"]), LINE,
                     "💰 Призовой фонд: {}".format(fmt_km(ev["fund"])),
                     "Топ-3 по пробегу за период:"]
            if top:
                for i, (name, km) in enumerate(top):
                    lines.append("{} {} — {} км".format(medals[i], name, fmt_km(km)))
            else:
                lines.append("В периоде ивента не было закрытых рейсов.")
            lines.append(LINE)
            lines.append("Премии — по номерам игровых счетов победителей. 💸")
            text = "\n".join(lines)
            ev["announced"] = True
            ev["results_ts"] = now
            ev["results_text"] = text
            msgs.append(text)
    db["events"] = [e for e in db["events"]
                    if not (e.get("announced") and now - e["results_ts"] > EVENT_HISTORY_HOURS * 3600)]
    return msgs


# ---------------- Еженедельный отчёт ----------------

def weekly_report(db, now):
    start = now - 7 * 86400
    scores = {}
    for d in db["drivers"].values():
        km = sum(h["km"] for h in d.get("history", []) if h["ts"] >= start)
        if km:
            scores[d.get("name") or "водитель"] = km
    fines = db["meta"].get("weekly_fines") or {}
    lines = ["📅 НЕДЕЛЬНЫЙ ОТЧЁТ БРИГАДЫ", LINE, "Топ-5 по пробегу за неделю:"]
    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
    medals = ["🥇", "🥈", "🥉"]
    if top:
        for i, (name, km) in enumerate(top):
            lines.append("{} {} — {} км".format(
                medals[i] if i < 3 else "{}.".format(i + 1), name, fmt_km(km)))
    else:
        lines.append("Неделя прошла без закрытых рейсов. 🛌")
    if fines:
        lines.append(LINE)
        lines.append("⚠️ Штрафы за неделю:")
        for name, n in fines.items():
            lines.append("• {} — штрафов: {}".format(name, n))
    else:
        lines.append(LINE)
        lines.append("✨ Штрафов за неделю — ноль. Так держать!")
    return "\n".join(lines)


def maintenance(db):
    """Фоновые задачи: сгорание путёвок, ивенты, недельный отчёт."""
    now = time.time()
    msgs = []
    for m in burn_trips(db):
        msgs.append(m)
    for text in finalize_events(db, now):
        msgs.append((0, int(db["chat"].get("chat_id") or 0), text))
    if msk_weekday(now) == 6:  # воскресенье
        today = msk_date(now)
        last = db["meta"].get("last_weekly")
        if last != today and (now + MSK_OFFSET) % 86400 >= 12 * 3600:
            db["meta"]["last_weekly"] = today
            report_text = weekly_report(db, now)
            db["meta"]["weekly_fines"] = {}
            msgs.append((0, int(db["chat"].get("chat_id") or 0), report_text))
    return msgs


# ---------------- Админ-команды ----------------

def is_admin(user_id):
    return user_id in ADMIN_IDS or user_id in SUPER_IDS


def is_super(user_id):
    return user_id in SUPER_IDS


def admin_command(db, user_id, text, reply_user, now):
    low = text.lower()
    parts = text.split()

    if low.startswith("!закрепить"):
        if not is_super(user_id):
            return "👑 Закреплять машины может только суперадмин."
        args = parts[1:]
        if len(args) != 2:
            return "Формат: !закрепить НОМЕР|ID Ник|ID — пример: !закрепить Т740ТТ77 Виктор"
        plate, car = find_car(db, args[0])
        if not car:
            return "❌ Авто «{}» не найдено. Список: гараж".format(args[0])
        vk, target = None, None
        if args[1].isdigit():
            vk, target = find_driver_by_id(db, int(args[1]))
        if not target:
            vk, target = find_driver_by_name(db, args[1])
        if not target or not registered(target):
            return "❌ Водитель «{}» не найден или не зарегистрирован.".format(args[1])
        if car["status"] == "в рейсе":
            return "❌ {} сейчас в рейсе — закрепить нельзя до закрытия путёвки.".format(plate)
        car["assigned"] = str(vk)
        target["assigned_plate"] = plate
        return ("🔒 АВТО ЗАКРЕПЛЕНО\n" + LINE + "\n"
                "🚛 {} | {} — теперь закреплён за {}.\n"
                "Путёвку на это авто выписывает только закреплённый водитель."
                ).format(car["model"], plate, target["name"])

    if low.startswith("!открепить"):
        if not is_super(user_id):
            return "👑 Откреплять машины может только суперадмин."
        if len(parts) != 2:
            return "Формат: !открепить НОМЕР|ID"
        plate, car = find_car(db, parts[1])
        if not car:
            return "❌ Авто «{}» не найдено.".format(parts[1])
        prev = car.get("assigned")
        car["assigned"] = None
        if prev:
            d2 = db["drivers"].get(str(prev))
            if d2 and d2.get("assigned_plate") == plate:
                d2["assigned_plate"] = None
        return "🔓 {} | {} — закрепление снято. Машина в свободном пользовании.".format(
            car["model"], plate)

    if low.startswith("!стоп путевки"):
        hours = DEFAULT_PAUSE_HOURS
        if len(parts) >= 3 and parts[2].isdigit():
            hours = int(parts[2])
        db["pause_until"] = now + hours * 3600
        chat_send(db, (
            "⏸ ВЫДАЧА ПУТЁВОК ПРИОСТАНОВЛЕНА\n" + LINE + "\n"
            "🔧 Причина: контрольная сверка пробегов.\n"
            "⏳ Возобновление: примерно через {}\n"
            "Уже открытые путёвки остаются рабочими — их можно закрывать."
        ).format(fmt_left(hours * 3600)))
        return "✅ Выдача путёвок остановлена на {}. Открытые путёвки работают.".format(
            fmt_left(hours * 3600))

    if low.startswith("!пуск путевки"):
        if not db.get("pause_until"):
            return "ℹ️ Выдача путёвок и так активна."
        db["pause_until"] = 0
        chat_send(db, "▶️ ВЫДАЧА ПУТЁВОК ВОЗОБНОВЛЕНА. Сверка пробегов завершена. В путь! 🚛")
        return "✅ Выдача путёвок возобновлена."

    if low.startswith("!то "):
        plate, car = find_car(db, parts[1] if len(parts) > 1 else "")
        if not car:
            return "❌ Авто не найдено. Формат: !то НОМЕР|ID"
        car["service_km"] = 0
        return "🔧 Счётчик ТО обнулён: {} | {}. Следующее ТО — через {} км.".format(
            car["model"], plate, TO_LIMIT_KM)

    if low.startswith("!авто "):
        m = re.match(r"!авто\s+(.+?)\s+(\S+)\s+(\d+)\s*$", text, re.I)
        if not m:
            return "Формат: !авто МАРКА НОМЕР пробег — пример: !авто КАМАЗ 5490 НЕО А999АА77 0"
        model, plate_token, km = m.group(1), norm_plate(m.group(2)), int(m.group(3))
        if plate_token in db["cars"]:
            return "❌ Авто с номером {} уже есть в парке.".format(plate_token)
        db["meta"]["car_seq"] = int(db["meta"].get("car_seq", 0)) + 1
        db["cars"][plate_token] = {
            "model": model, "mileage": km, "status": "стоит", "driver_id": 0,
            "driver_name": "", "assigned": None, "service_km": 0, "journal": [],
            "car_id": int(db["meta"]["car_seq"]),
        }
        return ("🚛 АВТО ДОБАВЛЕНО\n" + LINE + "\n"
                "{} | {} (ID {})\n"
                "🛣 Одометр при постановке на учёт: {} км").format(
            model, plate_token, db["cars"][plate_token]["car_id"], fmt_km(km))

    if low.startswith("!пробег "):
        if len(parts) < 3 or not parts[2].isdigit():
            return "Формат: !пробег НОМЕР|ID значение"
        plate, car = find_car(db, parts[1])
        if not car:
            return "❌ Авто «{}» не найдено.".format(parts[1])
        old = car["mileage"]
        car["mileage"] = int(parts[2])
        return ("🛠 ОДОМЕТР СКОРРЕКТИРОВАН\n"
                "🚛 {} | {}: {} → {} км").format(car["model"], plate, fmt_km(old), fmt_km(car["mileage"]))

    if low.startswith("!удалить авто"):
        if len(parts) < 3:
            return "Формат: !удалить авто НОМЕР|ID"
        plate, car = find_car(db, parts[2])
        if not car:
            return "❌ Авто «{}» не найдено.".format(parts[2])
        if car["status"] == "в рейсе":
            return ("❌ {} | {} сейчас в рейсе у {}.\n"
                    "Сначала дождитесь закрытия путёвки.").format(
                car["model"], plate, car.get("driver_name") or "?")
        if car.get("assigned"):
            d2 = db["drivers"].get(str(car["assigned"]))
            if d2:
                d2["assigned_plate"] = None
        del db["cars"][plate]
        return "🗑 {} | {} удалён из парка. ID других машин не изменились.".format(
            car["model"], plate)

    if low.startswith("!профиль "):
        arg = parts[1]
        vk, target = None, None
        if arg.isdigit():
            vk, target = find_driver_by_id(db, int(arg))
        if not target:
            vk, target = find_driver_by_name(db, arg)
        if not target:
            return "❌ Водитель «{}» не найден.".format(arg)
        return cmd_profile(db, target, vk)

    if low.startswith("!ивент "):
        parsed = parse_event_args(text[len("!ивент "):])
        if not parsed:
            return ("Формат: !ивент Название 26.11-05.12 100000\n"
                    "(название, сроки ДД.ММ-ДД.ММ, призовой фонд)")
        name, start, end, fund = parsed
        now = time.time()
        for ev in db["events"]:
            if not ev.get("announced") and now < ev["end_ts"]:
                return ("❌ Активный ивент уже идёт: «{}» до {}.\n"
                        "Одновременно — только один.").format(
                    ev["name"], time.strftime("%d.%m", time.gmtime(ev["end_ts"] + MSK_OFFSET)))
        db["events"].append({"name": name, "start_ts": start, "end_ts": end,
                             "fund": fund, "announced": False})
        chat_send(db, (
            "🎯 ИВЕНТ СОЗДАН\n" + LINE + "\n"
            "🏁 «{}»\n"
            "📅 Период: {} — {}\n"
            "💰 Призовой фонд: {}\n"
            "🏆 Приз — топ-3 по пробегу за период.\n"
            "Подробности: ивент"
        ).format(name,
                 time.strftime("%d.%m", time.gmtime(start + MSK_OFFSET)),
                 time.strftime("%d.%m", time.gmtime(end + MSK_OFFSET)),
                 fmt_km(fund)))
        return ("🎯 ИВЕНТ СОЗДАН\n"
                "🏁 «{}» — период {} — {}, фонд {}.\n"
                "Победители определятся автоматически по пробегу.").format(
            name, time.strftime("%d.%m", time.gmtime(start + MSK_OFFSET)),
            time.strftime("%d.%m", time.gmtime(end + MSK_OFFSET)), fmt_km(fund))

    if low.startswith("!штраф"):
        if not reply_user:
            return "Штраф ставится ответом на сообщение нарушителя."
        target = get_driver(db, reply_user)
        amount = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        rating_down(db, target, amount)
        return "⚠️ {}: штраф −{} к рейтингу (⭐{}).".format(
            target.get("name") or "водитель", amount, target["rating"])

    if low.startswith("!сброс рейтинга"):
        if not reply_user:
            return "Сброс ставится ответом на сообщение нарушителя."
        target = get_driver(db, reply_user)
        target["rating"] = 100
        target["penalties"] = 0
        return "🔄 {}: рейтинг и штрафы сброшены (⭐100).".format(
            target.get("name") or "водитель")

    if low == "!удалить":
        if not reply_user:
            return "Удаление регистрации — ответом на сообщение водителя."
        vk_str = str(reply_user)
        target = db["drivers"].get(vk_str)
        if not target:
            return "❌ У этого пользователя нет регистрации."
        if target.get("open_trip"):
            plate = target["open_trip"]["plate"]
            car = db["cars"].get(plate)
            if car:
                car["status"] = "стоит"
                car["driver_id"] = 0
                car["driver_name"] = ""
        if target.get("assigned_plate"):
            car = db["cars"].get(target["assigned_plate"])
            if car:
                car["assigned"] = None
        del db["drivers"][vk_str]
        return ("🗑 Регистрация удалена с полным обнулением:\n"
                "ник, счёт, пробег, статистика, закрепление.\n"
                "Машины освобождены. ID других водителей не изменились.")

    if low.startswith("!объявление"):
        body = text[len("!объявление"):].strip()
        if not body:
            return "Формат: !объявление текст рассылки"
        regs = [int(vk) for vk, d in db["drivers"].items() if registered(d)]
        for vk in regs:
            send(vk, "📢 ОБЪЯВЛЕНИЕ\n" + LINE + "\n" + body)
            time.sleep(0.35)
        return "📢 Объявление разослано {} зарегистрированным водителям.".format(len(regs))

    return None


# ---------------- Обработчик сообщений ----------------

def handle(db, user_id, text, reply_user, now):
    reply = None
    low = text.lower()
    d = get_driver(db, user_id)

    if text.startswith("!") and is_admin(user_id):
        reply = admin_command(db, user_id, text, reply_user, now)
        if reply:
            return reply

    if low == "помощь":
        return HELP

    if low.startswith("анкета"):
        parts = text.split()
        if len(parts) != 3 or not parts[2].isdigit() or len(parts[2]) != 6:
            return ("📋 Формат: анкета Ник 123456\n"
                    "Игровой ник и 6-значный номер игрового счёта.")
        name, account = parts[1], parts[2]
        for vk, other in db["drivers"].items():
            if vk != str(user_id) and (other.get("name") or "").lower() == name.lower():
                return "❌ Ник «{}» уже занят другим водителем.".format(name)
        d["name"] = name
        d["account"] = account
        if d.get("id") == 0:
            db["meta"]["drv_seq"] = int(db["meta"].get("drv_seq", 0)) + 1
            d["id"] = int(db["meta"]["drv_seq"])
        return ("✅ РЕГИСТРАЦИЯ ПРИНЯТА\n" + LINE + "\n"
                "👤 Ник: {}\n"
                "💳 Игровой счёт: {}\n"
                "🆔 Ваш ID в боте: {}\n" + LINE + "\n"
                "🚛 Выберите машину: гараж\n"
                "🛣 В путь: выписать путевку НОМЕР <пробег>").format(name, account, d["id"])

    if low == "мой id":
        return "🆔 Ваш VK ID: {}\n🆔 Ваш ID в боте: {}".format(user_id, d.get("id", "?"))

    if low.startswith("выписать путевку"):
        m = re.match(r"выписать\s+путевку\s+(\S+)\s+(\d+)", low)
        if not m:
            return ("🛣 Формат: выписать путевку НОМЕР <пробег>\n"
                    "Пример: выписать путевку Т740ТТ77 100\n"
                    "Список машин: гараж")
        if db.get("pause_until") and now < db["pause_until"]:
            return ("⏸ Выдача путёвок временно приостановлена\n"
                    "(контрольная сверка пробегов).\n"
                    "⏳ Возобновление: через {}").format(fmt_left(db["pause_until"] - now))
        return issue_trip(db, d, user_id, norm_plate(m.group(1)), int(m.group(2)))

    if low.startswith("сдать путевку"):
        m = re.match(r"сдать\s+путевку\s+(\S+)\s+(\d+)", low)
        if not m:
            return ("🛣 Формат: сдать путевку НОМЕР <пробег>\n"
                    "Пример: сдать путевку Т740ТТ77 250")
        return close_trip(db, d, user_id, norm_plate(m.group(1)), int(m.group(2)))

    if low.startswith("история"):
        token = text.split(" ", 1)[1].strip() if " " in text else ""
        if not token:
            return "📜 Формат: история НОМЕР — пример: история Т740ТТ77"
        return cmd_history(db, token)

    if low == "гараж":
        return fmt_garage(db)

    if low == "за сегодня":
        return cmd_today(db)

    if low == "профиль":
        if not registered(d):
            return "❌ Сначала зарегистрируйтесь: анкета Ник 123456"
        return cmd_profile(db, d, user_id)

    if low == "путевки":
        hist = d.get("history", [])
        if not hist:
            return "📭 Рейсов ещё не было. Начните: выписать путевку НОМЕР <пробег>"
        out = ["📋 ВАШИ РЕЙСЫ", LINE]
        for t in hist[-10:]:
            out.append("• {} — {} | {} — {} км".format(
                t["date"], t.get("model", ""), t.get("plate", ""), fmt_km(t["km"])))
        out.append(LINE)
        out.append("Показаны последние {}, всего: {}".format(min(10, len(hist)), len(hist)))
        return "\n".join(out)

    if low == "рейтинг":
        return cmd_rating(db)

    if low == "водители":
        return cmd_drivers(db)

    if low == "ивент":
        return cmd_event_view(db, now)

    if low == "бинды":
        return BINDS

    if low == "создать чат":
        if db["chat"].get("chat_id"):
            link = export_invite(db)
            if link:
                return ("💬 Чат бригады уже есть. Ссылка-приглашение:\n{}\n"
                        "Прислать снова: ссылка").format(link)
            return "💬 Чат бригады уже есть, но ссылка недоступна. Попробуйте: ссылка"
        created = call("messages.createChat", title=CHAT_TITLE)
        if created is None:
            return ("❌ Не удалось создать беседу через API. Обычно это право ключа:\n"
                    "перевыпустите токен с галочкой «Управление сообществом»\n"
                    "и напишите: создать чат")
        chat_id = int(created)
        call("messages.inviteChat", chat_id=chat_id, user_id=user_id)
        db["chat"]["chat_id"] = chat_id
        link = export_invite(db)
        if link:
            return ("💬 Беседа создана, вы добавлены!\n"
                    "🔗 Ссылка для коллег:\n{}\n"
                    "Прислать снова: ссылка").format(link)
        return "💬 Беседа создана, вы добавлены! Ссылку получите командой: ссылка"

    if low == "ссылка":
        link = export_invite(db)
        if link:
            return "🔗 Ссылка-приглашение в беседу бригады:\n{}".format(link)
        return ("❌ Беседа ещё не создана или ссылка недоступна.\n"
                "Создать: создать чат")

    if low in ("флот",):
        return cmd_today(db)

    return reply


def export_invite(db):
    chat_id = db["chat"].get("chat_id")
    if not chat_id:
        return None
    resp = call("messages.exportChatInvite", chat_id=chat_id)
    if isinstance(resp, dict) and resp.get("link"):
        db["chat"]["link"] = resp["link"]
        return resp["link"]
    return db["chat"].get("link")


def init_longpoll(gid):
    resp = call("groups.getLongPollServer", group_id=gid)
    if not resp or "server" not in resp:
        raise RuntimeError("groups.getLongPollServer не ответил — проверьте токен и права ключа")
    return resp["server"], resp["key"], resp["ts"]


def bg_loop():
    """Фоновый поток: сгорание путёвок, итоги ивентов, недельный отчёт."""
    while True:
        time.sleep(BURN_CHECK_EVERY)
        try:
            with _lock:
                db = load_db()
                msgs = maintenance(db)
                if msgs:
                    save_db(db)
            for vk, chat_id, text in msgs:
                if vk:
                    send(vk, text)
                if chat_id:
                    send(chat_id, text)
        except Exception as e:
            print("bg_loop error:", e)


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

    threading.Thread(target=bg_loop, daemon=True).start()

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
                with _lock:
                    db = load_db()
                    if db["chat"].get("chat_id") != peer_id:
                        db["chat"]["chat_id"] = peer_id
                    save_db(db)
                send(peer_id, WELCOME)
                continue

            if msg.get("from_id", 0) <= 0:
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            user_id = int(msg["from_id"])
            reply_user = 0
            rm = msg.get("reply_message") or (msg.get("fwd_messages") or [{}])[0]
            if rm and rm.get("from_id", 0) > 0:
                reply_user = int(rm["from_id"])

            with _lock:
                db = load_db()
                d = get_driver(db, user_id)
                now = time.time()
                try:
                    reply = handle(db, user_id, text, reply_user, now)
                    extra = []
                    if reply is None:
                        reply = ("❓ Не понял команду. Полный список: помощь")
                    for m in burn_trips(db):
                        if m[0] == user_id:
                            reply = reply + "\n\n" + m[2]
                        else:
                            extra.append(m)
                    for t in finalize_events(db, now):
                        extra.append((0, int(db["chat"].get("chat_id") or 0), t))
                    if msk_weekday(now) == 6:
                        today = msk_date(now)
                        if db["meta"].get("last_weekly") != today and \
                                (now + MSK_OFFSET) % 86400 >= 12 * 3600:
                            db["meta"]["last_weekly"] = today
                            report_text = weekly_report(db, now)
                            db["meta"]["weekly_fines"] = {}
                            extra.append((0, int(db["chat"].get("chat_id") or 0), report_text))
                    save_db(db)
                except Exception as e:
                    print("handle error:", e)
                    reply = "⚠️ Внутренняя ошибка. Напишите: помощь"
                    extra = []

            if peer_id > 0 or (text.startswith("!") and is_admin(user_id)):
                send(peer_id, reply)
            else:
                # в беседах бот отвечает всем командам (учёт общий)
                send(peer_id, reply)
            for vk, chat_id, t in extra:
                if vk:
                    send(vk, t)
                if chat_id:
                    send(chat_id, t)


if __name__ == "__main__":
    main()
