import os
import json
import time
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from hashlib import sha256

import requests
import telebot
from telebot import types
from flask import Flask


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не установлен в Render Environment Variables")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID не установлен в Render Environment Variables")


bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

DATA_LOCK = threading.Lock()

KYIV = ZoneInfo("Europe/Kyiv")

API_BASE = "https://fortnite-api.com"

USERS_FILE = "users.json"
SETTINGS_FILE = "settings.json"


DEFAULT_SETTINGS = {
    "bot_enabled": True,
    "shop_enabled": True,
    "track_enabled": True,
    "last_daily_shop_date": ""
}


# =========================
# ФАЙЛЫ
# =========================

def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default.copy()


users = load_json(USERS_FILE, {})
settings = load_json(SETTINGS_FILE, DEFAULT_SETTINGS)

for key, value in DEFAULT_SETTINGS.items():
    settings.setdefault(key, value)


def save_users():
    with DATA_LOCK:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                users,
                f,
                ensure_ascii=False,
                indent=2
            )


def save_settings():
    with DATA_LOCK:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(
                settings,
                f,
                ensure_ascii=False,
                indent=2
            )


# =========================
# ПОЛЬЗОВАТЕЛИ
# =========================

def ensure_user(user):
    uid = str(user.id)

    with DATA_LOCK:
        if uid not in users:
            users[uid] = {
                "name": user.first_name or user.username or "Пользователь",
                "skins": [],
                "waiting_search": False,
                "pending_item": None
            }
        else:
            users[uid].setdefault("skins", [])
            users[uid].setdefault("waiting_search", False)
            users[uid].setdefault("pending_item", None)

            users[uid]["name"] = (
                user.first_name
                or user.username
                or users[uid].get("name", "Пользователь")
            )

    save_users()


def is_admin(message):
    return message.from_user.id == ADMIN_ID


def bot_is_available(message):
    return (
        settings.get("bot_enabled", True)
        or is_admin(message)
    )


# =========================
# КЛАВИАТУРА
# =========================

def main_keyboard():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    kb.row(
        "🛍 Магазин",
        "🔎 Поиск"
    )

    kb.row(
        "🔔 Мои уведомления"
    )

    return kb


# =========================
# ДЛИННЫЕ СООБЩЕНИЯ
# =========================

def send_long_text(chat_id, text):
    limit = 3900

    if len(text) <= limit:
        bot.send_message(chat_id, text)
        return

    parts = []
    current = ""

    for line in text.splitlines():

        if len(current) + len(line) + 1 > limit:

            if current:
                parts.append(current)

            current = line

        else:

            if current:
                current += "\n"

            current += line

    if current:
        parts.append(current)

    for part in parts:
        bot.send_message(chat_id, part)


# =========================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================

def normalize_name(value):
    return " ".join(
        str(value or "").casefold().split()
    )


def format_price(value):

    if value is None or value == "":
        return "—"

    try:
        return (
            f"{int(value):,}".replace(",", " ")
            + " V-Bucks"
        )

    except Exception:
        return str(value)


def get_image(item):

    images = item.get("images") or {}

    if isinstance(images, dict):

        for key in (
            "icon",
            "featured",
            "smallIcon",
            "lego"
        ):

            if images.get(key):
                return images[key]

    return (
        item.get("image")
        or item.get("icon")
    )


def get_price(item):

    for key in (
        "finalPrice",
        "price",
        "regularPrice",
        "priceInVbucks",
        "vbucks",
        "offerPrice",
        "cost"
    ):

        value = item.get(key)

        if value is not None and value != "":
            return value

    return None


# =========================
# ПОЛУЧЕНИЕ МАГАЗИНА
# =========================

def extract_shop_items(payload):

    result = []
    seen = set()

    def walk(obj, inherited_price=None):

        if isinstance(obj, dict):

            own_price = get_price(obj)

            current_price = (
                own_price
                if own_price is not None
                else inherited_price
            )

            name = obj.get("name")

            item_id = (
                obj.get("id")
                or obj.get("offerId")
            )

            if name and item_id:

                key = str(item_id)

                if key not in seen:

                    seen.add(key)

                    result.append({
                        "id": key,
                        "name": str(name),
                        "price": current_price,
                        "image": get_image(obj)
                    })

            for value in obj.values():

                if isinstance(
                    value,
                    (dict, list)
                ):
                    walk(
                        value,
                        current_price
                    )

        elif isinstance(obj, list):

            for value in obj:
                walk(
                    value,
                    inherited_price
                )

    walk(payload)

    return result


