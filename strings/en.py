# Strings / localization file for Waitlist Registration Bot
# Can be edited, but DON'T REMOVE THE REPLACEMENT FIELDS (words surrounded by {curly braces})

# Username prompt
msg_ask_username = "✨ What <b>username</b> would you like for our platform?\n\n📝 Use 5–32 characters: letters, numbers, underscore\n💡 Example: <code>myusername</code>"
msg_username_taken = "❌ That username is already taken.\n\n👉 Please choose another one!"
msg_username_invalid = "⚠️ Invalid username.\n\nUse 5–32 characters: letters, numbers, underscore only.\n💡 Example: <code>myusername</code>"
msg_username_forbidden = "🚫 This username is reserved.\n\n👉 Please choose a different one!"

# Registration
msg_registered = "🎉 You're on the list!\n\n✅ We'll notify you when the platform launches."
msg_welcome_back = "👋 Welcome back! You're already on the waitlist."
msg_registrations_closed = "🚫 Registrations are closed.\n\nWe're not accepting new sign-ups at the moment."

# Link button (customer)
msg_link_btn = "🔗 Link"
msg_link_no_links = "Link: Coming soon"
msg_link_header = "🔗 <b>Your platform access</b>"
msg_link_username = "👤 Username:"
msg_link_password = "🔑 Password:"
msg_link_single = "🌐 Link:"
msg_link_links = "🌐 Links:"
msg_link_place = "📍 Place:"
msg_link_registered = "📅 Registered:"
msg_link_bonus = "💰 Bonus:"
msg_link_password_note = "🔒 <i>For security purposes please change your password on first login, password you got was auto-generated.Please set the strongest possible password (letters, numbers, special characters).</i>"
msg_place_admin = "Admin"
msg_place_test = "Test"
msg_bonus_na = "N/A"

# Admin
msg_admin_download = "📥 Download users .txt file"
msg_admin_set_link = "🔗 Set Link"
msg_admin_delete = "🗑 Delete by username"
msg_admin_broadcast = "📤 Send platform access message"
msg_admin_stop_broadcast = "⏹ Stop recurring broadcast"
msg_switch_to_user = "👤 Switch to User menu"
msg_switch_to_admin = "🔐 Switch to Admin menu"
msg_file_caption = "📋 Waitlist users export (older → newer)"
msg_first_admin = "👑 You're the first user — you're now admin!\n\n👇 Choose your username below to join the waitlist."
msg_admin_menu = "🔐 <b>Admin menu</b>\n\nUse the keyboard below to manage the waitlist."
msg_set_link_current = "📎 <b>Current links:</b>\n\n{links}\n\n👇 Send new links to replace (comma-separated), or press Skip to keep."
msg_set_link_prompt = "👇 Send one or more links separated by comma.\n\nValid: <code>http://</code>, <code>https://</code>, or <code>.onion</code>"
msg_set_link_invalid = "⚠️ Invalid link(s). Use http://, https://, or .onion addresses."
msg_set_link_saved = "✅ Links saved!"
msg_set_link_skip = "⏭️ Skip"
msg_delete_prompt = "👇 Send the username to delete:"
msg_delete_success = "✅ User deleted."
msg_delete_not_found = "❌ Username not found."
msg_delete_cancel = "⏭️ Cancel"
msg_broadcast_prompt = "How often (minutes)? Enter a number for recurring every N minutes, or press Skip for one-time send now."
msg_broadcast_invalid = "Invalid. Send a number (e.g. 30) or Skip."
msg_broadcast_recurring = "Recurring broadcast started: every {minutes} minutes."
msg_broadcast_one_time = "Sending now (one-time)..."
msg_broadcast_complete = "Broadcast complete. Sent: {sent}, Failed: {failed}"
msg_broadcast_stopped = "Recurring broadcast stopped."
msg_broadcast_not_active = "No recurring broadcast is active."

# Errors
msg_private_only = "🔒 This bot only works in private chats. Please message me directly!"
