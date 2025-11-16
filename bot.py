import os
import logging
import re
from datetime import datetime
import aiosqlite
import asyncio
from telegram import (Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton)
from telegram.ext import (Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes)
from PIL import Image
import io

# --------------- Configuration ---------------

# Read sensitive settings from environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')  # REQUIRED
ADMINS_RAW = os.environ.get('ADMIN_IDS', '')  # comma-separated admin ids
ACCOUNT_MOBILE = os.environ.get('ACCOUNT_MOBILE', '00799999004268889017')

# Parse admin ids into a set of ints
ADMINS = set()
for part in ADMINS_RAW.split(','):
    part = part.strip()
    if not part:
        continue
    try:
        ADMINS.add(int(part))
    except ValueError:
        pass

# Fallback admin for testing (remove/change in production)
if not ADMINS:
    try:
        ADMINS.add(7918198745)
    except Exception:
        pass

# Links to book downloads (provided by user)
MARKETING_LINK = "https://drive.google.com/uc?export=download&id=1ENHW-0NWmuzrxAS-XWDn_A6FR_NTPo7c"
PRODUCT_LINK = "https://drive.google.com/uc?export=download&id=1_sy-LpuU5u-SnDZ1E6URutFMxW0Mzf1R"

# Group links and responsible username
GROUP_LINKS = {
    'marketing': 'https://t.me/+39YNXIC0CgJkNTdk',
    'product': 'https://t.me/+c9rnGxHKsX5mYjA0'
}
RESPONSIBLE_USERNAME = '@aleeddin'

DB_PATH = 'payments.db'

# Prices
PRICES = {
    'marketing': 1000,
    'product': 1500
}

# States
CHOOSING = 'CHOOSING'
WAITING_RECEIPT = 'WAITING_RECEIPT'
WAITING_EMAIL = 'WAITING_EMAIL'

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- Database helpers ----------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
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
        await db.commit()

