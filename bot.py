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
PENALTY_ODOMETER = 10   # штраф за минус пробега (ошибка в показаниях одометра)
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


def norm_plate(s):
    return s.strip().upper().translate(LAT2CYR)


def fmt_dt(ts):
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(ts + MSK_OFFSET)) + " МСК"


def fmt_date(ts):
    return time.strftime("%d.%m.%Y", time.gmtime(ts + MSK_OFFSET))


def fmt_km(n):
    return "{:,}".format(int(n)).replace(",", " ")


HELP = (
    "Грузовой поток — команды:\n"
    "анкета Ник 123456 — регистрация: игровой ник и 6-значный номер игрового счёта\n"
    "выписать путевку Т740ТТ77 100 — открыть рейс (номер авто, пробег на выезде)\n"
    "сдать путевку Т740ТТ77 250 — закрыть рейс (номер авто, пробег на возврате)\n"
    "⚠️ Если пробег на возврате меньше выездного (минус пробег) — штраф −" + str(PENALTY_ODOMETER) + " к рейтингу\n"
    "путевки — история ваших рейсов\n"
    "профиль — ваша статистика и пробег за сутки по каждому авто\n"
    "гараж — весь парк: марки, номера, пробег, статус\n"
    "за сегодня — пробег бригады за последние 24 часа и всего парка\n"
    "ивент — активный ивент и текущая пятёрка, либо итоги (сутки после завершения)\n"
    "рейтинг — топ водителей\n"
    "водители — список зарегистрированных\n"
    "создать чат — бот сам создаст беседу бригады и пришлёт ссылку\n"
    "ссылка — прислать ссылку-приглашение в беседу ещё раз\n"
    "Админ-команды:\n"
    "!авто МАРКА НОМЕР пробег — добавить авто в парк (пробег на момент постановки на учёт)\n"
    "!пробег НОМЕР значение — скорректировать одометр авто\n"
    "!удалить авто НОМЕР — убрать авто из парка (если оно не в рейсе)\n"
    "!профиль Ник — показать профиль водителя по игровому нику\n"
    "!ивент Название 26.11-05.12 100000 — создать ивент (сроки, призовой фонд)\n"
    "!удалить — удалить регистрацию водителя с обнулением (ответом на его сообщение)\n"
    "!штраф [баллы] — штраф (по умолчанию 10), ответом на сообщение нарушителя\n"
    "!сброс рейтинга — сброс статистики, ответом на сообщение\n"
    "!объявление текст — рассылка всем зарегистрированным водителям в личку"
)

