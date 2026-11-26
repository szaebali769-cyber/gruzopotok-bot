# -*- coding: utf-8 -*-
"""
Грузовой поток — бот ВК для учёта путёвок и рейтинга водителей.
Ключ доступа берётся из переменной окружения BOT_TOKEN (или TOKEN),
ID админов — из переменной окружения ADMINS (через запятую).
"""

import os
import re
import json
import random
import threading

import vk_api
from vk_api.longpoll import VkBotLongPoll, VkEventType

TOKEN = (os.environ.get("BOT_TOKEN") or os.environ.get("TOKEN") or "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
GROUP_ID = int(os.environ.get("GROUP_ID") or 0)

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
    "!сброс рейтинга — обнулить рейтинг (ответьте на сообщение нарушителя)"
)


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


def send(api, peer_id, text):
    try:
        api.messages.send(peer_id=peer_id, message=text,
                          random_id=random.randint(0, 2 ** 31))
    except Exception as e:
        print("send error:", e)


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


def main():
    if not TOKEN:
        print("ERROR: переменная окружения BOT_TOKEN не задана")
        return

    session = vk_api.VkApi(token=TOKEN)
    api = session.get_api()

    global GROUP_ID
    if not GROUP_ID:
        info = api.groups.getById()
        GROUP_ID = int((info.get("groups") or info)[0]["id"])
    print("GROUP_ID =", GROUP_ID)

    longpoll = VkBotLongPoll(session, GROUP_ID)
    print("Бот запущен, ожидание сообщений...")

    for event in longpoll.listen():
        if event.type != VkEventType.MESSAGE_NEW or event.to_me:
            continue

        text = (event.text or "").strip()
        user_id = int(event.user_id)
        peer_id = event.peer_id
        if not text:
            continue

        with _lock:
            db = load_db()
            d = get_driver(db, user_id)
            msg = text.lower()
            reply = None

            if msg == "помощь":
                reply = HELP

            elif msg.startswith("анкета"):
                m = re.search(r"анкета\s+(\S+)(?:\s+(\S+))?", text, re.IGNORECASE)
                if m:
                    d["name"] = m.group(1)
                    if m.group(2):
                        d["truck"] = m.group(2)
                    reply = "Анкета принята: {} / {}".format(
                        d["name"], d["truck"] or "авто не указано")
                else:
                    reply = "Формат: анкета Имя №ТС"

            elif re.match(r"^выписать\s+путевку\s+(\d+)", msg):
                km = int(re.search(r"\d+", msg).group())
                if d["open_trip"]:
                    reply = ("У вас уже открыта путёвка с {} км. "
                             "Сначала закройте её: закрыть путевку <пробег>").format(d["open_trip"])
                else:
                    d["open_trip"] = km
                    reply = "Путёвка открыта. Выезд на {} км. Счастливого пути, {}!".format(
                        km, d["name"] or "водитель")

            elif re.match(r"^закрыть\s+путевку\s+(\d+)", msg):
                km = int(re.search(r"\d+", msg).group())
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

            elif msg == "рейтинг":
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

            elif msg == "профиль":
                reply = fmt_profile(d)

            elif text.startswith("!"):
                if user_id not in ADMIN_IDS:
                    reply = "Команда только для админов."
                else:
                    target_id = None
                    if event.reply_message:
                        target_id = event.reply_message.get("from_id")
                    if target_id:
                        t = get_driver(db, int(target_id))
                        if msg.startswith("!штраф"):
                            t["rating"] = max(0, t["rating"] - 10)
                            reply = "Штраф применён. Рейтинг: {}".format(t["rating"])
                        elif msg.startswith("!сброс рейтинга"):
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
                send(api, peer_id, reply)


if __name__ == "__main__":
    main()