def fetch_shop():

    response = requests.get(
        f"{API_BASE}/v2/shop",
        params={
            "language": "ru"
        },
        timeout=25
    )

    response.raise_for_status()

    payload = response.json()

    data = payload.get("data") or {}

    items = extract_shop_items(data)

    signature_source = (
        data.get("hash")
        or "|".join(
            sorted(
                f"{x['id']}:{x['price']}"
                for x in items
            )
        )
    )

    signature = sha256(
        str(signature_source).encode("utf-8")
    ).hexdigest()

    return items, signature


# =========================
# ПОИСК СКИНА
# =========================

def parse_results(payload):

    data = payload.get("data")

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return [data]

    return []


def search_cosmetic(query):

    params = {
        "name": query,
        "matchMethod": "contains",
        "language": "ru",
        "searchLanguage": "ru"
    }

    response = requests.get(
        f"{API_BASE}/v2/cosmetics/br/search/all",
        params=params,
        timeout=25
    )

    if response.ok:

        results = parse_results(
            response.json()
        )

        if results:
            return results[:10]

    # Если русского поиска не хватило
    params["language"] = "en"
    params["searchLanguage"] = "en"

    response = requests.get(
        f"{API_BASE}/v2/cosmetics/br/search/all",
        params=params,
        timeout=25
    )

    response.raise_for_status()

    return parse_results(
        response.json()
    )[:10]


# =========================
# ПОСЛЕДНЕЕ ПОЯВЛЕНИЕ
# =========================

def format_last_appearance(value):

    if value is None or value == "":
        return "Нет данных"

    try:

        if isinstance(
            value,
            (int, float)
        ):

            timestamp = float(value)

            if timestamp > 10_000_000_000:
                timestamp /= 1000

            dt = datetime.fromtimestamp(
                timestamp,
                timezone.utc
            ).astimezone(KYIV)

            return dt.strftime("%d.%m.%Y")

        return str(value)

    except Exception:

        return str(value)


# =========================
# ПОИСК В ТЕКУЩЕМ МАГАЗИНЕ
# =========================

def find_shop_match(
    cosmetic,
    shop_items
):

    cosmetic_id = str(
        cosmetic.get("id") or ""
    )

    cosmetic_name = normalize_name(
        cosmetic.get("name")
    )

    for item in shop_items:

        if (
            cosmetic_id
            and item["id"] == cosmetic_id
        ):
            return item

        if (
            cosmetic_name
            and normalize_name(
                item["name"]
            ) == cosmetic_name
        ):
            return item

    return None


# =========================
# ОТПРАВКА РЕЗУЛЬТАТА ПОИСКА
# =========================

def search_and_send(
    message,
    query
):

    try:

        results = search_cosmetic(query)

    except Exception as e:

        bot.send_message(
            message.chat.id,
            f"❌ Ошибка поиска:\n{e}"
        )

        return

    if not results:

        bot.send_message(
            message.chat.id,
            "😕 Ничего не найдено.\n"
            "Попробуй другое название."
        )

        return

    try:
        shop_items, _ = fetch_shop()
    except Exception:
        shop_items = []

    item = results[0]

    name = (
        item.get("name")
        or query
    )

    item_id = str(
        item.get("id")
        or name
    )

    shop_match = find_shop_match(
        item,
        shop_items
    )

    price = item.get("price")

    if price is None and shop_match:
        price = shop_match.get("price")

    image = (
        get_image(item)
        or (
            shop_match.get("image")
            if shop_match
            else None
        )
    )

    last_appearance = format_last_appearance(
        item.get("lastAppearance")
    )

    uid = str(
        message.from_user.id
    )

    tracked = False

    with DATA_LOCK:

        tracked = any(
            str(x.get("id")) == item_id
            for x in users
            .get(uid, {})
            .get("skins", [])
            if isinstance(x, dict)
        )

        users[uid]["pending_item"] = {
            "id": item_id,
            "name": name
        }

    save_users()

    text = (
        f"🎮 {name}\n\n"
        f"💰 Цена: {format_price(price)}\n"
        f"📅 Последнее появление: "
        f"{last_appearance}\n"
        f"🛍 Сейчас в магазине: "
        f"{'ДА ✅' if shop_match else 'НЕТ'}"
    )

    kb = types.InlineKeyboardMarkup()

    if tracked:

        kb.add(
            types.InlineKeyboardButton(
                "❌ Убрать из отслеживания",
                callback_data=f"untrack:{item_id}"
            )
        )

    else:

        kb.add(
            types.InlineKeyboardButton(
                "🔔 Записать",
                callback_data=f"track:{item_id}"
            )
        )

    if image:

        try:

            bot.send_photo(
                message.chat.id,
                image,
                caption=text,
                reply_markup=kb
            )

            return

        except Exception:
            pass

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=kb
    )


