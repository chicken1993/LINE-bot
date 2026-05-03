# ======================
# Flask / LINE Bot 家計簿（完成版）
# ======================

from flask import Flask, request, Response
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
import os, re, io, traceback

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ======================
# 初期化
# ======================
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# DBプール
# ======================
pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL, sslmode="require")

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

# ======================
# DB初期化
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

    conn.commit()
    cur.close()
    put_conn(conn)

init_db()

# ======================
# DB操作
# ======================
def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s,%s,%s)",
        (user_id, amount, category)
    )
    conn.commit()
    cur.close()
    put_conn(conn)

def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)=DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))
    total = cur.fetchone()[0]
    cur.close()
    put_conn(conn)
    return total

# ======================
# グラフ
# ======================
@app.route("/chart/<user_id>")
def chart(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
    """, (user_id,))

    data = cur.fetchall()
    cur.close()
    put_conn(conn)

    plt.figure(figsize=(6,6))

    if not data:
        plt.text(0.5, 0.5, "No Data", ha='center')
    else:
        labels = [d[0] for d in data]
        values = [d[1] for d in data]
        plt.pie(values, labels=labels, autopct="%1.1f%%")

    img = io.BytesIO()
    plt.savefig(img, format="png")
    plt.close()
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")

# ======================
# Webhook
# ======================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, signature)
    except:
        print(traceback.format_exc())

    return "OK"

@app.route("/")
def home():
    return "OK"

# ======================
# メイン処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip().replace(" ", "").replace("　", "")
    user_id = event.source.user_id

    try:

        # ===== 使い方 =====
        if text in ["はじめて", "使い方", "ヘルプ"]:
            msg = """【使い方】
①「1000 食費」で即登録
②「今月」で合計確認
③「グラフ」で内訳チェック
"""
            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        # ===== 今月（Flex）=====
        if text in ["今月", "今月合計"]:
            total = get_month_total(user_id)

            bubble = {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "今月の支出", "weight": "bold", "size": "xl"},
                        {"type": "text", "text": f"{total}円", "size": "xxl", "weight": "bold"}
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "button",
                            "style": "primary",
                            "action": {"type": "message", "label": "グラフ", "text": "グラフ"}
                        }
                    ]
                }
            }

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="今月", contents=bubble)
            )
            return

        # ===== グラフ =====
        if text == "グラフ":
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(event.reply_token, ImageSendMessage(url, url))
            return

        # ===== 一発入力 =====
        quick = re.match(r'^(\d+)(円)?(.+)$', text)

        if quick:
            amount = int(quick.group(1))
            category = quick.group(3).strip()

            save_expense(user_id, amount, category)

            msg = f"{category}:{amount}円 登録OK👍"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        # ===== fallback =====
        line_bot_api.reply_message(event.reply_token, TextSendMessage("「1000 食費」で入力できるよ"))

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))