async def add_payment(user_id, username, full_name, book, payment_time, status, receipt_file_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('''
            INSERT INTO payments (user_id, username, full_name, book, payment_time, status, receipt_file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, book, payment_time, status, receipt_file_id))
        await db.commit()
        rowid = cur.lastrowid
        await cur.close()
        return rowid

async def update_payment_status(payment_id, status, verified_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        if verified_by is None:
            await db.execute('UPDATE payments SET status = ? WHERE id = ?', (status, payment_id))
        else:
            await db.execute('UPDATE payments SET status = ?, verified_by = ? WHERE id = ?', (status, verified_by, payment_id))
        await db.commit()

async def set_payment_email(payment_id, email):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE payments SET email = ? WHERE id = ?', (email, payment_id))
        await db.commit()

async def get_payment(payment_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('SELECT * FROM payments WHERE id = ?', (payment_id,))
        row = await cur.fetchone()
        await cur.close()
        return row

# ---------------- Helpers ----------------

def is_valid_email(email: str) -> bool:
    pattern = r"^[\w.-]+@[\w.-]+\.\w{2,}$"
    return bool(re.match(pattern, email))

async def download_and_check_image(bot, file_id):
    try:
        file = await bot.get_file(file_id)
        data = await file.download_as_bytearray()
        img = Image.open(io.BytesIO(data))
        return img.format
    except Exception:
        return None

# ---------------- Bot Handlers ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton('معلومات'), KeyboardButton('شراء الكتاب')]
    ]
    reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        'أهلاً وسهلاً 👋\n'
        'أنا مساعد مشروعنا لتأهيل الشباب في التسويق وصناعة المنتجات الرقمية.\n'
        'اختر أحد الخيارات:',
        reply_markup=reply
    )
    context.user_data.clear()
    context.user_data['state'] = CHOOSING

async def info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'هذا المشروع يهدف إلى تدريب الشباب في مجالي التسويق وصنع المنتجات الرقمية.\n'
        f'⚠️ يجب أن تمتلك حساب Gumroad قبل المتابعة.\n'
        f'⚠️ الدفع يكون عن طريق التحويل إلى الحساب البريدي/موب: {ACCOUNT_MOBILE}\n'
        'يفضل أن تمتلك اسم مستخدم في تلغرام ليسهل التواصل.\n\n'
        'اضغط "شراء الكتاب" للمتابعة.'
    )
    context.user_data['state'] = CHOOSING

async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton('كتاب التسويق'), KeyboardButton('كتاب صنع المنتجات')],
        [KeyboardButton('العودة للرئيسية')]
    ]
    reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text('اختر الكتاب الذي تريد شراءه:', reply_markup=reply)
    context.user_data['state'] = CHOOSING

async def choose_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()
    if text == 'العودة للرئيسية':
        return await start(update, context)

    if text not in ['كتاب التسويق', 'كتاب صنع المنتجات']:
        await update.message.reply_text('اختر أحد خيارات الأزرار.')
        return

    book_key = 'marketing' if text == 'كتاب التسويق' else 'product'
    context.user_data['chosen_book'] = book_key
    price = PRICES[book_key]

    # Immediately prompt user to send receipt (no separate button)
    await update.message.reply_text(
        f'سعر الكتاب هو {price} دج.\n'
        f'الرجاء تحويل المبلغ إلى: {ACCOUNT_MOBILE} (CCP/MOB).\n'
        'بعد التحويل، أرسل صورة أو ملف PDF لإثبات الدفع هنا.\n'
        'ستصلك رسالة تأكيد بعد استلامنا للوثيقة.'
    )

    # initialize attempts and waiting state
    context.user_data['state'] = WAITING_RECEIPT
    context.user_data['attempts_left'] = 3
    context.bot_data[f'attempts_left_{update.message.from_user.id}'] = 3

async def received_photo_or_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    state = context.user_data.get('state')

    key = f'attempts_left_{user.id}'
    attempts_bot = context.bot_data.get(key)

    # allow if user is in WAITING_RECEIPT or has attempts tracking (re-send after rejection)
    if state != WAITING_RECEIPT and attempts_bot is None:
        await update.message.reply_text('لست في مرحلة دفع الآن. ابدأ بالضغط على "شراء الكتاب" إذا رغبت.')
        return

    file_id = None
    is_pdf = False
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        doc = update.message.document
        if doc.mime_type == 'application/pdf' or (hasattr(doc, 'file_name') and str(doc.file_name).lower().endswith('.pdf')):
            file_id = doc.file_id
            is_pdf = True
        else:
            await update.message.reply_text('نقبل فقط صور (JPEG/PNG) أو ملف PDF كإثبات. أعد الإرسال.')
            return
    else:
        await update.message.reply_text('أرسل صورة أو ملف PDF لإثبات الدفع.')
        return

    book = context.user_data.get('chosen_book', 'marketing')
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()

    if not is_pdf and file_id:
        fmt = await download_and_check_image(context.bot, file_id)
        if fmt is None:
            await update.message.reply_text('الملف المرسل ليس صورة صالحة. يرجى إرسال صورة بصيغة JPEG أو PNG أو إرسال PDF.')
            return

    payment_id = await add_payment(user.id, user.username or '', full_name, book, now, 'قيد المراجعة', file_id)

    await update.message.reply_text('✅ تم استلام إثبات الدفع. جاري إرساله للمسؤول للمراجعة.')

    kb = InlineKeyboardMarkup([[InlineKeyboardButton('✅ قبول', callback_data=f'accept:{payment_id}'),
                                InlineKeyboardButton('❌ رفض', callback_data=f'reject:{payment_id}')]])

    caption = (
        f'🔔 طلب جديد:\n'
        f'المستخدم: @{user.username or "(بدون اسم)"}\n'
        f'الكتاب: {"كتاب التسويق" if book=="marketing" else "كتاب صنع المنتجات"}\n'
        f'الوقت (UTC): {now}\nID الدفع: {payment_id}'
    )

    for admin_id in ADMINS:
        try:
            if is_pdf:
                await context.bot.send_document(chat_id=admin_id, document=file_id, caption=caption, reply_markup=kb)
            else:
                await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=caption, reply_markup=kb)
        except Exception:
            logger.exception('Failed to forward receipt to admin %s', admin_id)

    # user submitted receipt; move them back to menu state until admin responds
    context.user_data['state'] = CHOOSING

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    await query.answer()

    if user.id not in ADMINS:
        try:
            await query.edit_message_caption(caption='غير مصرح لك باتخاذ هذا الإجراء.')
        except Exception:
            await context.bot.send_message(chat_id=user.id, text='ليس لديك صلاحية اتخاذ هذا الإجراء.')
        return

    try:
        action, pid = data.split(':')
        pid = int(pid)
    except Exception:
        try:
            await query.edit_message_caption(caption='حدث خطأ في معالجة الطلب.')
        except Exception:
            pass
        return

    payment = await get_payment(pid)
    if not payment:
        await context.bot.send_message(chat_id=user.id, text='خطأ: لم أجد عملية الدفع.')
        return

    buyer_id = payment[1]
    buyer_username = payment[2]
    book = payment[4]

    if action == 'accept':
        await update_payment_status(pid, 'مقبول', verified_by=user.id)
        try:
            await query.edit_message_caption(caption=(query.message.caption or '') + '\n\nتم القبول ✅')
        except Exception:
            pass

        # send download link
        try:
            link = MARKETING_LINK if book == 'marketing' else PRODUCT_LINK
            await context.bot.send_message(chat_id=buyer_id, text=f'📚 تم قبول إثبات الدفع!\nيمكنك تحميل الكتاب من هنا: {link}')
        except Exception:
            logger.exception('Failed to send book link to buyer %s', buyer_id)

        # ask for Gumroad email
        await context.bot.send_message(chat_id=buyer_id, text='✅ تم قبول الإثبات بنجاح!\nالرجاء الآن إدخال البريد الإلكتروني الذي تستخدمه في Gumroad (فقط البريد المرتبط بحسابك).')
        context.bot_data[f'waiting_email_for_{buyer_id}'] = pid
        # clear attempts
        context.bot_data.pop(f'attempts_left_{buyer_id}', None)

    elif action == 'reject':
        await update_payment_status(pid, 'مرفوض', verified_by=user.id)
        try:
            await query.edit_message_caption(caption=(query.message.caption or '') + '\n\nتم الرفض ❌')
        except Exception:
            pass

        # decrement attempts and prompt user to resend immediately
        key = f'attempts_left_{buyer_id}'
        attempts = context.bot_data.get(key, 3)
        attempts -= 1
        context.bot_data[key] = attempts

        try:
            if attempts > 0:
                await context.bot.send_message(chat_id=buyer_id, text=f'❌ لم يتم قبول إثبات الدفع. لديك {attempts} محاولة/محاولات متبقية — أرسل الإثبات مرة أخرى الآن (صورة أو PDF).')
                # ensure user's state allows receiving the next photo
                # we can't directly set another user's user_data here, so we rely on attempts key in bot_data
            else:
                # no attempts left -> return to main menu
                context.bot_data.pop(key, None)
                keyboard = [[KeyboardButton('معلومات'), KeyboardButton('شراء الكتاب')]]
                reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await context.bot.send_message(chat_id=buyer_id, text='انتهت محاولاتك. تم إرجاعك إلى الصفحة الرئيسية.', reply_markup=reply)
        except Exception:
            logger.exception('Could not notify buyer after rejection %s', buyer_id)

# handle text messages (including receiving gumroad email)
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()

    # check if waiting for gumroad email
    key = f'waiting_email_for_{update.message.from_user.id}'
    if key in context.bot_data:
        pid = context.bot_data.pop(key)
        email = text
        if not is_valid_email(email):
            await update.message.reply_text('يرجى إدخال بريد إلكتروني صحيح. مثال: user@example.com')
            context.bot_data[key] = pid
            return
        await set_payment_email(pid, email)
        payment = await get_payment(pid)
        book = payment[4]
        verified_by = payment[9] or next(iter(ADMINS))

        try:
            await context.bot.send_message(chat_id=verified_by, text=f'📧 بريد المستخدم:\nالمستخدم: @{payment[2]}\nالإيميل: {email}\nالكتاب: {"التسويق" if book=="marketing" else "صنع المنتجات"}')
        except Exception:
            for admin_id in ADMINS:
                await context.bot.send_message(chat_id=admin_id, text=f'📧 (تنبيه) بريد المستخدم: @{payment[2]} - {email} - الكتاب: {book}')

        if book == 'marketing':
            await context.bot.send_message(chat_id=update.message.from_user.id, text=f'✅ تم تسجيل بريدك الإلكتروني بنجاح!\nيمكنك الآن الانضمام إلى المجموعة: {GROUP_LINKS["marketing"]}')
        else:
            await context.bot.send_message(chat_id=update.message.from_user.id, text=(f'✅ تم تسجيل بريدك الإلكتروني بنجاح!\nيمكنك الآن الانضمام إلى مجموعة المنتجات: {GROUP_LINKS["product"]}\nكما يمكنك التواصل مع المسؤول للمساعدة في رفع المنتجات: {RESPONSIBLE_USERNAME}'))

        # notify admins about the completed sale
        buyer_id = payment[1]
        buyer_username = payment[2]
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        note = f'📥 تم شراء كتاب:\nالمستخدم: @{buyer_username}\nالوقت(UTC): {now}\nالكتاب: {"التسويق" if book=="marketing" else "صنع المنتجات"}'
        for admin_id in ADMINS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=note)
            except Exception:
                pass
        return

    # menu navigation
    if text == 'معلومات':
        return await info_handler(update, context)
    if text == 'شراء الكتاب':
        return await buy_handler(update, context)
    if text in ['كتاب التسويق', 'كتاب صنع المنتجات', 'العودة للرئيسية']:
        return await choose_book(update, context)

    await update.message.reply_text('لم أفهم ما تقصده. استخدم الأزرار للاختيار.')

# error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error('Exception while handling an update:', exc_info=context.error)
    try:
        if isinstance(update, Update) and getattr(update, 'message', None):
            await update.message.reply_text('حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.')
    except Exception:
        pass

# ---------------- Main ----------------
async def main_async():
    if not BOT_TOKEN:
        logger.error('BOT_TOKEN environment variable not set. Aborting.')
        return

    await init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler((filters.PHOTO | filters.Document.PDF) & filters.ChatType.PRIVATE, received_photo_or_doc))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_text))
    application.add_handler(CallbackQueryHandler(admin_callback))
    application.add_error_handler(error_handler)

    logger.info('Bot started...')

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()

        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        logger.info('Bot stopped by user')
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        logger.info('Bot stopped gracefully')
    finally:
        loop.close()