# =========================
# МОИ УВЕДОМЛЕНИЯ
# =========================

def show_my_tracks(message):

    uid = str(
        message.from_user.id
    )

    tracks = users.get(
        uid,
        {}
    ).get("skins", [])

    if not tracks:

        bot.send_message(
            message.chat.id,
            "🔔 У тебя пока нет "
            "отслеживаемых скинов."
        )

        return

    bot.send_message(
        message.chat.id,
        "🔔 Отслеживание:"
    )

    for item in tracks:

        name = item.get(
            "name",
            "Без названия"
        )

        item_id = str(
            item.get("id", name)
        )

        kb = types.InlineKeyboardMarkup()

        kb.add(
            types.InlineKeyboardButton(
                "❌ Убрать",
                callback_data=f"untrack:{item_id}"
            )
        )

        bot.send_message(
            message.chat.id,
            f"🎮 {name}",
            reply_markup=kb
        )


# =========================
# ДОБАВИТЬ / УДАЛИТЬ
# =========================

def add_track(uid, item):

    uid = str(uid)

    with DATA_LOCK:

        users.setdefault(
            uid,
            {
                "name": "Пользователь",
                "skins": []
            }
        )

        users[uid].setdefault(
            "skins",
            []
        )

        exists = any(
            str(x.get("id"))
            == str(item["id"])
            for x in users[uid]["skins"]
            if isinstance(x, dict)
        )

        if not exists:

            users[uid]["skins"].append({
                "id": str(item["id"]),
                "name": item["name"]
            })

    save_users()


def remove_track(
    uid,
    item_id
):

    uid = str(uid)

    with DATA_LOCK:

        if uid in users:

            users[uid]["skins"] = [
                x
                for x in users[uid].get(
                    "skins",
                    []
                )
                if str(x.get("id"))
                != str(item_id)
            ]

    save_users()


# =========================
# ОТПРАВИТЬ МАГАЗИН
# =========================

def send_shop_to_user(chat_id):

    items, _ = fetch_shop()

    if not items:

        bot.send_message(
            chat_id,
            "😕 Магазин вернул пустой список."
        )

        return

    lines = [
        "🛍 МАГАЗИН FORTNITE",
        ""
    ]

    for index, item in enumerate(
        items,
        1
    ):

        lines.append(
            f"{index}. "
            f"{item['name']} — "
            f"{format_price(item['price'])}"
        )

    send_long_text(
        chat_id,
        "\n".join(lines)
    )


# =========================
# ПРОВЕРКА ОТСЛЕЖИВАНИЯ
# =========================

def check_tracked():

    if not settings.get(
        "track_enabled",
        True
    ):
        return

    try:

        shop_items, _ = fetch_shop()

    except Exception:

        return

    shop_ids = {
        str(x["id"])
        for x in shop_items
    }

    shop_names = {
        normalize_name(x["name"])
        for x in shop_items
    }

    changed = False
    notifications = []

    with DATA_LOCK:

        for uid, data in users.items():

            for tracked in list(
                data.get("skins", [])
            ):

                tracked_id = str(
                    tracked.get("id")
                )

                tracked_name = normalize_name(
                    tracked.get("name")
                )

                if (
                    tracked_id in shop_ids
                    or tracked_name in shop_names
                ):

                    notifications.append(
                        (
                            uid,
                            tracked.get(
                                "name",
                                "Скин"
                            )
                        )
                    )

                    data["skins"].remove(
                        tracked
                    )

                    changed = True

    if changed:
        save_users()

    for uid, name in notifications:

        try:

            bot.send_message(
                int(uid),
                f"🎉 Скин «{name}» "
                f"появился в магазине!\n\n"
                f"🔔 Он убран из списка "
                f"отслеживания после уведомления."
            )

        except Exception:
            pass


# =========================
# ЕЖЕДНЕВНЫЙ МАГАЗИН
# =========================

def send_daily_shop():

    if not settings.get(
        "shop_enabled",
        True
    ):
        return

    try:

        shop_items, _ = fetch_shop()

        if not shop_items:
            return

        lines = [
            "🌅 Ежедневный магазин Fortnite",
            "🕒 03:00 по Киеву",
            ""
        ]

        for index, item in enumerate(
            shop_items,
            1
        ):

            lines.append(
                f"{index}. "
                f"{item['name']} — "
                f"{format_price(item['price'])}"
            )

        text = "\n".join(lines)

        for uid in list(users.keys()):

            try:

                send_long_text(
                    int(uid),
                    text
                )

            except Exception:
                pass

    except Exception:
        pass


