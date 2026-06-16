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

telebot_logger = logging.getLogger('TeleBot')
telebot_logger.setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)

# ================= НАСТРОЙКИ БОТА =================
BOT_TOKEN = '8851806070:AAHNdr-RCXC92uZYimYnoTHllIO2Wv2jK_M'
ADMIN_GROUP_ID = -1003749193820 
ADMIN_IDS = [6118149728, 6615178975, 5955159206]

FORUM_CHAT_ID = -1003906481423  
APPROVED_TOPIC_ID = 2           
QUEUE_TOPIC_ID = 238            
LEADERBOARD_TOPIC_ID = 905
PREMII_TOPIC_ID = 4             

BUILD_PRICES = {
    'Низкий': 0,
    'Средний': 250000,
    'Высокий': 450000
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

user_data = {}
report_data = {}
last_empty_sync_time = 0
last_leaderboard_sync_time = time.time()

# ================= ЛОКАЛЬНАЯ БД (SQLITE) =================


def get_db_connection():
    # Указываем полный путь к твоему файлу базы
    db_path = 'database.db'

        
    conn = sqlite3.connect(db_path, timeout=10)
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            ID_TG INTEGER PRIMARY KEY,
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
    print("[БАЗА ДАННЫХ] Локальная база SQLite успешно создана/загружена.")

init_db()

# Фоновый чекер КД
def cooldown_checker():
    global last_empty_sync_time
    global last_leaderboard_sync_time
    while True:
        time.sleep(30)
        try:
            conn = get_db_connection()
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
                    bot.send_message(tg_id, "🔔 <b>КД закончилось!</b> Вы можете снова занимать очередь на стройку!", reply_markup=main_menu_markup(tg_id))
                    cursor.execute("DELETE FROM cooldowns WHERE ID_TG = ?", (tg_id,))
                elif remaining_mins <= 5 and remaining_mins > 0 and not n5:
                    bot.send_message(tg_id, "⏳ До окончания КД стройки осталось <b>5 минут</b>!")
                    cursor.execute("UPDATE cooldowns SET notified_5 = 1 WHERE ID_TG = ?", (tg_id,))
                elif remaining_mins <= 10 and remaining_mins > 5 and not n10:
                    bot.send_message(tg_id, "⏳ До окончания КД стройки осталось <b>10 минут</b>!")
                    cursor.execute("UPDATE cooldowns SET notified_10 = 1 WHERE ID_TG = ?", (tg_id,))
            
            # Блок обработки глобального КД
            cursor.execute("SELECT value FROM settings WHERE key = 'global_cooldown_end'")
            global_cd_row = cursor.fetchone()
            
            if global_cd_row:
                try:
                    cd_end_time = datetime.datetime.strptime(global_cd_row[0], "%Y-%m-%d %H:%M")
                    cd_end_time = pytz.timezone('Europe/Moscow').localize(cd_end_time)
                    remaining_g_mins = (cd_end_time - now).total_seconds() / 60
                    
                    cursor.execute("SELECT value FROM settings WHERE key = 'global_notified_10'")
                    g10 = cursor.fetchone()
                    g10_val = g10[0] if g10 else '0'
                    
                    cursor.execute("SELECT value FROM settings WHERE key = 'global_notified_0'")
                    g0 = cursor.fetchone()
                    g0_val = g0[0] if g0 else '0'
                    
                    # Получаем список всех, кто в очереди, чтобы отправить им уведомления в ЛС
                    cursor.execute("SELECT ID_TG FROM queue")
                    queued_users = [row[0] for row in cursor.fetchall()]
                        
                    if remaining_g_mins <= 0 and g0_val == '0':
                        for tg_id in queued_users:
                            try:
                                bot.send_message(tg_id, "🔔 <b>Глобальное КД окончено!</b> Вы можете приступать к работе.", reply_markup=main_menu_markup(tg_id))
                            except: pass
                        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_notified_0', '1')")
                        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_notified_10', '1')")
                    
                    elif remaining_g_mins <= 10 and remaining_g_mins > 0 and g10_val == '0':
                        for tg_id in queued_users:
                            try:
                                bot.send_message(tg_id, f"⏳ Внимание! До окончания глобального КД осталось <b>10 минут</b>!")
                            except: pass
                        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_notified_10', '1')")
                except Exception as e:
                    print(f"Ошибка парсинга глобального КД: {e}")
            
            cursor.execute("SELECT COUNT(*) FROM queue")
            q_count = cursor.fetchone()[0]
            conn.commit()
            conn.close()
            
            current_ts = time.time()
            if q_count == 0 and not has_active_cd:
                if current_ts - last_empty_sync_time >= 45 * 60:
                    sync_queue_to_forum()
                    last_empty_sync_time = current_ts
            else:
                sync_queue_to_forum()
                if q_count == 0 and has_active_cd:
                    last_empty_sync_time = current_ts
                    
            if current_ts - last_leaderboard_sync_time >= 2 * 60 * 60:
                try:
                    update_leaderboard()
                    last_leaderboard_sync_time = current_ts
                except Exception as e:
                    print(f"Ошибка автообновления лидерборда: {e}")
                    
        except Exception as e:
            pass 

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
            pass
    if 'ч' in cd_str.lower():
        nums = re.findall(r'\d+', cd_str)
        minutes = int(nums[0]) * 60 if nums else 60
    else:
        nums = re.findall(r'\d+', cd_str)
        minutes = int(nums[0]) if nums else 60
    return (current_time + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M")

def is_allowed(message):
    if message.chat.type == 'private': return True
    if message.chat.id == ADMIN_GROUP_ID: return True
    return False

def set_bot_commands():
    commands = [
        types.BotCommand("start", "🏠 Главное меню / Перезапуск"),
        types.BotCommand("leave", "❌ Покинуть очередь"),
        types.BotCommand("premii", "🏆 Список на премии (Админ)"),
        types.BotCommand("clearstats", "🧹 Выдать премии и очистить статистику (Админ)"),
        types.BotCommand("del", "🗑 Удалить игрока из БД (Админ)"),
        types.BotCommand("stop", "⏸ Заблокировать игрока (Админ)"),
        types.BotCommand("export_logs", "📊 Выгрузить CSV логи (Админ)"),
        types.BotCommand("upd_leaderboard", "🔄 Обновить Доску Почёта (Админ)")
    ]
    bot.set_my_commands(commands)

set_bot_commands()

def get_user(tg_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE ID_TG = ?", (tg_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def sync_queue_to_forum():
    try:
        conn = get_db_connection()
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
                    cd_status_text = f"⏱ <b>Глобальное КД:</b> до <code>{cd_end_time.strftime('%H:%M')}</code> (Осталось: {rem_mins} мин)\n\n"
                else:
                    cd_status_text = "🟢 <b>Стройка полностью свободна!</b>\n\n"
            except:
                cd_status_text = "🟢 <b>Стройка полностью свободна!</b>\n\n"
        else:
            cd_status_text = "🟢 <b>Стройка полностью свободна!</b>\n\n"

        cursor.execute('''
            SELECT u.Nick_Name, q.status, q.time_booked, q.time_started 
            FROM queue q 
            JOIN users u ON q.ID_TG = u.ID_TG 
            ORDER BY q.id ASC
        ''')
        queue_list = cursor.fetchall()
        
        text = "📋 <b>АКТУАЛЬНАЯ ОЧЕРЕДЬ НА СТРОЙКУ</b> 📋\n"
        text += "───────────────────────────\n"
        text += cd_status_text
        
        if not queue_list:
            text += "<i>Сейчас очередь пуста. Станьте первым!</i>"
        else:
            for idx, item in enumerate(queue_list, 1):
                nick = item[0]
                status = item[1]
                t_booked = item[2]
                t_started = item[3]
                started_info = f" | 🕒 Начал: {t_started}" if t_started else ""
                
                emoji = "🏗" if status == 'Выполняет' else "⏳"
                text += f"{idx}. {emoji} <b>{nick}</b> — <code>{status}</code> (Занял: {t_booked}{started_info})\n"
        
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
            if "message is not modified" not in str(e).lower():
                try:
                    new_msg = bot.send_message(FORUM_CHAT_ID, text, message_thread_id=QUEUE_TOPIC_ID, parse_mode='HTML')
                    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('queue_msg_id', ?)", (str(new_msg.message_id),))
                    conn.commit()
                except Exception: pass
        except Exception: pass
        finally:
            conn.close()
    except Exception: pass

def validate_cd(text):
    text = text.strip().replace(" ", "")
    if ':' in text:
        parts = text.split(':')
        if len(parts) != 2: return False
        if not (parts[0].isdigit() and parts[1].isdigit()): return False
        return int(parts[0]) <= 24 and int(parts[1]) <= 60
    else:
        if text.isdigit(): return int(text) <= 24
    return False

def main_menu_markup(tg_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("📝 Подать отчёт"), types.KeyboardButton("🚀 Приступить к стройке"))
    
    if tg_id:
        try:
            conn = get_db_connection()
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
                markup.add(types.KeyboardButton("📅 Забронировать Стройку"))
        except:
            markup.add(types.KeyboardButton("📅 Забронировать Стройку"))
    else:
        markup.add(types.KeyboardButton("📅 Забронировать Стройку"))
        
    markup.add(types.KeyboardButton("📊 Статистика"))
    return markup

def get_msk_time():
    msk_tz = pytz.timezone('Europe/Moscow')
    return datetime.datetime.now(msk_tz)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    user = get_user(tg_id)

    if user is None:
        bot.send_message(message.chat.id, "👋 Привет! Добро пожаловать. Введи свой точный игровой Nick_Name на сервере:")
        bot.register_next_step_handler(message, process_nickname_step)
    elif user[5] == 'pending':
        bot.send_message(message.chat.id, "⏳ Твоя заявка на регистрацию всё ещё находится на рассмотрении.")
    elif user[5] == 'stopped':
        bot.send_message(message.chat.id, "❌ Ваш аккаунт в боте заблокирован администрацией.")
    elif user[5] == 'approved':
        bot.send_message(message.chat.id, f"📋 <b>Главное меню загружено.</b>\nВаш никнейм: <b>{user[1]}</b>", reply_markup=main_menu_markup(tg_id))

@bot.message_handler(func=lambda message: message.text == "➡️ Пропустить вперед")
def handle_skip_forward(message):
    tg_id = message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, ID_TG, status, time_booked, time_started FROM queue ORDER BY id ASC LIMIT 2")
    rows = cursor.fetchall()
    
    if len(rows) < 2:
        bot.send_message(message.chat.id, "❗️ За вами никого нет в очереди.", reply_markup=main_menu_markup(tg_id))
        conn.close()
        return
        
    first, second = rows[0], rows[1]
    if first[1] != tg_id:
        bot.send_message(message.chat.id, "❗️ Вы должны стоять на 1-м месте в очереди.", reply_markup=main_menu_markup(tg_id))
        conn.close()
        return
        
    cursor.execute("UPDATE queue SET ID_TG=?, status=?, time_booked=?, time_started=? WHERE id=?", 
                   (second[1], second[2], second[3], second[4], first[0]))
    cursor.execute("UPDATE queue SET ID_TG=?, status=?, time_booked=?, time_started=? WHERE id=?", 
                   (first[1], first[2], first[3], first[4], second[0]))
    
    conn.commit()
    conn.close()
    
    bot.send_message(tg_id, "➡️ Вы успешно уступили очередь следующему человеку.", reply_markup=main_menu_markup(tg_id))
    bot.send_message(second[1], "🎉 Внимание! Вас пропустили вперед! Ваша очередь брать стройку!", reply_markup=main_menu_markup(second[1]))
    sync_queue_to_forum()

@bot.message_handler(commands=['leave'])
@bot.message_handler(func=lambda message: message.text == "❌ Покинуть очередь")
def leave_queue(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM queue WHERE ID_TG = ?", (tg_id,))
    
    if cursor.fetchone():
        cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (tg_id,))
        conn.commit()
        bot.send_message(message.chat.id, "❌ Вы покинули очередь на стройку.", reply_markup=main_menu_markup(tg_id))
        sync_queue_to_forum()
    else:
        bot.send_message(message.chat.id, "❗️ Вас не было в текущей очереди.", reply_markup=main_menu_markup(tg_id))
    
    conn.close()

def process_nickname_step(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    user_data[tg_id] = {'nickname': message.text}
    bot.send_message(message.chat.id, "Отлично. Введите номер вашего банковского счёта в игре (только цифры):")
    bot.register_next_step_handler(message, process_bank_step)

def process_bank_step(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    
    if not message.text.isdigit():
        msg = bot.send_message(message.chat.id, "❗️ Неверный формат. Банковский счет должен состоять только из цифр. Повторите ввод:")
        bot.register_next_step_handler(msg, process_bank_step)
        return

    nickname = user_data[tg_id]['nickname']
    bank_account = int(message.text)
    username = f"@{message.from_user.username}" if message.from_user.username else "Нет юзернейма"

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO users (ID_TG, Nick_Name, bank_id, collector, otchetov_za_nedelyu, status)
            VALUES (?, ?, ?, 0, 0, 'pending')
        ''', (tg_id, nickname, bank_account))
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, "❌ Никнейм или банковский счет уже заняты в системе. Проверьте данные и напишите /start.")
        return

    bot.send_message(message.chat.id, "✅ Заявка успешно отправлена руководству СК. Ожидайте подтверждения!")

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Одобрить", callback_data=f"reg_app_{tg_id}"),
        types.InlineKeyboardButton("❌ Отказать", callback_data=f"reg_rej_{tg_id}")
    )

    admin_text = (f"🆕 <b>Запрос на регистрацию в СК:</b>\n\n"
                  f"👤 Логин: {username}\n"
                  f"🎮 Игровой ник: <b>{nickname}</b>\n"
                  f"💳 Счет в банке: <code>{bank_account}</code>")
    bot.send_message(ADMIN_GROUP_ID, admin_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('reg_'))
def handle_registration_verdict(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "У вас нет прав администратора!", show_alert=True)
        return

    action, tg_id = call.data.split('_')[1], int(call.data.split('_')[2])
    user = get_user(tg_id)
    if not user: return

    conn = get_db_connection()
    cursor = conn.cursor()

    if action == 'app':
        cursor.execute("UPDATE users SET status = 'approved' WHERE ID_TG = ?", (tg_id,))
        bot.edit_message_text(f"✅ Заявка игрока {user[1]} успешно одобрена.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try: bot.send_message(tg_id, "🎉 Поздравляем! Руководитель одобрил вашу заявку. Нажмите /start для входа.", reply_markup=main_menu_markup(tg_id))
        except: pass
    elif action == 'rej':
        cursor.execute("DELETE FROM users WHERE ID_TG = ?", (tg_id,))
        bot.edit_message_text(f"❌ Заявка игрока {user[1]} была отклонена.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try: bot.send_message(tg_id, "❌ К сожалению, вам отказано в регистрации.")
        except: pass
        
    conn.commit()
    conn.close()

# ================= ПОДАЧА ОТЧЕТА =================
@bot.message_handler(func=lambda message: message.text == "📝 Подать отчёт")
def start_report(message):
    if not is_allowed(message): return
    user = get_user(message.from_user.id)
    if not user or user[5] != 'approved': return

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🟢 Низкая стройка", callback_data="rep_type_Низкий"),
        types.InlineKeyboardButton("🟡 Средняя стройка", callback_data="rep_type_Средний"),
        types.InlineKeyboardButton("🔴 Высокая стройка", callback_data="rep_type_Высокий")
    )
    bot.send_message(message.chat.id, f"Игрок: <b>{user[1]}</b>.\nУкажите тип выполненной стройки:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_type_'))
def process_report_type(call):
    tg_id = call.from_user.id
    report_data[tg_id] = {'type': call.data.split('_')[2]}
    bot.edit_message_text("📸 Отправьте 1-й скриншот (Начало стройки с /time):", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.register_next_step_handler(call.message, process_report_photo_start)

def process_report_photo_start(message):
    if not is_allowed(message): return
    if not message.photo:
        msg = bot.send_message(message.chat.id, "❗️ Это не изображение. Загрузите скриншот начала стройки:")
        bot.register_next_step_handler(msg, process_report_photo_start)
        return
    report_data[message.from_user.id]['photo_start'] = message.photo[-1].file_id
    bot.send_message(message.chat.id, "📸 Теперь отправьте 2-й скриншот (Конец стройки с /time):")
    bot.register_next_step_handler(message, process_report_photo_end)

def process_report_photo_end(message):
    if not is_allowed(message): return
    if not message.photo:
        msg = bot.send_message(message.chat.id, "❗️ Это не изображение. Загрузите скриншот завершения стройки:")
        bot.register_next_step_handler(msg, process_report_photo_end)
        return
    report_data[message.from_user.id]['photo_end'] = message.photo[-1].file_id
    bot.send_message(message.chat.id, "⏳ Введите точное КД стройки (например, 17:40):")
    bot.register_next_step_handler(message, process_report_cd)

def process_report_cd(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    if not validate_cd(message.text):
        msg = bot.send_message(message.chat.id, "❌ Неверный формат времени! Формат должен быть строго ЧЧ:ММ (например 15:05):")
        bot.register_next_step_handler(msg, process_report_cd)
        return

    report_data[tg_id]['cd'] = message.text
    user = get_user(tg_id)
    data = report_data[tg_id]
    
    text = (f"🔍 <b>ПРОВЕРКА ВАШЕГО ОТЧЁТА</b>\n"
            f"───────────────────────────\n"
            f"👤 Никнейм: <b>{user[1]}</b>\n"
            f"🏗 Тип стройки: <b>{data['type']} класс</b>\n"
            f"⏱ Установленное КД: <code>{data['cd']}</code>")
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🚀 Да, отправить отчет", callback_data="rep_confirm_yes"),
        types.InlineKeyboardButton("❌ Отменить", callback_data="rep_confirm_cancel")
    )
    
    bot.send_media_group(message.chat.id, [
        types.InputMediaPhoto(data['photo_start']),
        types.InputMediaPhoto(data['photo_end'], caption=text, parse_mode='HTML')
    ])
    bot.send_message(message.chat.id, "Данные заполнены верно?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_confirm_'))
def handle_report_confirmation(call):
    tg_id = call.from_user.id
    action = call.data.split('_')[2]

    if action == 'cancel':
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(tg_id, "❌ Отправка отчёта аннулирована.", reply_markup=main_menu_markup(tg_id))
        if tg_id in report_data: del report_data[tg_id]
        
    elif action == 'yes':
        data = report_data.get(tg_id)
        if not data: return

        bot.delete_message(call.message.chat.id, call.message.message_id)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        earned_money = BUILD_PRICES.get(data['type'], 0)
        msk_now = get_msk_time()
        msk_now_str = msk_now.strftime("%Y-%m-%d %H:%M")
        
        cursor.execute("UPDATE users SET collector = collector + ?, otchetov_za_nedelyu = otchetov_za_nedelyu + 1, last_report = ? WHERE ID_TG = ?", 
                       (earned_money, msk_now_str, tg_id))
        
        cursor.execute("INSERT INTO reports (ID_TG, type, money, date_approved) VALUES (?, ?, ?, ?)",
                       (tg_id, data['type'], earned_money, msk_now_str))
        
        cd_end = calculate_cd_end(data['cd'], msk_now)
        
        # Надежный SQLite Upsert (совместимо со всеми версиями)
        cursor.execute("INSERT OR REPLACE INTO cooldowns (ID_TG, end_time, notified_10, notified_5, notified_0) VALUES (?, ?, 0, 0, 0)", (tg_id, cd_end))
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_cooldown_end', ?)", (cd_end,))
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_notified_10', '0')")
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('global_notified_0', '0')")
        
        cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (tg_id,))
        
        cursor.execute("SELECT Nick_Name, bank_id FROM users WHERE ID_TG = ?", (tg_id,))
        user = cursor.fetchone()
        conn.commit()
        conn.close()

        forum_text = (f"✅ <b>НОВЫЙ ОДОБРЕННЫЙ ОТЧЁТ</b>\n"
                      f"───────────────────────────\n"
                      f"👤 Работник СК: <b>{user[0]}</b>\n"
                      f"🏗 Тип стройки: <b>{data['type']} класс</b>\n"
                      f"⏱ Время КД: <code>{data['cd']}</code>\n"
                      f"💰 Начислено: <code>{earned_money:,} руб.</code>\n"
                      f"💳 Банковский счёт: <code>{user[1]}</code>")
        
        try:
            bot.send_media_group(FORUM_CHAT_ID, [
                types.InputMediaPhoto(data['photo_start']),
                types.InputMediaPhoto(data['photo_end'], caption=forum_text, parse_mode='HTML')
            ], message_thread_id=APPROVED_TOPIC_ID)
            bot.send_message(tg_id, f"✅ Отчёт успешно опубликован на форум! Ваше КД зафиксировано до {cd_end}.", reply_markup=main_menu_markup(tg_id))
        except Exception as e:
            bot.send_message(tg_id, "⚠️ Отчёт принят в БД, но возникла ошибка публикации медиа на форуме.", reply_markup=main_menu_markup(tg_id))

        sync_queue_to_forum()
        update_leaderboard()
        del report_data[tg_id]

# ================= СТАТИСТИКА И БРОНЬ =================
@bot.message_handler(func=lambda message: message.text == "📊 Статистика")
def show_stats(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    user = get_user(tg_id)
    if not user or user[5] != 'approved': return

    text = (f"📊 <b>ЛИЧНЫЙ ПРОФИЛЬ СОТРУДНИКА</b>\n"
            f"───────────────────────────\n"
            f"👤 Никнейм: <b>{user[1]}</b>\n"
            f"💳 Номер счёта: <code>{user[2]}</code>\n"
            f"💰 В копилке премий: <b>{user[3]:,} руб.</b>\n"
            f"🏗 Всего отчётов: <b>{user[4]} шт.</b>\n"
            f"⏱ Последняя сдача: <code>{user[6] if user[6] else 'Нет данных'}</code>")
    bot.send_message(message.chat.id, text, reply_markup=main_menu_markup(tg_id))

@bot.message_handler(func=lambda message: message.text == "📅 Забронировать Стройку")
def handle_booking(message):
    if not is_allowed(message): return
    tg_id = message.from_user.id
    user = get_user(tg_id)
    if not user or user[5] != 'approved': return

    conn = get_db_connection()
    cursor = conn.cursor()
    
    now = get_msk_time().strftime("%H:%M")
    try:
        cursor.execute("INSERT INTO queue (ID_TG, time_booked) VALUES (?, ?)", (tg_id, now))
        conn.commit()
        bot.send_message(message.chat.id, f"✅ Вы успешно заняли очередь в <code>{now}</code>.", reply_markup=main_menu_markup(tg_id))
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, "❗️ Вы уже числитесь в очереди на стройку.", reply_markup=main_menu_markup(tg_id))
    finally:
        conn.close()
    
    sync_queue_to_forum()

@bot.message_handler(func=lambda message: message.text == "🚀 Приступить к стройке")
def start_working(message):
    tg_id = message.from_user.id
    now = get_msk_time().strftime("%H:%M")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ID_TG FROM queue ORDER BY id ASC LIMIT 1")
    first = cursor.fetchone()
    
    if first and first[0] == tg_id:
        cursor.execute("UPDATE queue SET status = 'Выполняет', time_started = ? WHERE ID_TG = ?", (now, tg_id))
        conn.commit()
        bot.send_message(message.chat.id, f"🏗 Вы переведены в статус работы в <code>{now}</code>. Успешной стройки!", reply_markup=main_menu_markup(tg_id))
        sync_queue_to_forum()
    else:
        bot.send_message(message.chat.id, "❗️ Вы не первый в списке! Дождитесь продвижения очереди.", reply_markup=main_menu_markup(tg_id))
    conn.close()

def update_leaderboard():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        now = get_msk_time()
        
        cursor.execute('''
            SELECT Nick_Name, otchetov_za_nedelyu, collector
            FROM users
            WHERE otchetov_za_nedelyu > 0
            ORDER BY otchetov_za_nedelyu DESC, collector DESC
            LIMIT 15
        ''')
        rows = cursor.fetchall()

        text = "🏆 <b>ДОСКА ПОЧЁТА СТРОИТЕЛЬНОЙ КОМПАНИИ</b> 🏆\n"
        text += "<i>(с момента крайней выдачи премий)</i>\n"
        text += "───────────────────────────\n"
        
        if not rows:
            text += "<i>На доске почёта пока нет кандидатов...</i>\n"
        else:
            for idx, row in enumerate(rows, 1):
                text += f"{idx}. <b>{row[0]}</b> — <code>{row[1]} стр.</code> | <code>{row[2]:,} руб.</code>\n"
                
        text += f"───────────────────────────\n<i>🔄 Обновлено: {now.strftime('%d.%m %H:%M')} (МСК)</i>"
        
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
            new_msg = bot.send_message(FORUM_CHAT_ID, text, message_thread_id=LEADERBOARD_TOPIC_ID, parse_mode='HTML')
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('leaderboard_msg_id', ?)", (str(new_msg.message_id),))
            conn.commit()
            
        conn.close()
    except Exception as e:
        print(f"Ошибка при обновлении лидерборда: {e}")

# ================= АДМИНСКИЕ КОМАНДЫ =================
@bot.message_handler(commands=['del', 'stop', 'premii', 'clearstats', 'upd_leaderboard', 'export_logs'])
def admin_commands(message):
    if not is_allowed(message): return
    if message.from_user.id not in ADMIN_IDS: return

    cmd = message.text.split()[0]
    args = message.text.split()[1:]

    if cmd == '/upd_leaderboard':
        try:
            update_leaderboard()
            bot.send_message(message.chat.id, "✅ Доска почёта на форуме успешно принудительно обновлена!")
        except Exception as e: bot.send_message(message.chat.id, f"❌ Ошибка лидерборда: {e}")
        return

    if cmd == '/export_logs':
        conn = get_db_connection()
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
            bot.reply_to(message, "Логи пустые.")
            return
            
        import csv
        filename = "logs_building_co.csv"
        with open(filename, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(['ID записи', 'Никнейм', 'Тип', 'Премия', 'Дата/Время'])
            writer.writerows(rows)
            
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="📊 Полная выгрузка логов.")
        os.remove(filename)
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    if cmd == '/premii':
        cursor.execute("SELECT Nick_Name, bank_id, collector, otchetov_za_nedelyu FROM users WHERE status = 'approved' AND collector > 0")
        users = cursor.fetchall()
        if not users:
            bot.send_message(message.chat.id, "Ни у кого нет накопленных премий на данный момент.")
        else:
            text = "🏆 <b>СПИСОК НА ВЫПЛАТУ ПРЕМИЙ:</b>\n\n"
            for u in users:
                text += f"👤 <code>{u[0]}</code> | Счет: <code>{u[1]}</code>\n💰 Сумма: <b>{u[2]:,} руб.</b> (Строек: {u[3]})\n───────────────────\n"
            bot.send_message(message.chat.id, text)

    elif cmd == '/clearstats':
        cursor.execute("SELECT Nick_Name, bank_id, collector FROM users WHERE status = 'approved' AND collector > 0 ORDER BY collector DESC")
        payouts = cursor.fetchall()
        
        report_text = "🏆 <b>ВЫПЛАТА ПРЕМИЙ ПРОИЗВЕДЕНА!</b> 🏆\n"
        report_text += "───────────────────────────\n"
        
        if not payouts:
            report_text += "В этом периоде не обнаружено игроков с активными накоплениями."
        else:
            for p in payouts:
                report_text += f"👤 <b>{p[0]}</b> | Счет: <code>{p[1]}</code> -> Выдано: <b>{p[2]:,} руб.</b>\n"
                
        report_text += f"\n<i>📅 База обнулена: {get_msk_time().strftime('%d.%m.%Y %H:%M')}</i>"
        
        try:
            bot.send_message(FORUM_CHAT_ID, report_text, message_thread_id=PREMII_TOPIC_ID, parse_mode='HTML')
        except Exception as e:
            pass

        cursor.execute("UPDATE users SET collector = 0, otchetov_za_nedelyu = 0")
        conn.commit()
        
        update_leaderboard()
        bot.send_message(message.chat.id, "✅ Премии успешно зафиксированы в теме №4, копилки игроков обнулены!")

    elif cmd in ['/del', '/stop']:
        if not args:
            bot.send_message(message.chat.id, f"Использование: {cmd} [ID_TG]")
            conn.close()
            return
        try:
            target_id = int(args[0])
            if cmd == '/del':
                cursor.execute("DELETE FROM users WHERE ID_TG = ?", (target_id,))
                bot.send_message(message.chat.id, f"✅ Пользователь {target_id} полностью удален.")
            elif cmd == '/stop':
                cursor.execute("UPDATE users SET status = 'stopped' WHERE ID_TG = ?", (target_id,))
                bot.send_message(message.chat.id, f"⏸ Пользователь {target_id} заблокирован.")
            
            cursor.execute("DELETE FROM queue WHERE ID_TG = ?", (target_id,))
            sync_queue_to_forum()
        except ValueError:
            bot.send_message(message.chat.id, "ID должен быть числом.")

    conn.commit()
    conn.close()

if __name__ == '__main__':
    print("Бот запущен локально через SQLite. Ожидание команд...")
    threading.Thread(target=cooldown_checker, daemon=True).start()
    while True:
        try:
            bot.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60, logger_level=logging.CRITICAL)
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            print(f"[{get_msk_time().strftime('%H:%M:%S')}] Потеряно соединение с Telegram, реконнект...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"Произошла ошибка поллинга: {e}")
            time.sleep(5)
            continue