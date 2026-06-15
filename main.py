import telebot
from telebot import types
import sqlite3
import datetime
import pytz
import logging
import re
import threading
import time
import os
import requests

# === ЖЕСТКОЕ И ПОЛНОЕ УДАЛЕНИЕ СИСТЕМНЫХ ПРОКСИ ИЗ ПАМЯТИ БОТА ===
for env_var in ['http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 
                'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY']:
    os.environ.pop(env_var, None)

# Полностью глушим логгер самого Телебота
telebot_logger = logging.getLogger('TeleBot')
telebot_logger.setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = '8851806070:AAHNdr-RCXC92uZYimYnoTHllIO2Wv2jK_M'
ADMIN_GROUP_ID = -1003749193820 
ADMIN_IDS = [6118149728, 6615178975, 5955159206]

FORUM_CHAT_ID = -1003906481423  
APPROVED_TOPIC_ID = 2           # Топик для одобренных отчетов
QUEUE_TOPIC_ID = 238            # Топик для очереди
LEADERBOARD_TOPIC_ID = 905

BUILD_PRICES = {
    'Низкая': 0,
    'Средняя': 250000,
    'Высокая': 450000
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

user_data = {}
report_data = {}
last_empty_sync_time = 0
last_leaderboard_sync_time = time.time()  # Таймер для Доски Почёта

def cooldown_checker():
    global last_empty_sync_time
    global last_leaderboard_sync_time
    while True:
        time.sleep(30)  # Проверка каждые 30 секунд
        try:
            conn = sqlite3.connect('database.db')
            cursor = conn.cursor()
            now = get_msk_time()
            
            cursor.execute("SELECT ID_TG, end_time, notified_10, notified_5, notified_0 FROM cooldowns")
            rows = cursor.fetchall()
            
            for row in rows:
                tg_id, end_time_str, n10, n5, n0 = row
                end_time = datetime.datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
                end_time = pytz.timezone('Europe/Moscow').localize(end_time)
                
                remaining_mins = (end_time - now).total_seconds() / 60
                
                if remaining_mins <= 0 and not n0:
                    bot.send_message(tg_id, "🔔 <b>КД закончилось!</b> Вы можете снова занимать очередь на стройку!")
                    cursor.execute("DELETE FROM cooldowns WHERE ID_TG = ?", (tg_id,))
                elif remaining_mins <= 5 and remaining_mins > 0 and not n5:
                    bot.send_message(tg_id, "⏳ До окончания КД стройки осталось <b>5 минут</b>!")
                    cursor.execute("UPDATE cooldowns SET notified_5 = 1 WHERE ID_TG = ?", (tg_id,))
                elif remaining_mins <= 10 and remaining_mins > 5 and not n10:
                    bot.send_message(tg_id, "⏳ До окончания КД стройки осталось <b>10 минут</b>!")
                    cursor.execute("UPDATE cooldowns SET notified_10 = 1 WHERE ID_TG = ?", (tg_id,))
                    
            conn.commit()
            
            # === УМНЫЙ ТАЙМЕР ОБНОВЛЕНИЯ ОЧЕРЕДИ НА ФОРУМЕ ===
            cursor.execute("SELECT COUNT(*) FROM queue")
            q_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT value FROM settings WHERE key = 'global_cooldown_end'")
            global_cd_row = cursor.fetchone()
            has_active_cd = False
            
            if global_cd_row:
                try:
                    cd_end_time = datetime.datetime.strptime(global_cd_row[0], "%Y-%m-%d %H:%M")
                    cd_end_time = pytz.timezone('Europe/Moscow').localize(cd_end_time)
                    if now < cd_end_time:
                        has_active_cd = True
                except:
                    pass
            conn.close()
            
            current_ts = time.time()
            
            # Логика автообновления очереди
            if q_count == 0 and not has_active_cd:
                if current_ts - last_empty_sync_time >= 45 * 60:
                    sync_queue_to_forum()
                    last_empty_sync_time = current_ts
            else:
                sync_queue_to_forum()
                if q_count == 0 and has_active_cd:
                    last_empty_sync_time = current_ts
                    
            # === АВТО-ОБНОВЛЕНИЕ ЛИДЕРБОРДА (РАЗ В 2 ЧАСА) ===
            if current_ts - last_leaderboard_sync_time >= 2 * 60 * 60:  # 7200 секунд = 2 часа
                try:
                    update_leaderboard()
                    last_leaderboard_sync_time = current_ts
                except Exception as e:
                    print(f"Ошибка автообновления лидерборда: {e}")
                    
        except Exception as e:
            print(f"Ошибка фонового трекера: {e}")

def calculate_cd_end(cd_str, current_time):
    cd_str = str(cd_str).strip()
    if ':' in cd_str:
        try:
            nums = re.findall(r'\d+', cd_str)
            if len(nums) >= 2:
                hours, mins = int(nums[0]), int(nums[1])
                cd_time = current_time.replace(hour=hours, minute=mins, second=0, microsecond=0)
                if cd_time < current_time:
                    cd_time += datetime.timedelta(days=1)
                return cd_time.strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            print(f"Ошибка парсинга точного времени: {e}")

    if 'ч' in cd_str.lower():
        nums = re.findall(r'\d+', cd_str)
        minutes = int(nums[0]) * 60 if nums else 60
    else:
        nums = re.findall(r'\d+', cd_str)
        minutes = int(nums[0]) if nums else 60
        
    return (current_time + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")

# ================= ФИЛЬТР ЧАТОВ =================
def is_allowed(message):
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ID_TG INTEGER UNIQUE,
            status TEXT DEFAULT 'В очереди',
            time_booked TEXT,
            time_started TEXT
            )
        ''')
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ID_TG INTEGER,
                type TEXT,
                money INTEGER,
                date_approved TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cooldowns (
                ID_TG INTEGER PRIMARY KEY,
                end_time TEXT,
                notified_10 INTEGER DEFAULT 0,
                notified_5 INTEGER DEFAULT 0,
                notified_0 INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
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
        types.BotCommand("leave", "Покинуть очередь на стройку"),
        types.BotCommand("premii", "Список на премии (для админов)"),
        types.BotCommand("clearstats", "Обнулить копилки и доску почёта (для админов)"),
        types.BotCommand("del", "Удалить игрока из БД (для админов)"),
        types.BotCommand("stop", "Приостановить аккаунт игрока (для админов)"),
        types.BotCommand("export_logs", "Выгрузить CSV-логи строек (для админов)"),
        types.BotCommand("queue_db", "Посмотреть сырую таблицу очереди (для админов)"),
        types.BotCommand("upd_leaderboard", "Принудительно обновить Доску Почёта (для админов)")
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
    cursor.execute("SELECT value FROM settings WHERE key = 'global_cooldown_end'")
    global_cd_row = cursor.fetchone()
    cd_status_text = ""
    
    if global_cd_row:
        now = get_msk_time()
        try:
            cd_end_time = datetime.datetime.strptime(global_cd_row[0], "%Y-%m-%d %H:%M")
            cd_end_time = pytz.timezone('Europe/Moscow').localize(cd_end_time)
            if now < cd_end_time:
                remaining = cd_end_time - now
                rem_mins = int(remaining.total_seconds() // 60)
                cd_status_text = f"⏱ <b>Глобальное КД:</b> до {cd_end_time.strftime('%H:%M')} (Осталось: {rem_mins} мин)\n\n"
            else:
                cd_status_text = "✅ <b>Стройка свободна для выполнения!</b>\n\n"
        except:
            cd_status_text = "✅ <b>Стройка свободна для выполнения!</b>\n\n"
    else:
        cd_status_text = "✅ <b>Стройка свободна для выполнения!</b>\n\n"

    cursor.execute('''
        SELECT u.Nick_Name, q.status, q.time_booked, q.time_started 
        FROM queue q 
        JOIN users u ON q.ID_TG = u.ID_TG 
        ORDER BY q.id ASC
    ''')
    queue_list = cursor.fetchall()
    
    text = "📋 <b>АКТУАЛЬНАЯ ОЧЕРЕДЬ НА СТРОЙКУ</b> 📋\n\n"
    text += cd_status_text
    
    if not queue_list:
        text += "Сейчас очередь пуста."
    else:
        for idx, item in enumerate(queue_list, 1):
            nick = item[0]
            status = item[1]
            t_booked = item[2]
            t_started = item[3]
            started_info = f" | Начал: {t_started}" if t_started else ""
            text += f"{idx}. {nick} — <b>{status}</b> (Занял: {t_booked}{started_info})\n"
    
    cursor.execute("SELECT value FROM settings WHERE key = 'queue_msg_id'")
    msg_id_row = cursor.fetchone()
    
    try:
        if msg_id_row:
            bot.edit_message_text(text, FORUM_CHAT_ID, int(msg_id_row[0]), parse_mode='HTML')
        else:
            new_msg = bot.send_message(FORUM_CHAT_ID, text, message_thread_id=QUEUE_TOPIC_ID, parse_mode='HTML')
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('queue_msg_id', ?)", (str(new_msg.message_id),))
            conn.commit()
    except telebot.apihelper.ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            try:
                new_msg = bot.send_message(FORUM_CHAT_ID, text, message_thread_id=QUEUE_TOPIC_ID, parse_mode='HTML')
                cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('queue_msg_id', ?)", (str(new_msg.message_id),))
                conn.commit()
            except Exception as send_err:
                print(f"Ошибка пересоздания очереди: {send_err}")
    except Exception as e:
        print(f"Общая ошибка обновления очереди: {e}")
            
    conn.close()

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

def main_menu_markup(tg_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Подать отчёт"), types.KeyboardButton("Приступить к стройке"))
    
    if tg_id:
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT ID_TG FROM queue ORDER BY id ASC LIMIT 1")
        first_user = cursor.fetchone()
        
        cursor.execute("SELECT id FROM queue WHERE ID_TG = ?", (tg_id,))
        in_queue = cursor.fetchone()
        conn.close()
        
        if first_user and first_user[0] == tg_id:
            markup.add(types.KeyboardButton("➡️ Пропустить вперед"), types.KeyboardButton("❌ Покинуть очередь"))
        elif in_queue:
            markup.add(types.KeyboardButton("❌ Покинуть очередь"))
        else:
            markup.add(types.KeyboardButton("Забронировать Стройку"))
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

@bot.message_handler(func=lambda message: message.text == "➡️ Пропустить вперед")
def handle_skip_forward(message):
    tg_id = message.from_user.id
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, ID_TG, status, time_booked, time_started FROM queue ORDER BY id ASC LIMIT 2")
    rows = cursor.fetchall()
    
    if len(rows) < 2:
        bot.send_message(message.chat.id, "❗️ За вами никого нет в очереди.")
        conn.close()
        return
        
    first, second = rows[0], rows[1]
    if first[1] != tg_id:
        bot.send_message(message.chat.id, "❗️ Вы должны быть первым в очереди.")
        conn.close()
        return
        
    cursor.execute("UPDATE queue SET ID_TG=?, status=?, time_booked=?, time_started=? WHERE id=?", 
                   (second[1], second[2], second[3], second[4], first[0]))
    cursor.execute("UPDATE queue SET ID_TG=?, status=?, time_booked=?, time_started=? WHERE id=?", 
                   (first[1], first[2], first[3], first[4], second[0]))
    
    conn.commit()
    conn.close()
    
    bot.send_message(tg_id, "➡️ Вы успешно пропустили человека вперед.", reply_markup=main_menu_markup(tg_id))
    bot.send_message(second[1], "🎉 Вас пропустили вперед! Теперь ваша очередь брать стройку!", reply_markup=main_menu_markup(second[1]))
    sync_queue_to_forum()

@bot.message_handler(func=lambda message: message.text == "❌ Покинуть очередь")
def handle_leave_button(message):
    leave_queue(message)

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
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        
        earned_money = BUILD_PRICES.get(data['type'], 0)
        msk_now = get_msk_time()
        msk_now_str = msk_now.strftime("%Y-%m-%d %H:%M")
        
        cursor.execute("UPDATE users SET collector = collector + ?, otchetov_za_nedelyu = otchetov_za_nedelyu + 1, last_report = ? WHERE ID_TG = ?", 
                       (earned_money, msk_now_str, tg_id))
        
        cursor.execute("INSERT INTO reports (ID_TG, type, money, date_approved) VALUES (?, ?, ?, ?)",
                       (tg_id, data['type'], earned_money, msk_now_str))
        
        cd_end = calculate_cd_end(data['cd'], msk_now)
        cursor.execute("INSERT OR REPLACE INTO cooldowns (ID_TG, end_time) VALUES (?, ?)", (tg_id, cd_end))
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_cooldown_end', ?)", (cd_end,))
        cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (tg_id,))
        
        cursor.execute("SELECT Nick_Name, bank_id FROM users WHERE ID_TG = ?", (tg_id,))
        user = cursor.fetchone()
        conn.commit()
        conn.close()

        forum_text = (f"✅ <b>НОВЫЙ ОТЧЁТ</b>\n\n"
                      f"👤 Работник: <b>{user[0]}</b>\n"
                      f"🏗 Тип стройки: <b>{data['type']}</b>\n"
                      f"⏱ КД стройки: {data['cd']}\n"
                      f"💰 Начислено: <b>{earned_money}</b>\n"
                      f"💳 Счёт: <code>{user[1]}</code>")
        
        try:
            bot.send_media_group(FORUM_CHAT_ID, [
                types.InputMediaPhoto(data['photo_start']),
                types.InputMediaPhoto(data['photo_end'], caption=forum_text, parse_mode='HTML')
            ], message_thread_id=APPROVED_TOPIC_ID)
            bot.send_message(tg_id, f"✅ Отчёт опубликован! КД установлено до {cd_end}.")
        except Exception as e:
            bot.send_message(tg_id, "❌ Ошибка публикации на форум.")
            print(f"Ошибка публикации: {e}")

        sync_queue_to_forum()
        update_leaderboard()
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
        msk_now_str = get_msk_time().strftime("%Y-%m-%d %H:%M")
        
        cursor.execute('''
            UPDATE users 
            SET collector = collector + ?, otchetov_za_nedelyu = otchetov_za_nedelyu + 1, last_report = ? 
            WHERE ID_TG = ?
        ''', (earned_money, msk_now_str, tg_id))
        
        cursor.execute('''
            INSERT INTO reports (ID_TG, type, money, date_approved) 
            VALUES (?, ?, ?, ?)
        ''', (tg_id, r_type, earned_money, msk_now_str))
        
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
        conn.close()
        
        update_leaderboard()

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
    
    cursor.execute("SELECT id FROM queue WHERE ID_TG = ?", (tg_id,))
    if cursor.fetchone():
        cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (tg_id,))
        conn.commit()
        bot.send_message(message.chat.id, "❌ Вы успешно покинули очередь.")
        sync_queue_to_forum()
    else:
        bot.send_message(message.chat.id, "❗️ Вас нет в очереди.")
    
    conn.close()

def update_leaderboard():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    now = get_msk_time()
    
    # Берем данные напрямую из users, исключая тех у кого 0 отчетов
    cursor.execute('''
        SELECT Nick_Name, otchetov_za_nedelyu, collector
        FROM users
        WHERE otchetov_za_nedelyu > 0
        ORDER BY otchetov_za_nedelyu DESC, collector DESC
        LIMIT 15 -- Можно убрать или изменить лимит отображаемых игроков
    ''')
    rows = cursor.fetchall()

    text = "🏆 <b>ДОСКА ПОЧЁТА СТРОИТЕЛЬНОЙ КОМПАНИИ</b> 🏆\n"
    text += "<i>(с момента крайней выдачи премий)</i>\n\n"
    
    if not rows:
        text += "<i>Пока пусто...</i>\n"
    else:
        for idx, row in enumerate(rows, 1):
            text += f"{idx}. <b>{row[0]}</b> — {row[1]} стр. | {row[2]} руб.\n"
            
    text += f"\n<i>Последнее обновление: {now.strftime('%d.%m %H:%M')} (МСК)</i>"
    
    cursor.execute("SELECT value FROM settings WHERE key = 'leaderboard_msg_id'")
    msg_id_row = cursor.fetchone()
    
    try:
        if msg_id_row:
            bot.edit_message_text(text, FORUM_CHAT_ID, int(msg_id_row[0]), parse_mode='HTML')
        else:
            new_msg = bot.send_message(FORUM_CHAT_ID, text, message_thread_id=LEADERBOARD_TOPIC_ID, parse_mode='HTML')
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('leaderboard_msg_id', ?)", (str(new_msg.message_id),))
            conn.commit()
    except Exception:
        # Если сообщение удалили вручную, отправляем заново
        new_msg = bot.send_message(FORUM_CHAT_ID, text, message_thread_id=LEADERBOARD_TOPIC_ID, parse_mode='HTML')
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('leaderboard_msg_id', ?)", (str(new_msg.message_id),))
        conn.commit()
        
    conn.close()

# ================= АДМИНСКИЕ КОМАНДЫ =================
@bot.message_handler(commands=['del', 'stop', 'premii', 'clearstats', 'upd_leaderboard', 'export_logs', 'queue_db'])
def admin_commands(message):
    if not is_allowed(message): return
    if message.from_user.id not in ADMIN_IDS: return

    cmd = message.text.split()[0]
    args = message.text.split()[1:]

    # === РУЧНОЕ ОБНОВЛЕНИЕ ЛИДЕРБОРДА ===
    if cmd == '/upd_leaderboard':
        try:
            update_leaderboard()
            bot.send_message(message.chat.id, "✅ Доска почёта на форуме успешно обновлена!")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Ошибка при обновлении доски почёта: {e}")
        return

    # === ВЫГРУЗКА ЛОГОВ ===
    if cmd == '/export_logs':
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.id, u.Nick_Name, r.type, r.money, r.date_approved 
            FROM reports r 
            JOIN users u ON r.ID_TG = u.ID_TG 
            ORDER BY r.id DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            bot.reply_to(message, "История логов пуста.")
            return
            
        import csv
        import os
        filename = "logs_building_co.csv"
        
        with open(filename, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['ID записи', 'Никнейм игрока', 'Тип объекта', 'Выданная премия', 'Дата и время одобрения'])
            writer.writerows(rows)
            
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="📊 Полная выгрузка логов строительной компании.")
            
        os.remove(filename)
        return

    # === СЫРАЯ БАЗА ОЧЕРЕДИ ===
    if cmd == '/queue_db':
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
        return

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
        conn.commit() # Обязательно сохраняем изменения до вызова update_leaderboard()
        update_leaderboard() # Обновляем сообщение на форуме
        bot.send_message(message.chat.id, "✅ Статистика отчетов и копилки всех игроков успешно обнулена, доска почёта очищена!")

    elif cmd in ['/del', '/stop']:
        if not args:
            bot.send_message(message.chat.id, f"Использование: {cmd} [ID пользователя]")
            conn.close()
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

if __name__ == '__main__':
    print("Бот запущен. Ожидание команд...")
    threading.Thread(target=cooldown_checker, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60, logger_level=logging.CRITICAL)
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            print(f"[{get_msk_time().strftime('%H:%M:%S')}] Соединение потеряно...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"Произошла непредвиденная ошибка: {e}")
            time.sleep(5)
            continue