WELCOME = (
    "Привет! Я бот учёта рейсов «Грузовой поток».\n"
    "Для работы нужно зарегистрироваться: анкета Ник 123456\n"
    "(игровой ник и 6-значный номер счёта для премий)\n"
    "Полный список команд: помощь"
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


# ---------------- Команды ----------------

def issue_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not car:
        return ("Авто с номером {} нет в парке.\n"
                "Проверьте номер — команда: гараж").format(plate)
    if not registered(d):
        return ("Путёвки доступны только зарегистрированным водителям.\n"
                "Регистрация: анкета Ник 123456 (6 цифр — номер игрового счёта)")
    if d.get("open_trip"):
        return ("У вас уже открыта путёвка на {} ({}). Сначала закройте её:\n"
                "сдать путевку {} <пробег>").format(
            d["open_trip"]["plate"], car["model"], d["open_trip"]["plate"])
    if car["status"] == "в рейсе" and car["driver_id"] != user_id:
        return ("{} ({}) сейчас в рейсе у {}. Выберите другое авто.".format(
            plate, car["model"], car["driver_name"] or "другого водителя"))
    now = time.time()
    d["open_trip"] = {"plate": plate, "start": km, "ts": now}
    car["status"] = "в рейсе"
    car["driver_id"] = user_id
    car["driver_name"] = d["name"]
    return ("Путёвка выдана ✅\n"
            "Авто: {} / {}\n"
            "Пробег на выезде: {} км\n"
            "Дата и время выдачи: {}\n"
            "Медосмотр пройден. Водитель проинструктирован при выходе в рейс "
            "о соблюдении правил ПДД и ВПС.\n"
            "Счастливого пути, {}!").format(car["model"], plate, km, fmt_dt(now), d["name"])


def close_trip(db, d, user_id, plate, km):
    car = db["cars"].get(plate)
    if not registered(d):
        return ("Путёвки доступны только зарегистрированным водителям.\n"
                "Регистрация: анкета Ник 123456")
    trip = d.get("open_trip")
    if not trip:
        return ("Открытой путёвки нет. Сначала:\n"
                "выписать путевку НОМЕР пробег")
    if trip["plate"] != plate:
        return ("У вас открыта путёвка на {}, а не на {}.\n"
                "Закройте её: сдать путевку {} <пробег>").format(
            trip["plate"], plate, trip["plate"])

    # Минус пробега: ошибка в показаниях одометра — штраф, путёвка остаётся открытой
    if km < trip["start"]:
        d["rating"] = max(0, d["rating"] - PENALTY_ODOMETER)
        return ("⚠️ Ошибка в показаниях одометра: возврат ({}) меньше выезда ({}).\n"
                "Штраф за минус пробега: −{} к рейтингу (теперь {}).\n"
                "Путёвка остаётся открытой на {} с {} км.\n"
                "Проверьте одометр и повторите: сдать путевку {} <пробег>").format(
            km, trip["start"], PENALTY_ODOMETER, d["rating"], plate, trip["start"], plate)

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
    return ("Путёвка закрыта ✅\n"
            "Авто: {} / {}\n"
            "Пройдено: {} км (одометр авто теперь {} км)\n"
            "Дата и время закрытия: {}\n"
            "Всего по вам: {} км, рейсов: {} (+1 к рейтингу)").format(
        car["model"] if car else "—", plate, distance,
        car["mileage"] if car else "?", fmt_dt(now), d["total_km"], d["trips"])


def garage(db):
    if not db["cars"]:
        return "Парк пуст. Админ может добавить авто: !авто МАРКА НОМЕР пробег"
    lines = ["Парк автомобилей ({}):".format(len(db["cars"]))]
    for plate in sorted(db["cars"]):
        c = db["cars"][plate]
        if c.get("status") == "в рейсе" and c.get("driver_name"):
            status = "в рейсе ({})".format(c["driver_name"])
        else:
            status = "стоит"
        lines.append("• {} | {} | {} км | {}".format(
            c["model"], plate, c.get("mileage", 0), status))
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
    lines = ["Пробег за последние 24 часа:"]
    if per_driver:
        for name, km in sorted(per_driver.items(), key=lambda x: -x[1]):
            lines.append("• {} — {} км".format(name, km))
        lines.append("Итого бригадой: {} км".format(total))
    else:
        lines.append("За сутки рейсов не было.")
    lines.append("Пробег всего парка за всё время: {} км".format(park_km))
    return "\n".join(lines)


def fmt_profile(d):
    out = ("Ник: {}\nСчёт: {}\nРейтинг: {}\nРейсов: {}\nВсего км: {}".format(
        d["name"] or "не указан", d["account"] or "не указан",
        d["rating"], d["trips"], d["total_km"]))
    trip = d.get("open_trip")
    if trip:
        out += "\nОткрытая путёвка: {} с {} км (с {})".format(
            trip["plate"], trip["start"], fmt_dt(trip.get("ts", 0)))
    cutoff = time.time() - 86400
    per_car = {}
    for e in d.get("log") or []:
        if e.get("ts", 0) >= cutoff:
            per_car[e["plate"]] = per_car.get(e["plate"], 0) + e.get("km", 0)
    if per_car:
        out += "\nЗа минувшие сутки:"
        for plate, km in per_car.items():
            out += "\n• {} — {} км".format(plate, km)
    hist = d.get("history") or []
    if hist:
        out += "\nПоследние рейсы:"
        for t in hist[-3:]:
            out += "\n• {} — {} — {} км".format(t["date"], t.get("plate", "—"), t["km"])
    if not registered(d):
        out += "\n\nЗавершите регистрацию: анкета Ник 123456"
    return out


# ---------------- Ивенты ----------------

def brigade_peer(db):
    chat_id = db.get("chat", {}).get("chat_id")
    if chat_id:
        return 2000000000 + int(chat_id)
    return None


def top_by_period(db, start_ts, end_ts, top_n=3):
    rows = []
    for drv in db["drivers"].values():
        if not drv.get("name"):
            continue
        s = 0
        for e in drv.get("log") or []:
            if start_ts <= e.get("ts", 0) <= end_ts:
                s += e.get("km", 0)
        if s:
            rows.append((drv["name"], s))
    rows.sort(key=lambda x: (-x[1], x[0].lower()))
    return rows[:top_n]


def event_results_text(ev, rows):
    lines = ["🏆 Ивент «{}» завершён!".format(ev["name"]),
             "Призовой фонд: {}".format(fmt_km(ev["fund"]))]
    if rows:
        lines.append("Топ-3 по пробегу:")
        for i, (name, km) in enumerate(rows, 1):
            lines.append("{}. {} — {} км".format(i, name, km))
    else:
        lines.append("За время ивента закрытых рейсов не было.")
    lines.append("Итоги доступны ещё 24 часа по команде: ивент")
    return "\n".join(lines)


def parse_event_date(s, end_of_day):
    mm = re.match(r"^(\d{1,2})\.(\d{2})$", s)
    if not mm:
        return None
    dd, mon = int(mm.group(1)), int(mm.group(2))
    if not (1 <= dd <= 31 and 1 <= mon <= 12):
        return None
    y = time.gmtime(time.time() + MSK_OFFSET).tm_year
    if end_of_day:
        ts = calendar.timegm((y, mon, dd, 23, 59, 59, 0, 0, 0)) - MSK_OFFSET
    else:
        ts = calendar.timegm((y, mon, dd, 0, 0, 0, 0, 0, 0)) - MSK_OFFSET
    return ts


def create_event(db, text):
    for ev in db["events"]:
        if ev.get("status") == "active":
            return ("Уже идёт ивент «{}» (до {}). Сначала дождитесь его завершения.".format(
                ev["name"], fmt_date(ev["end_ts"])))
    mm = re.match(
        r"^!ивент\s+(.+?)\s+(\d{1,2}\.\d{2})\s*[-—–]\s*(\d{1,2}\.\d{2})\s+(\d+)\s*$",
        text, re.IGNORECASE)
    if not mm:
        return "Формат: !ивент Название 26.11-05.12 100000"
    name = mm.group(1).strip()
    fund = int(mm.group(4))
    start = parse_event_date(mm.group(2), False)
    end = parse_event_date(mm.group(3), True)
    if start is None or end is None:
        return "Не понял сроки. Формат дат: 26.11-05.12"
    if end <= start:
        # конец раньше начала — считаем, что ивент уходит на следующий год
        end += 366 * 86400
    now = time.time()
    ev = {"name": name, "fund": fund, "start_ts": start, "end_ts": end,
          "status": "active", "done_ts": 0, "results": ""}
    db["events"].append(ev)
    save_db(db)
    return ("Ивент создан ✅\n"
            "Название: {}\n"
            "Период: {} — {}\n"
            "Призовой фонд: {}\n\n"
            "По окончании срока бот объявит в чате топ-3 водителей по пробегу. "
            "Итоги будут доступны сутки по команде: ивент").format(
        name, fmt_date(start), fmt_date(end), fmt_km(fund))


def event_status(db):
    now = time.time()
    for ev in db.get("events", []):
        if ev.get("status") == "active" and now < ev.get("end_ts", 0):
            left = ev["end_ts"] - now
            hours = int(left // 3600)
            mins = int((left % 3600) // 60)
            rows = top_by_period(db, ev["start_ts"], now, 5)
            out = ("🏆 Ивент «{}» идёт!\n"
                   "Период: {} — {}\n"
                   "Призовой фонд: {}\n"
                   "До конца: {} ч {} мин").format(
                ev["name"], fmt_date(ev["start_ts"]), fmt_date(ev["end_ts"]),
                fmt_km(ev["fund"]), hours, mins)
            if rows:
                out += "\nТекущая пятёрка по пробегу:"
                for i, (name, km) in enumerate(rows, 1):
                    out += "\n{}. {} — {} км".format(i, name, km)
            else:
                out += "\nПока никто не закрыл рейс — всё впереди!"
            return out
    for ev in db.get("events", []):
        if ev.get("status") == "done" and now - ev.get("done_ts", 0) <= EVENT_HISTORY_HOURS * 3600:
            return "Итоги завершённого ивента:\n\n" + (ev.get("results") or "Итоги недоступны.")
    return ("Активных ивентов нет.\n"
            "Ждём следующий — совсем скоро!")


def check_events(db):
    """Завершение просроченных ивентов и очистка истории старше суток.
    Возвращает (изменения, список сообщений для отправки)."""
    now = time.time()
    changed = False
    sends = []
    for ev in db.get("events", []):
        if ev.get("status") == "active" and now >= ev.get("end_ts", 0):
            rows = top_by_period(db, ev["start_ts"], ev["end_ts"])
            text = event_results_text(ev, rows)
            ev["status"] = "done"
            ev["done_ts"] = now
            ev["results"] = text
            changed = True
            peer = brigade_peer(db)
            if peer:
                sends.append((peer, text))
    before = len(db["events"])
    db["events"] = [
        ev for ev in db.get("events", [])
        if not (ev.get("status") == "done" and now - ev.get("done_ts", 0) > EVENT_HISTORY_HOURS * 3600)
    ]
    if len(db["events"]) != before:
        changed = True
    return changed, sends


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
            return "Чат бригады уже есть. Ссылка-приглашение:\n{}".format(link)
        return "Чат бригады уже есть, но ссылка недоступна. Попробуйте команду: ссылка"
    created = call("messages.createChat", title=CHAT_TITLE)
    if created is None:
        return ("Не удалось создать беседу через API. Обычно это право ключа: "
                "перевыпустите токен с галочкой «Управление сообществом» и напишите: создать чат")
    chat_id = int(created)
    call("messages.inviteChat", chat_id=chat_id, user_id=user_id)
    db["chat"]["chat_id"] = chat_id
    link = export_invite(db)
    if link:
        return ("Беседа создана, вы добавлены!\n"
                "Ссылка для коллег (по ней они заходят сами):\n{}\n"
                "Ссылку можно получить снова командой: ссылка").format(link)
    return "Беседа создана, вы добавлены! Ссылку можно получить командой: ссылка"


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

        # фоновые задачи: завершение ивентов, очистка истории старше суток
        with _lock:
            db = load_db()
            changed, announcements = check_events(db)
            if changed:
                save_db(db)
        for peer, text in announcements:
            send(peer, text)

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
                        reply = ("Регистрация принята ✅\n"
                                 "Ник: {}\nИгровой счёт: {}\n\n"
                                 "Теперь доступны путёвки:\n"
                                 "выписать путевку НОМЕР пробег").format(d["name"], d["account"])
                    else:
                        reply = ("Формат: анкета Ник 123456\n"
                                 "Ник — ваш игровой никнейм, 123456 — 6-значный номер "
                                 "игрового счёта банка (по нему приходят премии).")

                elif re.match(r"^выписать\s+путевку\s+\S+\s+\d+", m):
                    mm = re.match(r"^выписать\s+путевку\s+(\S+)\s+(\d+)", m)
                    reply = issue_trip(db, d, user_id, norm_plate(mm.group(1)), int(mm.group(2)))

                elif re.match(r"^сдать\s+путевку\s+\S+\s+\d+", m):
                    mm = re.match(r"^сдать\s+путевку\s+(\S+)\s+(\d+)", m)
                    reply = close_trip(db, d, user_id, norm_plate(mm.group(1)), int(mm.group(2)))

                elif m in ("гараж", "парк"):
                    reply = garage(db)

                elif m in ("за сегодня", "сегодня"):
                    reply = today_stats(db)

                elif m == "ивент":
                    reply = event_status(db)

                elif m == "путевки":
                    hist = d.get("history") or []
                    if not hist:
                        reply = ("Рейсов пока нет. Откройте путёвку:\n"
                                 "выписать путевку НОМЕР пробег")
                    else:
                        lines = ["Ваши рейсы (последние {}):".format(len(hist))]
                        for t in hist:
                            lines.append("• {} — {} — {} км".format(
                                t["date"], t.get("plate", "—"), t["km"]))
                        reply = "\n".join(lines)

                elif m == "рейтинг":
                    drivers = [x for x in db["drivers"].values() if registered(x)]
                    if not drivers:
                        reply = "Рейтинг пока пуст. Зарегистрируйтесь: анкета Ник 123456"
                    else:
                        drivers.sort(key=lambda x: (-x["rating"], -x["total_km"]))
                        lines = ["Рейтинг водителей:"]
                        for i, x in enumerate(drivers[:15], 1):
                            lines.append("{}. {} — рейтинг {}, {} км".format(
                                i, x["name"], x["rating"], x["total_km"]))
                        reply = "\n".join(lines)

                elif m == "водители":
                    drivers = [x for x in db["drivers"].values() if registered(x)]
                    if not drivers:
                        reply = "Пока никто не зарегистрирован. Первый шаг: анкета Ник 123456"
                    else:
                        drivers.sort(key=lambda x: x["name"].lower())
                        lines = ["В бригаде {} водителей:".format(len(drivers))]
                        for x in drivers:
                            lines.append("• {} — счёт {}".format(x["name"], x["account"]))
                        reply = "\n".join(lines)

                elif m == "создать чат":
                    reply = create_chat(db, user_id)

                elif m == "ссылка":
                    if not db["chat"].get("chat_id"):
                        reply = "Чат ещё не создан. Напишите: создать чат"
                    else:
                        link = export_invite(db)
                        reply = ("Ссылка-приглашение в чат бригады:\n{}".format(link)
                                 if link else "Не удалось получить ссылку. Попробуйте позже.")

                elif m == "профиль":
                    reply = fmt_profile(d)

                elif text.startswith("!"):
                    if user_id not in ADMIN_IDS:
                        reply = "Команда только для админов."
                    else:
                        target_id = None
                        rep = msg.get("reply_message")
                        if rep:
                            target_id = rep.get("from_id")

                        if m.startswith("!авто"):
                            mm = re.match(r"^!авто\s+(.+)\s+(\S+)\s+(\d+)\s*$", text, re.IGNORECASE)
                            if mm:
                                model = mm.group(1).strip()
                                plate = norm_plate(mm.group(2))
                                mileage = int(mm.group(3))
                                if plate in db["cars"]:
                                    reply = ("Авто {} уже в парке. "
                                             "Скорректировать пробег: !пробег {} {}").format(
                                        plate, plate, mileage)
                                else:
                                    db["cars"][plate] = {
                                        "model": model, "mileage": mileage,
                                        "status": "стоит", "driver_id": 0, "driver_name": "",
                                    }
                                    reply = "Авто добавлено в парк: {} / {} ({} км).".format(
                                        model, plate, mileage)
                            else:
                                reply = "Формат: !авто МАРКА НОМЕР пробег"

                        elif m.startswith("!пробег"):
                            mm = re.match(r"^!пробег\s+(\S+)\s+(\d+)", m)
                            if mm:
                                plate = norm_plate(mm.group(1))
                                if plate in db["cars"]:
                                    db["cars"][plate]["mileage"] = int(mm.group(2))
                                    reply = "Одометр {} ({}) установлен: {} км.".format(
                                        plate, db["cars"][plate]["model"], mm.group(2))
                                else:
                                    reply = "Авто {} не найдено. Список: гараж".format(plate)
                            else:
                                reply = "Формат: !пробег НОМЕР значение"

                        elif m.startswith("!удалить авто"):
                            mm = re.match(r"^!удалить\s+авто\s+(\S+)", m)
                            if not mm:
                                reply = "Формат: !удалить авто НОМЕР (список: гараж)"
                            else:
                                plate = norm_plate(mm.group(1))
                                car = db["cars"].get(plate)
                                if not car:
                                    reply = "Авто {} не найдено. Список: гараж".format(plate)
                                elif car.get("status") == "в рейсе":
                                    reply = ("{} ({}) сейчас в рейсе у {}. "
                                             "Сначала закройте путёвку.").format(
                                        plate, car["model"], car.get("driver_name") or "водителя")
                                else:
                                    db["cars"].pop(plate, None)
                                    reply = "Авто удалено из парка: {} / {}.".format(
                                        car["model"], plate)

                        elif m.startswith("!профиль"):
                            mm = re.match(r"^!профиль\s+(\S+)", m)
                            if not mm:
                                reply = "Формат: !профиль Ник (список ников: водители)"
                            else:
                                nick = mm.group(1).lower()
                                found = None
                                for drv in db["drivers"].values():
                                    if (drv.get("name") or "").lower() == nick:
                                        found = drv
                                        break
                                reply = fmt_profile(found) if found else \
                                    "Водитель «{}» не найден. Список ников: водители".format(mm.group(1))

                        elif m.startswith("!ивент"):
                            reply = create_event(db, text)

                        elif m.startswith("!объявление"):
                            body = text[len("!объявление"):].strip()
                            if not body:
                                reply = "Формат: !объявление <текст>"
                            else:
                                ok = 0
                                total = 0
                                for uid, drv in db["drivers"].items():
                                    if registered(drv):
                                        total += 1
                                        resp = call("messages.send",
                                                    user_id=int(uid),
                                                    message="Объявление:\n" + body,
                                                    random_id=random.randint(0, 2 ** 31))
                                        if resp is not None:
                                            ok += 1
                                        time.sleep(0.3)
                                reply = "Объявление отправлено {} из {} водителей.".format(ok, total)

                        elif target_id:
                            t = get_driver(db, int(target_id))
                            if m.startswith("!штраф"):
                                mm = re.search(r"!штраф\s+(\d+)", m)
                                amount = int(mm.group(1)) if mm else 10
                                t["rating"] = max(0, t["rating"] - amount)
                                reply = "Штраф −{}. Рейтинг: {}".format(amount, t["rating"])
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
                                reply = "Рейтинг и статистика сброшены."
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
                                reply = ("Регистрация {} удалена полностью: "
                                         "ник, счёт, пробег и статистика обнулены.").format(name)
                            else:
                                reply = ("Админ-команды (ответом на сообщение): "
                                         "!штраф [баллы], !сброс рейтинга, !удалить")
                        else:
                            reply = ("Для !штраф, !сброс рейтинга и !удалить ответьте (reply) "
                                     "на сообщение водителя.\nОстальные: !авто, !пробег, "
                                     "!удалить авто, !профиль, !ивент, !объявление.")

                if reply is not None:
                    save_db(db)
                    send(peer_id, reply)


if __name__ == "__main__":
    main()
