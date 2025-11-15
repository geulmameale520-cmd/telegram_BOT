import os
import logging
import re
from datetime import datetime
import aiosqlite
import asyncio
from pathlib import Path
from telegram import (Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, InputFile)
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
        # ignore invalid values
        pass

# Fallback: if ADMINS is empty, you can still add a default admin id here for testing.
if not ADMINS:
    # NOTE: remove this in production or set ADMIN_IDS env variable.
    try:
        ADMINS.add(7918198745)
    except Exception:
        pass

# Paths to book PDFs (place your book files here)
BOOKS_DIR = Path(__file__).parent / 'books'
BOOK_FILES = {
    'marketing': BOOKS_DIR / 'marketing.pdf',
    'product': BOOKS_DIR / 'product.pdf'
}

# Group links and responsible username
GROUP_LINKS = {
    'marketing': 'https://t.me/+39YNXIC0CgJkNTdk',
    'product': 'https://t.me/+c9rnGxHKsX5mYjA0'
}
RESPONSIBLE_USERNAME = '@aleeddin'  # contact for product buyers

DB_PATH = 'payments.db'

# Prices
PRICES = {
    'marketing': 1000,
    'product': 1500
}

# States (not using ConversationHandler but simple state flags in user_data)
CHOOSING = 'CHOOSING'
WAITING_RECEIPT = 'WAITING_RECEIPT'
WAITING_EMAIL = 'WAITING_EMAIL'

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------- Database helpers (async) ----------------

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

# ----------------- Helpers ------------------

def is_valid_email(email: str) -> bool:
    pattern = r"^[\w.-]+@[\w.-]+\.\w{2,}$"
    return bool(re.match(pattern, email))

async def download_and_check_image(bot, file_id):
    """Download a Telegram file by file_id and try to determine if it's a valid image.
    Returns format string like 'JPEG'/'PNG' or None if not an image.
    """
    try:
        file = await bot.get_file(file_id)
        data = await file.download_as_bytearray()
        img = Image.open(io.BytesIO(data))
        return img.format
    except Exception:
        return None

# ------------------ Bot Handlers ------------------

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
    # clear any user-specific state
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
    text = update.message.text.strip()
    if text == 'العودة للرئيسية':
        return await start(update, context)

    if text not in ['كتاب التسويق', 'كتاب صنع المنتجات']:
        await update.message.reply_text('اختر أحد خيارات الأزرار.')
        return

    book_key = 'marketing' if text == 'كتاب التسويق' else 'product'
    context.user_data['chosen_book'] = book_key
    price = PRICES[book_key]

    keyboard = [[KeyboardButton('إرسال إثبات الدفع'), KeyboardButton('العودة للرئيسية')]]
    reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        f'سعر الكتاب هو {price} دج.\n'
        f'الرجاء تحويل المبلغ إلى: {ACCOUNT_MOBILE} (CCP/MOB).\n'
        'بعد التحويل، اضغط "إرسال إثبات الدفع" أو أرسل صورة/ملف PDF هنا.\n'
        'ستصلك رسالة تأكيد بعد استلامنا للوثيقة.',
        reply_markup=reply
    )

    # mark that we're expecting a receipt; initialize attempts
    context.user_data['state'] = WAITING_RECEIPT
    context.user_data['attempts_left'] = 3  # total attempts allowed

