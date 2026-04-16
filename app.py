from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import os
from dotenv import load_dotenv
import psycopg2
import re

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# PostgreSQL接続
# ======================

def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            amount INTEGER,
            category TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

# 👇ここ追加🔥
def reset_table():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS expenses")
    conn.commit()
    cur.close()
    conn.close()

# 👇ここ追加🔥（順番重要）
reset_table()
init_db()

# ======================
# カテゴリ判定
# ======================

def get_category(name):
    if any(word in name for word in ["ラーメン", "ご飯", "寿司", "カフェ", "スタバ"]):
        return "食費"
    elif any(word in name for word in ["電車", "バス", "タクシー"]):
        return "交通費"
    elif any(word in name for word in ["Amazon", "買い物", "服"]):
        return "買い物"
    else:
        return "その他"

def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s, %s, %s)",
        (user_id, amount, category)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_total(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id=%s",
        (user_id,)
    )
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    return total if total else 0

# ======================
# ルーティング
# ======================

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

# ======================
# LINE処理
# ======================

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text
    print("受信:", text, "ユーザー:", user_id)

    if text.strip() == "合計":
        total = get_total(user_id)
        reply_text = f"合計：{total}円"

    elif text.strip() == "リセット":
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
        conn.commit()
        cur.close()
        conn.close()
        reply_text = "データをリセットしたよ！"

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

            category = get_category(name)
            save_expense(user_id, price, category)

            reply_text = f"{name} を {price}円で記録したよ！（{category}）"

        except Exception as e:
            print("🔥エラー:", e)
            reply_text = "入力がおかしいよ💦（例：ラーメン900）"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# ======================
# 起動
# ======================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)