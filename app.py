# ======================
# Flask
# ======================
from flask import Flask, request, Response, send_file

# ======================
# LINE
# ======================
from linebot import LineBotApi, WebhookHandler
from linebot.models import *

# ======================
import os, re, traceback, io
from dotenv import load_dotenv

import psycopg2

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ======================
load_dotenv()

app = Flask(__name__)

line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("CHANNEL_SECRET"))
BASE_URL = os.getenv("BASE_URL")

# ======================
# DB
# ======================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

# ======================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            amount INTEGER,
            category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id TEXT PRIMARY KEY,
            step TEXT,
            category TEXT
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# ======================
# STATE
# ======================
def set_state(uid, step, cat=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_states VALUES (%s,%s,%s)
        ON CONFLICT (user_id)
        DO UPDATE SET step=%s, category=%s
    """, (uid, step, cat, step, cat))
    conn.commit()
    cur.close()
    conn.close()

def get_state(uid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT step, category FROM user_states WHERE user_id=%s", (uid,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    return r

def clear_state(uid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_states WHERE user_id=%s", (uid,))
    conn.commit()
    cur.close()
    conn.close()

# ======================
# SAFE NUMBER（重要修正）
# ======================
def safe_amount(text):
    m = re.search(r'\d+', text)
    if not m:
        return None
    return int(m.group())

# ======================
# SAVE
# ======================
def save_expense(uid, amount, cat):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s,%s,%s)",
        (uid, amount, cat)
    )
    conn.commit()
    cur.close()
    conn.close()

# ======================
# TOTAL
# ======================
def get_month_total(uid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s
    """, (uid,))
    r = cur.fetchone()[0]
    cur.close()
    conn.close()
    return r

# ======================
# CHART（404修正ここ）
# ======================
@app.route("/chart/<uid>")
def chart(uid):

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
    """, (uid,))
    data = cur.fetchall()
    cur.close()
    conn.close()

    if not data:
        return "no data"

    labels = [d[0] for d in data]
    values = [d[1] for d in data]

    plt.figure()
    plt.bar(labels, values)

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)

    return send_file(buf, mimetype="image/png")

# ======================
# WEBHOOK
# ======================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    sig = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, sig)
    except:
        print(traceback.format_exc())

    return "OK"

@app.route("/")
def home():
    return "OK"

# ======================
# LINE MAIN
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle(event):

    text = event.message.text
    uid = event.source.user_id
    state = get_state(uid)

    try:

        # ===== 支出 =====
        if text == "支出":
            set_state(uid, "cat")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("カテゴリ入力")
            )
            return

        if state and state[0] == "cat":
            set_state(uid, "amount", text)
            line_bot_api.reply_message(event.reply_token, TextSendMessage("金額"))
            return

        if state and state[0] == "amount":

            amount = safe_amount(text)

            # ❗ここが今回の修正ポイント
            if amount is None:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("数字入れて")
                )
                return

            cat = state[1]

            save_expense(uid, amount, cat)
            clear_state(uid)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("OK")
            )
            return

        # ===== グラフ =====
        if text == "グラフ":
            url = f"{BASE_URL}/chart/{uid}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("メニュー使って")
        )

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))

# ======================
# RUN（gunicorn対応OK）
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))