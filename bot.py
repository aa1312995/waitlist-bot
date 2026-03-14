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
import threading
import time
import traceback
from io import BytesIO

import telegram
import telegram.error
import toml

from sqlalchemy import and_, or_

from database import Admin, BotConfig, WaitlistEntry, init_db

# Import strings
from strings import en as strings

log = logging.getLogger(__name__)

# Username: 5-32 chars, alphanumeric + underscore, optional leading @
USERNAME_RE = re.compile(r"^@?([a-zA-Z][a-zA-Z0-9_]{4,31})$")

# Forbidden usernames (admin can use any, including "admin")
FORBIDDEN_USERNAMES = frozenset(
    {"admin", "owner", "operator", "courier", "kurir", "vendor", "dealer", "diler"}
)

# Link validation: http://, https://, or .onion
LINK_RE = re.compile(
    r"^(https?://[^\s,]+|[a-zA-Z0-9\-]+\.onion[a-zA-Z0-9/]*)$",
    re.IGNORECASE,
)


def telegram_html_escape(s: str) -> str:
    """Escape string for Telegram HTML."""
    return s.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;").replace('"', "&quot;")


def normalize_username(raw: str) -> str | None:
    """Normalize username: validate, return lowercase for storage (no @ prefix)."""
    raw = raw.strip()
    if not raw:
        return None
    m = USERNAME_RE.match(raw)
    if not m:
        return None
    return m.group(1).lower()


def generate_password(length: int = 12) -> str:
    """Generate a secure random password with letters, digits, special chars."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))


def get_platform_links(session) -> list[str]:
    """Get stored platform links from config."""
    row = session.query(BotConfig).filter_by(key="platform_links").first()
    if not row or not row.value:
        return []
    return [s.strip() for s in row.value.split(",") if s.strip()]


def validate_and_parse_links(raw: str) -> list[str] | None:
    """Validate comma-separated links; return list or None if invalid."""
    parts = [s.strip() for s in raw.split(",") if s.strip()]
    if not parts:
        return None
    for p in parts:
        if not LINK_RE.match(p):
            return None
    return parts


def get_user_position(session, entry: WaitlistEntry) -> int:
    """Get 1-based position by created_at order (oldest first)."""
    count = (
        session.query(WaitlistEntry)
        .filter(
            or_(
                WaitlistEntry.created_at < entry.created_at,
                and_(
                    WaitlistEntry.created_at == entry.created_at,
                    WaitlistEntry.id < entry.id,
                ),
            )
        )
        .count()
    )
    return count + 1


def get_bonus_display(position: int) -> str:
    """Get bonus text: positions 1-3 show N/A, 4+ show EUR amount."""
    if position <= 3:
        return strings.msg_bonus_na
    eff = position - 3
    if eff <= 50:
        return "12 EUR"
    if eff <= 100:
        return "5 EUR"
    return "1 EUR"


def get_place_display(position: int, is_admin: bool) -> str:
    """Get place text: #1/#2... for position 4+, Admin/Test for 1-3."""
    if position <= 3:
        return strings.msg_place_admin if is_admin else strings.msg_place_test
    return f"#{position - 3}"


def build_platform_access_message(session, entry: WaitlistEntry, links: list[str]) -> str:
    """Build the platform access message for a given entry (same as Link button)."""
    is_admin = session.query(Admin).filter_by(user_id=entry.user_id).first() is not None
    position = get_user_position(session, entry)
    place_str = get_place_display(position, is_admin)
    bonus_str = get_bonus_display(position)
    date_str = entry.created_at.strftime("%d.%m.%Y %H:%M:%S")

    lines = [
        strings.msg_link_header,
        "",
        f"{strings.msg_link_username}",
        f"<code>{telegram_html_escape(entry.wanted_username)}</code>",
        "",
        f"{strings.msg_link_password}",
        f"<code>{telegram_html_escape(entry.password)}</code>",
        "",
    ]
    if links:
        label = strings.msg_link_links if len(links) > 1 else strings.msg_link_single
        lines.append(label)
        for link in links:
            lines.append(f"<code>{telegram_html_escape(link)}</code>")
        lines.append("")
    else:
        lines.append(strings.msg_link_no_links)
        lines.append("")

    lines.extend([
        f"{strings.msg_link_place} {place_str}",
        f"{strings.msg_link_registered} {date_str}",
        f"{strings.msg_link_bonus} {bonus_str}",
        "",
        strings.msg_link_password_note,
    ])
    return "\n".join(lines)


