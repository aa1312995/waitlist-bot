"""
Waitlist Registration Telegram Bot.

Collects user-desired usernames with auto-generated passwords.
First /start user becomes admin. Admin can download users .txt file.
"""

import logging
import os
import re
import secrets
import string
import sys
import time
import traceback
from io import BytesIO

import telegram
import telegram.error
import toml

from database import Admin, WaitlistEntry, init_db

# Import strings
from strings import en as strings

log = logging.getLogger(__name__)

# Username: 5-32 chars, alphanumeric + underscore, optional leading @
USERNAME_RE = re.compile(r"^@?([a-zA-Z][a-zA-Z0-9_]{4,31})$")


def telegram_html_escape(s: str) -> str:
    """Escape string for Telegram HTML."""
    return s.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;").replace('"', "&quot;")


def normalize_username(raw: str) -> str | None:
    """Normalize username: ensure @ prefix, validate, return lowercase for storage."""
    raw = raw.strip()
    if not raw:
        return None
    m = USERNAME_RE.match(raw)
    if not m:
        return None
    name = m.group(1)
    return f"@{name.lower()}"


def generate_password(length: int = 12) -> str:
    """Generate a secure random password with letters, digits, special chars."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))


def catch_telegram_errors(func):
    """Decorator to retry on Telegram API errors."""

    def wrapper(*args, **kwargs):
        retry_count = 0
        max_retries = 5
        base_delay = 1
        while True:
            try:
                return func(*args, **kwargs)
            except telegram.error.Unauthorized:
                log.debug(f"Unauthorized in {func.__name__}")
                return None
            except telegram.error.RetryAfter as e:
                delay = getattr(e, "retry_after", 10)
                log.warning(f"Flood control, retrying in {delay}s")
                time.sleep(delay)
                continue
            except (telegram.error.TimedOut, telegram.error.NetworkError) as e:
                retry_count += 1
                delay = min(base_delay * (2 ** (retry_count - 1)), 30)
                log.warning(f"Network/timeout in {func.__name__}, retry {retry_count}/{max_retries}")
                if retry_count >= max_retries:
                    log.error(f"Max retries exceeded for {func.__name__}")
                    return None
                time.sleep(delay)
                continue
            except telegram.error.BadRequest as e:
                err = str(e).lower()
                if "chat not found" in err or "not found" in err:
                    log.debug(f"Chat not found in {func.__name__}")
                    return None
                raise
            except telegram.error.TelegramError as e:
                retry_count += 1
                delay = min(base_delay * (2 ** (retry_count - 1)), 30)
                log.error(f"Telegram error in {func.__name__}: {e}")
                traceback.print_exc()
                if retry_count >= max_retries:
                    return None
                time.sleep(delay)
                continue

    return wrapper


class WaitlistBot:
    """Bot wrapper with error-handled Telegram API calls."""

    def __init__(self, token: str):
        self.bot = telegram.Bot(token=token)

    @catch_telegram_errors
    def send_message(self, chat_id, text, **kwargs):
        return self.bot.send_message(chat_id, text, parse_mode="HTML", **kwargs)

    @catch_telegram_errors
    def get_updates(self, **kwargs):
        return self.bot.get_updates(**kwargs)

    @catch_telegram_errors
    def answer_callback_query(self, callback_query_id, **kwargs):
        return self.bot.answer_callback_query(callback_query_id, **kwargs)

    @catch_telegram_errors
    def send_document(self, chat_id, document, filename=None, caption=None):
        return self.bot.send_document(chat_id, document, filename=filename, caption=caption)

    @catch_telegram_errors
    def get_me(self):
        return self.bot.get_me()


def main():
    config_path = os.environ.get("CONFIG_PATH", "config/config.toml")
    if not os.path.isfile(config_path):
        log.fatal(f"Config not found: {config_path}")
        sys.exit(1)

    cfg = toml.load(config_path)
    token = cfg["Telegram"]["token"]
    db_engine = cfg["Database"]["engine"]

    Session = init_db(db_engine)
    bot = WaitlistBot(token)

    me = bot.get_me()
    if not me:
        log.fatal("Invalid bot token")
        sys.exit(1)

    log.info(f"@{me.username} starting (waitlist bot)")

    # In-memory state: chat_id -> "awaiting_username" or None
    user_states = {}
    next_update = None

    user_keyboard = telegram.ReplyKeyboardMarkup(
        [[telegram.KeyboardButton(strings.msg_coming_soon_btn)]],
        one_time_keyboard=False,
    )

    admin_keyboard = telegram.ReplyKeyboardMarkup(
        [[telegram.KeyboardButton(strings.msg_admin_download)]],
        one_time_keyboard=False,
    )

    while True:
        try:
            updates = bot.get_updates(offset=next_update, timeout=cfg["Telegram"].get("long_polling_timeout", 30))
        except Exception as e:
            log.error(f"get_updates failed: {e}")
            time.sleep(5)
            continue

        if updates is None:
            time.sleep(5)
            continue

        for update in updates:
            chat_id = None
            text = None
            from_user = None

            if update.message:
                chat_id = update.message.chat.id
                from_user = update.message.from_user
                text = (update.message.text or "").strip()

                if update.message.chat.type != "private":
                    bot.send_message(chat_id, strings.msg_private_only)
                    continue

            if chat_id is None:
                continue

            session = Session()

            try:
                is_admin = session.query(Admin).filter_by(user_id=from_user.id).first() is not None
                entry = session.query(WaitlistEntry).filter_by(user_id=from_user.id).first()

                # Handle /start
                if text == "/start":
                    user_states.pop(chat_id, None)

                    admin_count = session.query(Admin).count()
                    if admin_count == 0:
                        session.add(Admin(user_id=from_user.id))
                        session.commit()
                        is_admin = True
                        bot.send_message(chat_id, strings.msg_first_admin)

                    if is_admin and entry:
                        bot.send_message(
                            chat_id,
                            strings.msg_admin_menu,
                            reply_markup=admin_keyboard,
                        )
                        continue

                    if entry:
                        bot.send_message(
                            chat_id,
                            strings.msg_welcome_back,
                            reply_markup=user_keyboard,
                        )
                        continue

                    user_states[chat_id] = "awaiting_username"
                    bot.send_message(chat_id, strings.msg_ask_username)
                    continue

                # User: Coming soon button
                if entry and text == strings.msg_coming_soon_btn:
                    bot.send_message(chat_id, strings.msg_coming_soon_response)
                    continue

                # Admin: Download users .txt file
                if is_admin and text == strings.msg_admin_download:
                    entries = (
                        session.query(WaitlistEntry)
                        .order_by(WaitlistEntry.created_at.asc())
                        .all()
                    )
                    lines = []
                    for i, e in enumerate(entries, 1):
                        lines.append(f"{i}. {e.wanted_username} {e.password}")
                    content = "\n".join(lines)
                    buf = BytesIO(content.encode("utf-8"))
                    buf.name = "users.txt"
                    bot.send_document(chat_id, buf, filename="users.txt", caption=strings.msg_file_caption)
                    continue

                # Awaiting username: treat any text as username input
                if user_states.get(chat_id) == "awaiting_username" and text:
                    username_norm = normalize_username(text)
                    if not username_norm:
                        bot.send_message(chat_id, strings.msg_username_invalid)
                        continue

                    existing = (
                        session.query(WaitlistEntry)
                        .filter(WaitlistEntry.wanted_username == username_norm)
                        .first()
                    )
                    if existing:
                        bot.send_message(chat_id, strings.msg_username_taken)
                        continue

                    password = generate_password()
                    session.add(WaitlistEntry(
                        user_id=from_user.id,
                        wanted_username=username_norm,
                        password=password,
                    ))
                    session.commit()
                    user_states.pop(chat_id, None)

                    bot.send_message(
                        chat_id,
                        strings.msg_registered,
                        reply_markup=user_keyboard,
                    )

            finally:
                session.close()

        if updates:
            next_update = updates[-1].update_id + 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="{asctime} | {name} | {message}",
        style="{",
    )
    main()
