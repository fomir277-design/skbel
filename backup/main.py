import telebot
from telebot import types
import sqlite3
import datetime
import pytz

# ================= НАСТРОЙКИ =================
BOT_TOKEN = '8851806070:AAHNdr-RCXC92uZYimYnoTHllIO2Wv2jK_M'
ADMIN_GROUP_ID = -1003749193820 
ADMIN_IDS = [6118149728, 6615178975, 5955159206]

FORUM_CHAT_ID = -1003906481423  
APPROVED_TOPIC_ID = 2           # Топик для одобренных отчетов
QUEUE_TOPIC_ID = 238            # Топик для очереди

BUILD_PRICES = {
    'Низкая': 0,
    'Средняя': 250000,
    'Высокая': 450000
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# Словари для временного хранения данных
user_data = {}
report_data = {}

# ================= ФИЛЬТР ЧАТОВ =================
def is_allowed(message):
    """Бот работает ТОЛЬКО в личке и в конкретной админ-группе."""
    if message.chat.type == 'private':
        return True
    if message.chat.id == ADMIN_GROUP_ID:
        return True
    return False

# ================= БАЗА ДАННЫХ =================
def init_db():
    try:
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        # Основная таблица пользователей (строго по скриншоту)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                ID_TG INTEGER PRIMARY KEY UNIQUE NOT NULL,
                Nick_Name TEXT UNIQUE NOT NULL,
                bank_id INTEGER UNIQUE NOT NULL,
                collector INTEGER DEFAULT 0,
                otchetov_za_nedelyu INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                last_report TEXT
            )
        ''')
        # Таблица очереди
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ID_TG INTEGER UNIQUE,
            status TEXT DEFAULT 'В очереди',
            time_booked TEXT,
            time_started TEXT
            )
        ''')
        # Временная таблица для отчетов на проверке
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ID_TG INTEGER,
                type TEXT,
                cd TEXT,
                photo_start TEXT,
                photo_end TEXT
            )
        ''')
        conn.commit()
        conn.close()
        print("[БАЗА ДАННЫХ] Таблицы успешно инициализированы.")
    except Exception as e:
        print(f"[КРИТИЧЕСКАЯ ОШИБКА БД] Не удалось создать таблицы: {e}")

init_db()

# ================= УСТАНОВКА КОМАНД (МЕНЮ) =================
def set_bot_commands():
    commands = [
        types.BotCommand("start", "Запустить/Перезапустить бота"),
        types.BotCommand("premii", "Список на премии (для админов)"),
        types.BotCommand("clearstats", "Обнулить копилки (для админов)"),
        types.BotCommand("del", "Удалить игрока (для админов)"),
        types.BotCommand("stop", "Приостановить игрока (для админов)")
    ]
    try:
        bot.set_my_commands(commands)
    except Exception as e:
        print("Ошибка установки команд:", e)

set_bot_commands()

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_user(tg_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE ID_TG = ?", (tg_id,))
    user = cursor.fetchone()
    conn.close()
    return user
def sync_queue_to_forum():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    # Запрашиваем ник, статус, время брони и время начала стройки
    cursor.execute('''
        SELECT u.Nick_Name, q.status, q.time_booked, q.time_started 
        FROM queue q 
        JOIN users u ON q.ID_TG = u.ID_TG 
        ORDER BY q.id ASC
    ''')
    queue_list = cursor.fetchall()
    conn.close()
    
    # Формируем красивый текст
    text = "📋 <b>АКТУАЛЬНАЯ ОЧЕРЕДЬ НА СТРОЙКУ</b> 📋\n\n"
    if not queue_list:
        text += "Сейчас очередь пуста."
    else:
        for idx, item in enumerate(queue_list, 1):
            nick = item[0]
            status = item[1] # Статус (В очереди / Выполняет)
            t_booked = item[2] # Время брони
            t_started = item[3] # Время начала (если есть)
            
            # Если стройку начали, добавляем время начала в строку
            started_info = f" | Начал: {t_started}" if t_started else ""
            text += f"{idx}. {nick} — <b>{status}</b> (Занял: {t_booked}{started_info})\n"
    
    # Отправка сообщения
    try:
        # Если сообщение уже есть, желательно его удалять/редактировать, 
        # но для начала просто отправляем новое в нужный топик
        bot.send_message(FORUM_CHAT_ID, text, message_thread_id=QUEUE_TOPIC_ID)
    except Exception as e:
        print(f"Ошибка отправки очереди: {e}")

def validate_cd(text):
    text = text.strip().replace(" ", "")
    if ':' in text:
        parts = text.split(':')
        if len(parts) != 2: return False
        if not (parts[0].isdigit() and parts[1].isdigit()): return False
        return int(parts[0]) <= 24 and int(parts[1]) <= 60
    else:
        if text.isdigit():
            return int(text) <= 24
    return False

def main_menu_markup(tg_id):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM queue WHERE ID_TG = ?", (tg_id,))
    in_queue = cursor.fetchone()
    conn.close()

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Подать отчёт"), types.KeyboardButton("Приступить к стройке"))
    
    if in_queue:
        markup.add(types.KeyboardButton("❌ Покинуть очередь"))
    else:
        markup.add(types.KeyboardButton("Забронировать Стройку"))
        
    markup.add(types.KeyboardButton("Статистика"))
    return markup

def get_msk_time():
    msk_tz = pytz.timezone('Europe/Moscow')
    return datetime.datetime.now(msk_tz)

# ================= СТАРТ И РЕГИСТРАЦИЯ =================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    user = get_user(tg_id)

    if user is None:
        bot.send_message(message.chat.id, "Привет! Добро пожаловать. Введи свой игровой ник на 45 сервере:")
        bot.register_next_step_handler(message, process_nickname_step)
    elif user[5] == 'pending':
        bot.send_message(message.chat.id, "⏳ Твоя заявка на рассмотрении у администраторов.")
    elif user[5] == 'stopped':
        bot.send_message(message.chat.id, "❌ Твой аккаунт приостановлен администратором.")
    elif user[5] == 'approved':
        bot.send_message(message.chat.id, f"С возвращением, {user[1]}!", reply_markup=main_menu_markup(tg_id))

@bot.message_handler(func=lambda message: message.text == "❌ Покинуть очередь")
def handle_leave_button(message):
    # Просто вызываем функцию удаления из очереди
    leave_queue(message)

@bot.message_handler(commands=['queue_db'])
def show_db_queue(message):
    if not is_allowed(message): return
    # Можно ограничить только для админов, если нужно
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "У вас нет прав для доступа к базе данных.")
        return

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, ID_TG, status, time_booked, time_started FROM queue ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "База данных очереди пуста.")
        return

    text = "🔍 <b>ТЕКУЩЕЕ СОСТОЯНИЕ ТАБЛИЦЫ QUEUE:</b>\n\n"
    for row in rows:
        text += f"ID: {row[0]} | TG: {row[1]} | Статус: {row[2]} | Бронь: {row[3]} | Старт: {row[4] or '—'}\n"
    
    bot.reply_to(message, text)

def process_nickname_step(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    user_data[tg_id] = {'nickname': message.text}
    bot.send_message(message.chat.id, "Отлично. Теперь введи свой банковский счёт (только цифры):")
    bot.register_next_step_handler(message, process_bank_step)

def process_bank_step(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    
    if not message.text.isdigit():
        msg = bot.send_message(message.chat.id, "❗️ Банковский счет должен состоять только из цифр! Введи еще раз:")
        bot.register_next_step_handler(msg, process_bank_step)
        return

    nickname = user_data[tg_id]['nickname']
    bank_account = int(message.text)
    username = f"@{message.from_user.username}" if message.from_user.username else "Без юзернейма"

    try:
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (ID_TG, Nick_Name, bank_id, collector, otchetov_za_nedelyu, status)
            VALUES (?, ?, ?, 0, 0, 'pending')
        ''', (tg_id, nickname, bank_account))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка БД при регистрации: {e}")
        bot.send_message(message.chat.id, "Произошла ошибка (возможно, такой ник или счет уже есть). Напишите /start для повторной попытки.")
        return

    bot.send_message(message.chat.id, "Заявка отправлена руководству. Ожидай вердикта!")

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("Одобрить", callback_data=f"reg_app_{tg_id}"),
        types.InlineKeyboardButton("Отказать", callback_data=f"reg_rej_{tg_id}")
    )

    admin_text = (f"🆕 <b>Новый запрос на регистрацию:</b>\n\n"
                  f"👤 Telegram: {username}\n"
                  f"🎮 Никнейм: <b>{nickname}</b>\n"
                  f"💳 Счёт: <code>{bank_account}</code>")
    bot.send_message(ADMIN_GROUP_ID, admin_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('reg_'))
