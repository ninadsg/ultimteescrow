from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ChatMember
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, ConversationHandler
import aiohttp
import time
import asyncio
from datetime import datetime
import os
from flask import Flask
from threading import Thread

# ============ CONFIGURATION ============
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
API_BASE_URL = os.environ.get("API_BASE_URL", "https://fam-way-pro.onrender.com")
OWNER_USER_ID = int(os.environ.get("OWNER_USER_ID", 8558052873))
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "clerkMM")
ESCROW_FEE = 0

# ============ KEEP ALIVE WEB SERVER ============
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "🤖 Escrow Bot is Running!"

@app_flask.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port, debug=False)

# ============ DEAL COUNTER START FROM 400 ============
deal_counter = 408

# ============ CONVERSATION STATES ============
SELECT_MODE, UPI_ID, GMAIL_KEY_STATE, UPLOAD_QR = range(4)

# ============ STORAGE ============
escrows = {}
pending_payments = {}
verified_payments = set()
agreements = {}
release_agreements = {}
refund_agreements = {}
pinned_messages = {}
user_active_deal = {}
user_stats = {}
user_upi = {}
promoted_escrowers = set()
escrower_username_map = {}
deal_form_messages = {}
pending_escrow_selection = {}

# ============ API CLASS ============

class FamVerificationAPI:
    def __init__(self):
        self.base_url = API_BASE_URL
    
    async def generate_qr(self, amount, gmail_key):
        if not gmail_key:
            return None
            
        url = f"{self.base_url}/qr-gen"
        params = {
            "amount": amount,
            "gmail_key": gmail_key
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("success"):
                            return data["data"]
                    return None
            except Exception as e:
                print(f"QR Error: {e}")
                return None
    
    async def verify_payment(self, order_id, gmail_key):
        if not gmail_key:
            return None
            
        url = f"{self.base_url}/verify"
        params = {
            "order_id": order_id,
            "gmail_key": gmail_key
        }
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, timeout=15) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("success"):
                            return data["data"]
                    return None
            except Exception as e:
                print(f"Verify Error: {e}")
                return None

api = FamVerificationAPI()

# ============ HELPER FUNCTIONS ============

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_USER_ID

def is_escrower(user_id: int) -> bool:
    return user_id in promoted_escrowers or is_owner(user_id)

def get_escrower_data(username: str):
    return user_upi.get(username.lower())

def get_all_escrowers():
    escrowers = []
    for user_id in promoted_escrowers:
        username = escrower_username_map.get(user_id)
        if username:
            data = get_escrower_data(username)
            escrowers.append({
                "user_id": user_id,
                "username": username,
                "upi": data['upi'] if data else 'Not set',
                "has_gmail": bool(data and data.get('gmail_key')),
                "manual": data.get('manual', False) if data else False,
                "has_qr": bool(data and data.get('qr_photo')) if data else False
            })
    return escrowers

def normalize_username(username: str) -> str:
    return username.lower() if username else ""

async def pin_message(context, chat_id, message_id):
    try:
        await context.bot.pin_chat_message(chat_id, message_id)
        return True
    except:
        return False

async def unpin_message(context, chat_id, message_id):
    try:
        await context.bot.unpin_chat_message(chat_id, message_id)
        return True
    except:
        return False

def generate_deal_id():
    global deal_counter
    deal_counter += 1
    return str(deal_counter).zfill(3)

def find_deal_by_username(username: str, chat_id: int, status_filter=None):
    username_lower = normalize_username(username)
    
    for deal_id, escrow in escrows.items():
        if escrow["group_id"] != chat_id:
            continue
        
        if status_filter and escrow["status"] != status_filter:
            continue
        
        buyer_lower = normalize_username(escrow["buyer"])
        seller_lower = normalize_username(escrow["seller"])
        
        if username_lower == buyer_lower or username_lower == seller_lower:
            return deal_id
    
    return None

def update_user_stats(username, deal_id, amount, role, action):
    if username not in user_stats:
        user_stats[username] = {
            "total_deals": 0,
            "completed": 0,
            "pending": 0,
            "total_amount": 0,
            "deals": []
        }
    
    user_stats[username]["total_deals"] += 1
    user_stats[username]["total_amount"] += float(amount)
    user_stats[username]["deals"].append({
        "deal_id": deal_id,
        "amount": amount,
        "role": role,
        "action": action,
        "status": "completed" if action == "release" else "pending"
    })
    
    if action == "release":
        user_stats[username]["completed"] += 1
    elif action == "refund":
        user_stats[username]["pending"] += 1

# ============ STYLING FUNCTIONS ============

def format_agreement(deal_id, escrow, agreed_count):
    return f"""
📋 ESCROW AGREEMENT
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
📅 {datetime.fromtimestamp(escrow['timestamp']).strftime('%d %b %Y, %I:%M %p')}

👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
💰 Amount: ₹{escrow['amount']}
📦 Item: {escrow['item']}

✅ @{escrow['buyer']}: {'✅ Agreed' if agreements[deal_id]['buyer_agreed'] else '⏳ Pending'}
✅ @{escrow['seller']}: {'✅ Agreed' if agreements[deal_id]['seller_agreed'] else '⏳ Pending'}

📊 Progress: {agreed_count}/2

💡 @{escrow['buyer']} → /agree
💡 @{escrow['seller']} → /agree

━━━━━━━━━━━━━━━━━━━━━
🤖 @{OWNER_USERNAME}
"""

def format_deal_form(deal_id, escrow):
    escrow_data = get_escrower_data(escrow['escrower_username'])
    upi = escrow_data['upi'] if escrow_data else 'Not set'
    mode = "🔐 Auto (Gmail)" if (escrow_data and escrow_data.get('gmail_key')) else "📱 Manual (QR)"
    
    return f"""
📋 DEAL FORM #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
💰 Amount: ₹{escrow['amount']}
📦 Item: {escrow['item']}
🔐 Escrower: @{escrow['escrower_username']}
💳 Pay to: {upi}
📱 Mode: {mode}
🆔 Order: {escrow.get('order_id', 'N/A')}

⏳ AWAITING PAYMENT

🔔 @{escrow['buyer']} please complete payment

━━━━━━━━━━━━━━━━━━━━━
🤖 @{OWNER_USERNAME}
"""

def format_release_complete(deal_id, escrow, seller_upi):
    return f"""
✅ DEAL COMPLETED #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
📅 {datetime.now().strftime('%d %b %Y, %I:%M %p')}

💰 Amount: ₹{escrow['amount']}
📦 Item: {escrow['item']}

👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
🔐 Escrower: @{escrow['escrower_username']}
💳 Seller UPI: {seller_upi}
🆔 TXN: {escrow.get('txn_id', 'N/A')}

✅ Payment Released!

━━━━━━━━━━━━━━━━━━━━━
🤖 @{OWNER_USERNAME}
"""

def format_refund_complete(deal_id, escrow, buyer_upi):
    return f"""
↩️ DEAL REFUNDED #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
📅 {datetime.now().strftime('%d %b %Y, %I:%M %p')}

💰 Amount: ₹{escrow['amount']}
📦 Item: {escrow['item']}

👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
🔐 Escrower: @{escrow['escrower_username']}
💳 Buyer UPI: {buyer_upi}
🆔 TXN: {escrow.get('txn_id', 'N/A')}

↩️ Refunded to Buyer

━━━━━━━━━━━━━━━━━━━━━
🤖 @{OWNER_USERNAME}
"""

# ============ PAYMENT CHECKER ============

async def check_pending_payments():
    while True:
        try:
            for order_id, data in list(pending_payments.items()):
                escrow_id = data["escrow_id"]
                
                if order_id in verified_payments:
                    continue
                
                if escrow_id not in escrows:
                    continue
                
                escrow = escrows[escrow_id]
                escrow_data = get_escrower_data(escrow['escrower_username'])
                
                if not escrow_data or not escrow_data.get('gmail_key'):
                    continue
                
                verification_data = await api.verify_payment(order_id, escrow_data['gmail_key'])
                
                if verification_data and verification_data.get("status") == "paid":
                    verified_payments.add(order_id)
                    
                    if escrow_id in escrows:
                        escrow = escrows[escrow_id]
                        if escrow["status"] == "awaiting_payment":
                            escrow["status"] = "payment_received"
                            escrow["txn_id"] = verification_data.get("txn_id")
                            escrow["payer_name"] = verification_data.get("payer_name")
                            del pending_payments[order_id]
        except Exception as e:
            print(f"Payment check error: {e}")
        
        await asyncio.sleep(30)

# ============ DM DASHBOARD ============

