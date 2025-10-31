     from telegram import (
    Bot, Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, ConversationHandler, CallbackContext
)
from PIL import Image  # بديل imghdr
import io
import sqlite3
import logging
from datetime import datetime
import os

# ---------------------- CONFIG ----------------------
BOT_TOKEN = "8118917119:AAEH57njy93GeGEQhochhIeqxZBhv5BjZ3k"
ADMIN_ID = 7918198745  # ضع هنا معرف المسؤول الرقمي (رقم telegram ID)

# الحساب البنكي / رقم البريد موب
ACCOUNT_NUMBER = "00799999004268889017"

# روابط المجموعات
GROUP_LINKS = {
    'marketing': 'https://t.me/+39YNXIC0CgJkNTdk',
    'product': 'https://t.me/+c9rnGxHKsX5mYjA0'
}

# اسم مسؤول المنتجات (للإشارة بعد القبول)
RESPONSIBLE_USERNAME = '@aleeddin'

DB_PATH = 'payments.db'
# ----------------------------------------------------

# المراحل في ConversationHandler
CHOOSING, WAITING_RECEIPT, ADMIN_REVIEW, WAITING_EMAIL = range(4)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ Database helpers ------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            book TEXT,
            payment_time TEXT,
            status TEXT,
            receipt_file_id TEXT,
            email TEXT,
            verified_by INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def add_payment(user_id, username, full_name, book, payment_time, status, receipt_file_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO payments (user_id, username, full_name, book, payment_time, status, receipt_file_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, full_name, book, payment_time, status, receipt_file_id))
    conn.commit()
    pid = c.lastrowid
    conn.close()
    return pid

