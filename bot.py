# -*- coding: utf-8 -*-
# Бот организации «Грузовой поток» (МТА Провинция)
# Путёвки, рейтинг, роли, анкеты
# Запуск: python bot.py   (нужен pip install vk_api)

import sqlite3, re, random, threading, time, datetime
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType

TOKEN = "vk1.a.dJP7Fr6bqByJ8DzvivBZsD2eYVgyCpmHphEwXREqSoYyhYWetIZqirygq1wMXT3LwE0_5NXMfIVF7ge2C6NCrGt1QVeZTHVGvw8ZzbuxQBX91HvlJF74smwRy-Oh47nB0vOUBK-GKyFz9QcYGIHErDBe459TneW0Gb2YbThlAmukaIWUN5ZDq4uCqUkvdtQINKCVdjlSGfFbuReRYha4DQ"
ADMINS = [deniskhomutov,suetologerende]          # VK id руководства

PTS_TRIP     = 10     # баллы за закрытую путёвку
PTS_KM       = 0.01   # баллы за каждый км (100 км = 1 балл)
BURN_PENALTY = 15     # минус за сгоревшую путёвку
MAX_TRIPS    = 5      # максимум путёвок в сутки на игрока

MSK = datetime.timezone(datetime.timedelta(hours=3))

db = sqlite3.connect("gruzopotok.db", check_same_thread=False)
lock = threading.Lock()
cur = db.cursor()
cur.executescript("""
CREATE TABLE IF NOT EXISTS users(vk_id INTEGER PRIMARY KEY, nick TEXT, account TEXT, reg_at TEXT);
CREATE TABLE IF NOT EXISTS roles_list(name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS user_roles(vk_id INTEGER PRIMARY KEY, role TEXT);
CREATE TABLE IF NOT EXISTS trips(id INTEGER PRIMARY KEY AUTOINCREMENT,
    vk_id INTEGER, opened_at TEXT, open_date TEXT, start_km REAL,
    closed_at TEXT, end_km REAL, km REAL, status TEXT);
CREATE TABLE IF NOT EXISTS adjustments(id INTEGER PRIMARY KEY AUTOINCREMENT,
    vk_id INTEGER, delta REAL, comment TEXT, by_id INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS fines(id INTEGER PRIMARY KEY AUTOINCREMENT,
    vk_id INTEGER, amount REAL, reason TEXT, by_id INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
""")
# стартовый список ролей
for r in ["Первый заведующий", "Водитель", "Водитель НЕО",
          "Водитель х740xx77", "Водитель м740мм77", "Водитель с740сс77",
          "Водитель о740оо77", "Водитель т740тт77", "Водитель в740вв77",
          "Водитель у740уу77", "Водитель НЕО к740кк77",
          "Водитель НЕО н740нн77", "Водитель НЕО а740аа77"]:
    cur.execute("INSERT OR IGNORE INTO roles_list VALUES(?)", (r,))
db.commit()

vk = vk_api.VkApi(token=TOKEN)
api = vk.get_api()
name_cache = {}

def now_s():
    return datetime.datetime.now(MSK).strftime("%d.%m.%Y %H:%M")

def today():
    return datetime.datetime.now(MSK).strftime("%d.%m.%Y")

def send(peer_id, text):
    api.messages.send(peer_id=peer_id, message=text, random_id=random.getrandbits(64))

def name(uid):
    if uid not in name_cache:
        u = api.users.get(user_ids=uid)[0]
        name_cache[uid] = f"{u['first_name']} {u['last_name']}"
    return name_cache[uid]

def tag(uid):
    return f"[id{uid}|{name(uid)}]"

def get_meta(key):
    r = cur.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None

def set_meta(key, value):
    cur.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, value))
    db.commit()