def get_dashboard_keyboard():
    keyboard = [
        [KeyboardButton("📊 My Stats")],
        [KeyboardButton("📋 My Dealing History")],
        [KeyboardButton("⏳ My Pending Deals")],
        [KeyboardButton("🔍 View Past Deal Info")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        username = user.username or "User"
        user_id = user.id
        
        is_escrower_user = is_escrower(user_id)
        
        stats = user_stats.get(username, {"total_deals": 0, "completed": 0, "pending": 0, "total_amount": 0})
        
        text = f"""
👋 Welcome, @{username}!

This is your personal deal dashboard.

📊 Your Stats:
• Total Deals: {stats['total_deals']}
• Completed: {stats['completed']}
• Pending: {stats['pending']}
• Total Amount: ₹{stats['total_amount']}
"""
        
        if is_escrower_user:
            keyboard = [
                [KeyboardButton("📊 My Stats")],
                [KeyboardButton("📋 My Dealing History")],
                [KeyboardButton("⏳ My Pending Deals")],
                [KeyboardButton("🔍 View Past Deal Info")],
                [KeyboardButton("🔐 Admin Panel")]
            ]
        else:
            keyboard = [
                [KeyboardButton("📊 My Stats")],
                [KeyboardButton("📋 My Dealing History")],
                [KeyboardButton("⏳ My Pending Deals")],
                [KeyboardButton("🔍 View Past Deal Info")]
            ]
        
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
        
        await update.message.reply_text(text, reply_markup=reply_markup)
        
    except Exception as e:
        print(f"Dashboard error: {e}")

async def handle_dashboard_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return
        
        text = update.message.text
        user = update.effective_user
        username = user.username or "User"
        user_id = user.id
        
        if not text:
            return
        
        stats = user_stats.get(username, {"total_deals": 0, "completed": 0, "pending": 0, "total_amount": 0, "deals": []})
        
        if text == "📊 My Stats":
            reply = f"""
📊 YOUR STATS
━━━━━━━━━━━━━━━━━━━━━

👤 @{username}

• Total Deals: {stats['total_deals']}
• Completed: {stats['completed']}
• Pending: {stats['pending']}
• Total Amount: ₹{stats['total_amount']}

━━━━━━━━━━━━━━━━━━━━━
Click /start to go back
"""
            await update.message.reply_text(reply, reply_markup=get_dashboard_keyboard())
            
        elif text == "📋 My Dealing History":
            if not stats['deals']:
                reply = "📋 You have no deal history yet."
                await update.message.reply_text(reply, reply_markup=get_dashboard_keyboard())
                return
            
            reply = "📋 YOUR DEALING HISTORY\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            for deal in stats['deals'][-10:]:
                reply += f"🆔 #{deal['deal_id']} | ₹{deal['amount']} | {deal['role']} | {deal['status']}\n"
            
            reply += "\n━━━━━━━━━━━━━━━━━━━━━\nClick /start to go back"
            await update.message.reply_text(reply, reply_markup=get_dashboard_keyboard())
            
        elif text == "⏳ My Pending Deals":
            pending_deals = [d for d in stats['deals'] if d['status'] == "pending"]
            if not pending_deals:
                reply = "⏳ You have no pending deals."
                await update.message.reply_text(reply, reply_markup=get_dashboard_keyboard())
                return
            
            reply = "⏳ YOUR PENDING DEALS\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            for deal in pending_deals:
                reply += f"🆔 #{deal['deal_id']} | ₹{deal['amount']} | {deal['role']}\n"
            
            reply += "\n━━━━━━━━━━━━━━━━━━━━━\nClick /start to go back"
            await update.message.reply_text(reply, reply_markup=get_dashboard_keyboard())
            
        elif text == "🔍 View Past Deal Info":
            if not stats['deals']:
                reply = "🔍 You have no past deals."
                await update.message.reply_text(reply, reply_markup=get_dashboard_keyboard())
                return
            
            reply = "🔍 PAST DEALS\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            reply += "Type /status DEAL_ID to view details\n"
            reply += "Example: /status 401\n\n"
            
            for deal in stats['deals'][-5:]:
                reply += f"🆔 #{deal['deal_id']} | ₹{deal['amount']}\n"
            
            reply += "\n━━━━━━━━━━━━━━━━━━━━━\nClick /start to go back"
            await update.message.reply_text(reply, reply_markup=get_dashboard_keyboard())
            
        elif text == "🔐 Admin Panel":
            if not is_escrower(user_id):
                await update.message.reply_text("❌ Only promoted escrowers can access Admin Panel!", reply_markup=get_dashboard_keyboard())
                return
            
            await admin_panel(update, context)
            
    except Exception as e:
        print(f"Dashboard text error: {e}")

# ============ ADMIN PANEL ============

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        username = update.effective_user.username
        
        if not is_escrower(user_id):
            await update.message.reply_text("❌ Only promoted escrowers can access Admin Panel!")
            return
        
        if not username:
            await update.message.reply_text("❌ You need a username!")
            return
        
        escrow_data = get_escrower_data(username)
        upi = escrow_data['upi'] if escrow_data else 'Not set'
        has_gmail = bool(escrow_data and escrow_data.get('gmail_key'))
        manual = escrow_data.get('manual', False) if escrow_data else False
        has_qr = bool(escrow_data and escrow_data.get('qr_photo')) if escrow_data else False
        
        my_deals = []
        for deal_id, escrow in escrows.items():
            if escrow["escrower_username"].lower() == username.lower():
                my_deals.append((deal_id, escrow))
        
        text = f"""
🔐 ADMIN PANEL
━━━━━━━━━━━━━━━━━━━━━

👤 @{username}
💳 UPI: {upi}
📱 Mode: {'🔐 Auto (Gmail)' if has_gmail else '📱 Manual (QR)'}
{'✅ QR Uploaded' if has_qr else '❌ No QR Uploaded'}

📊 YOUR DEALS:
• Total: {len(my_deals)}
• Pending: {len([d for d in my_deals if d[1]['status'] in ['awaiting_payment', 'payment_received']])}
• Completed: {len([d for d in my_deals if d[1]['status'] == 'released'])}

━━━━━━━━━━━━━━━━━━━━━
"""
        
        keyboard = []
        
        if not escrow_data:
            keyboard.append([InlineKeyboardButton("📝 Setup UPI & QR", callback_data="admin_setup")])
        elif not has_qr and manual:
            keyboard.append([InlineKeyboardButton("📤 Upload QR Code", callback_data="admin_upload_qr")])
        elif not has_gmail and not manual:
            keyboard.append([InlineKeyboardButton("📤 Upload QR Code (Manual)", callback_data="admin_upload_qr")])
        
        if my_deals:
            keyboard.append([InlineKeyboardButton("📋 My Deals", callback_data="admin_my_deals")])
        
        keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh")])
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        
        await update.message.reply_text(text, reply_markup=reply_markup)
        
    except Exception as e:
        print(f"Admin panel error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ============ ADMIN PANEL BUTTON HANDLERS ============

async def handle_admin_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if not is_escrower(user_id):
            await query.edit_message_text("❌ Only escrowers can setup!")
            return
        
        keyboard = [
            [InlineKeyboardButton("🔐 With Gmail Key (Auto)", callback_data="setup_gmail")],
            [InlineKeyboardButton("📱 Without Gmail Key (Manual QR)", callback_data="setup_manual")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"""
🔐 ESCROWER SETUP
━━━━━━━━━━━━━━━━━━━━━

Choose how you want to receive payments:

🔐 With Gmail Key
→ Auto verification of payments
→ QR generated automatically

📱 Without Gmail Key
→ Manual verification via /received
→ Upload your own QR code

Select an option:
""",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        print(f"Admin setup error: {e}")

async def handle_admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text("❌ Setup cancelled.")
    except Exception as e:
        print(f"Admin cancel error: {e}")

async def handle_setup_gmail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if not is_escrower(user_id):
            await query.edit_message_text("❌ Only escrowers can setup!")
            return
        
        await query.edit_message_text(
            f"🔐 Please provide your UPI ID.\n"
            f"Example: username@fam\n\n"
            f"Type your UPI ID:"
        )
        
        context.user_data['setup_mode'] = 'gmail'
        return UPI_ID
        
    except Exception as e:
        print(f"Setup Gmail error: {e}")

async def handle_setup_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if not is_escrower(user_id):
            await query.edit_message_text("❌ Only escrowers can setup!")
            return
        
        await query.edit_message_text(
            f"📱 Please provide your UPI ID.\n"
            f"Example: username@fam\n\n"
            f"Type your UPI ID:"
        )
        
        context.user_data['setup_mode'] = 'manual'
        return UPI_ID
        
    except Exception as e:
        print(f"Setup Manual error: {e}")

async def handle_admin_upload_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if not is_escrower(user_id):
            await query.edit_message_text("❌ Only escrowers can upload QR!")
            return
        
        await query.edit_message_text(
            f"📤 Please upload your QR code image.\n\n"
            f"Send a photo of your UPI QR code:"
        )
        
        context.user_data['uploading_qr'] = True
        return UPLOAD_QR
        
    except Exception as e:
        print(f"Admin upload QR error: {e}")

async def handle_admin_my_deals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        username = update.effective_user.username
        
        if not is_escrower(user_id):
            await query.edit_message_text("❌ Only escrowers can view!")
            return
        
        my_deals = []
        for deal_id, escrow in escrows.items():
            if escrow["escrower_username"].lower() == username.lower():
                my_deals.append((deal_id, escrow))
        
        if not my_deals:
            await query.edit_message_text("📭 You have no deals yet.")
            return
        
        text = "📋 YOUR DEALS\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for deal_id, escrow in my_deals:
            text += f"🆔 #{deal_id} | ₹{escrow['amount']} | {escrow['status']}\n"
            text += f"   Buyer: @{escrow['buyer']} | Seller: @{escrow['seller']}\n\n"
        
        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
        
    except Exception as e:
        print(f"Admin my deals error: {e}")

async def handle_admin_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        await admin_panel(update, context)
        
    except Exception as e:
        print(f"Admin refresh error: {e}")

# ============ MAIN ADMIN PANEL BUTTON HANDLER ============

async def handle_admin_panel_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "admin_setup":
            await handle_admin_setup(update, context)
        elif data == "admin_cancel":
            await handle_admin_cancel(update, context)
        elif data == "setup_gmail":
            await handle_setup_gmail(update, context)
        elif data == "setup_manual":
            await handle_setup_manual(update, context)
        elif data == "admin_upload_qr":
            await handle_admin_upload_qr(update, context)
        elif data == "admin_my_deals":
            await handle_admin_my_deals(update, context)
        elif data == "admin_refresh":
            await handle_admin_refresh(update, context)
        else:
            print(f"Unknown admin button: {data}")
            
    except Exception as e:
        print(f"Admin panel button error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")

# ============ CONVERSATION: ADD UPI ============

async def get_upi_from_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        upi = update.message.text.strip()
        username = update.effective_user.username
        
        if not upi or '@' not in upi:
            await update.message.reply_text("❌ Invalid UPI! Please enter a valid UPI ID (e.g., name@fam)")
            return UPI_ID
        
        context.user_data['upi'] = upi
        
        mode = context.user_data.get('setup_mode', 'gmail')
        
        if mode == 'gmail':
            await update.message.reply_text(
                f"✅ UPI set to: {upi}\n\n"
                f"Now please provide your Gmail Key.\n"
                f"(Get it from: {API_BASE_URL})\n\n"
                f"Type your Gmail Key:"
            )
            return GMAIL_KEY_STATE
        else:
            await update.message.reply_text(
                f"✅ UPI set to: {upi}\n\n"
                f"Now please upload your UPI QR code.\n"
                f"Send a photo of your QR code:"
            )
            context.user_data['uploading_qr'] = True
            return UPLOAD_QR
        
    except Exception as e:
        print(f"Get UPI error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return ConversationHandler.END

async def get_gmail_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        gmail_key = update.message.text.strip()
        username = update.effective_user.username
        
        if not gmail_key or len(gmail_key) < 4:
            await update.message.reply_text("❌ Invalid Gmail Key! Please enter a valid key.")
            return GMAIL_KEY_STATE
        
        upi = context.user_data.get('upi')
        
        if not upi:
            await update.message.reply_text("❌ UPI not found! Please start again.")
            return ConversationHandler.END
        
        user_upi[username.lower()] = {
            "upi": upi,
            "gmail_key": gmail_key,
            "manual": False,
            "qr_photo": None
        }
        
        await update.message.reply_text(
            f"✅ SUCCESS! Your details have been saved.\n\n"
            f"👤 Username: @{username}\n"
            f"💳 UPI: {upi}\n"
            f"🔑 Gmail Key: {gmail_key[:4]}...\n"
            f"📱 Mode: 🔐 Auto (Gmail)\n\n"
            f"You can now escrow deals!\n"
            f"Type /escrow @buyer @seller amount item in any group."
        )
        
        owner_text = f"""
🔔 NEW ESCROWER ADDED!

👤 Username: @{username}
💳 UPI: {upi}
🔑 Gmail Key: {gmail_key[:4]}...
📱 Mode: Auto (Gmail)
"""
        await context.bot.send_message(chat_id=OWNER_USER_ID, text=owner_text)
        
        return ConversationHandler.END
        
    except Exception as e:
        print(f"Get Gmail error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return ConversationHandler.END

async def get_manual_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        username = update.effective_user.username
        
        if not username:
            await update.message.reply_text("❌ You need a username!")
            return ConversationHandler.END
        
        if not update.message.photo:
            await update.message.reply_text("❌ Please send a photo of your QR code.")
            return UPLOAD_QR
        
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        upi = context.user_data.get('upi')
        
        if not upi:
            await update.message.reply_text("❌ UPI not found! Please start again.")
            return ConversationHandler.END
        
        user_upi[username.lower()] = {
            "upi": upi,
            "gmail_key": None,
            "manual": True,
            "qr_photo": file_id
        }
        
        await update.message.reply_text(
            f"✅ SUCCESS! Your details have been saved.\n\n"
            f"👤 Username: @{username}\n"
            f"💳 UPI: {upi}\n"
            f"📱 Mode: 📱 Manual (QR)\n"
            f"✅ QR Code uploaded\n\n"
            f"You can now escrow deals!\n"
            f"Type /escrow @buyer @seller amount item in any group.\n\n"
            f"⚠️ For manual mode, you need to use /received and /rlsdone manually."
        )
        
        owner_text = f"""
🔔 NEW ESCROWER ADDED!

👤 Username: @{username}
💳 UPI: {upi}
📱 Mode: Manual (QR)
✅ QR Uploaded
"""
        await context.bot.send_message(chat_id=OWNER_USER_ID, text=owner_text)
        
        return ConversationHandler.END
        
    except Exception as e:
        print(f"Get QR error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return ConversationHandler.END

async def cancel_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Setup cancelled.")
    return ConversationHandler.END

# ============ COMMANDS ============

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        chat_type = update.effective_chat.type
        
        if chat_type == "private":
            await show_dashboard(update, context)
            return
        
        return
        
    except Exception as e:
        print(f"Start error: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        username = update.effective_user.username or "User"
        chat_type = update.effective_chat.type
        
        if chat_type == "private":
            await show_dashboard(update, context)
            return
        
        is_owner_user = is_owner(user_id)
        is_escrower_user = is_escrower(user_id)
        
        stats = user_stats.get(username, {"total_deals": 0, "completed": 0, "pending": 0, "total_amount": 0})
        
        text = f"""
📚 HELP & INFO
━━━━━━━━━━━━━━━━━━━━━

👤 @{username}
🆔 {user_id}

📊 YOUR STATS:
Total Deals: {stats['total_deals']}
Completed: {stats['completed']}
Pending: {stats['pending']}
Total Amount: ₹{stats['total_amount']}

📌 COMMANDS:
/escrow @buyer @seller amount item
/agree
/release 001
/refund 001
/received 001 (Escrower only)
/status 001
/help

💰 FEES: {ESCROW_FEE}%
"""
        
        if is_escrower_user:
            text += f"""
━━━━━━━━━━━━━━━━━━━━━
🔐 ESCROWER ACCESS:
Use /start in DM for Admin Panel
"""
        
        if is_owner_user:
            text += f"""
━━━━━━━━━━━━━━━━━━━━━
👑 OWNER COMMANDS:
/owner_panel
/promote @user
/demote @user
/fee 5
"""
        
        text += f"""
━━━━━━━━━━━━━━━━━━━━━
🤖 @{OWNER_USERNAME}
"""
        await update.message.reply_text(text)
    except Exception as e:
        print(f"Help error: {e}")

# ============ PROMOTE/DEMOTE ============

async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        if not is_owner(user_id):
            await update.message.reply_text("❌ Only owner can promote!")
            return
        
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /promote @username")
            return
        
        username = args[0].replace('@', '')
        
        try:
            chat = update.effective_chat
            members = await chat.get_members()
            for member in members:
                if member.user.username and normalize_username(member.user.username) == normalize_username(username):
                    promoted_escrowers.add(member.user.id)
                    escrower_username_map[member.user.id] = username
                    await update.message.reply_text(
                        f"✅ @{username} is now an escrower!\n\n"
                        f"Tell them to use /start in DM and click 'Admin Panel' to setup their UPI."
                    )
                    return
            
            promoted_escrowers.add(username)
            escrower_username_map[username] = username
            await update.message.reply_text(
                f"✅ @{username} is now an escrower!\n\n"
                f"Tell them to use /start in DM and click 'Admin Panel' to setup their UPI."
            )
            
        except Exception as e:
            promoted_escrowers.add(username)
            escrower_username_map[username] = username
            await update.message.reply_text(
                f"✅ @{username} is now an escrower!\n\n"
                f"Tell them to use /start in DM and click 'Admin Panel' to setup their UPI."
            )
            
    except Exception as e:
        print(f"Promote error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        if not is_owner(user_id):
            await update.message.reply_text("❌ Only owner can demote!")
            return
        
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /demote @username")
            return
        
        username = args[0].replace('@', '')
        
        try:
            chat = update.effective_chat
            members = await chat.get_members()
            for member in members:
                if member.user.username and normalize_username(member.user.username) == normalize_username(username):
                    if member.user.id in promoted_escrowers:
                        promoted_escrowers.remove(member.user.id)
                        if member.user.id in escrower_username_map:
                            del escrower_username_map[member.user.id]
                        if username.lower() in user_upi:
                            del user_upi[username.lower()]
                        await update.message.reply_text(f"✅ @{username} is no longer an escrower!")
                        return
            
            if username in promoted_escrowers:
                promoted_escrowers.remove(username)
                if username in escrower_username_map:
                    del escrower_username_map[username]
                if username.lower() in user_upi:
                    del user_upi[username.lower()]
                await update.message.reply_text(f"✅ @{username} is no longer an escrower!")
                return
            
            await update.message.reply_text(f"❌ @{username} is not an escrower!")
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")
            
    except Exception as e:
        print(f"Demote error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def fee_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ESCROW_FEE
    
    try:
        user_id = update.effective_user.id
        
        if not is_owner(user_id):
            await update.message.reply_text("❌ Only owner!")
            return
        
        args = context.args
        if not args:
            await update.message.reply_text(f"Current fee: {ESCROW_FEE}%\nUsage: /fee 5")
            return
        
        try:
            new_fee = float(args[0])
            if new_fee < 0 or new_fee > 100:
                await update.message.reply_text("❌ Fee must be 0-100!")
                return
            
            ESCROW_FEE = new_fee
            await update.message.reply_text(f"✅ Fee set to {ESCROW_FEE}%")
        except:
            await update.message.reply_text("❌ Invalid fee!")
            
    except Exception as e:
        print(f"Fee error: {e}")

# ============ RECEIVED COMMAND ============

async def received_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        
        if not args:
            await update.message.reply_text("Usage: /received 001")
            return
        
        deal_id = args[0]
        
        if deal_id not in escrows:
            await update.message.reply_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        
        if escrow["escrower_id"] != user_id and not is_owner(user_id):
            await update.message.reply_text("❌ Only the escrower can use /received!")
            return
        
        if escrow["status"] != "awaiting_payment":
            await update.message.reply_text(f"❌ Cannot confirm. Status: {escrow['status']}")
            return
        
        if escrow.get("order_id") in verified_payments:
            await update.message.reply_text("✅ Payment already verified!")
            return
        
        verified_payments.add(escrow["order_id"])
        escrow["status"] = "payment_received"
        
        # Send notification
        final_text = f"""
✅ PAYMENT VERIFIED! #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
🔐 Escrower: @{escrow['escrower_username']}

⏳ Escrower will release soon.
"""
        
        msg = await context.bot.send_message(
            chat_id=escrow["group_id"],
            text=final_text
        )
        
        await pin_message(context, escrow["group_id"], msg.message_id)
        pinned_messages[escrow["group_id"]] = msg.message_id
        
        await context.bot.send_message(
            chat_id=escrow["escrower_id"],
            text=f"""
🔔 PAYMENT RECEIVED! #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}

Type /release {deal_id} to start release
"""
        )
        
        await update.message.reply_text(
            f"✅ Payment manually confirmed!\n\nDeal: #{deal_id}\nAmount: ₹{escrow['amount']}\n\n⏳ Now type /release {deal_id} to start release"
        )
        
    except Exception as e:
        print(f"Received error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ============ ESCROW COMMAND ============

async def escrow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_chat.type not in ["group", "supergroup"]:
            await update.message.reply_text("❌ Use in a group!")
            return
        
        args = context.args
        if len(args) < 4:
            await update.message.reply_text(
                "Usage: /escrow @buyer @seller amount item\n"
                "Example: /escrow @john @jane 500 iPhone"
            )
            return
        
        buyer = args[0].replace('@', '')
        seller = args[1].replace('@', '')
        amount = args[2]
        item = " ".join(args[3:])
        creator = update.effective_user.username or update.effective_user.first_name
        user_id = update.effective_user.id
        
        if not buyer or not seller:
            await update.message.reply_text("❌ Both need usernames!")
            return
        
        if buyer.lower() == seller.lower():
            await update.message.reply_text("❌ Buyer and Seller same person!")
            return
        
        try:
            amount_float = float(amount)
            if amount_float <= 0:
                raise ValueError
        except:
            await update.message.reply_text("❌ Invalid amount!")
            return
        
        escrowers = get_all_escrowers()
        if not escrowers:
            await update.message.reply_text("❌ No escrowers available! Contact owner.")
            return
        
        deal_id = generate_deal_id()
        
        keyboard = []
        for escrower in escrowers:
            keyboard.append([
                InlineKeyboardButton(
                    f"@{escrower['username']} {'🔐' if escrower['has_gmail'] else '📱'}",
                    callback_data=f"select_escrow_{deal_id}_{escrower['username']}"
                )
            ])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_deal_{deal_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        pending_escrow_selection[deal_id] = {
            "buyer": buyer,
            "seller": seller,
            "amount": amount,
            "item": item,
            "creator": creator,
            "creator_id": user_id,
            "group_id": update.effective_chat.id
        }
        
        await update.message.reply_text(
            f"""
🔐 SELECT ESCROWER
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
👤 Buyer: @{buyer}
👤 Seller: @{seller}
💰 Amount: ₹{amount}
📦 Item: {item}

Please select an escrower:
(Only buyer or seller can select)
""",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        print(f"Escrow error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ============ ESCROWER SELECTION HANDLER ============

async def handle_escrow_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data.split("_")
        action = data[0]
        deal_id = data[2]
        escrower_username = data[3]
        
        if action == "cancel":
            if deal_id in pending_escrow_selection:
                del pending_escrow_selection[deal_id]
            await query.edit_message_text("❌ Deal cancelled.")
            return
        
        if deal_id not in pending_escrow_selection:
            await query.edit_message_text("❌ Deal expired!")
            return
        
        pending = pending_escrow_selection[deal_id]
        user = update.effective_user
        username = user.username
        
        if not username:
            await query.answer("❌ You need a username!", show_alert=True)
            return
        
        if username != pending['buyer'] and username != pending['seller']:
            await query.answer("❌ Only buyer or seller can select escrower!", show_alert=True)
            return
        
        escrower_data = get_escrower_data(escrower_username)
        
        if not escrower_data:
            await query.edit_message_text(f"❌ Escrower @{escrower_username} not set up!")
            return
        
        # Create the deal
        new_deal_id = generate_deal_id()
        
        escrows[new_deal_id] = {
            "buyer": pending['buyer'],
            "seller": pending['seller'],
            "amount": pending['amount'],
            "item": pending['item'],
            "status": "awaiting_agreement",
            "escrower_id": None,
            "escrower_username": escrower_username,
            "group_id": pending['group_id'],
            "timestamp": time.time(),
            "creator": pending['creator'],
            "creator_id": pending['creator_id']
        }
        
        escrower_user_id = None
        for uid, uname in escrower_username_map.items():
            if uname.lower() == escrower_username.lower():
                escrower_user_id = uid
                break
        
        if escrower_user_id:
            escrows[new_deal_id]["escrower_id"] = escrower_user_id
        
        agreements[new_deal_id] = {
            "buyer_agreed": False,
            "seller_agreed": False
        }
        
        release_agreements[new_deal_id] = {
            "buyer_agreed": False,
            "seller_agreed": False
        }
        
        refund_agreements[new_deal_id] = {
            "buyer_agreed": False,
            "seller_agreed": False
        }
        
        user_active_deal[normalize_username(pending['buyer'])] = new_deal_id
        user_active_deal[normalize_username(pending['seller'])] = new_deal_id
        
        del pending_escrow_selection[deal_id]
        
        form_text = format_agreement(new_deal_id, escrows[new_deal_id], 0)
        
        keyboard = [
            [InlineKeyboardButton("📋 View Form", callback_data=f"view_form_{new_deal_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg = await context.bot.send_message(
            chat_id=pending['group_id'],
            text=form_text,
            reply_markup=reply_markup
        )
        
        escrows[new_deal_id]["message_id"] = msg.message_id
        await pin_message(context, pending['group_id'], msg.message_id)
        pinned_messages[pending['group_id']] = msg.message_id
        
        await query.edit_message_text(
            f"✅ Escrower @{escrower_username} selected!\n\n"
            f"🆔 Deal: #{new_deal_id}\n"
            f"Agreement message posted in the group."
        )
        
        if escrower_user_id:
            await context.bot.send_message(
                chat_id=escrower_user_id,
                text=f"""
🔔 NEW DEAL ASSIGNED TO YOU!

━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{new_deal_id}
💰 Amount: ₹{pending['amount']}
📦 Item: {pending['item']}
👤 Buyer: @{pending['buyer']}
👤 Seller: @{pending['seller']}

⚠️ Waiting for both parties to agree.
You will be notified when payment is received.
"""
            )
        
    except Exception as e:
        print(f"Escrow selection error: {e}")
        await query.edit_message_text(f"❌ Error: {str(e)}")

# ============ CANCEL DEAL HANDLER ============

async def handle_cancel_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        data = query.data.split("_")
        if len(data) >= 3:
            deal_id = data[2]
        
            if deal_id in pending_escrow_selection:
                del pending_escrow_selection[deal_id]
        
        await query.edit_message_text("❌ Deal cancelled.")
        
    except Exception as e:
        print(f"Cancel deal error: {e}")

# ============ AGREE COMMAND ============

async def agree_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        username = user.username
        chat_id = update.effective_chat.id
        
        if not username:
            await update.message.reply_text("❌ You need a username!")
            return
        
        deal_id = find_deal_by_username(username, chat_id, "awaiting_agreement")
        
        if not deal_id:
            await update.message.reply_text("❌ No pending agreement!")
            return
        
        escrow = escrows[deal_id]
        username_lower = normalize_username(username)
        buyer_lower = normalize_username(escrow["buyer"])
        seller_lower = normalize_username(escrow["seller"])
        
        if username_lower == buyer_lower:
            if agreements[deal_id]["buyer_agreed"]:
                await update.message.reply_text("✅ Already agreed!")
                return
            agreements[deal_id]["buyer_agreed"] = True
            await update.message.reply_text(f"✅ @{username} agreed as BUYER!")
        elif username_lower == seller_lower:
            if agreements[deal_id]["seller_agreed"]:
                await update.message.reply_text("✅ Already agreed!")
                return
            agreements[deal_id]["seller_agreed"] = True
            await update.message.reply_text(f"✅ @{username} agreed as SELLER!")
        else:
            await update.message.reply_text("❌ Not part of this deal!")
            return
        
        agreed_count = sum(1 for v in agreements[deal_id].values() if v)
        form_text = format_agreement(deal_id, escrow, agreed_count)
        
        keyboard = [
            [InlineKeyboardButton("📋 View Form", callback_data=f"view_form_{deal_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=escrow["message_id"],
                text=form_text,
                reply_markup=reply_markup
            )
        except:
            pass
        
        if agreements[deal_id]["buyer_agreed"] and agreements[deal_id]["seller_agreed"]:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🎉 Both agreed! Creating deal..."
            )
            await create_deal_after_agreement(context, deal_id)
            
    except Exception as e:
        print(f"Agree error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ============ CREATE DEAL AFTER AGREEMENT ============

async def create_deal_after_agreement(context, deal_id):
    try:
        escrow = escrows[deal_id]
        escrower_data = get_escrower_data(escrow['escrower_username'])
        
        if not escrower_data:
            await context.bot.send_message(
                chat_id=escrow["group_id"],
                text=f"❌ Escrower @{escrow['escrower_username']} has no setup!"
            )
            return
        
        if escrower_data.get('manual', False) and escrower_data.get('qr_photo'):
            await send_manual_qr(context, deal_id, escrower_data)
        elif escrower_data.get('gmail_key'):
            await send_auto_qr(context, deal_id, escrower_data)
        else:
            await context.bot.send_message(
                chat_id=escrow["group_id"],
                text=f"❌ Escrower @{escrow['escrower_username']} has no QR or Gmail Key!"
            )
            return
        
    except Exception as e:
        print(f"Create deal error: {e}")

async def send_auto_qr(context, deal_id, escrower_data):
    try:
        escrow = escrows[deal_id]
        
        qr_data = await api.generate_qr(escrow["amount"], escrower_data['gmail_key'])
        
        if not qr_data:
            await context.bot.send_message(
                chat_id=escrow["group_id"],
                text="❌ QR failed. Try again."
            )
            return
        
        order_id = qr_data["order_id"]
        qr_image_url = qr_data["qr_code"]["image_url"]
        
        escrow["order_id"] = order_id
        escrow["status"] = "awaiting_payment"
        
        pending_payments[order_id] = {
            "escrow_id": deal_id,
            "amount": escrow["amount"],
            "timestamp": time.time()
        }
        
        if escrow["group_id"] in pinned_messages:
            await unpin_message(context, escrow["group_id"], pinned_messages[escrow["group_id"]])
        
        qr_text = f"""
📱 PAYMENT QR CODE
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🔐 Escrower: @{escrow['escrower_username']}
💳 Pay to: {escrower_data['upi']}

Scan QR to pay ₹{escrow['amount']}
"""
        
        keyboard_qr = [
            [InlineKeyboardButton("✅ I've Paid", callback_data=f"paid_{deal_id}")],
            [InlineKeyboardButton("🔄 Check Status", callback_data=f"check_{deal_id}")]
        ]
        reply_markup_qr = InlineKeyboardMarkup(keyboard_qr)
        
        qr_msg = await context.bot.send_photo(
            chat_id=escrow["group_id"],
            photo=qr_image_url,
            caption=qr_text,
            reply_markup=reply_markup_qr
        )
        
        escrow["qr_message_id"] = qr_msg.message_id
        
        form_text = format_deal_form(deal_id, escrow)
        
        keyboard_form = [
            [InlineKeyboardButton("📋 View Form", callback_data=f"view_form_{deal_id}")]
        ]
        reply_markup_form = InlineKeyboardMarkup(keyboard_form)
        
        form_msg = await context.bot.send_message(
            chat_id=escrow["group_id"],
            text=form_text,
            reply_markup=reply_markup_form
        )
        
        escrow["form_message_id"] = form_msg.message_id
        deal_form_messages[deal_id] = form_msg.message_id
        
        await pin_message(context, escrow["group_id"], form_msg.message_id)
        pinned_messages[escrow["group_id"]] = form_msg.message_id
        
        if normalize_username(escrow["buyer"]) in user_active_deal:
            del user_active_deal[normalize_username(escrow["buyer"])]
        if normalize_username(escrow["seller"]) in user_active_deal:
            del user_active_deal[normalize_username(escrow["seller"])]
        
    except Exception as e:
        print(f"Send auto QR error: {e}")

async def send_manual_qr(context, deal_id, escrower_data):
    try:
        escrow = escrows[deal_id]
        
        order_id = f"MANUAL-{deal_id}-{int(time.time())}"
        
        escrow["order_id"] = order_id
        escrow["status"] = "awaiting_payment"
        
        pending_payments[order_id] = {
            "escrow_id": deal_id,
            "amount": escrow["amount"],
            "timestamp": time.time()
        }
        
        if escrow["group_id"] in pinned_messages:
            await unpin_message(context, escrow["group_id"], pinned_messages[escrow["group_id"]])
        
        qr_text = f"""
📱 PAYMENT QR CODE (Manual)
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🔐 Escrower: @{escrow['escrower_username']}
💳 Pay to: {escrower_data['upi']}

Scan QR to pay ₹{escrow['amount']}
⚠️ Manual Mode: Escrower will confirm payment manually.
"""
        
        keyboard_qr = [
            [InlineKeyboardButton("✅ I've Paid", callback_data=f"paid_{deal_id}")],
            [InlineKeyboardButton("🔄 Check Status", callback_data=f"check_{deal_id}")]
        ]
        reply_markup_qr = InlineKeyboardMarkup(keyboard_qr)
        
        qr_msg = await context.bot.send_photo(
            chat_id=escrow["group_id"],
            photo=escrower_data['qr_photo'],
            caption=qr_text,
            reply_markup=reply_markup_qr
        )
        
        escrow["qr_message_id"] = qr_msg.message_id
        
        form_text = format_deal_form(deal_id, escrow)
        
        keyboard_form = [
            [InlineKeyboardButton("📋 View Form", callback_data=f"view_form_{deal_id}")]
        ]
        reply_markup_form = InlineKeyboardMarkup(keyboard_form)
        
        form_msg = await context.bot.send_message(
            chat_id=escrow["group_id"],
            text=form_text,
            reply_markup=reply_markup_form
        )
        
        escrow["form_message_id"] = form_msg.message_id
        deal_form_messages[deal_id] = form_msg.message_id
        
        await pin_message(context, escrow["group_id"], form_msg.message_id)
        pinned_messages[escrow["group_id"]] = form_msg.message_id
        
        if normalize_username(escrow["buyer"]) in user_active_deal:
            del user_active_deal[normalize_username(escrow["buyer"])]
        if normalize_username(escrow["seller"]) in user_active_deal:
            del user_active_deal[normalize_username(escrow["seller"])]
        
    except Exception as e:
        print(f"Send manual QR error: {e}")

# ============ PAYMENT HANDLERS ============

async def handle_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        deal_id = query.data.split("_")[1]
        
        if deal_id not in escrows:
            await query.edit_message_text("❌ Deal expired!")
            return
        
        escrow = escrows[deal_id]
        
        user = update.effective_user
        username = user.username
        
        if not username:
            await query.answer("❌ Need username!", show_alert=True)
            return
        
        username_lower = normalize_username(username)
        buyer_lower = normalize_username(escrow["buyer"])
        
        if username_lower != buyer_lower:
            await query.answer("❌ Only buyer can tap!", show_alert=True)
            return
        
        if escrow["status"] != "awaiting_payment":
            await query.answer(f"Status: {escrow['status']}", show_alert=True)
            return
        
        if escrow.get("order_id") in verified_payments:
            await query.answer("✅ Already verified!", show_alert=True)
            return
        
        escrower_data = get_escrower_data(escrow['escrower_username'])
        
        if escrower_data and escrower_data.get('manual', False):
            await query.edit_message_caption(
                caption=f"""
📱 PAYMENT QR CODE (Manual)
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🔐 Escrower: @{escrow['escrower_username']}

⏳ Payment notification sent to escrower.
They will confirm manually.
"""
            )
            
            await context.bot.send_message(
                chat_id=escrow["escrower_id"],
                text=f"""
🔔 PAYMENT CONFIRMATION REQUEST!

━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}

⚠️ Buyer says they have paid.
Check your UPI and confirm with /received {deal_id}
"""
            )
            
            await query.answer("📱 Payment notification sent to escrower!", show_alert=True)
            return
        
        if not escrower_data or not escrower_data.get('gmail_key'):
            await query.answer("❌ Escrower has no Gmail Key!", show_alert=True)
            return
        
        verification_data = await api.verify_payment(escrow["order_id"], escrower_data['gmail_key'])
        
        if verification_data and verification_data.get("status") == "paid":
            verified_payments.add(escrow["order_id"])
            escrow["status"] = "payment_received"
            escrow["txn_id"] = verification_data.get("txn_id")
            escrow["payer_name"] = verification_data.get("payer_name")
            
            # Send notification
            final_text = f"""
✅ PAYMENT VERIFIED! #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
🔐 Escrower: @{escrow['escrower_username']}
🆔 TXN: {verification_data.get('txn_id', 'N/A')}

⏳ Escrower will release soon.
"""
            
            # Delete QR message
            if escrow.get("qr_message_id"):
                try:
                    await context.bot.delete_message(
                        chat_id=escrow["group_id"],
                        message_id=escrow["qr_message_id"]
                    )
                except:
                    pass
            
            msg = await context.bot.send_message(
                chat_id=escrow["group_id"],
                text=final_text
            )
            
            await pin_message(context, escrow["group_id"], msg.message_id)
            pinned_messages[escrow["group_id"]] = msg.message_id
            
            await context.bot.send_message(
                chat_id=escrow["escrower_id"],
                text=f"""
🔔 PAYMENT RECEIVED! #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
🆔 TXN: {verification_data.get('txn_id', 'N/A')}

Type /release {deal_id} to start release
"""
            )
            
            await query.edit_message_caption(
                caption=f"""
✅ PAYMENT VERIFIED!

━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🆔 TXN: {verification_data.get('txn_id', 'N/A')}

⏳ Escrower will release soon.
"""
            )
        else:
            await query.answer("⏳ Payment not detected. Try again.", show_alert=True)
            
            keyboard = [
                [InlineKeyboardButton("✅ I've Paid", callback_data=f"paid_{deal_id}")],
                [InlineKeyboardButton("🔄 Check Status", callback_data=f"check_{deal_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_caption(
                caption=f"""
📱 PAYMENT QR CODE
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🔐 Escrower: @{escrow['escrower_username']}

⏳ Payment not detected yet.
Please wait and try again.
""",
                reply_markup=reply_markup
            )
            
    except Exception as e:
        print(f"Paid error: {e}")

async def handle_check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        deal_id = query.data.split("_")[1]
        
        if deal_id not in escrows:
            await query.edit_message_text("❌ Deal expired!")
            return
        
        escrow = escrows[deal_id]
        
        if escrow["status"] != "awaiting_payment":
            await query.edit_message_text(f"Status: {escrow['status']}")
            return
        
        escrower_data = get_escrower_data(escrow['escrower_username'])
        
        if escrower_data and escrower_data.get('manual', False):
            await query.answer("📱 Manual mode - check with escrower", show_alert=True)
            await query.edit_message_caption(
                caption=f"""
📱 PAYMENT QR CODE (Manual)
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🔐 Escrower: @{escrow['escrower_username']}

⏳ Manual mode. Escrower will confirm.
"""
            )
            return
        
        if not escrower_data or not escrower_data.get('gmail_key'):
            await query.edit_message_text("❌ Escrower has no Gmail Key!")
            return
        
        verification_data = await api.verify_payment(escrow["order_id"], escrower_data['gmail_key'])
        
        if verification_data and verification_data.get("status") == "paid":
            verified_payments.add(escrow["order_id"])
            escrow["status"] = "payment_received"
            escrow["txn_id"] = verification_data.get("txn_id")
            escrow["payer_name"] = verification_data.get("payer_name")
            
            # Send notification
            final_text = f"""
✅ PAYMENT VERIFIED! #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
🔐 Escrower: @{escrow['escrower_username']}
🆔 TXN: {verification_data.get('txn_id', 'N/A')}

⏳ Escrower will release soon.
"""
            
            # Delete QR message
            if escrow.get("qr_message_id"):
                try:
                    await context.bot.delete_message(
                        chat_id=escrow["group_id"],
                        message_id=escrow["qr_message_id"]
                    )
                except:
                    pass
            
            msg = await context.bot.send_message(
                chat_id=escrow["group_id"],
                text=final_text
            )
            
            await pin_message(context, escrow["group_id"], msg.message_id)
            pinned_messages[escrow["group_id"]] = msg.message_id
            
            await context.bot.send_message(
                chat_id=escrow["escrower_id"],
                text=f"""
🔔 PAYMENT RECEIVED! #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
👤 Seller: @{escrow['seller']}
🆔 TXN: {verification_data.get('txn_id', 'N/A')}

Type /release {deal_id} to start release
"""
            )
            
            await query.edit_message_caption(
                caption=f"""
✅ PAYMENT VERIFIED!

━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🆔 TXN: {verification_data.get('txn_id', 'N/A')}

⏳ Escrower will release soon.
"""
            )
        else:
            await query.answer("⏳ Not detected yet.", show_alert=True)
            
            keyboard = [
                [InlineKeyboardButton("✅ I've Paid", callback_data=f"paid_{deal_id}")],
                [InlineKeyboardButton("🔄 Check Status", callback_data=f"check_{deal_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_caption(
                caption=f"""
📱 PAYMENT QR CODE
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']}
🔐 Escrower: @{escrow['escrower_username']}

⏳ Payment not detected yet.
Please wait and try again.
""",
                reply_markup=reply_markup
            )
            
    except Exception as e:
        print(f"Check payment error: {e}")

# ============ VIEW FORM HANDLER ============

async def handle_view_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        deal_id = query.data.split("_")[2]
        
        if deal_id not in escrows:
            await query.edit_message_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        escrow_data = get_escrower_data(escrow['escrower_username'])
        upi = escrow_data['upi'] if escrow_data else 'Not set'
        mode = "🔐 Auto (Gmail)" if (escrow_data and escrow_data.get('gmail_key')) else "📱 Manual (QR)"
        
        form_text = f"""
📋 DEAL FORM #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 {deal_id}
👤 @{escrow['buyer']}
👤 @{escrow['seller']}
💰 ₹{escrow['amount']}
📦 {escrow['item']}
🔐 @{escrow['escrower_username']}
💳 Pay to: {upi}
📱 Mode: {mode}
📊 {escrow['status']}
🆔 Order: {escrow.get('order_id', 'N/A')}
"""
        
        await query.edit_message_text(form_text)
        
    except Exception as e:
        print(f"View form error: {e}")

# ============ RELEASE & REFUND ============

async def release_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /release 001")
            return
        
        deal_id = args[0]
        
        if deal_id not in escrows:
            await update.message.reply_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        
        if escrow["status"] != "payment_received":
            await update.message.reply_text(f"❌ Cannot release. Status: {escrow['status']}")
            return
        
        user = update.effective_user
        username = user.username
        
        if not username:
            await update.message.reply_text("❌ You need a username!")
            return
        
        username_lower = normalize_username(username)
        buyer_lower = normalize_username(escrow["buyer"])
        seller_lower = normalize_username(escrow["seller"])
        
        if username_lower != buyer_lower and username_lower != seller_lower:
            await update.message.reply_text("❌ You are not part of this deal!")
            return
        
        # BUYER AGREES TO RELEASE
        if username_lower == buyer_lower:
            if release_agreements[deal_id]["buyer_agreed"]:
                await update.message.reply_text("✅ You already agreed to release!")
                return
            release_agreements[deal_id]["buyer_agreed"] = True
            await update.message.reply_text(f"✅ @{username} (BUYER) agreed to RELEASE!")
        
        # SELLER AGREES TO RELEASE
        elif username_lower == seller_lower:
            seller_upi = user_upi.get(seller_lower)
            if not seller_upi:
                await update.message.reply_text(
                    f"⚠️ @{username}, please provide your UPI ID first.\n"
                    f"Type: /upi {deal_id} your_upi_id\n"
                    f"Example: /upi {deal_id} seller@upi"
                )
                return
            
            if release_agreements[deal_id]["seller_agreed"]:
                await update.message.reply_text("✅ You already agreed to release!")
                return
            release_agreements[deal_id]["seller_agreed"] = True
            await update.message.reply_text(f"✅ @{username} (SELLER) agreed to RELEASE!")
        
        # Check if both agreed
        if release_agreements[deal_id]["buyer_agreed"] and release_agreements[deal_id]["seller_agreed"]:
            seller_upi = user_upi.get(seller_lower)
            
            await context.bot.send_message(
                chat_id=escrow["group_id"],
                text=f"""
✅ RELEASE AGREEMENT COMPLETE!
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']} ✅
👤 Seller: @{escrow['seller']} ✅
💳 Seller UPI: {seller_upi}

🔐 Escrower @{escrow['escrower_username']}
Type /rlsdone {deal_id} to complete release
"""
            )
            
            await context.bot.send_message(
                chat_id=escrow["escrower_id"],
                text=f"""
🔔 RELEASE READY!
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']} ✅
👤 Seller: @{escrow['seller']} ✅
💳 Seller UPI: {seller_upi}

Type /rlsdone {deal_id} to complete release
"""
            )
            
    except Exception as e:
        print(f"Release error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def refund_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /refund 001")
            return
        
        deal_id = args[0]
        
        if deal_id not in escrows:
            await update.message.reply_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        
        if escrow["status"] != "payment_received":
            await update.message.reply_text(f"❌ Cannot refund. Status: {escrow['status']}")
            return
        
        user = update.effective_user
        username = user.username
        
        if not username:
            await update.message.reply_text("❌ You need a username!")
            return
        
        username_lower = normalize_username(username)
        buyer_lower = normalize_username(escrow["buyer"])
        seller_lower = normalize_username(escrow["seller"])
        
        if username_lower != buyer_lower and username_lower != seller_lower:
            await update.message.reply_text("❌ You are not part of this deal!")
            return
        
        # BUYER AGREES TO REFUND
        if username_lower == buyer_lower:
            buyer_upi = user_upi.get(buyer_lower)
            if not buyer_upi:
                await update.message.reply_text(
                    f"⚠️ @{username}, please provide your UPI ID first.\n"
                    f"Type: /upi {deal_id} your_upi_id\n"
                    f"Example: /upi {deal_id} buyer@upi"
                )
                return
            
            if refund_agreements[deal_id]["buyer_agreed"]:
                await update.message.reply_text("✅ You already agreed to refund!")
                return
            refund_agreements[deal_id]["buyer_agreed"] = True
            await update.message.reply_text(f"✅ @{username} (BUYER) agreed to REFUND!")
        
        # SELLER AGREES TO REFUND
        elif username_lower == seller_lower:
            if refund_agreements[deal_id]["seller_agreed"]:
                await update.message.reply_text("✅ You already agreed to refund!")
                return
            refund_agreements[deal_id]["seller_agreed"] = True
            await update.message.reply_text(f"✅ @{username} (SELLER) agreed to REFUND!")
        
        # Check if both agreed
        if refund_agreements[deal_id]["buyer_agreed"] and refund_agreements[deal_id]["seller_agreed"]:
            buyer_upi = user_upi.get(buyer_lower)
            
            await context.bot.send_message(
                chat_id=escrow["group_id"],
                text=f"""
✅ REFUND AGREEMENT COMPLETE!
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']} ✅
👤 Seller: @{escrow['seller']} ✅
💳 Buyer UPI: {buyer_upi}

🔐 Escrower @{escrow['escrower_username']}
Type /refunddone {deal_id} to complete refund
"""
            )
            
            await context.bot.send_message(
                chat_id=escrow["escrower_id"],
                text=f"""
🔔 REFUND READY!
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Buyer: @{escrow['buyer']} ✅
👤 Seller: @{escrow['seller']} ✅
💳 Buyer UPI: {buyer_upi}

Type /refunddone {deal_id} to complete refund
"""
            )
            
    except Exception as e:
        print(f"Refund error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ============ UPI FOR DEAL ============

async def upi_for_deal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text(
                "Usage: /upi deal_id UPI_ID\n"
                "Example: /upi 001 seller@upi"
            )
            return
        
        deal_id = args[0]
        upi_id = args[1]
        
        if deal_id not in escrows:
            await update.message.reply_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        user = update.effective_user
        username = user.username
        
        if not username:
            await update.message.reply_text("❌ Need username!")
            return
        
        username_lower = normalize_username(username)
        buyer_lower = normalize_username(escrow["buyer"])
        seller_lower = normalize_username(escrow["seller"])
        
        if username_lower == seller_lower and escrow["status"] == "payment_received":
            user_upi[seller_lower] = upi_id
            await update.message.reply_text(f"✅ UPI set!\nNow type /release {deal_id}")
        elif username_lower == buyer_lower and escrow["status"] == "payment_received":
            user_upi[buyer_lower] = upi_id
            await update.message.reply_text(f"✅ UPI set!\nNow type /refund {deal_id}")
        else:
            await update.message.reply_text("❌ Not authorized!")
            
    except Exception as e:
        print(f"UPI error: {e}")

# ============ ESCROWER COMPLETE ============

async def rlsdone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        
        if not args:
            await update.message.reply_text("Usage: /rlsdone 001")
            return
        
        deal_id = args[0]
        
        if deal_id not in escrows:
            await update.message.reply_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        
        if escrow["escrower_id"] != user_id and not is_owner(user_id):
            await update.message.reply_text("❌ Only escrower or owner!")
            return
        
        if escrow["status"] != "payment_received":
            await update.message.reply_text(f"❌ Status: {escrow['status']}")
            return
        
        escrow["status"] = "released"
        
        update_user_stats(escrow["buyer"], deal_id, escrow["amount"], "buyer", "release")
        update_user_stats(escrow["seller"], deal_id, escrow["amount"], "seller", "release")
        
        if escrow["group_id"] in pinned_messages:
            await unpin_message(context, escrow["group_id"], pinned_messages[escrow["group_id"]])
        
        seller_upi = user_upi.get(normalize_username(escrow["seller"]), 'Not set')
        
        completion_text = format_release_complete(deal_id, escrow, seller_upi)
        
        keyboard = [
            [InlineKeyboardButton("📋 View Form", callback_data=f"view_form_{deal_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg = await context.bot.send_message(
            chat_id=escrow["group_id"],
            text=completion_text,
            reply_markup=reply_markup
        )
        
        await pin_message(context, escrow["group_id"], msg.message_id)
        pinned_messages[escrow["group_id"]] = msg.message_id
        
        await context.bot.send_message(
            chat_id=escrow["buyer"],
            text=f"""
✅ PAYMENT RELEASED!
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
👤 Seller: @{escrow['seller']}

🎉 Complete!
"""
        )
        
        await context.bot.send_message(
            chat_id=escrow["seller"],
            text=f"""
🎉 PAYMENT RECEIVED!
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}
🔐 Released by: @{escrow['escrower_username']}

🚀 Complete!
"""
        )
        
        await update.message.delete()
        
        if deal_id in escrows:
            del escrows[deal_id]
        if deal_id in agreements:
            del agreements[deal_id]
        if deal_id in release_agreements:
            del release_agreements[deal_id]
        if deal_id in refund_agreements:
            del refund_agreements[deal_id]
        if deal_id in deal_form_messages:
            del deal_form_messages[deal_id]
            
    except Exception as e:
        print(f"RLSDone error: {e}")

async def refunddone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        
        if not args:
            await update.message.reply_text("Usage: /refunddone 001")
            return
        
        deal_id = args[0]
        
        if deal_id not in escrows:
            await update.message.reply_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        
        if escrow["escrower_id"] != user_id and not is_owner(user_id):
            await update.message.reply_text("❌ Only escrower or owner!")
            return
        
        if escrow["status"] != "payment_received":
            await update.message.reply_text(f"❌ Status: {escrow['status']}")
            return
        
        escrow["status"] = "refunded"
        
        update_user_stats(escrow["buyer"], deal_id, escrow["amount"], "buyer", "refund")
        update_user_stats(escrow["seller"], deal_id, escrow["amount"], "seller", "refund")
        
        if escrow["group_id"] in pinned_messages:
            await unpin_message(context, escrow["group_id"], pinned_messages[escrow["group_id"]])
        
        buyer_upi = user_upi.get(normalize_username(escrow["buyer"]), 'Not set')
        
        refund_text = format_refund_complete(deal_id, escrow, buyer_upi)
        
        keyboard = [
            [InlineKeyboardButton("📋 View Form", callback_data=f"view_form_{deal_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg = await context.bot.send_message(
            chat_id=escrow["group_id"],
            text=refund_text,
            reply_markup=reply_markup
        )
        
        await pin_message(context, escrow["group_id"], msg.message_id)
        pinned_messages[escrow["group_id"]] = msg.message_id
        
        await context.bot.send_message(
            chat_id=escrow["buyer"],
            text=f"""
↩️ PAYMENT REFUNDED!
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}

↩️ Refunded to you!
"""
        )
        
        await context.bot.send_message(
            chat_id=escrow["seller"],
            text=f"""
↩️ DEAL CANCELLED
━━━━━━━━━━━━━━━━━━━━━

🆔 Deal: #{deal_id}
💰 Amount: ₹{escrow['amount']}

↩️ Refunded to @{escrow['buyer']}
"""
        )
        
        await update.message.delete()
        
        if deal_id in escrows:
            del escrows[deal_id]
        if deal_id in agreements:
            del agreements[deal_id]
        if deal_id in release_agreements:
            del release_agreements[deal_id]
        if deal_id in refund_agreements:
            del refund_agreements[deal_id]
        if deal_id in deal_form_messages:
            del deal_form_messages[deal_id]
            
    except Exception as e:
        print(f"RefundDone error: {e}")

# ============ STATUS & CANCEL ============

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /status 001")
            return
        
        deal_id = args[0]
        if deal_id not in escrows:
            await update.message.reply_text("❌ Deal not found!")
            return
        
        escrow = escrows[deal_id]
        escrow_data = get_escrower_data(escrow['escrower_username'])
        upi = escrow_data['upi'] if escrow_data else 'Not set'
        
        await update.message.reply_text(
            f"""
📋 DEAL STATUS #{deal_id}
━━━━━━━━━━━━━━━━━━━━━

🆔 #{deal_id}
📊 {escrow['status']}
💰 ₹{escrow['amount']}
📦 {escrow['item']}
👤 @{escrow['buyer']}
👤 @{escrow['seller']}
🔐 @{escrow['escrower_username']}
💳 Pay to: {upi}
"""
        )
    except Exception as e:
        print(f"Status error: {e}")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /cancel 001")
            return
        
        deal_id = args[0]
        if deal_id in escrows:
            escrow = escrows[deal_id]
            if escrow["group_id"] in pinned_messages:
                await unpin_message(context, escrow["group_id"], pinned_messages[escrow["group_id"]])
            del escrows[deal_id]
        
        if deal_id in agreements:
            del agreements[deal_id]
        if deal_id in release_agreements:
            del release_agreements[deal_id]
        if deal_id in refund_agreements:
            del refund_agreements[deal_id]
        if deal_id in deal_form_messages:
            del deal_form_messages[deal_id]
        
        await update.message.reply_text(f"✅ Deal #{deal_id} cancelled!")
    except Exception as e:
        print(f"Cancel error: {e}")

# ============ OWNER PANEL ============

async def owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        
        if not is_owner(user_id):
            await update.message.reply_text("❌ Only owner can access!")
            return
        
        keyboard = [
            [InlineKeyboardButton("📊 Bot Stats", callback_data="bot_stats")],
            [InlineKeyboardButton("📋 Active Deals", callback_data="active_deals")],
            [InlineKeyboardButton("👥 Escrowers", callback_data="escrower_list")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"""
👑 OWNER PANEL
━━━━━━━━━━━━━━━━━━━━━

📊 Active Deals: {len(escrows)}
⏳ Pending Payments: {len(pending_payments)}
✅ Verified Payments: {len(verified_payments)}
👥 Escrowers: {len(promoted_escrowers)}
💰 Fee: {ESCROW_FEE}%

Select option:
""",
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"Owner panel error: {e}")

async def handle_owner_panel_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        if not is_owner(user_id):
            await query.edit_message_text("❌ Only owner!")
            return
        
        action = query.data
        
        if action == "bot_stats":
            text = f"""
📊 BOT STATS
━━━━━━━━━━━━━━━━━━━━━

Active Deals: {len(escrows)}
Pending Payments: {len(pending_payments)}
Verified Payments: {len(verified_payments)}
Escrowers: {len(promoted_escrowers)}
Fee: {ESCROW_FEE}%
"""
            await query.edit_message_text(text)
        elif action == "active_deals":
            if not escrows:
                await query.edit_message_text("📭 No active deals.")
                return
            
            text = "📋 ACTIVE DEALS\n\n"
            for eid, escrow in list(escrows.items())[:10]:
                text += f"━━━ #{eid} ━━━\n💰 ₹{escrow['amount']}\n📊 {escrow['status']}\n🔐 @{escrow['escrower_username']}\n\n"
            await query.edit_message_text(text)
        elif action == "escrower_list":
            if not promoted_escrowers:
                await query.edit_message_text("👥 No escrowers.")
                return
            
            text = "👥 ESCROWERS\n\n"
            for e in list(promoted_escrowers):
                try:
                    user = await context.bot.get_chat(e)
                    username = user.username or "Unknown"
                    escrow_data = get_escrower_data(username)
                    upi = escrow_data['upi'] if escrow_data else 'Not set'
                    mode = "🔐 Auto" if (escrow_data and escrow_data.get('gmail_key')) else "📱 Manual"
                    text += f"• @{username} | UPI: {upi} | {mode}\n"
                except:
                    text += f"• ID: {e}\n"
            await query.edit_message_text(text)
        elif action == "refresh_panel":
            await owner_panel(update, context)
            
    except Exception as e:
        print(f"Owner panel button error: {e}")

# ============ MAIN ============

def main():
    try:
        print("━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("🤖 Starting Escrow Bot...")
        print(f"👑 Owner: @{OWNER_USERNAME}")
        print(f"💰 Fee: {ESCROW_FEE}%")
        print(f"📊 Deal Counter: {deal_counter}")
        print("⏳ Checking payments every 30 seconds...")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # Start web server for Render
        Thread(target=run_web_server).start()
        
        # Create Application
        app = Application.builder().token(TOKEN).build()
        
        # ... ALL YOUR HANDLERS ...
        
        print("✅ Bot is running!")
        app.run_polling()
        
    except Exception as e:
        print(f"❌ Main error: {e}")

if __name__ == "__main__":
    main()