async def received_photo_or_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Accept both photos and documents (pdf)
    user = update.message.from_user
    state = context.user_data.get('state')
    if state != WAITING_RECEIPT:
        await update.message.reply_text('لست في مرحلة دفع الآن. ابدأ بالضغط على "شراء الكتاب" إذا رغبت.')
        return

    # Determine if photo or pdf document
    file_id = None
    is_pdf = False
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        # Accept PDF documents
        doc = update.message.document
        if doc.mime_type == 'application/pdf' or str(doc.file_name).lower().endswith('.pdf'):
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

    # Optional: check image validity for photos
    if not is_pdf and file_id:
        fmt = await download_and_check_image(context.bot, file_id)
        if fmt is None:
            await update.message.reply_text('الملف المرسل ليس صورة صالحة. يرجى إرسال صورة بصيغة JPEG أو PNG أو إرسال PDF.')
            return

    payment_id = await add_payment(user.id, user.username or '', full_name, book, now, 'قيد المراجعة', file_id)

    await update.message.reply_text('✅ تم استلام إثبات الدفع. جاري إرساله للمسؤول للمراجعة.')

    accept_button = InlineKeyboardButton('✅ قبول', callback_data=f'accept:{payment_id}')
    reject_button = InlineKeyboardButton('❌ رفض', callback_data=f'reject:{payment_id}')
    kb = InlineKeyboardMarkup([[accept_button, reject_button]])

    # send to all admins
    caption = (
        f'🔔 طلب جديد:\n'
        f'المستخدم: @{user.username or "(بدون اسم)"}\n'
        f'الكتاب: {"كتاب التسويق" if book=="marketing" else "كتاب صنع المنتجات"}\n'
        f'الوقت (UTC): {now}\nID الدفع: {payment_id}'
    )

    for admin_id in ADMINS:
        try:
            # Send as photo if original was photo, else as document
            if is_pdf:
                await context.bot.send_document(chat_id=admin_id, document=file_id, caption=caption, reply_markup=kb)
            else:
                await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=caption, reply_markup=kb)
        except Exception as e:
            logger.exception('Failed to forward receipt to admin %s: %s', admin_id, e)

    # mark user no longer waiting for receipt (they submitted), but attempts_left stays tracked
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
            # editing caption may fail if message type different; just notify the admin
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

    # payment fields: (id, user_id, username, full_name, book, payment_time, status, receipt_file_id, email, verified_by)
    buyer_id = payment[1]
    buyer_username = payment[2]
    book = payment[4]
    receipt_file_id = payment[7]

    if action == 'accept':
        await update_payment_status(pid, 'مقبول', verified_by=user.id)
        try:
            await query.edit_message_caption(caption=(query.message.caption or '') + '\n\nتم القبول ✅')
        except Exception:
            pass

        # Send the book PDF to the buyer
        try:
            book_path = BOOK_FILES.get(book)
            if book_path and book_path.exists():
                # send local file
                await context.bot.send_document(chat_id=buyer_id, document=InputFile(str(book_path)), caption='📚 هذا ملف الكتاب. شكراً لدعمك!')
            else:
                # fallback: send the receipt back to buyer and notify admin to send PDF manually
                await context.bot.send_message(chat_id=buyer_id, text='✅ تم قبول إثبات الدفع!\nلكن ملف الكتاب غير متوفر الآن تلقائياً، سيتواصل معنا المسؤول لإرساله.')
                # notify admin
                await context.bot.send_message(chat_id=user.id, text=f'ملف الكتاب {book} غير موجود في الخادم. يرجى إرساله يدوياً.')
        except Exception:
            logger.exception('Failed to send book to buyer %s', buyer_id)

        # ask for Gumroad email
        await context.bot.send_message(
            chat_id=buyer_id,
            text='✅ تم قبول الإثبات بنجاح!\nالرجاء الآن إدخال البريد الإلكتروني الذي تستخدمه في Gumroad (فقط البريد المرتبط بحسابك).'
        )

        # store pending mapping so next text from that user is treated as email
        context.bot_data[f'waiting_email_for_{buyer_id}'] = pid

    elif action == 'reject':
        await update_payment_status(pid, 'مرفوض', verified_by=user.id)
        try:
            await query.edit_message_caption(caption=(query.message.caption or '') + '\n\nتم الرفض ❌')
        except Exception:
            pass

        # Notify buyer
        try:
            await context.bot.send_message(chat_id=buyer_id, text='❌ لم يتم قبول إثبات الدفع. يمكنك إعادة المحاولة مرتين أخريين.')
        except Exception:
            logger.exception('Could not notify buyer about rejection %s', buyer_id)

        # Track attempts using bot_data keyed by buyer id
        key = f'attempts_left_{buyer_id}'
        attempts = context.bot_data.get(key, 3)
        attempts -= 1
        context.bot_data[key] = attempts

        if attempts > 0:
            # ask user to resend receipt
            try:
                await context.bot.send_message(chat_id=buyer_id, text=f'لديك {attempts} محاولة/محاولات متبقية. أرسل إثبات الدفع (صورة أو PDF) مرة أخرى.')
            except Exception:
                pass
        else:
            # return to main menu and reset attempts
            context.bot_data.pop(key, None)
            try:
                keyboard = [[KeyboardButton('معلومات'), KeyboardButton('شراء الكتاب')]]
                reply = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                await context.bot.send_message(
                    chat_id=buyer_id,
                    text='انتهت محاولاتك. تم إرجاعك إلى الصفحة الرئيسية.',
                    reply_markup=reply
                )
            except Exception:
                pass

# Text handler for various commands and for receiving the Gumroad email after acceptance

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or '').strip()

    # If waiting for email mapping exists for this user
    key = f'waiting_email_for_{update.message.from_user.id}'
    if key in context.bot_data:
        pid = context.bot_data.pop(key)
        email = text
        if not is_valid_email(email):
            await update.message.reply_text('يرجى إدخال بريد إلكتروني صحيح. مثال: user@example.com')
            # restore mapping
            context.bot_data[key] = pid
            return
        await set_payment_email(pid, email)
        payment = await get_payment(pid)
        book = payment[4]
        verified_by = payment[9] or next(iter(ADMINS))

        # notify the admin who verified (or the first admin)
        try:
            await context.bot.send_message(
                chat_id=verified_by,
                text=f'📧 بريد المستخدم:\nالمستخدم: @{payment[2]}\nالإيميل: {email}\nالكتاب: {"التسويق" if book=="marketing" else "صنع المنتجات"}'
            )
        except Exception:
            # fallback: notify all admins
            for admin_id in ADMINS:
                await context.bot.send_message(chat_id=admin_id, text=f'📧 (تنبيه) بريد المستخدم: @{payment[2]} - {email} - الكتاب: {book}')

        # send groups / responsible username
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

        # Log the buyer info to admins (username, time, book)
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

    # Menu navigation
    if text == 'معلومات':
        return await info_handler(update, context)
    if text == 'شراء الكتاب':
        return await buy_handler(update, context)
    if text in ['كتاب التسويق', 'كتاب صنع المنتجات', 'العودة للرئيسية', 'إرسال إثبات الدفع']:
        return await choose_book(update, context)

    await update.message.reply_text('لم أفهم ما تقصده. استخدم الأزرار للاختيار.')

# Error handler

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error('Exception while handling an update:', exc_info=context.error)
    try:
        if isinstance(update, Update) and getattr(update, 'message', None):
            await update.message.reply_text('حدث خطأ غير متوقع. يرجى المحاولة لاحقاً.')
    except Exception:
        pass

# ------------------ Main ------------------
# ------------------ Main ------------------
# ------------------ Main ------------------
# ------------------ Main ------------------

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
        # الطريقة القديمة الموثوقة
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        
        # انتظار إلى الأبد
        while True:
            await asyncio.sleep(3600)  # انتظار ساعة ثم كرر
            
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == '__main__':
    # تجنب استخدام asyncio.run لمشاكل Render
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        logger.info("Bot stopped gracefully")
    finally:
        loop.close()