def rating(uid):
    r = cur.execute("""SELECT
        (SELECT COUNT(*) FROM trips WHERE vk_id=? AND status='closed'),
        (SELECT IFNULL(SUM(km),0) FROM trips WHERE vk_id=? AND status='closed'),
        (SELECT COUNT(*) FROM trips WHERE vk_id=? AND status='burned'),
        (SELECT IFNULL(SUM(delta),0) FROM adjustments WHERE vk_id=?),
        (SELECT IFNULL(SUM(amount),0) FROM fines WHERE vk_id=?)""",
        (uid, uid, uid, uid, uid)).fetchone()
    closed, km, burned, adj, fine = r
    return (closed * PTS_TRIP + km * PTS_KM - burned * BURN_PENALTY + adj - fine,
            closed, km, burned, adj, fine)

def find_target(text):
    m = re.search(r"
$$
id(\d+)\|", text)    return int(m.group(1)) if m else None# --- сгорание открытых путёвок в 00:00 МСК ---def burn_loop():    while True:        try:            t = datetime.datetime.now(MSK)            if t.hour == 0 and t.minute < 2 and get_meta("last_burn") != today():                with lock:                    rows = cur.execute("SELECT id, vk_id FROM trips WHERE status='open'").fetchall()                    for tid, uid in rows:                        cur.execute("UPDATE trips SET status='burned' WHERE id=?", (tid,))                    set_meta("last_burn", today())                    db.commit()                    if rows:                        for uid in {r[1] for r in rows}:                            print(f"Сгорела путёвка у {uid}")        except Exception as e:            print("burn error:", e)        time.sleep(60)threading.Thread(target=burn_loop, daemon=True).start()# --- основная логика ---def handle(text, user_id, peer_id):    t = text.lower()    is_admin = user_id in ADMINS    target = find_target(text)    uid = target if (target and is_admin) else user_id    # ----- АНКЕТА -----    m = re.search(r"анкета\s+(.+?)\s+(\d{6})(?:\D|)", text, re.IGNORECASE)    if m:        nick, acc = m.group(1).strip(), m.group(2)        if target and is_admin:  # админ правит чужую анкету            if not cur.execute("SELECT 1 FROM users WHERE vk_id=?", (uid,)).fetchone():                send(peer_id, f"У {tag(uid)} нет анкеты.")                return            cur.execute("UPDATE users SET nick=?, account=? WHERE vk_id=?", (nick, acc, uid))            db.commit()            send(peer_id, f"✏️ Анкета {tag(uid)} обновлена: ник {nick}, счёт {acc}.")            return        if cur.execute("SELECT 1 FROM users WHERE vk_id=?", (user_id,)).fetchone():            # старый игрок обновляет свои данные, роль не трогаем            cur.execute("UPDATE users SET nick=?, account=? WHERE vk_id=?", (nick, acc, user_id))            db.commit()            send(peer_id, f"✏️ {tag(user_id)}, данные обновлены: ник {nick}, счёт {acc}.")            return        cur.execute("INSERT INTO users VALUES(?,?,?,?)", (user_id, nick, acc, now_s()))        cur.execute("INSERT OR REPLACE INTO user_roles VALUES(?,?)", (user_id, "Водитель"))        db.commit()        send(peer_id, f"📋 Анкета принята, {tag(user_id)}! Ник: {nick}, счёт: {acc}.\n"                      f"Тебе присвоена роль «Водитель». Добро пожаловать в «Грузовой поток»! 🚚")        return    # ----- СМЕНА НИКА И СЧЁТА -----    m = re.match(r"^\s*ник\s+(.+)", text, re.IGNORECASE)    if m and not target:        if not cur.execute("SELECT 1 FROM users WHERE vk_id=?", (user_id,)).fetchone():            send(peer_id, f"{tag(user_id)}, сначала заполни анкету: анкета Ник 123456")            return        cur.execute("UPDATE users SET nick=? WHERE vk_id=?", (m.group(1).strip(), user_id))        db.commit()        send(peer_id, f"Ник {tag(user_id)} теперь: {m.group(1).strip()}")        return    m = re.match(r"^\s*сч[её]т\s+(\d{6})(?:\D|)", text, re.IGNORECASE)    if m and not target:        if not cur.execute("SELECT 1 FROM users WHERE vk_id=?", (user_id,)).fetchone():            send(peer_id, f"{tag(user_id)}, сначала заполни анкету: анкета Ник 123456")            return        cur.execute("UPDATE users SET account=? WHERE vk_id=?", (m.group(1), user_id))        db.commit()        send(peer_id, f"Счёт {tag(user_id)} обновлён: {m.group(1)}")        return    # ----- РОЛИ (админ) -----    m = re.match(r"^\s*!роль\s+id\d+\|[^]+\s+(.+)", text, re.IGNORECASE)    if m and is_admin and target:        role = m.group(1).strip()        if not cur.execute("SELECT 1 FROM roles_list WHERE name=?", (role,)).fetchone():            send(peer_id, f"Такой роли нет. Добавь её: !добавить роль {role}")            return        if not cur.execute("SELECT 1 FROM users WHERE vk_id=?", (target,)).fetchone():            send(peer_id, f"У {tag(target)} нет анкеты — сначала пусть заполнит её.")            return        cur.execute("INSERT OR REPLACE INTO user_roles VALUES(?,?)", (target, role))        db.commit()        send(peer_id, f"🏅 {tag(target)} теперь «{role}».")        return    if re.search(r"!снять\s+роль", t) and is_admin and target:        cur.execute("DELETE FROM user_roles WHERE vk_id=?", (target,))        db.commit()        send(peer_id, f"У {tag(target)} роль снята.")        return    m = re.match(r"^\s*!добавить\s+роль\s+(.+)", text, re.IGNORECASE)    if m and is_admin:        role = m.group(1).strip()        cur.execute("INSERT OR IGNORE INTO roles_list VALUES(?)", (role,))        db.commit()        send(peer_id, f"Роль «{role}» добавлена. Выдавать: !роль @игрок {role}")        return    m = re.match(r"^\s*!удалить\s+роль\s+(.+)", text, re.IGNORECASE)    if m and is_admin:        cur.execute("DELETE FROM roles_list WHERE name=?", (m.group(1).strip(),))        db.commit()        send(peer_id, "Роль удалена из списка.")        return    if re.fullmatch(r"\s*роли\W*", text):        rows = cur.execute("SELECT name FROM roles_list ORDER BY name").fetchall()        send(peer_id, "📋 Роли «Грузового потока»:\n" + "\n".join("• " + r[0] for r in rows))        return    # ----- ПУТЁВКА: ОТКРЫТИЕ -----    m = re.search(r"выписа\w*\s*(?:путев\w*\s*)?(?:лист)?\s*(\d+)", t)    if m:        if not cur.execute("SELECT 1 FROM users WHERE vk_id=?", (user_id,)).fetchone():            send(peer_id, f"{tag(user_id)}, сначала заполни анкету: анкета Ник 123456")            return        with lock:            open_trip = cur.execute("SELECT start_km FROM trips WHERE vk_id=? AND status='open'", (user_id,)).fetchone()            if open_trip:                send(peer_id, f"{tag(user_id)}, у тебя уже открыта путёвка (стартовый пробег {open_trip[0]:.0f} км).")                return            cnt = cur.execute("SELECT COUNT(*) FROM trips WHERE vk_id=? AND open_date=?", (user_id, today())).fetchone()[0]            if cnt >= MAX_TRIPS:                send(peer_id, f"{tag(user_id)}, дневной лимит путёвок ({MAX_TRIPS}) исчерпан.")                return            start = float(m.group(1))            cur.execute("INSERT INTO trips(vk_id, opened_at, open_date, start_km, status) VALUES(?,?,?,?, 'open')",                        (user_id, now_s(), today(), start))            db.commit()        send(peer_id, f"🚚 {tag(user_id)}, путёвка открыта. Стартовый пробег: {int(start)} км.\n"                      f"Не забудь закрыть до 00:00 МСК, иначе сгорит (−{BURN_PENALTY} к рейтингу).")        return    # ----- ПУТЁВКА: ЗАКРЫТИЕ -----    m = re.search(r"закрыл\w*\s*(?:путев\w*\s*)?(?:лист)?\s*(\d+)", t)    if m:        with lock:            trip = cur.execute("SELECT id, opened_at, start_km FROM trips WHERE vk_id=? AND status='open'", (user_id,)).fetchone()            if not trip:                send(peer_id, f"{tag(user_id)}, открытых путёвок нет.")                return            end = float(m.group(1))            km = end - trip[2]            if km <= 0:                send(peer_id, f"{tag(user_id)}, конечный пробег меньше стартового ({trip[2]:.0f} км). Проверь цифры.")                return            if km > 3000:                send(peer_id, f"{tag(user_id)}, пробег {km:.0f} км за рейс выглядит неправдоподобно. Уточни у руководства.")                return            cur.execute("UPDATE trips SET closed_at=?, end_km=?, km=?, status='closed' WHERE id=?",                        (now_s(), end, km, trip[0]))            db.commit()        _, closed, km_total, _, _, _ = rating(user_id)        send(peer_id, f"✅ {tag(user_id)}, путёвка закрыта: {km:.0f} км (с {trip[2]:.0f} до {end:.0f}).\n"                      f"Закрытых путёвок: {closed}, всего км: {km_total:.0f}.")        return    if is_admin and re.search(r"!отмена\s+путев\w*", t) and target:        cur.execute("DELETE FROM trips WHERE vk_id=? AND status='open'", (target,))        db.commit()        send(peer_id, f"Открытая путёвка {tag(target)} аннулирована без сгорания.")        return    # ----- ПОПРАВКИ И ШТРАФЫ (админ) -----    m = re.match(r"^\s*!поправка\s+id\d+\|[^]+\s*([+\-]?\d+(?:[.,]\d+)?)\s*(.*)", text, re.IGNORECASE)    if m and is_admin and target:        delta = float(m.group(1).replace(",", "."))        comment = m.group(2).strip() or "без комментария"        cur.execute("INSERT INTO adjustments VALUES(NULL,?,?,?,?,?)", (target, delta, comment, user_id, now_s()))        db.commit()        send(peer_id, f"⚖️ Рейтинг {tag(target)} скорректирован на {delta:+.1f} ({comment}).")        return    m = re.match(r"^\s*!штраф\s+id\d+\|[^]+
$$
\s+(\d+(?:[.,]\d+)?)\s*(.*)", text, re.IGNORECASE)
    if m and is_admin and target:
        amount = float(m.group(1).replace(",", "."))
        reason = m.group(2).strip() or "нарушение"
        cur.execute("INSERT INTO fines VALUES(NULL,?,?,?,?,?)", (target, amount, reason, user_id, now_s()))
        db.commit()
        send(peer_id, f"⚠️ {tag(target)} получает штраф {amount:.1f} к рейтингу: {reason}.")
        return

    # ----- СБРОС РЕЙТИНГА (админ) -----
    if re.search(r"!сброс\s+рейтинга", t) and is_admin:
        if target:  # сброс конкретному игроку
            cur.execute("DELETE FROM trips WHERE vk_id=?", (target,))
            cur.execute("DELETE FROM adjustments WHERE vk_id=?", (target,))
            cur.execute("DELETE FROM fines WHERE vk_id=?", (target,))
            db.commit()
            send(peer_id, f"🧹 Рейтинг {tag(target)} полностью обнулён: путёвки, поправки и штрафы удалены.")
        else:  # полный сброс всей организации
            cur.execute("DELETE FROM trips")
            cur.execute("DELETE FROM adjustments")
            cur.execute("DELETE FROM fines")
            db.commit()
            send(peer_id, "🧹 Рейтинг всей организации обнулён: путёвки, поправки и штрафы удалены.\n"
                          "Анкеты и роли игроков сохранены.")
        return

    # ----- ПРОФИЛЬ -----
    if re.search(r"\bпрофиль\b|\bстатус\b", t):
        uid2 = target or user_id
        if not cur.execute("SELECT 1 FROM users WHERE vk_id=?", (uid2,)).fetchone():
            send(peer_id, f"{tag(uid2)} не зарегистрирован (нет анкеты).")
            return
        u = cur.execute("SELECT nick, account FROM users WHERE vk_id=?", (uid2,)).fetchone()
        role_row = cur.execute("SELECT role FROM user_roles WHERE vk_id=?", (uid2,)).fetchone()
        rt = rating(uid2)
        fine_sum = cur.execute("SELECT IFNULL(SUM(amount),0) FROM fines WHERE vk_id=?", (uid2,)).fetchone()[0]
        open_trip = cur.execute("SELECT start_km FROM trips WHERE vk_id=? AND status='open'", (uid2,)).fetchone()
        msg = (f"👤 {tag(uid2)}\n"
               f"Ник: {u[0]} | Счёт: {u[1]}\n"
               f"Роль: {role_row[0] if role_row else '—'}\n"
               f"⭐ Рейтинг: {rt[0]:.1f}\n"
               f"Закрытых путёвок: {rt[1]} | Сгорело: {rt[3]} | Пробег: {rt[2]:.0f} км\n"
               f"Штрафы: {fine_sum:.1f}")
        if open_trip:
            msg += f"\n🚚 Открытая путёвка, стартовый пробег: {open_trip[0]:.0f} км"
        send(peer_id, msg)
        return

    # ----- РЕЙТИНГ -----
    if re.fullmatch(r"\s*рейтинг\W*|\s*топ\W*", t):
        rows = cur.execute("SELECT vk_id FROM users").fetchall()
        stats = sorted(((u3, rating(u3)[0]) for u3 in rows), key=lambda x: -x[1])
        lines = [f"{i+1}. {tag(u)} — {rt:.1f}" for i, (u, rt) in enumerate(stats[:15])]
        send(peer_id, "🏆 Рейтинг «Грузового потока»:\n" + "\n".join(lines))
        return

    # ----- ПОМОЩЬ -----
    if "помощь" in t or text.strip() == "!":
        send(peer_id, "📌 Команды:\n"
                      "анкета Ник 123456 — регистрация\n"
                      "ник НовоеИмя — сменить свой ник\n"
                      "счет 123456 — сменить свой игровой счёт\n"
                      "выписать путевку 45230 — открыть рейс\n"
                      "закрыть путевку 45870 — закрыть рейс\n"
                      "профиль / профиль @игрок — статистика и роль\n"
                      "рейтинг — топ организации\nроли — список ролей\n"
                      "Команды руководства начинаются с !")
        if is_admin:
            send(peer_id, "🔧 Команды руководства:\n"
                          "!роль @игрок Название — выдать роль\n"
                          "!снять роль @игрок — снять роль\n"
                          "!добавить роль Название — новая роль\n"
                          "!удалить роль Название — убрать роль\n"
                          "!анкета @игрок Ник 123456 — правка данных игрока\n"
                          "!поправка @игрок +10 причина — правка рейтинга\n"
                          "!штраф @игрок 10 причина — штраф\n"
                          "!отмена путевки @игрок — аннулировать открытую путёвку\n"
                          "!сброс рейтинга @игрок — обнулить рейтинг игроку\n"
                          "!сброс рейтинга — обнулить рейтинг всей организации")

# --- запуск ---
for event in VkLongPoll(vk).listen():
    if event.type == VkEventType.MESSAGE_NEW and event.to_me:
        try:
            handle(event.text, event.user_id, event.peer_id)
        except Exception as e:
            print("Ошибка:", e)