def do_broadcast(bot_instance: "WaitlistBot", Session, admin_user_ids: list[int]) -> tuple[int, int]:
    """Send platform access message to all registered users. Returns (sent, failed)."""
    session = Session()
    try:
        entries = (
            session.query(WaitlistEntry)
            .order_by(WaitlistEntry.created_at.asc())
            .all()
        )
        links = get_platform_links(session)

        sent = 0
        failed = 0
        for entry in entries:
            text = build_platform_access_message(session, entry, links)
            result = bot_instance.send_message(entry.user_id, text)
            if result is not None:
                sent += 1
            else:
                failed += 1
            time.sleep(0.1)

        complete_msg = strings.msg_broadcast_complete.format(sent=sent, failed=failed)
        for admin_id in admin_user_ids:
            bot_instance.send_message(admin_id, complete_msg)

        return (sent, failed)
    finally:
        session.close()


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

    # In-memory state: chat_id -> "awaiting_username" | "awaiting_links" | "awaiting_delete_username" | "awaiting_broadcast_minutes" | None
    user_states = {}
    next_update = None

    user_keyboard = telegram.ReplyKeyboardMarkup(
        [[telegram.KeyboardButton(strings.msg_link_btn)]],
        one_time_keyboard=False,
    )

    admin_user_keyboard = telegram.ReplyKeyboardMarkup(
        [
            [telegram.KeyboardButton(strings.msg_link_btn), telegram.KeyboardButton(strings.msg_switch_to_admin)],
        ],
        one_time_keyboard=False,
    )

    admin_keyboard = telegram.ReplyKeyboardMarkup(
        [
            [telegram.KeyboardButton(strings.msg_admin_download), telegram.KeyboardButton(strings.msg_admin_set_link)],
            [telegram.KeyboardButton(strings.msg_admin_delete)],
            [telegram.KeyboardButton(strings.msg_admin_broadcast), telegram.KeyboardButton(strings.msg_admin_stop_broadcast)],
            [telegram.KeyboardButton(strings.msg_switch_to_user)],
        ],
        one_time_keyboard=False,
    )

    broadcast_state = {"active": False, "stop_requested": False}

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
                        user_states[chat_id] = "awaiting_username"
                        bot.send_message(chat_id, strings.msg_first_admin)
                        continue

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

                    # Registrations closed: block new unregistered users
                    bot.send_message(chat_id, strings.msg_registrations_closed)
                    continue

                # User: Link button
                if entry and text == strings.msg_link_btn:
                    links = get_platform_links(session)
                    msg = build_platform_access_message(session, entry, links)
                    bot.send_message(chat_id, msg)
                    continue

                # Admin: Switch to User menu
                if is_admin and text == strings.msg_switch_to_user:
                    bot.send_message(
                        chat_id,
                        strings.msg_welcome_back,
                        reply_markup=admin_user_keyboard,
                    )
                    continue

                # Admin: Switch to Admin menu
                if is_admin and text == strings.msg_switch_to_admin:
                    bot.send_message(
                        chat_id,
                        strings.msg_admin_menu,
                        reply_markup=admin_keyboard,
                    )
                    continue

                # Admin: Set Link
                if is_admin and text == strings.msg_admin_set_link:
                    links = get_platform_links(session)
                    skip_keyboard = telegram.ReplyKeyboardMarkup(
                        [[telegram.KeyboardButton(strings.msg_set_link_skip)]],
                        one_time_keyboard=True,
                    )
                    if links:
                        links_text = "\n".join(f"<code>{telegram_html_escape(l)}</code>" for l in links)
                        bot.send_message(
                            chat_id,
                            strings.msg_set_link_current.format(links=links_text),
                            reply_markup=skip_keyboard,
                        )
                    else:
                        bot.send_message(
                            chat_id,
                            strings.msg_set_link_prompt,
                            reply_markup=skip_keyboard,
                        )
                    user_states[chat_id] = "awaiting_links"
                    continue

                # Admin: Send platform access message
                if is_admin and text == strings.msg_admin_broadcast:
                    skip_keyboard = telegram.ReplyKeyboardMarkup(
                        [[telegram.KeyboardButton(strings.msg_set_link_skip)]],
                        one_time_keyboard=True,
                    )
                    bot.send_message(
                        chat_id,
                        strings.msg_broadcast_prompt,
                        reply_markup=skip_keyboard,
                    )
                    user_states[chat_id] = "awaiting_broadcast_minutes"
                    continue

                # Admin: Stop recurring broadcast
                if is_admin and text == strings.msg_admin_stop_broadcast:
                    if broadcast_state["active"]:
                        broadcast_state["stop_requested"] = True
                        bot.send_message(chat_id, strings.msg_broadcast_stopped)
                    else:
                        bot.send_message(chat_id, strings.msg_broadcast_not_active)
                    continue

                # Admin: Delete by username
                if is_admin and text == strings.msg_admin_delete:
                    cancel_keyboard = telegram.ReplyKeyboardMarkup(
                        [[telegram.KeyboardButton(strings.msg_delete_cancel)]],
                        one_time_keyboard=True,
                    )
                    bot.send_message(
                        chat_id,
                        strings.msg_delete_prompt,
                        reply_markup=cancel_keyboard,
                    )
                    user_states[chat_id] = "awaiting_delete_username"
                    continue

                # Admin: awaiting_broadcast_minutes
                if is_admin and user_states.get(chat_id) == "awaiting_broadcast_minutes":
                    if text == strings.msg_delete_cancel:
                        user_states.pop(chat_id, None)
                        bot.send_message(
                            chat_id,
                            strings.msg_admin_menu,
                            reply_markup=admin_keyboard,
                        )
                        continue
                    if text == strings.msg_admin_download:
                        user_states.pop(chat_id, None)
                        entries = (
                            session.query(WaitlistEntry)
                            .order_by(WaitlistEntry.created_at.asc())
                            .all()
                        )
                        lines = [f"{i}. {e.wanted_username} {e.password}" for i, e in enumerate(entries, 1)]
                        buf = BytesIO("\n".join(lines).encode("utf-8"))
                        buf.name = "users.txt"
                        bot.send_document(chat_id, buf, filename="users.txt", caption=strings.msg_file_caption)
                        bot.send_message(chat_id, strings.msg_admin_menu, reply_markup=admin_keyboard)
                        continue
                    if text == strings.msg_set_link_skip:
                        user_states.pop(chat_id, None)
                        admin_ids = [a.user_id for a in session.query(Admin).all()]
                        bot.send_message(chat_id, strings.msg_broadcast_one_time, reply_markup=admin_keyboard)

                        def run_one_time():
                            do_broadcast(bot, Session, admin_ids)

                        threading.Thread(target=run_one_time, daemon=True).start()
                        continue
                    try:
                        minutes = int(text)
                        if minutes < 1:
                            raise ValueError("must be positive")
                    except ValueError:
                        bot.send_message(chat_id, strings.msg_broadcast_invalid)
                        continue
                    user_states.pop(chat_id, None)
                    if broadcast_state["active"]:
                        broadcast_state["stop_requested"] = True
                        time.sleep(2)
                    admin_ids = [a.user_id for a in session.query(Admin).all()]
                    bot.send_message(
                        chat_id,
                        strings.msg_broadcast_recurring.format(minutes=minutes),
                        reply_markup=admin_keyboard,
                    )

                    def run_recurring():
                        broadcast_state["stop_requested"] = False
                        broadcast_state["active"] = True
                        try:
                            while not broadcast_state["stop_requested"]:
                                do_broadcast(bot, Session, admin_ids)
                                for _ in range(minutes * 60):
                                    if broadcast_state["stop_requested"]:
                                        break
                                    time.sleep(1)
                        finally:
                            broadcast_state["active"] = False

                    threading.Thread(target=run_recurring, daemon=True).start()
                    continue

                # Admin: awaiting_delete_username
                if is_admin and user_states.get(chat_id) == "awaiting_delete_username":
                    if text == strings.msg_delete_cancel:
                        user_states.pop(chat_id, None)
                        bot.send_message(
                            chat_id,
                            strings.msg_admin_menu,
                            reply_markup=admin_keyboard,
                        )
                        continue
                    if text == strings.msg_admin_download:
                        user_states.pop(chat_id, None)
                        entries = (
                            session.query(WaitlistEntry)
                            .order_by(WaitlistEntry.created_at.asc())
                            .all()
                        )
                        lines = [f"{i}. {e.wanted_username} {e.password}" for i, e in enumerate(entries, 1)]
                        buf = BytesIO("\n".join(lines).encode("utf-8"))
                        buf.name = "users.txt"
                        bot.send_document(chat_id, buf, filename="users.txt", caption=strings.msg_file_caption)
                        bot.send_message(chat_id, strings.msg_admin_menu, reply_markup=admin_keyboard)
                        continue
                    username_norm = normalize_username(text)
                    if not username_norm:
                        bot.send_message(chat_id, strings.msg_username_invalid)
                        continue
                    target = session.query(WaitlistEntry).filter_by(wanted_username=username_norm).first()
                    if not target:
                        bot.send_message(chat_id, strings.msg_delete_not_found)
                        continue
                    session.delete(target)
                    session.commit()
                    user_states.pop(chat_id, None)
                    bot.send_message(
                        chat_id,
                        strings.msg_delete_success,
                        reply_markup=admin_keyboard,
                    )
                    continue

                # Admin: awaiting_links (Set Link flow)
                if is_admin and user_states.get(chat_id) == "awaiting_links":
                    if text == strings.msg_set_link_skip:
                        user_states.pop(chat_id, None)
                        bot.send_message(
                            chat_id,
                            strings.msg_admin_menu,
                            reply_markup=admin_keyboard,
                        )
                        continue
                    if text == strings.msg_admin_download:
                        user_states.pop(chat_id, None)
                        entries = (
                            session.query(WaitlistEntry)
                            .order_by(WaitlistEntry.created_at.asc())
                            .all()
                        )
                        lines = [f"{i}. {e.wanted_username} {e.password}" for i, e in enumerate(entries, 1)]
                        buf = BytesIO("\n".join(lines).encode("utf-8"))
                        buf.name = "users.txt"
                        bot.send_document(chat_id, buf, filename="users.txt", caption=strings.msg_file_caption)
                        bot.send_message(chat_id, strings.msg_admin_menu, reply_markup=admin_keyboard)
                        continue
                    parsed = validate_and_parse_links(text)
                    if parsed is None:
                        bot.send_message(chat_id, strings.msg_set_link_invalid)
                        continue
                    row = session.query(BotConfig).filter_by(key="platform_links").first()
                    if row:
                        row.value = ",".join(parsed)
                    else:
                        session.add(BotConfig(key="platform_links", value=",".join(parsed)))
                    session.commit()
                    user_states.pop(chat_id, None)
                    bot.send_message(
                        chat_id,
                        strings.msg_set_link_saved,
                        reply_markup=admin_keyboard,
                    )
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

                    # Forbidden usernames (admin can use any)
                    if not is_admin and username_norm in FORBIDDEN_USERNAMES:
                        bot.send_message(chat_id, strings.msg_username_forbidden)
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

                    markup = admin_keyboard if is_admin else user_keyboard
                    bot.send_message(
                        chat_id,
                        strings.msg_registered,
                        reply_markup=markup,
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