# =========================
# ФОНОВЫЙ ПОТОК
# =========================

def scheduler_loop():

    last_track_check = 0

    while True:

        try:

            now = datetime.now(KYIV)

            # Ежедневно в 03:00 по Киеву
            if (
                settings.get(
                    "shop_enabled",
                    True
                )
                and now.hour == 3
                and now.minute == 0
            ):

                today = now.date().isoformat()

                if (
                    settings.get(
                        "last_daily_shop_date"
                    )
                    != today
                ):

                    send_daily_shop()

                    settings[
                        "last_daily_shop_date"
                    ] = today

                    save_settings()

            # Проверка отслеживаемых
            # каждые 15 минут
            if (
                time.time()
                - last_track_check
                >= 900
            ):

                check_tracked()

                last_track_check = time.time()

        except Exception:
            pass

        time.sleep(20)


# =========================
# /START
# =========================

@bot.message_handler(
    commands=["start"]
)
def start(message):

    ensure_user(
        message.from_user
    )

    if not bot_is_available(message):
        return

    text = (
        "👋 Привет! "
        "Я Fortnite Shop Bot.\n\n"
        "🛍 Показываю магазин Fortnite.\n"
        "🔎 Ищу скины по названию.\n"
        "🔔 Можно записать скин "
        "и получить уведомление, "
        "когда он появится.\n\n"
        "Выбери действие:"
    )

    bot.send_message(
        message.chat.id,
        text,
        reply_markup=main_keyboard()
    )


# =========================
# /SHOP
# =========================

@bot.message_handler(
    commands=["shop"]
)
def shop_command(message):

    ensure_user(
        message.from_user
    )

    if not bot_is_available(message):
        return

    try:

        send_shop_to_user(
            message.chat.id
        )

    except Exception as e:

        bot.send_message(
            message.chat.id,
            f"❌ Ошибка получения "
            f"магазина:\n{e}"
        )


# =========================
# АДМИН: СТАТИСТИКА
# =========================

@bot.message_handler(
    commands=["stats"]
)
def stats(message):

    if not is_admin(message):
        return

    total_skins = sum(
        len(
            data.get("skins", [])
        )
        for data in users.values()
    )

    bot.send_message(
        message.chat.id,
        f"📊 Статистика:\n"
        f"👥 Пользователей: "
        f"{len(users)}\n"
        f"🔔 Скинов в отслеживании: "
        f"{total_skins}"
    )


# =========================
# АДМИН: ПОЛЬЗОВАТЕЛИ
# =========================

@bot.message_handler(
    commands=["users"]
)
def list_users(message):

    if not is_admin(message):
        return

    if not users:

        bot.send_message(
            message.chat.id,
            "📭 Пользователей нет"
        )

        return

    lines = [
        "👥 Пользователи:"
    ]

    for uid, data in users.items():

        lines.append(
            f"• {data.get('name', 'Без имени')} "
            f"(ID: {uid}) — "
            f"{len(data.get('skins', []))} скинов"
        )

    send_long_text(
        message.chat.id,
        "\n".join(lines)
    )


# =========================
# АДМИН: ПЕРЕКЛЮЧАТЕЛИ
# =========================

@bot.message_handler(
    commands=[
        "bot_on",
        "bot_off",
        "shop_on",
        "shop_off",
        "track_on",
        "track_off"
    ]
)
def toggle(message):

    if not is_admin(message):
        return

    cmd = (
        message.text
        .split()[0]
        .replace("/", "")
        .split("@")[0]
    )

    key_map = {
        "bot_on": "bot_enabled",
        "bot_off": "bot_enabled",

        "shop_on": "shop_enabled",
        "shop_off": "shop_enabled",

        "track_on": "track_enabled",
        "track_off": "track_enabled"
    }

    value = not cmd.endswith("_off")

    settings[
        key_map[cmd]
    ] = value

    save_settings()

    status = (
        "включено ✅"
        if value
        else "выключено ⛔"
    )

    bot.send_message(
        message.chat.id,
        f"{cmd}: {status}"
    )


# =========================
# АДМИН: РАССЫЛКА
# =========================

@bot.message_handler(
    commands=["broadcast"]
)
def broadcast(message):

    if not is_admin(message):
        return

    text = message.text.replace(
        "/broadcast",
        "",
        1
    ).strip()

    if not text:

        bot.send_message(
            message.chat.id,
            "❌ Использование:\n"
            "/broadcast текст"
        )

        return

    sent = 0

    for uid in list(users.keys()):

        try:

            bot.send_message(
                int(uid),
                f"📢 {text}"
            )

            sent += 1

        except Exception:
            pass

    bot.send_message(
        message.chat.id,
        f"✅ Отправлено: {sent}"
    )


