text += f"🔍 Отслеживание: {'ВКЛ' if settings.get('track_enabled', True) else 'ВЫКЛ'}\n\n"
    text += "📌 Команды:\n"
    text += "/stats - статистика\n"
    text += "/users - список пользователей\n"
    text += "/bot_on / bot_off - вкл/выкл бота\n"
    text += "/shop_on / shop_off - вкл/выкл магазин\n"
    text += "/track_on / track_off - вкл/выкл отслеживание\n"
    text += "/broadcast <текст> - рассылка\n"
    text += "/say <текст> - отправить сообщение от бота"
    bot.reply_to(m, text, parse_mode="Markdown")
TOKEN = "8621405739:AAEY7sSZduBmnG6j-FC5xXYKPebpSyyOYRM"
ADMIN_ID = 5631896858

@bot.message_handler(commands=['stats'])
def stats(m):
    if m.from_user.id != ADMIN_ID:
        return
    total_skins = sum(len(u["skins"]) for u in users.values())
    bot.reply_to(m, f"📊 Статистика:\n👥 Пользователей: {len(users)}\n🎯 Скинов в отслеживании: {total_skins}")

@bot.message_handler(commands=['users'])
def list_users(m):
    if m.from_user.id != ADMIN_ID:
        return
    if not users:
        bot.reply_to(m, "📭 Пользователей нет")
        return
    text = "👥 Список пользователей:\n"
    for uid, data in users.items():
        text += f"• {data.get('name', 'Без имени')} (ID: {uid}) — {len(data['skins'])} скинов\n"
    bot.reply_to(m, text[:4000])  # Telegram лимит 4096 символов

@bot.message_handler(commands=['bot_on', 'bot_off', 'shop_on', 'shop_off', 'track_on', 'track_off'])
def toggle(m):
    if m.from_user.id != ADMIN_ID:
        return
    cmd = m.text.replace("/", "")
    key_map = {
        "bot_on": "bot_enabled", "bot_off": "bot_enabled",
        "shop_on": "shop_enabled", "shop_off": "shop_enabled",
        "track_on": "track_enabled", "track_off": "track_enabled"
    }
    value = not cmd.endswith("_off")
    settings[key_map[cmd]] = value
    save_settings()
    status = "включён ✅" if value else "отключён ⛔"
    bot.reply_to(m, f"Команда {cmd.replace('_', ' ')}: {status}")

@bot.message_handler(commands=['broadcast'])
def broadcast(m):
    if m.from_user.id != ADMIN_ID:
        return
    text = m.text.replace("/broadcast", "").strip()
    if not text:
        bot.reply_to(m, "❌ Напишите текст для рассылки")
        return
    sent = 0
    for uid in users:
        try:
            bot.send_message(int(uid), f"📢 {text}")
            sent += 1
        except:
            pass
    bot.reply_to(m, f"✅ Рассылка отправлена {sent} пользователям")

@bot.message_handler(commands=['say'])
def say(m):
    if m.from_user.id != ADMIN_ID:
        return
    text = m.text.replace("/say", "").strip()
    if not text:
        bot.reply_to(m, "❌ Напишите текст")
        return
    bot.reply_to(m, text)

# ---------- ФОНОВАЯ ПРОВЕРКА ----------
def check_shop():
    while True:
        time.sleep(3600)
        if not settings.get("track_enabled", True):
            continue
        try:
            r = requests.get("https://fnbr.co/api/shop")
            data = r.json()
            shop = [i.get("name", "").lower() for i in data.get("data", {}).get("featured", [])]
            for uid, data in list(users.items()):
                for s in data["skins"][:]:
                    if s.lower() in shop:
                        bot.send_message(int(uid), f"🎉 {s} появился в магазине!")
                        users[uid]["skins"].remove(s)
                        save_users()
        except:
            pass

threading.Thread(target=check_shop, daemon=True).start()

# ---------- Flask для Render ----------
app = Flask(name)

@app.route('/')
def index():
    return "Bot is running"

if name == "main":
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    app.run(host='0.0.0.0', port=8080)