def update_payment_status(payment_id, status, verified_by=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if verified_by is None:
        c.execute('UPDATE payments SET status = ? WHERE id = ?', (status, payment_id))
    else:
        c.execute('UPDATE payments SET status = ?, verified_by = ? WHERE id = ?', (status, verified_by, payment_id))
    conn.commit()
    conn.close()

def set_payment_email(payment_id, email):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE payments SET email = ? WHERE id = ?', (email, payment_id))
    conn.commit()
    conn.close()

def get_payment(payment_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
    row = c.fetchone()
    conn.close()
    return row

# ------------------ Image Type Helper ------------------

def get_image_format(file_bytes):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        return img.format  # مثال: 'JPEG', 'PNG'
    except Exception:
        return None

# ------------------ Bot Handlers ------------------

def start(update: Update, context: CallbackContext):
    keyboard = [[KeyboardButton('معلومات'), KeyboardButton('شراء الكتاب')]]
    reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    update.message.reply_text(
        'أهلاً وسهلاً 👋\n'
        'أنا مساعد مشروعنا لتأهيل الشباب في التسويق وصناعة المنتجات الرقمية.\n'
        'اختر أحد الخيارات:',
        reply_markup=reply
    )
    return CHOOSING

def info_handler(update: Update, context: CallbackContext):
    update.message.reply_text(
        'هذا المشروع يهدف إلى تدريب الشباب في مجالي التسويق وصنع المنتجات الرقمية.\n'
        'عند رغبتك بالانضمام ستختار أحد الكتابين لتحديد مسارك.\n'
        '⚠️ يجب أن تمتلك حساب Gumroad قبل المتابعة.\n\n'
        'اضغط "شراء الكتاب" للمتابعة.'
    )
    return CHOOSING

def buy_handler(update: Update, context: CallbackContext):
    keyboard = [[KeyboardButton('كتاب التسويق'), KeyboardButton('كتاب صنع المنتجات')]]
    reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    update.message.reply_text('اختر الكتاب الذي تريد شراءه:', reply_markup=reply)
    return CHOOSING

def choose_book(update: Update, context: CallbackContext):
    text = update.message.text.strip()
    user = update.message.from_user

    if text not in ['كتاب التسويق', 'كتاب صنع المنتجات']:
        return CHOOSING

    # احفظ اختيار الكتاب في حالة المستخدم
    context.user_data['chosen_book'] = 'marketing' if text == 'كتاب التسويق' else 'product'

    # رسالة موحدة للشراء
    update.message.reply_text(
        f'سعر الكتاب هو 1000 دج.\nالرجاء تحويل المبلغ إلى: {ACCOUNT_NUMBER}\n'
        'بعد التحويل، أرسل صورة إيصال الدفع هنا في المحادثة.\n'
        'ستصلك رسالة تأكيد بعد استلامنا للوثيقة.'
    )
    # ضع علامة أن المستخدم في مرحلة انتظار الإيصال
    context.user_data['awaiting_receipt'] = True
    return WAITING_RECEIPT

def received_photo(update: Update, context: CallbackContext):
    user = update.message.from_user
    if not context.user_data.get('awaiting_receipt'):
        update.message.reply_text('لست في مرحلة دفع الآن. ابدأ بالضغط على "شراء الكتاب" إذا رغبت.')
        return CHOOSING

    photo = update.message.photo[-1]
    file_id = photo.file_id
    book = context.user_data.get('chosen_book', 'marketing')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    # خيار: تحقق من نوع الصورة إذا أردت (غير ضروري غالباً)
    # file = context.bot.get_file(file_id)
    # file_bytes = file.download_as_bytearray()
    # img_type = get_image_format(file_bytes)
    # if img_type not in ['JPEG', 'PNG']:
    #     update.message.reply_text('الملف المرسل ليس صورة صالحة. يرجى إرسال صورة بإمتداد JPEG أو PNG.')
    #     return WAITING_RECEIPT

    payment_id = add_payment(user.id, user.username or '', user.full_name or '',
                             book, now, 'قيد المراجعة', file_id)

    update.message.reply_text('✅ تم استلام إثبات الدفع. جاري إرساله للمسؤول للمراجعة. يرجى الانتظار قليلاً.')

    accept_button = InlineKeyboardButton('✅ قبول', callback_data=f'accept:{payment_id}')
    reject_button = InlineKeyboardButton('❌ رفض', callback_data=f'reject:{payment_id}')
    kb = InlineKeyboardMarkup([[accept_button, reject_button]])

    # إرسال صورة للإدارة مع وصف
    context.bot.send_photo(
        chat_id=ADMIN_ID, photo=file_id,
        caption=(
            f'🔔 طلب جديد:\n'
            f'المستخدم: @{user.username or "(بدون اسم)"}\n'
            f'الكتاب: {"كتاب التسويق" if book=="marketing" else "كتاب صنع المنتجات"}\n'
            f'الوقت (UTC): {now}\nID الدفع: {payment_id}'
        ),
        reply_markup=kb
    )

    context.user_data['awaiting_receipt'] = False
    return CHOOSING

def admin_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    data = query.data
    query.answer()

    if user.id != ADMIN_ID:
        query.edit_message_caption(caption='غير مصرح لك باتخاذ هذا الإجراء.')
        return

    action, pid = data.split(':')
    pid = int(pid)

    if action == 'accept':
        update_payment_status(pid, 'مقبول', verified_by=user.id)
        query.edit_message_caption(caption=query.message.caption + '\n\nتم القبول ✅')

        payment = get_payment(pid)
        if payment is None:
            context.bot.send_message(chat_id=ADMIN_ID, text='خطأ: لم أجد عملية الدفع.')
            return

        user_id = payment[1]
        book = payment[4]
        context.bot.send_message(
            chat_id=user_id,
            text=(
                '✅ تم قبول إثبات الدفع بنجاح!\n'
                'الرجاء الآن إدخال البريد الإلكتروني الذي تستخدمه في Gumroad (فقط البريد المرتبط بحسابك).'
            )
        )

        context.user_data['last_verified_payment'] = pid
        context.bot_data[f'waiting_email_for_{user_id}'] = pid

    elif action == 'reject':
        update_payment_status(pid, 'مرفوض', verified_by=user.id)
        query.edit_message_caption(caption=query.message.caption + '\n\nتم الرفض ❌')
        payment = get_payment(pid)
        if payment:
            target_user_id = payment[1]
            context.bot.send_message(
                chat_id=target_user_id,
                text='❌ لم يتم قبول إثبات الدفع. يرجى التأكد من صحة التحويل وإعادة الإرسال.'
            )

def handle_text(update: Update, context: CallbackContext):
    text = update.message.text.strip()

    key = f'waiting_email_for_{update.message.from_user.id}'
    if key in context.bot_data:
        pid = context.bot_data.pop(key)
        email = text
        set_payment_email(pid, email)
        payment = get_payment(pid)
        book = payment[4]
        verified_by = payment[9]

        # إشعار للمسؤول
        context.bot.send_message(
            chat_id=verified_by,
            text=f'📧 بريد المستخدم:\nالمستخدم: @{payment[2]}\nالإيميل: {email}\nالكتاب: {"التسويق" if book=="marketing" else "صنع المنتجات"}'
        )

        # إرسال الرابط للمستخدم حسب الكتاب
        if book == 'marketing':
            context.bot.send_message(
                chat_id=update.message.from_user.id,
                text=f'✅ تم تسجيل بريدك الإلكتروني بنجاح!\nيمكنك الآن الانضمام إلى المجموعة: {GROUP_LINKS["marketing"]}'
            )
        else:
            context.bot.send_message(
                chat_id=update.message.from_user.id,
                text=(
                    f'✅ تم تسجيل بريدك الإلكتروني بنجاح!\n'
                    f'يمكنك الآن الانضمام إلى مجموعة المنتجات: {GROUP_LINKS["product"]}\n'
                    f'كما يمكنك التواصل مع المسؤول للمساعدة في رفع المنتجات: {RESPONSIBLE_USERNAME}'
                )
            )
        return

    if text == 'معلومات':
        return info_handler(update, context)
    if text == 'شراء الكتاب':
        return buy_handler(update, context)
    if text in ['كتاب التسويق', 'كتاب صنع المنتجات']:
        return choose_book(update, context)

    update.message.reply_text('لم أفهم ما تقصده. استخدم الأزرار للاختيار.')
    return CHOOSING

def error_handler(update: Update, context: CallbackContext):
    logger.error('Exception while handling an update:', exc_info=context.error)

# ------------------ Main ------------------
def main():
    init_db()

    if BOT_TOKEN == 'PUT_YOUR_TOKEN_HERE':
        print('ضع توكن البوت في المتغير BOT_TOKEN داخل الكود أو استخدم متغير بيئة.')
        return

    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler('start', start))
    dp.add_handler(MessageHandler(Filters.photo & Filters.private, received_photo))
    dp.add_handler(MessageHandler(Filters.text & Filters.private, handle_text))
    dp.add_handler(CallbackQueryHandler(admin_callback))
    dp.add_error_handler(error_handler)

    print('Bot started...')
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()   
