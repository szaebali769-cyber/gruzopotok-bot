# -*- coding: utf-8 -*-
"""
Грузовой поток — бот ВК для учёта путёвок и рейтинга водителей.
Работает напрямую через VK API (requests), без библиотеки vk_api.
Ключ доступа: переменная окружения BOT_TOKEN (или TOKEN).
ID админов: переменная окружения ADMINS (через запятую).
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

ADMIN_IDS = []
for chunk in (os.environ.get("ADMINS") or "").replace(" ", "").split(","):
    if chunk.isdigit():
        ADMIN_IDS.append(int(chunk))

DATA_FILE = os.path.join(DATA_DIR, "db.json")
os.makedirs(DATA_DIR, exist_ok=True)

_lock = threading.Lock()

HELP = (
    "Грузовой поток — команды:\n"
    "анкета Имя №ТС — регистрация (пример: анкета Виктор А123ВС)\n"
    "профиль — ваша статистика\n"
    "рейтинг — топ водителей\n"
    "выписать путевку 100 — открыть рейс (пробег на выезде)\n"
    "закрыть путевку 250 — закрыть рейс (пробег на возврате)\n"
    "Админ-команды (только для админов):\n"
    "!штраф — штраф (ответьте на сообщение нарушителя)\n"
    "!сброс рейтинга — сброс статистики (ответьте на сообщение нарушителя)"
)


def call(method, **params):
    params["access_token"] = TOKEN
    params["v"] = API_V
    try:
        r = requests.post(API_URL + method, data=params, timeout=15)
        data = r.json()
    except Exception as e:
        print("network error:", e)
        return None
    if "error" in data:
        print("VK error:", data["error"].get("error_msg"))
        return None
    return data.get("response")


def load_db():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict) or "drivers" not in data:
                raise ValueError
            return data
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return {"drivers": {}}


def save_db(db):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def get_driver(db, user_id):
    return db["drivers"].setdefault(str(user_id), {
        "name": "",
        "truck": "",
        "total_km": 0,
        "trips": 0,
        "rating": 100,
        "open_trip": None,
    })


def send(peer_id, text):
    resp = call("messages.send",
                peer_id=peer_id,
                message=text,
                random_id=random.randint(0, 2 ** 31))
    if resp is None:
        print("send error to peer", peer_id)


def fmt_profile(d):
    trip = ""
    if d["open_trip"]:
        trip = "\nОткрытая путёвка: выезд на {} км".format(d["open_trip"])
    return (
        "Имя: {}\nАвто: {}\n"
        "Рейтинг: {}\nРейсов: {}\nВсего км: {}{}".format(
            d["name"] or "не указано", d["truck"] or "не указано",
            d["rating"], d["trips"], d["total_km"], trip
        )
    )


def init_longpoll():
    resp = call("groups.getLongPollServer")
    if not resp:
        raise RuntimeError("groups.getLongPollServer не ответил — проверьте токен")
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

    # включаем приём сообщений через Long Poll (если есть право)
    call("groups.setLongPollSettings", group_id=GROUP_ID,
         enabled=1, message_new=1, api_version=API_V)

    server, key, ts = init_longpoll()
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
                server, key, ts = init_longpoll()
                continue
            time.sleep(3)
            continue

        ts = data.get("ts", ts)

        for update in data.get("updates", []):
            if update.get("type") != "message_new":
                continue
            msg = update.get("object", {}).get("message", {})
            if msg.get("from_id", 0) <= 0:
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            user_id = int(msg["from_id"])
            peer_id = int(msg.get("peer_id") or user_id)

            with _lock:
                db = load_db()
                d = get_driver(db, user_id)
                m = text.lower()
                reply = None

                if m == "помощь":
                    reply = HELP

                elif m.startswith("анкета"):
                    found = re.search(r"анкета\s+(\S+)(?:\s+(\S+))?", text, re.IGNORECASE)
                    if found:
                        d["name"] = found.group(1)
                        if found.group(2):
                            d["truck"] = found.group(2)
                        reply = "Анкета принята: {} / {}".format(
                            d["name"], d["truck"] or "авто не указано")
                    else:
                        reply = "Формат: анкета Имя №ТС"

                elif re.match(r"^выписать\s+путевку\s+(\d+)", m):
                    km = int(re.search(r"\d+", m).group())
                    if d["open_trip"]:
                        reply = ("У вас уже открыта путёвка с {} км. "
                                 "Сначала закройте её: закрыть путевку <пробег>").format(d["open_trip"])
                    else:
                        d["open_trip"] = km
                        reply = "Путёвка открыта. Выезд на {} км. Счастливого пути, {}!".format(
                            km, d["name"] or "водитель")

                elif re.match(r"^закрыть\s+путевку\s+(\d+)", m):
                    km = int(re.search(r"\d+", m).group())
                    if not d["open_trip"]:
                        reply = "Открытой путёвки нет. Сначала: выписать путевку <пробег>"
                    elif km < d["open_trip"]:
                        reply = ("Пробег на возврате ({}) меньше пробега на выезде ({}). "
                                 "Проверьте цифры.").format(km, d["open_trip"])
                    else:
                        distance = km - d["open_trip"]
                        d["open_trip"] = None
                        d["total_km"] += distance
                        d["trips"] += 1
                        reply = ("Путёвка закрыта. Пройдено {} км. "
                                 "Всего: {} км, рейсов: {}").format(distance, d["total_km"], d["trips"])

                elif m == "рейтинг":
                    drivers = [x for x in db["drivers"].values() if x["name"]]
                    if not drivers:
                        reply = "Рейтинг пока пуст. Зарегистрируйтесь: анкета Имя №ТС"
                    else:
                        drivers.sort(key=lambda x: (-x["rating"], -x["total_km"]))
                        lines = ["Рейтинг водителей:"]
                        for i, x in enumerate(drivers[:15], 1):
                            lines.append("{}. {} — рейтинг {}, {} км".format(
                                i, x["name"], x["rating"], x["total_km"]))
                        reply = "\n".join(lines)

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
                        if target_id:
                            t = get_driver(db, int(target_id))
                            if m.startswith("!штраф"):
                                t["rating"] = max(0, t["rating"] - 10)
                                reply = "Штраф применён. Рейтинг: {}".format(t["rating"])
                            elif m.startswith("!сброс рейтинга"):
                                t["rating"] = 100
                                t["total_km"] = 0
                                t["trips"] = 0
                                t["open_trip"] = None
                                reply = "Рейтинг и статистика сброшены."
                            else:
                                reply = "Админ-команды: !штраф, !сброс рейтинга (с ответом на сообщение)."
                        else:
                            reply = "Ответьте (reply) на сообщение нарушителя и повторите команду."

                if reply is not None:
                    save_db(db)
                    send(peer_id, reply)


if __name__ == "__main__":
    main()