# =========================
# АДМИН: SAY
# =========================

@bot.message_handler(
    commands=["say"]
)
def say(message):

    if not is_admin(message):
        return

    text = message.text.replace(
        "/say",
        "",
        1
    ).strip()

    if not text:

        bot.send_message(
            message.chat.id,
            "❌ Использование:\n"
            "/say текст"
        )

        return

    bot.send_message(
        message.chat.id,
        text
    )


# =========================
# КНОПКИ ОТСЛЕЖИВАНИЯ
# =========================

@bot.callback_query_handler(
    func=lambda call:
        call.data.startswith(
            ("track:", "untrack:")
        )
)
def callback_track(call):

    uid = str(
        call.from_user.id
    )

    action, item_id = (
        call.data.split(":", 1)
    )

    if action == "track":

        pending = (
            users
            .get(uid, {})
            .get("pending_item")
        )

        if (
            not pending
            or str(pending.get("id"))
            != str(item_id)
        ):

            bot.answer_callback_query(
                call.id,
                "Сначала снова найди этот скин."
            )

            return

        add_track(
            uid,
            pending
        )

        bot.answer_callback_query(
            call.id,
            "Скин записан 🔔"
        )

        try:

            new_kb = (
                types.InlineKeyboardMarkup()
            )

            new_kb.add(
                types.InlineKeyboardButton(
                    "❌ Убрать из отслеживания",
                    callback_data=
                    f"untrack:{item_id}"
                )
            )

            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=new_kb
            )

        except Exception:
            pass

    else:

        remove_track(
            uid,
            item_id
        )

        bot.answer_callback_query(
            call.id,
            "Скин убран"
        )

        try:

            new_kb = (
                types.InlineKeyboardMarkup()
            )

            new_kb.add(
                types.InlineKeyboardButton(
                    "🔔 Записать",
                    callback_data=
                    f"track:{item_id}"
                )
            )

            bot.edit_message_reply_markup(
                call.message.chat.id,
                call.message.message_id,
                reply_markup=new_kb
            )

        except Exception:
            pass


# =========================
# ОБЫЧНЫЕ КНОПКИ / ТЕКСТ
# =========================

@bot.message_handler(
    content_types=["text"]
)
def text_handler(message):

    ensure_user(
        message.from_user
    )

    if not bot_is_available(message):
        return

    text = (
        message.text or ""
    ).strip()

    uid = str(
        message.from_user.id
    )

    # Магазин
    if text == "🛍 Магазин":

        shop_command(message)

        return

    # Поиск
    if text == "🔎 Поиск":

        users[uid][
            "waiting_search"
        ] = True

        save_users()

        bot.send_message(
            message.chat.id,
            "🔎 Напиши название скина.\n"
            "Например: Добытчица"
        )

        return

    # Мои уведомления
    if text == "🔔 Мои уведомления":

        show_my_tracks(message)

        return

    # Если бот ждёт название
    if users.get(uid, {}).get(
        "waiting_search"
    ):

        users[uid][
            "waiting_search"
        ] = False

        save_users()

        search_and_send(
            message,
            text
        )

        return

    # Можно написать "записать"
    if text.casefold() in {
        "записать",
        "сохранить",
        "да"
    }:

        pending = (
            users
            .get(uid, {})
            .get("pending_item")
        )

        if pending:

            add_track(
                uid,
                pending
            )

            bot.send_message(
                message.chat.id,
                f"🔔 Записал: "
                f"{pending['name']}"
            )

        else:

            bot.send_message(
                message.chat.id,
                "Сначала найди скин "
                "через 🔎 Поиск."
            )

        return

    # Не показываем лишнее на команды
    if text.startswith("/"):
        return

    bot.send_message(
        message.chat.id,
        "Выбери кнопку:\n"
        "🛍 Магазин\n"
        "🔎 Поиск\n"
        "🔔 Мои уведомления"
    )


# =========================
# RENDER
# =========================

@app.route("/")
def index():
    return "Fortnite Shop Bot is running"


# =========================
# ЗАПУСК
# =========================

def run_bot():

    bot.infinity_polling(
        skip_pending=True,
        timeout=20,
        long_polling_timeout=20
    )


if __name__ == "__main__":

    threading.Thread(
        target=run_bot,
        daemon=True
    ).start()

    threading.Thread(
        target=scheduler_loop,
        daemon=True
    ).start()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
        )
