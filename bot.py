import logging
import sqlite3
from datetime import datetime
import io
import re
    
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ContextTypes
)
from PIL import Image

# ---------------------- CONFIG ----------------------
BOT_TOKEN = "8118917119:AAEH57njy93GeGEQhochhIeqxZBhv5BjZ3k"  # توكن البوت ظاهر هنا كما طلبت
ADMIN_ID = 7918198745  # معرف المسؤول الرقمي

ACCOUNT_NUMBER = "00799999004268889017"

GROUP_LINKS = {
    'marketing': 'https://t.me/+39YNXIC0CgJkNTdk',
    'product': 'https://t.me/+c9rnGxHKsX5mYjA0'
}

RESPONSIBLE_USERNAME = '@aleeddin'

DB_PATH = 'payments.db'
# ----------------------------------------------------

CHOOSING, WAITING_RECEIPT, ADMIN_REVIEW, WAITING_EMAIL = range(4)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------ Database helpers ------------------

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
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

def add_payment(user_id, username, full_name, book, payment_time, status, receipt_file_id):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO payments (user_id, username, full_name, book, payment_time, status, receipt_file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, book, payment_time, status, receipt_file_id))
        conn.commit()
        return c.lastrowid

def update_payment_status(payment_id, status, verified_by=None):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        if verified_by is None:
            c.execute('UPDATE payments SET status = ? WHERE id = ?', (status, payment_id))
        else:
            c.execute('UPDATE payments SET status = ?, verified_by = ? WHERE id = ?', (status, verified_by, payment_id))
        conn.commit()

def set_payment_email(payment_id, email):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('UPDATE payments SET email = ? WHERE id = ?', (email, payment_id))
        conn.commit()

def get_payment(payment_id):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
        return c.fetchone()

# ------------------ Image Type Helper ------------------

def get_image_format(file_bytes):
    try:
        img = Image.open(io.BytesIO(file_bytes))
        return img.format
    except Exception:
        return None

# ------------------ Bot Handlers ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton('معلومات'), KeyboardButton('شراء الكتاب')]]
    reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        'أهلاً وسهلاً 👋\n'
        'أنا مساعد مشروعنا لتأهيل الشباب في التسويق وصناعة المنتجات الرقمية.\n'
        'اختر أحد الخيارات:',
        reply_markup=reply
    )
    return CHOOSING

async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'هذا المشروع يهدف إلى تدريب الشباب في مجالي التسويق وصنع المنتجات الرقمية.\n'
        'عند رغبتك بالانضمام ستختار أحد الكتابين لتحديد مسارك.\n'
        '⚠️ يجب أن تمتلك حساب Gumroad قبل المتابعة.\n\n'
        'اضغط "شراء الكتاب" للمتابعة.'
    )
    return CHOOSING

async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton('كتاب التسويق'), KeyboardButton('كتاب صنع المنتجات')]]
    reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text('اختر الكتاب الذي تريد شراءه:', reply_markup=reply)
    return CHOOSING

async def choose_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.message.from_user

    if text not in ['كتاب التسويق', 'كتاب صنع المنتجات']:
        return CHOOSING

    context.user_data['chosen_book'] = 'marketing' if text == 'كتاب التسويق' else 'product'

    await update.message.reply_text(
        f'سعر الكتاب هو 1000 دج.\nالرجاء تحويل المبلغ إلى: {ACCOUNT_NUMBER}\n'
        'بعد التحويل، أرسل صورة إيصال الدفع هنا في المحادثة.\n'
        'ستصلك رسالة تأكيد بعد استلامنا للوثيقة.'
    )
    context.user_data['awaiting_receipt'] = True
    return WAITING_RECEIPT