def handle_registration_verdict(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "У вас нет прав!", show_alert=True)
        return

    action, tg_id = call.data.split('_')[1], int(call.data.split('_')[2])
    user = get_user(tg_id)
    if not user: return

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    if action == 'app':
        cursor.execute("UPDATE users SET status = 'approved' WHERE ID_TG = ?", (tg_id,))
        bot.edit_message_text(f"✅ Заявка {user[1]} одобрена.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try: bot.send_message(tg_id, "✅ Регистрация одобрена! Нажми /start для перезапуска бота.")
        except: pass
    elif action == 'rej':
        cursor.execute("DELETE FROM users WHERE ID_TG = ?", (tg_id,))
        bot.edit_message_text(f"❌ Заявка {user[1]} отклонена.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try: bot.send_message(tg_id, "❌ В регистрации отказано.")
        except: pass
        
    conn.commit()
    conn.close()

# ================= ПОДАЧА ОТЧЕТА =================
@bot.message_handler(func=lambda message: message.text == "Подать отчёт")
def start_report(message):
    if not is_allowed(message): return
    user = get_user(message.from_user.id)
    if not user or user[5] != 'approved': return

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("Низкая стройка", callback_data="rep_type_Низкая"),
        types.InlineKeyboardButton("Средняя стройка", callback_data="rep_type_Средняя"),
        types.InlineKeyboardButton("Высокая стройка", callback_data="rep_type_Высокая")
    )
    bot.send_message(message.chat.id, f"Ваш Nick_Name: <b>{user[1]}</b>.\nЗа какую стройку подаем отчёт?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_type_'))
def process_report_type(call):
    tg_id = call.from_user.id
    report_data[tg_id] = {'type': call.data.split('_')[2]}
    bot.edit_message_text("Отправьте первый скриншот (начало стройки):", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.register_next_step_handler(call.message, process_report_photo_start)

def process_report_photo_start(message):
    if not is_allowed(message): return
    if not message.photo:
        msg = bot.send_message(message.chat.id, "Это не фото. Отправьте скриншот начала:")
        bot.register_next_step_handler(msg, process_report_photo_start)
        return
    report_data[message.from_user.id]['photo_start'] = message.photo[-1].file_id
    bot.send_message(message.chat.id, "Теперь отправьте второй скриншот (конец стройки):")
    bot.register_next_step_handler(message, process_report_photo_end)

def process_report_photo_end(message):
    if not is_allowed(message): return
    if not message.photo:
        msg = bot.send_message(message.chat.id, "Это не фото. Отправьте скриншот конца:")
        bot.register_next_step_handler(msg, process_report_photo_end)
        return
    report_data[message.from_user.id]['photo_end'] = message.photo[-1].file_id
    bot.send_message(message.chat.id, "Укажите КД стройки (например, 14:55):")
    bot.register_next_step_handler(message, process_report_cd)

def process_report_cd(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    if not validate_cd(message.text):
        msg = bot.send_message(message.chat.id, "❌ Неверный формат! Введите правильное КД (например: 14:55):")
        bot.register_next_step_handler(msg, process_report_cd)
        return

    report_data[tg_id]['cd'] = message.text
    user = get_user(tg_id)
    data = report_data[tg_id]
    
    text = (f"<b>ПРОВЕРКА ОТЧЁТА</b>\n\n"
            f"Nick_Name: <b>{user[1]}</b>\n"
            f"Тип стройки: <b>{data['type']}</b>\n"
            f"КД стройки: {data['cd']}")
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("Да, подтвердить отправку", callback_data="rep_confirm_yes"),
        types.InlineKeyboardButton("Отмена", callback_data="rep_confirm_cancel")
    )
    
    bot.send_media_group(message.chat.id, [
        types.InputMediaPhoto(data['photo_start']),
        types.InputMediaPhoto(data['photo_end'], caption=text, parse_mode='HTML')
    ])
    bot.send_message(message.chat.id, "Всё верно?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_confirm_'))
def handle_report_confirmation(call):
    tg_id = call.from_user.id
    action = call.data.split('_')[2]

    if action == 'cancel':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(tg_id, "Отправка отменена.")
        if tg_id in report_data: del report_data[tg_id]
        
    elif action == 'yes':
        data = report_data.get(tg_id)
        if not data: return

        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(tg_id, "✅ Отчёт отправлен руководству.")

        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO active_reports (ID_TG, type, cd, photo_start, photo_end)
            VALUES (?, ?, ?, ?, ?)
        ''', (tg_id, data['type'], data['cd'], data['photo_start'], data['photo_end']))
        report_id = cursor.lastrowid
        
        cursor.execute("UPDATE users SET last_report = ? WHERE ID_TG = ?", (get_msk_time().strftime("%Y-%m-%d %H:%M"), tg_id))
        cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (tg_id,))
        
        cursor.execute("SELECT ID_TG FROM queue ORDER BY id ASC LIMIT 1")
        next_in_line = cursor.fetchone()
        conn.commit()
        conn.close()

        if next_in_line:
            try: bot.send_message(next_in_line[0], "🏗 <b>Очередь сдвинулась!</b> Следующая стройка твоя!")
            except: pass

        sync_queue_to_forum()

        user = get_user(tg_id)
        admin_text = (f"🏗 <b>НОВЫЙ ОТЧЁТ #{report_id}</b>\n\n"
                      f"Nick_Name: <b>{user[1]}</b>\n"
                      f"Тип стройки: <b>{data['type']}</b>\n"
                      f"Банковский счёт: <code>{user[2]}</code>\n"
                      f"КД стройки: {data['cd']}\n"
                      f"ID в ТГ: <code>{tg_id}</code>")
        
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("Одобрить", callback_data=f"admrep_app_{report_id}"),
            types.InlineKeyboardButton("Отказать", callback_data=f"admrep_rej_{report_id}")
        )

        sent_msgs = bot.send_media_group(ADMIN_GROUP_ID, [
            types.InputMediaPhoto(data['photo_start']),
            types.InputMediaPhoto(data['photo_end'], caption=admin_text, parse_mode='HTML')
        ])
        bot.send_message(ADMIN_GROUP_ID, f"Вердикт по отчету #{report_id}:", reply_to_message_id=sent_msgs[0].message_id, reply_markup=markup)
        del report_data[tg_id]

# ================= АДМИНСКИЕ РЕШЕНИЯ ПО ОТЧЕТУ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('admrep_'))
def handle_admin_report_verdict(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "У вас нет прав!", show_alert=True)
        return

    action = call.data.split('_')[1]
    report_id = int(call.data.split('_')[2])

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM active_reports WHERE id = ?", (report_id,))
    rep = cursor.fetchone()

    if not rep:
        bot.answer_callback_query(call.id, "Этот отчёт уже был обработан!", show_alert=True)
        conn.close()
        return

    tg_id, r_type, r_cd, photo_start, photo_end = rep[1], rep[2], rep[3], rep[4], rep[5]
    user = get_user(tg_id)

    if action == 'app':
        earned_money = BUILD_PRICES.get(r_type, 0)
        
        cursor.execute('''
            UPDATE users 
            SET collector = collector + ?, otchetov_za_nedelyu = otchetov_za_nedelyu + 1 
            WHERE ID_TG = ?
        ''', (earned_money, tg_id))
        
        bot.edit_message_text(f"✅ Отчёт #{report_id} одобрен. Начислено: {earned_money}.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        
        try: bot.send_message(tg_id, f"✅ Твой отчёт одобрен!\nТип: {r_type}\nЗаработано: <b>{earned_money}</b>")
        except: pass

        forum_text = (f"✅ <b>ОДОБРЕННЫЙ ОТЧЁТ</b>\n\n"
                      f"👤 Работник: <b>{user[1]}</b>\n"
                      f"🏗 Тип стройки: <b>{r_type}</b>\n"
                      f"⏱ КД стройки: {r_cd}\n"
                      f"💰 Заработано: <b>{earned_money}</b>\n"
                      f"💳 Счёт: <code>{user[2]}</code>")
        try:
            bot.send_media_group(FORUM_CHAT_ID, [
                types.InputMediaPhoto(photo_start),
                types.InputMediaPhoto(photo_end, caption=forum_text, parse_mode='HTML')
            ], message_thread_id=APPROVED_TOPIC_ID)
        except Exception as e:
            print("Ошибка отправки в форум одобренных:", e)

        cursor.execute("DELETE FROM active_reports WHERE id = ?", (report_id,))
        conn.commit()

    elif action == 'rej':
        msg = bot.edit_message_text(f"Укажите причину отказа ответом на это сообщение.\nID игрока: {tg_id} | Отчёт: {report_id}", 
                                    chat_id=call.message.chat.id, message_id=call.message.message_id)
        bot.register_next_step_handler(msg, process_reject_reason, tg_id, report_id)
    
    conn.close()

def process_reject_reason(message, target_id, report_id):
    if not is_allowed(message): return
    reason = message.text
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM active_reports WHERE id = ?", (report_id,))
    conn.commit()
    conn.close()

    bot.send_message(target_id, f"❌ Ваш отчёт <b>отклонён</b>.\nПричина: {reason}")
    bot.reply_to(message, "Отчёт отклонён, игрок уведомлён.")

# ================= СТАТИСТИКА И БРОНЬ =================
@bot.message_handler(func=lambda message: message.text == "Статистика")
def show_stats(message):
    if not is_allowed(message): return
    user = get_user(message.from_user.id)
    if not user or user[5] != 'approved': return

    text = (f"📊 <b>Ваша статистика:</b>\n\n"
            f"👤 Nick_Name: <b>{user[1]}</b>\n"
            f"💳 Банковский счёт: <code>{user[2]}</code>\n"
            f"💰 Копилка: <b>{user[3]}</b>\n"
            f"🏗 Одобрено отчётов: <b>{user[4]}</b>\n"
            f"⏱ Последний отчёт: {user[6] if user[6] else 'Нет данных'}")
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda message: message.text == "Забронировать Стройку")
def handle_booking(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    user = get_user(tg_id)
    if not user or user[5] != 'approved': return

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Записываем с временем брони
    now = get_msk_time().strftime("%H:%M")
    try:
        cursor.execute("INSERT INTO queue (ID_TG, time_booked) VALUES (?, ?)", (tg_id, now))
        conn.commit()
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, "❗️ Вы уже в очереди.")
        conn.close()
        return
    
    conn.close()
    bot.send_message(message.chat.id, f"✅ Вы встали в очередь в {now}.")
    sync_queue_to_forum()

@bot.message_handler(func=lambda message: message.text == "Приступить к стройке")
def start_working(message):
    tg_id = message.from_user.id
    now = get_msk_time().strftime("%H:%M")
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    # Проверяем, первый ли он в очереди
    cursor.execute("SELECT ID_TG FROM queue ORDER BY id ASC LIMIT 1")
    first = cursor.fetchone()
    
    if first and first[0] == tg_id:
        cursor.execute("UPDATE queue SET status = 'Выполняет', time_started = ? WHERE ID_TG = ?", (now, tg_id))
        conn.commit()
        bot.send_message(message.chat.id, f"🏗 Вы приступили к выполнению в {now}. Удачи!")
        sync_queue_to_forum()
    else:
        bot.send_message(message.chat.id, "❗️ Вы не первый в очереди, еще рано приступать!")
    conn.close()

@bot.message_handler(commands=['leave'])
def leave_queue(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Проверяем, есть ли человек в очереди
    cursor.execute("SELECT id FROM queue WHERE ID_TG = ?", (tg_id,))
    if cursor.fetchone():
        cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (tg_id,))
        conn.commit()
        bot.send_message(message.chat.id, "❌ Вы успешно покинули очередь.")
        sync_queue_to_forum() # Обновляем список в форуме
    else:
        bot.send_message(message.chat.id, "❗️ Вас нет в очереди.")
    
    conn.close()

# ================= АДМИНСКИЕ КОМАНДЫ =================
@bot.message_handler(commands=['del', 'stop', 'premii', 'clearstats'])
def admin_commands(message):
    if not is_allowed(message): return
    if message.from_user.id not in ADMIN_IDS: return

    cmd = message.text.split()[0]
    args = message.text.split()[1:]

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    if cmd == '/premii':
        cursor.execute("SELECT Nick_Name, bank_id, collector, otchetov_za_nedelyu FROM users WHERE status = 'approved'")
        users = cursor.fetchall()
        if not users:
            bot.send_message(message.chat.id, "Список пуст.")
        else:
            text = "🏆 <b>Список на выплату премий:</b>\n\n"
            for u in users:
                text += f"👤 {u[0]} | 💳 <code>{u[1]}</code>\n💰 {u[2]} | 🏗 Отчётов: {u[3]}\n〰️〰️〰️〰️〰️〰️〰️\n"
            bot.send_message(message.chat.id, text)

    elif cmd == '/clearstats':
        cursor.execute("UPDATE users SET collector = 0, otchetov_za_nedelyu = 0")
        bot.send_message(message.chat.id, "✅ Статистика отчетов и копилки всех игроков успешно обнулена!")

    elif cmd in ['/del', '/stop']:
        if not args:
            bot.send_message(message.chat.id, f"Использование: {cmd} [ID пользователя]")
            return
        try:
            target_id = int(args[0])
            if cmd == '/del':
                cursor.execute("DELETE FROM users WHERE ID_TG = ?", (target_id,))
                bot.send_message(message.chat.id, f"✅ Пользователь {target_id} удален.")
            elif cmd == '/stop':
                cursor.execute("UPDATE users SET status = 'stopped' WHERE ID_TG = ?", (target_id,))
                bot.send_message(message.chat.id, f"⏸ Пользователь {target_id} приостановлен.")
            
            cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (target_id,))
            sync_queue_to_forum()
        except ValueError:
            bot.send_message(message.chat.id, "ID должен быть числом.")

    conn.commit()
    conn.close()

import time

if __name__ == '__main__':
    print("Бот запущен. Ожидание команд...")
    
    while True:
        try:
            # Запускаем бота
            bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
            # Если сеть легла, просто пишем об этом и ждем
            print(f"[{get_msk_time().strftime('%H:%M:%S')}] Соединение потеряно...")
            time.sleep(5)
            continue
        except Exception as e:
            # Если возникла другая ошибка (код), пишем её и тоже не падаем
            print(f"Произошла непредвиденная ошибка: {e}")
            time.sleep(5)
            continue