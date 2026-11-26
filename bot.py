# -*- coding: utf-8 -*-
"""
Грузовой поток — бот ВК для учёта путёвок, парка автомобилей, водителей и ивентов.
Long Poll VK API (requests). Токен: переменная окружения BOT_TOKEN (или TOKEN).
ID админов: переменная окружения ADMINS (через запятую).
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
PENALTY_ODOMETER = 10     # штраф за минус пробега (ошибка в показаниях одометра)
EVENT_HISTORY_HOURS = 24  # сколько часов хранить итоги завершённого ивента

ADMIN_IDS = []
for chunk in (os.environ.get("ADMINS") or "").replace(" ", "").split(","):
    if chunk.isdigit():
        ADMIN_IDS.append(int(chunk))

DATA_FILE = os.path.join(DATA_DIR, "db.json")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()

# --- Парк автомобилей: марка, госномер (пробег ставится админом командой !пробег) ---
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

# Похожие латинские буквы -> кириллица, чтобы номера распознавались в любом регистре раскладки
LAT2CYR = str.maketrans("ABEKMHOPCTXY", "АВЕКМНОРСТХУ")

LINE = "━━━━━━━━━━━━━━"

HELP = (
    "🚚 ГРУЗОВОЙ ПОТОК — КОМАНДЫ\n" + LINE + "\n"
    "📋 Регистрация:\n"
    "анкета Ник 123456 — игровой ник + 6-значный номер счёта\n\n"
    "🛣 Путёвки:\n"
    "• выписать путевку Т740ТТ77 100 — выезд\n"
    "• сдать путевку Т740ТТ77 250 — возврат\n"
    "⚠️ Минус пробега — штраф −{} к рейтингу\n\n"
    "📊 Статистика:\n"
    "• профиль — ваши данные и пробег за сутки\n"
    "• путевки — история рейсов\n"
    "• гараж — весь парк: марка, номер, пробег, статус\n"
    "• за сегодня — пробег бригады за 24 часа\n"
    "• рейтинг — топ водителей\n"
    "• водители — кто в бригаде\n"
    "• ивент — активные ивенты и итоги\n\n"
    "💬 Чат бригады:\n"
    "• создать чат — бот создаст беседу и пришлёт ссылку\n"
    "• ссылка — прислать ссылку ещё раз\n\n"
    "🛠 Админ-команды:\n"
    "• !авто МАРКА НОМЕР пробег — добавить авто\n"
    "• !пробег НОМЕР значение — правка одометра\n"
    "• !удалить авто НОМЕР — убрать авто из парка\n"
    "• !профиль Ник — профиль водителя\n"
    "• !ивент Название 26.11-05.12 фонд — создать ивент\n"
    "• !штраф [баллы] — ответом на сообщение\n"
    "• !сброс рейтинга — ответом на сообщение\n"
    "• !удалить — удалить водителя, ответом на сообщение\n"
    "• !объявление текст — рассылка всем в личку"
).format(PENALTY_ODOMETER)

WELCOME = (
    "👋 Всем привет! Я бот учёта рейсов «Грузовой поток».\n\n"
    "Чтобы получать путёвки, зарегистрируйтесь:\n"
    "📋 анкета Ник 123456\n"
    "(игровой ник + 6-значный номер счёта для премий)\n\n"
    "Весь список команд: помощь"
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
    return data


def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_driver(db, user_id):
    d = db["drivers"].setdefault(str(user_id), {
        "name": "",
        "account": "",
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


def send(peer_id, text):
    resp = call("messages.send",
                peer_id=peer_id,
                message=text,
                random_id=random.randint(0, 2 ** 31))
    if resp is None:
        print("send error to peer", peer_id)


# ---------------- Вспомогательные ----------------

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


def find_driver_by_name(db, nick):
    nick = nick.strip().lower()
    for uid, drv in db["drivers"].items():
        if (drv.get("name") or "").lower() == nick:
            return uid, drv
    return None, None


def reg_error():
    return ("❌ Путёвки доступны только зарегистрированным водителям.\n\n"
            "📋 Зарегистрируйтесь:\n"
            "анкета Ник 123456\n"
            "(игровой ник + 6-значный номер счёта)")


# ---------------- Путёвки ----------------

def issue_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not car:
        return ("❌ Авто с номером {} нет в парке.\n\n"
                "🔍 Проверьте номер — вся техника здесь: гараж").format(plate)
    if not registered(d):
        return reg_error()
    if d.get("open_trip"):
        return ("❌ У вас уже открыта путёвка на {} ({}).\n\n"
                "Сначала закройте её:\n"
                "сдать путевку {} <пробег>").format(
            d["open_trip"]["plate"], car["model"], d["open_trip"]["plate"])
    if car["status"] == "в рейсе" and car["driver_id"] != user_id:
        return ("❌ {} ({}) сейчас в рейсе у {}.\n\n"
                "🚛 Свободные машины — команда: гараж").format(
            plate, car["model"], car["driver_name"] or "другого водителя")
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
            "Удачи в рейсе, {}! 🚛💨").format(
        car["model"], plate, km, fmt_dt(now), d["name"])


def close_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not registered(d):
        return reg_error()
    trip = d.get("open_trip")
    if not trip:
        return ("❌ Открытой путёвки нет.\n\n"
                "Сначала: выписать путевку НОМЕР пробег")
    if trip["plate"] != plate:
        return ("❌ У вас открыта путёвка на {}, а не на {}.\n\n"
                "Закройте её: сдать путевку {} <пробег>").format(
            trip["plate"], plate, trip["plate"])

    # Минус пробега: ошибка в показаниях одометра — штраф, путёвка остаётся открытой
    if km < trip["start"]:
        d["rating"] = max(0, d["rating"] - PENALTY_ODOMETER)
        return ("⚠️ ОШИБКА В ПОКАЗАНИЯХ ОДОМЕТРА\n" + LINE + "\n"
                "📉 Возврат: {} км, выезд был: {} км — минус {} км!\n"
                "💰 Штраф: −{} к рейтингу (теперь {})\n" + LINE + "\n"
                "🟢 Путёвка остаётся открытой: {} со {} км.\n"
                "🔁 Проверьте одометр и повторите:\n"
                "сдать путевку {} <пробег>").format(
            km, trip["start"], trip["start"] - km, PENALTY_ODOMETER,
            d["rating"], plate, trip["start"], plate)

    distance = km - trip["start"]
    now = time.time()
    d["open_trip"] = None
    d["total_km"] += distance
    d["trips"] += 1
    d["rating"] = min(100, d["rating"] + 1)
    d["history"] = (d.get("history") or [])[-49:]
    d["history"].append({
        "km": distance, "plate": plate, "ts": now,
        "date": time.strftime("%d.%m.%Y %H:%M", time.gmtime(now + MSK_OFFSET)),
    })
    d["log"] = (d.get("log") or [])[-299:]
    d["log"].append({"ts": now, "plate": plate, "km": distance})
    if car:
        car["mileage"] = car.get("mileage", 0) + distance
        car["status"] = "стоит"
        car["driver_id"] = 0
        car["driver_name"] = ""
    return ("🔴 ПУТЁВКА ЗАКРЫТА\n" + LINE + "\n"
            "🚛 Авто: {} | {}\n"
            "📏 Пройдено: {} км\n"
            "📊 Одометр авто: {} км\n"
            "🕒 Закрыта: {}\n" + LINE + "\n"
            "👤 {}: всего {} км, рейсов {} (+1 к рейтингу ⭐{})").format(
        car["model"] if car else "—", plate,
        fmt_km(distance), fmt_km(car["mileage"]) if car else "?",
        fmt_dt(now), d["name"], fmt_km(d["total_km"]), d["trips"], d["rating"])


# ---------------- Справочные команды ----------------

def garage(db):
    if not db["cars"]:
        return ("🚛 Парк пуст.\n\n"
                "🛠 Админ может добавить авто:\n!авто МАРКА НОМЕР пробег")
    lines = ["🚛 ПАРК АВТОМОБИЛЕЙ ({})".format(len(db["cars"])), LINE]
    for plate in sorted(db["cars"]):
        c = db["cars"][plate]
        if c.get("status") == "в рейсе" and c.get("driver_name"):
            status = "🔵 в рейсе ({})".format(c["driver_name"])
        else:
            status = "🟢 стоит"
        lines.append("{} | {} | {} км | {}".format(
            c["model"], plate, fmt_km(c.get("mileage", 0)), status))
    return "\n".join(lines)


def today_stats(db):
    cutoff = time.time() - 86400
    per_driver = {}
    total = 0
    for uid, drv in db["drivers"].items():
        s = 0
        for e in drv.get("log") or []:
            if e.get("ts", 0) >= cutoff:
                s += e.get("km", 0)
        if s:
            per_driver[drv.get("name") or uid] = s
            total += s
    park_km = sum(c.get("mileage", 0) for c in db["cars"].values())
    lines = ["📊 ПРОБЕГ ЗА СУТКИ", LINE]
    if per_driver:
        lines.append("🏆 За последние 24 часа:")
        for name, km in sorted(per_driver.items(), key=lambda x: -x[1]):
            lines.append("• {} — {} км".format(name, fmt_km(km)))
        lines.append("Итого бригадой: {} км".format(fmt_km(total)))
    else:
        lines.append("😴 За сутки рейсов не было.")
    lines.append("🚛 Весь парк за всё время: {} км".format(fmt_km(park_km)))
    return "\n".join(lines)


def fmt_profile(d):
    out = ("👤 ПРОФИЛЬ | {}\n" + LINE + "\n"
           "💳 Счёт: {}\n"
           "⭐ Рейтинг: {}\n"
           "🛣 Рейсов: {} | Всего: {} км").format(
        d["name"] or "не указан", d["account"] or "не указан",
        d["rating"], d["trips"], fmt_km(d["total_km"]))
    trip = d.get("open_trip")
    if trip:
        out += "\n🟢 Открытая путёвка: {} со {} км (с {})".format(
            trip["plate"], fmt_km(trip["start"]), fmt_dt(trip.get("ts", 0)))
    cutoff = time.time() - 86400
    per_car = {}
    for e in d.get("log") or []:
        if e.get("ts", 0) >= cutoff:
            per_car[e["plate"]] = per_car.get(e["plate"], 0) + e.get("km", 0)
    if per_car:
        out += "\n🕐 За минувшие сутки:"
        for plate, km in per_car.items():
            out += "\n• {} — {} км".format(plate, fmt_km(km))
    hist = d.get("history") or []
    if hist:
        out += "\n📜 Последние рейсы:"
        for t in hist[-3:]:
            out += "\n• {} | {} | {} км".format(t["date"], t.get("plate", "—"), fmt_km(t["km"]))
    if not registered(d):
        out += ("\n\n📋 Завершите регистрацию:\nанкета Ник 123456")
    return out


def trips_list(d):
    hist = d.get("history") or []
    if not hist:
        return ("📜 Рейсов пока нет.\n\n"
                "Откройте путёвку:\nвыписать путевку НОМЕР пробег")
    lines = ["📜 ВАШИ РЕЙСЫ (последние {})".format(len(hist)), LINE]
    for t in hist:
        lines.append("• {} | {} | {} км".format(t["date"], t.get("plate", "—"), fmt_km(t["km"])))
    return "\n".join(lines)


def rating_cmd(db):
    drivers = [x for x in db["drivers"].values() if registered(x)]
    if not drivers:
        return ("🏆 Рейтинг пока пуст.\n\n"
                "Первый шаг: анкета Ник 123456")
    drivers.sort(key=lambda x: (-x["rating"], -x["total_km"]))
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = ["🏆 РЕЙТИНГ ВОДИТЕЛЕЙ", LINE]
    for i, x in enumerate(drivers[:15], 1):
        lines.append("{} {} — ⭐{}, {} км".format(
            medals.get(i, "{}.".format(i)), x["name"], x["rating"], fmt_km(x["total_km"])))
    return "\n".join(lines)


def drivers_cmd(db):
    drivers = [x for x in db["drivers"].values() if registered(x)]
    if not drivers:
        return ("👥 В бригаде пока никого.\n\n"
                "Первый шаг: анкета Ник 123456")
    drivers.sort(key=lambda x: x["name"].lower())
    lines = ["👥 БРИГАДА ({})".format(len(drivers)), LINE]
    for x in drivers:
        lines.append("• {} — счёт {}".format(x["name"], x["account"]))
    return "\n".join(lines)


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
            return ("💬 Чат бригады уже есть.\n\n"
                    "🔗 Ссылка-приглашение:\n{}").format(link)
        return ("💬 Чат бригады уже есть, но ссылка недоступна.\n"
                "Попробуйте команду: ссылка")
    created = call("messages.createChat", title=CHAT_TITLE)
    if created is None:
        return ("❌ Не удалось создать беседу через API.\n\n"
                "Обычно это право ключа: перевыпустите токен\n"
                "с галочкой «Управление сообществом» и напишите: создать чат")
    chat_id = int(created)
    call("messages.inviteChat", chat_id=chat_id, user_id=user_id)
    db["chat"]["chat_id"] = chat_id
    link = export_invite(db)
    if link:
        return ("💬 Беседа создана, вы добавлены! 🎉\n\n"
                "🔗 Ссылка для коллег (по ней они заходят сами):\n{}\n\n"
                "Ссылку можно получить снова командой: ссылка").format(link)
    return ("💬 Беседа создана, вы добавлены! 🎉\n\n"
            "Ссылку можно получить командой: ссылка")


# ---------------- Ивенты ----------------

def parse_period(s):
    mm = re.match(r"^(\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})$", s.strip())
    if not mm:
        return None
    d1, m1, d2, m2 = (int(x) for x in mm.groups())
    y = int(time.strftime("%Y", time.gmtime(time.time() + MSK_OFFSET)))
    try:
        if not 1 <= d1 <= calendar.monthrange(y, m1)[1]:
            return None
        if not 1 <= d2 <= calendar.monthrange(y, m2)[1]:
            return None
    except Exception:
        return None
    start = calendar.timegm((y, m1, d1, 0, 0, 0)) - MSK_OFFSET
    end = calendar.timegm((y, m2, d2, 23, 59, 59)) - MSK_OFFSET
    if end <= start:
        end = calendar.timegm((y + 1, m2, d2, 23, 59, 59)) - MSK_OFFSET
    return start, end


def event_top(db, ev, limit=3):
    scores = {}
    for drv in db["drivers"].values():
        s = 0
        for e in drv.get("log") or []:
            if ev["start_ts"] <= e.get("ts", 0) <= ev["end_ts"]:
                s += e.get("km", 0)
        if s > 0 and drv.get("name"):
            scores[drv["name"]] = s
    return sorted(scores.items(), key=lambda x: -x[1])[:limit]


def create_event(db, text):
    mm = re.match(r"^!ивент\s+(.+?)\s+(\d{1,2}\.\d{1,2})-(\d{1,2}\.\d{1,2})\s+(\d+)\s*$",
                  text, re.IGNORECASE)
    if not mm:
        return ("🛠 Формат:\n"
                "!ивент Название 26.11-05.12 100000\n\n"
                "Пример: !ивент Зимний марафон 26.11-05.12 100000")
    now = time.time()
    active = [e for e in db["events"] if not e.get("finished") and now < e["end_ts"]]
    if active:
        return ("❌ Уже идёт ивент «{}».\n\n"
                "Он завершится {} — потом можно создать новый.").format(
            active[0]["name"], fmt_dt(active[0]["end_ts"]))
    period = parse_period(mm.group(2) + "-" + mm.group(3))
    if not period:
        return "❌ Не понял даты. Формат: 26.11-05.12 (день.месяц-день.месяц)"
    start_ts, end_ts = period
    ev = {
        "name": mm.group(1).strip(),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "fund": int(mm.group(4)),
        "peer_id": 0,
        "finished": False,
        "final_ts": None,
    }
    db["events"].append(ev)
    return ("🎯 ИВЕНТ СОЗДАН\n" + LINE + "\n"
            "🏁 «{}»\n"
            "📅 Сроки: {} — {}\n"
            "💰 Призовой фонд: {}\n\n"
            "📊 Участвуют все закрытые путёвки за период.\n"
            "По окончании бот сам объявит топ-3!").format(
        ev["name"], fmt_date(start_ts), fmt_date(end_ts), fmt_km(ev["fund"]))


def fmt_date(ts):
    return time.strftime("%d.%m.%Y", time.gmtime(ts + MSK_OFFSET))


def event_cmd(db):
    now = time.time()
    active = [e for e in db["events"] if not e.get("finished") and now <= e["end_ts"]]
    upcoming = [e for e in db["events"] if not e.get("finished") and now < e["start_ts"]]
    finished_recent = [e for e in db["events"] if e.get("finished") and e.get("final_ts")
                       and now - e["final_ts"] <= EVENT_HISTORY_HOURS * 3600]

    if finished_recent:
        ev = finished_recent[-1]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = ["🏁 ИТОГИ ИВЕНТА «{}»".format(ev["name"]), LINE]
        top = ev.get("top") or event_top(db, ev, 3)
        if top:
            for i, (name, km) in enumerate(top, 1):
                lines.append("{} {} — {} км".format(medals.get(i, "•"), name, fmt_km(km)))
        else:
            lines.append("За период не было закрытых путёвок.")
        lines.append("💰 Призовой фонд: {}".format(fmt_km(ev["fund"])))
        lines.append(LINE)
        lines.append("⏳ Итоги доступны ещё {} — потом бот их уберёт.".format(
            fmt_left(ev["final_ts"] + EVENT_HISTORY_HOURS * 3600 - now)))
        return "\n".join(lines)

    if active:
        ev = active[0]
        lines = ["🏁 ИВЕНТ «{}»".format(ev["name"]), LINE,
                 "📅 Сроки: {} — {}".format(fmt_date(ev["start_ts"]), fmt_date(ev["end_ts"])),
                 "💰 Призовой фонд: {}".format(fmt_km(ev["fund"])),
                 "⏳ До конца: {}".format(fmt_left(ev["end_ts"] - now)),
                 LINE, "🏆 Пятёрка лидеров:"]
        top = event_top(db, ev, 5)
        if top:
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            for i, (name, km) in enumerate(top, 1):
                lines.append("{} {} — {} км".format(medals.get(i, "•"), name, fmt_km(km)))
        else:
            lines.append("Пока ни одного закрытого рейса — время ваше! 🚛")
        return "\n".join(lines)

    if upcoming:
        ev = upcoming[0]
        return ("🏁 Ивент «{}» стартует {}\n📅 Сроки: {} — {}\n💰 Фонд: {}\n\n"
                "Все закрытые путёвки за период пойдут в зачёт!").format(
            ev["name"], fmt_dt(ev["start_ts"]),
            fmt_date(ev["start_ts"]), fmt_date(ev["end_ts"]), fmt_km(ev["fund"]))

    return ("😴 Активных ивентов нет.\n\n"
            "Ждём следующий — совсем скоро! 🏁")


def finalize_events(db):
    """Завершает истёкшие ивенты и убирает устаревшие итоги.
    Возвращает список (peer_id, текст) для отправки."""
    now = time.time()
    out = []
    for ev in db["events"]:
        if not ev.get("finished") and now >= ev["end_ts"]:
            ev["finished"] = True
            ev["final_ts"] = now
            ev["top"] = event_top(db, ev, 3)
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            lines = ["🏁 ИВЕНТ «{}» ЗАВЕРШЁН!".format(ev["name"]), LINE]
            if ev["top"]:
                for i, (name, km) in enumerate(ev["top"], 1):
                    lines.append("{} {} — {} км".format(medals.get(i, "•"), name, fmt_km(km)))
            else:
                lines.append("За период не было закрытых путёвок.")
            lines.append(LINE)
            lines.append("💰 Призовой фонд: {}".format(fmt_km(ev["fund"])))
            lines.append("🏆 Спасибо всем участникам! Итоги — командой: ивент")
            if ev.get("peer_id"):
                out.append((ev["peer_id"], "\n".join(lines)))
    before = len(db["events"])
    db["events"] = [e for e in db["events"]
                    if not (e.get("finished") and e.get("final_ts")
                            and now - e["final_ts"] > EVENT_HISTORY_HOURS * 3600)]
    return out, len(db["events"]) != before


# ---------------- Long Poll ----------------

def init_longpoll(gid):
    resp = call("groups.getLongPollServer", group_id=gid)
    if not resp or "server" not in resp:
        raise RuntimeError("groups.getLongPollServer не ответил — проверьте токен и права ключа")
    return resp["server"], resp["key"], resp["ts"]


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

    while True:
        # Ивенты: автоитоги по расписанию и чистка устаревших
        try:
            with _lock:
                db = load_db()
                outgoing, changed = finalize_events(db)
                if changed or outgoing:
                    save_db(db)
            for peer, text in outgoing:
                send(peer, text)
        except Exception as e:
            print("events tick error:", e)

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
            if action.get("type") == "chat_create":
                with _lock:
                    db = load_db()
                    db["chat"]["peer_id"] = peer_id
                    save_db(db)
                continue

            if msg.get("from_id", 0) <= 0:
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            user_id = int(msg["from_id"])
            is_chat = peer_id != user_id

            with _lock:
                db = load_db()
                d = get_driver(db, user_id)
                m = text.lower()
                reply = None

                if m == "помощь":
                    reply = HELP

                elif m.startswith("анкета"):
                    found = re.search(r"анкета\s+(\S+)\s+(\d{6})\s*$", text, re.IGNORECASE)
                    if found:
                        d["name"] = found.group(1)
                        d["account"] = found.group(2)
                        reply = ("✅ РЕГИСТРАЦИЯ ПРИНЯТА\n" + LINE + "\n"
                                 "👤 Ник: {}\n"
                                 "💳 Счёт: {}\n" + LINE + "\n"
                                 "🛣 Путёвки теперь доступны:\n"
                                 "выписать путевку НОМЕР пробег\n\n"
                                 "Добро пожаловать в бригаду, {}! 🚛").format(
                            d["name"], d["account"], d["name"])
                    else:
                        reply = ("📋 Формат регистрации:\n"
                                 "анкета Ник 123456\n\n"
                                 "Ник — ваш игровой никнейм,\n"
                                 "123456 — 6-значный номер игрового счёта банка\n"
                                 "(по нему приходят премии 💰)")

                elif re.match(r"^(выписать|открыть)\s+путевку\s+\S+\s+\d+", m):
                    mm = re.match(r"^(?:выписать|открыть)\s+путевку\s+(\S+)\s+(\d+)", m)
                    reply = issue_trip(db, d, user_id, norm_plate(mm.group(1)), int(mm.group(2)))

                elif re.match(r"^(сдать|закрыть)\s+путевку\s+\S+\s+\d+", m):
                    mm = re.match(r"^(?:сдать|закрыть)\s+путевку\s+(\S+)\s+(\d+)", m)
                    reply = close_trip(db, d, user_id, norm_plate(mm.group(1)), int(mm.group(2)))

                elif m in ("гараж", "парк"):
                    reply = garage(db)

                elif m in ("за сегодня", "сегодня"):
                    reply = today_stats(db)

                elif m == "путевки":
                    reply = trips_list(d)

                elif m == "рейтинг":
                    reply = rating_cmd(db)

                elif m == "водители":
                    reply = drivers_cmd(db)

                elif m == "ивент":
                    reply = event_cmd(db)
                    if reply and db["events"]:
                        for ev in db["events"]:
                            if not ev.get("peer_id") and is_chat:
                                ev["peer_id"] = peer_id

                elif m == "создать чат":
                    reply = create_chat(db, user_id)

                elif m == "ссылка":
                    if not db["chat"].get("chat_id"):
                        reply = ("💬 Чат ещё не создан.\n\n"
                                 "Напишите: создать чат")
                    else:
                        link = export_invite(db)
                        reply = ("🔗 Ссылка-приглашение в чат бригады:\n{}").format(link) \
                            if link else "❌ Не удалось получить ссылку. Попробуйте позже."

                elif m == "профиль":
                    reply = fmt_profile(d)

                elif text.startswith("!"):
                    if user_id not in ADMIN_IDS:
                        reply = ("🛠 Эта команда только для админов.\n"
                                 "Список доступных команд: помощь")
                    else:
                        target_id = None
                        rep = msg.get("reply_message")
                        if rep:
                            target_id = rep.get("from_id")

                        if m.startswith("!ивент"):
                            reply = create_event(db, text)

                        elif m.startswith("!авто"):
                            mm = re.match(r"^!авто\s+(.+)\s+(\S+)\s+(\d+)\s*$", text, re.IGNORECASE)
                            if mm:
                                model = mm.group(1).strip()
                                plate = norm_plate(mm.group(2))
                                mileage = int(mm.group(3))
                                if plate in db["cars"]:
                                    reply = ("❌ Авто {} уже в парке.\n\n"
                                             "🔧 Скорректировать пробег:\n"
                                             "!пробег {} {}").format(plate, plate, mileage)
                                else:
                                    db["cars"][plate] = {
                                        "model": model, "mileage": mileage,
                                        "status": "стоит", "driver_id": 0, "driver_name": "",
                                    }
                                    reply = ("🚛 АВТО ДОБАВЛЕНО В ПАРК\n" + LINE + "\n"
                                             "• {} | {}\n"
                                             "• Одометр на учёте: {} км").format(
                                        model, plate, fmt_km(mileage))
                            else:
                                reply = ("🛠 Формат:\n"
                                         "!авто МАРКА НОМЕР пробег\n"
                                         "Пример: !авто КАМАЗ 5490 НЕО Р740РР77 0")

                        elif m.startswith("!пробег"):
                            mm = re.match(r"^!пробег\s+(\S+)\s+(\d+)", m)
                            if mm:
                                plate = norm_plate(mm.group(1))
                                if plate in db["cars"]:
                                    db["cars"][plate]["mileage"] = int(mm.group(2))
                                    reply = ("🔧 Одометр {} ({}) установлен:\n📊 {} км").format(
                                        plate, db["cars"][plate]["model"], fmt_km(mm.group(2)))
                                else:
                                    reply = ("❌ Авто {} не найдено.\n\n"
                                             "🔍 Список техники: гараж").format(plate)
                            else:
                                reply = "🛠 Формат: !пробег НОМЕР значение"

                        elif m.startswith("!удалить авто"):
                            mm = re.match(r"^!удалить\s+авто\s+(\S+)", m)
                            if mm:
                                plate = norm_plate(mm.group(1))
                                car = db["cars"].get(plate)
                                if not car:
                                    reply = ("❌ Авто {} не найдено.\n\n"
                                             "🔍 Список техники: гараж").format(plate)
                                elif car.get("status") == "в рейсе":
                                    reply = ("❌ {} ({}) сейчас в рейсе у {}.\n\n"
                                             "Сначала закройте путёвку.").format(
                                        plate, car["model"],
                                        car.get("driver_name") or "другого водителя")
                                else:
                                    del db["cars"][plate]
                                    reply = ("🗑 АВТО УДАЛЕНО ИЗ ПАРКА\n" + LINE + "\n"
                                             "• {} | {} — убрано из списка.").format(
                                        car["model"], plate)
                            else:
                                reply = "🛠 Формат: !удалить авто НОМЕР (пример: !удалить авто Т740ТТ77)"

                        elif m.startswith("!профиль"):
                            nick = text[len("!профиль"):].strip()
                            if not nick:
                                reply = "🛠 Формат: !профиль Ник"
                            else:
                                uid, td = find_driver_by_name(db, nick)
                                if td is None:
                                    reply = ("❌ Водитель с ником «{}» не найден.\n\n"
                                             "🔍 Список бригады: водители").format(nick)
                                else:
                                    reply = fmt_profile(td)

                        elif m.startswith("!объявление"):
                            body = text[len("!объявление"):].strip()
                            if not body:
                                reply = "🛠 Формат: !объявление <текст>"
                            else:
                                ok = 0
                                total = 0
                                for uid, drv in db["drivers"].items():
                                    if registered(drv):
                                        total += 1
                                        resp = call("messages.send",
                                                    user_id=int(uid),
                                                    message=("📢 ОБЪЯВЛЕНИЕ\n" + LINE + "\n" + body),
                                                    random_id=random.randint(0, 2 ** 31))
                                        if resp is not None:
                                            ok += 1
                                        time.sleep(0.3)
                                reply = ("📢 Объявление отправлено {} из {} водителей.").format(ok, total)

                        elif target_id:
                            t = get_driver(db, int(target_id))
                            if m.startswith("!штраф"):
                                mm = re.search(r"!штраф\s+(\d+)", m)
                                amount = int(mm.group(1)) if mm else 10
                                t["rating"] = max(0, t["rating"] - amount)
                                reply = ("⚠️ Штраф −{} для {}\n⭐ Рейтинг теперь: {}").format(
                                    amount, t.get("name") or "водителя", t["rating"])
                            elif m.startswith("!сброс рейтинга"):
                                t["rating"] = 100
                                t["total_km"] = 0
                                t["trips"] = 0
                                trip = t.get("open_trip")
                                if trip:
                                    car = db["cars"].get(trip["plate"])
                                    if car:
                                        car["status"] = "стоит"
                                        car["driver_id"] = 0
                                        car["driver_name"] = ""
                                t["open_trip"] = None
                                t["history"] = []
                                t["log"] = []
                                reply = ("♻️ Статистика {} сброшена:\n"
                                         "⭐ рейтинг 100, 🛣 0 км, 0 рейсов.").format(
                                    t.get("name") or "водителя")
                            elif m.startswith("!удалить"):
                                trip = t.get("open_trip")
                                if trip:
                                    car = db["cars"].get(trip["plate"])
                                    if car:
                                        car["status"] = "стоит"
                                        car["driver_id"] = 0
                                        car["driver_name"] = ""
                                name = t.get("name") or str(target_id)
                                db["drivers"].pop(str(target_id), None)
                                reply = ("🗑 Регистрация {} удалена полностью.\n"
                                         "Ник, счёт, пробег и статистика обнулены.\n\n"
                                         "Вернуться в бригаду: анкета Ник 123456").format(name)
                            else:
                                reply = ("🛠 Админ-команды (ответом на сообщение):\n"
                                         "!штраф [баллы] • !сброс рейтинга • !удалить")
                        else:
                            reply = ("🛠 Для !штраф, !сброс рейтинга и !удалить\n"
                                     "ответьте (reply) на сообщение водителя.\n"
                                     "Остальные: !авто, !пробег, !удалить авто, !профиль, !ивент, !объявление")

                if reply is not None:
                    save_db(db)
                    send(peer_id, reply)


if __name__ == "__main__":
    main()