async def received_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if not context.user_data.get('awaiting_receipt'):
        await update.message.reply_text('لست في مرحلة دفع الآن. ابدأ بالضغط على "شراء الكتاب" إذا رغبت.')
        return CHOOSING

    photo = update.message.photo[-1]
    file_id = photo.file_id
    book = context.user_data.get('chosen_book', 'marketing')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()

    # تحقق من نوع الصورة (اختياري)
    # file = await context.bot.get_file(file_id)
    # file_bytes = await file.download_as_bytearray()
    # img_type = get_image_format(file_bytes)
    # if img_type not in ['JPEG', 'PNG']:
    #     await update.message.reply_text('الملف المرسل ليس صورة صالحة. يرجى إرسال صورة بإمتداد JPEG أو PNG.')
    #     return WAITING_RECEIPT

    payment_id = add_payment(user.id, user.username or '', full_name,
                             book, now, 'قيد المراجعة', file_id)

    await update.message.reply_text('✅ تم استلام إثبات الدفع. جاري إرساله للمسؤول للمراجعة. يرجى الانتظار قليلاً.')

    accept_button = InlineKeyboardButton('✅ قبول', callback_data=f'accept:{payment_id}')
    reject_button = InlineKeyboardButton('❌ رفض', callback_data=f'reject:{payment_id}')
    kb = InlineKeyboardMarkup([[accept_button, reject_button]])

    await context.bot.send_photo(
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

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    await query.answer()

    if user.id != ADMIN_ID:
        await query.edit_message_caption(caption='غير مصرح لك باتخاذ هذا الإجراء.')
        return

    try:
        action, pid = data.split(':')
        pid = int(pid)
    except Exception:
        await query.edit_message_caption(caption='حدث خطأ في معالجة الطلب.')
        return

    payment = get_payment(pid)
    if not payment:
        await context.bot.send_message(chat_id=ADMIN_ID, text='خطأ: لم أجد عملية الدفع.')
        return

    if action == 'accept':
        update_payment_status(pid, 'مقبول', verified_by=user.id)
        await query.edit_message_caption(caption=query.message.caption + '\n\nتم القبول ✅')

        user_id = payment[1]
        await context.bot.send_message(
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
        await query.edit_message_caption(caption=query.message.caption + '\n\nتم الرفض ❌')
        target_user_id = payment[1]
        await context.bot.send_message(
            chat_id=target_user_id,
            text='❌ لم يتم قبول إثبات الدفع. يرجى التأكد من صحة التحويل وإعادة الإرسال.'
        )

def is_valid_email(email):
    pattern = r"^[\w\.-]+@[\w\.-]+\.\w{2,}$"
    return re.match(pattern, email)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    key = f'waiting_email_for_{update.message.from_user.id}'
    if key in context.bot_data:
        pid = context.bot_data.pop(key)
        email = text
        if not is_valid_email(email):
            await update.message.reply_text('يرجى إدخال بريد إلكتروني صحيح.')
            context.bot_data[key] = pid
            return
        set_payment_email(pid, email)
        payment = get_payment(pid)
        book = payment[4]
        verified_by = payment[9]

        await context.bot.send_message(
            chat_id=verified_by,
            text=f'📧 بريد المستخدم:\nالمستخدم: @{payment[2]}\nالإيميل: {email}\nالكتاب: {"التسويق" if book=="marketing" else "صنع المنتجات"}'
        )

        if book == 'marketing':
            await context.bot.send_message(
                chat_id=update.message.from_user.id,
                text=f'✅ تم تسجيل بريدك الإلكتروني بنجاح!\nيمكنك الآن الانضمام إلى المجموعة: {GROUP_LINKS["marketing"]}'
            )
        else:
            await context.bot.send_message(
                chat_id=update.message.from_user.id,
                text=(
                    f'✅ تم تسجيل بريدك الإلكتروني بنجاح!\n'
                    f'يمكنك الآن الانضمام إلى مجموعة المنتجات: {GROUP_LINKS["product"]}\n'
                    f'كما يمكنك التواصل مع المسؤول للمساعدة في رفع المنتجات: {RESPONSIBLE_USERNAME}'
                )
            )
        return

    if text == 'معلومات':
        return await info_handler(update, context)
    if text == 'شراء الكتاب':
        return await buy_handler(update, context)
    if text in ['كتاب التسويق', 'كتاب صنع المنتجات']:
        return await choose_book(update, context)

    await update.message.reply_text('لم أفهم ما تقصده. استخدم الأزرار للاختيار.')
    return CHOOSING

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error('Exception while handling an update:', exc_info=context.error)
    if isinstance(update, Update) and getattr(update, "message", None):
        await update.message.reply_text('حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.')

# ------------------ Main ------------------
def main():
    init_db()

    if not BOT_TOKEN:
        print('ضع توكن البوت في المتغير BOT_TOKEN.')
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, received_photo))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_text))
    application.add_handler(CallbackQueryHandler(admin_callback))
    application.add_error_handler(error_handler)

    print('Bot started...')
    application.run_polling()

if __name__ == '__main__':
    main()



