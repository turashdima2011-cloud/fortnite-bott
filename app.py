import telebot
import requests
import time
import threading
from flask import Flask

TOKEN = "8621405739:AAGq040-5rKvTEqvAv2Ykw6wOL7"
bot = telebot.TeleBot(TOKEN)
users = {}

@bot.message_handler(commands=['start'])
def start(m):
    bot.reply_to(m, "Бот работает! Команды: /shop, /track, /mylist")

@bot.message_handler(commands=['shop'])
def shop(m):
    try:
        r = requests.get("https://fnbr.co/api/shop")
        data = r.json()
        text = "Магазин:\n"
        for item in data.get("data", {}).get("featured", [])[:3]:
            text += f"{item.get('name')}\n"
        bot.reply_to(m, text)
    except:
        bot.reply_to(m, "Ошибка")

@bot.message_handler(commands=['track'])
def track(m):
    skin = m.text.replace("/track", "").strip()
    if not skin:
        bot.reply_to(m, "Пример: /track Black Knight")
        return
    uid = m.from_user.id
    if uid not in users:
        users[uid] = []
    users[uid].append(skin)
    bot.reply_to(m, f"Добавлен: {skin}")

@bot.message_handler(commands=['mylist'])
def mylist(m):
    uid = m.from_user.id
    s = users.get(uid, [])
    bot.reply_to(m, f"Список: {', '.join(s) if s else 'пуст'}")

def check():
    while True:
        time.sleep(60)
        try:
            r = requests.get("https://fnbr.co/api/shop")
            data = r.json()
            shop = [i.get("name", "").lower() for i in data.get("data", {}).get("featured", [])]
            for uid, skins in list(users.items()):
                for s in skins:
                    if s.lower() in shop:
                        bot.send_message(uid, f"Скин {s} в магазине!")
                        users[uid].remove(s)
        except:
            pass

# --- Запуск бота и веб-сервера для Render ---
# Запускаем фоновую проверку в отдельном потоке
threading.Thread(target=check, daemon=True).start()

# Создаём минимальный веб-сервер для Render
app = Flask(name)

@app.route('/')
def index():
    return "Bot is running"

@app.route('/health')
def health():
    return "OK"

if name == "main":
    # Запускаем бота в отдельном потоке, чтобы он не блокировал веб-сервер
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    # Запускаем веб-сервер, который будет слушать порт, назначенный Render
    app.run(host='0.0.0.0', port=8080)
