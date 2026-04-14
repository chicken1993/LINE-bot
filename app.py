from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import os
from dotenv import load_dotenv
import sqlite3
import re

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

def init_db():
    conn = sqlite3.connect("kakeibo.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            amount INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_expense(user_id, amount):
    conn = sqlite3.connect("kakeibo.db")
    c = conn.cursor()
    c.execute("INSERT INTO expenses (user_id, amount) VALUES (?, ?)", (user_id, amount))
    conn.commit()
    conn.close()

def get_total(user_id):
    conn = sqlite3.connect("kakeibo.db")
    c = conn.cursor()
    c.execute("SELECT SUM(amount) FROM expenses WHERE user_id=?", (user_id,))
    total = c.fetchone()[0]
    conn.close()
    return total if total else 0

@app.route("/")
def home():
    return "Hello from LINE bot!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("エラー:", e)
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text
    print("受信:", text, "ユーザー:", user_id)

    if text.strip() == "合計":
        total = get_total(user_id)
        reply_text = f"合計：{total}円"
    else:
        try:
            text = text.strip()
            text = text.replace("　", " ")
            text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

            numbers = re.findall(r"\d+", text)
            if not numbers:
                raise Exception("金額なし")

            price = int(numbers[-1])

            name = re.sub(r"\d+|円", "", text).strip()
            if not name:
                name = "不明"

            save_expense(user_id, price)

            reply_text = f"{name} を {price}円で記録したよ！"

        except Exception as e:
            print("🔥エラー:", e)
            reply_text = "入力がおかしいよ💦（例：ラーメン900）"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)