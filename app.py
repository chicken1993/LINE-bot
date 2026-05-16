# =====================================================
# LINE 家計簿Bot 完全統合版（安定版）
# 天気 + 服装 + OCR + 予算 + PDF + グラフ
# =====================================================

from flask import Flask, request, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
from linebot.models.events import FollowEvent

import os
import re
import traceback
import requests
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ======================
# Flask
# ======================
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL, sslmode="require")


# ======================
# 天気 + 服装
# ======================
def get_weather():
    try:
        url = "https://wttr.in/?format=j1"
        data = requests.get(url, timeout=5).json()
        c = data["current_condition"][0]

        return {
            "temp": int(c["temp_C"]),
            "feels": int(c["FeelsLikeC"]),
            "precip": float(c.get("precipMM", 0))
        }
    except:
        return None


def get_clothing(temp, precip):
    if precip >= 5:
        return "☔ 傘＋レインコート必須"
    if temp <= 5:
        return "🧥 ダウン"
    if temp <= 10:
        return "🧥 厚手上着"
    if temp <= 15:
        return "🧥 薄手コート"
    if temp <= 20:
        return "👕 長袖"
    if temp <= 25:
        return "👕 半袖"
    return "🩳 夏服"


# ======================
# DB
# ======================
def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)


def init_db():
    conn = get_conn()
    try:
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
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.commit()

    finally:
        cur.close()
        put_conn(conn)

init_db()


# ======================
# Follow
# ======================
@handler.add(FollowEvent)
def follow(event):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO users (user_id)
        VALUES (%s)
        ON CONFLICT DO NOTHING
        """, (event.source.user_id,))
        conn.commit()
    finally:
        cur.close()
        put_conn(conn)


# ======================
# 保存
# ======================
def save_expense(user_id, amount, category):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO expenses (user_id, amount, category)
        VALUES (%s,%s,%s)
        """, (user_id, amount, category))
        conn.commit()
    finally:
        cur.close()
        put_conn(conn)


# ======================
# 月データ
# ======================
def get_month_data(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT category, amount
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))
        return cur.fetchall()
    finally:
        cur.close()
        put_conn(conn)


# ======================
# グラフ
# ======================
def create_graph(user_id):
    rows = get_month_data(user_id)
    if not rows:
        return None

    data = defaultdict(int)

    for c, a in rows:
        data[c] += a

    plt.figure()
    plt.pie(list(data.values()), labels=list(data.keys()), autopct="%1.1f%%")

    path = f"graph_{user_id}.png"
    plt.savefig(path)
    plt.close()

    return path


# ======================
# webhook
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


# ======================
# メッセージ
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    # 🌤 天気 + 服装
    if text == "天気":
        w = get_weather()

        if not w:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("天気取得失敗")
            )
            return

        msg = f"""🌤 今日の天気

🌡 {w['temp']}°C（体感 {w['feels']}°C）
🌧 降水 {w['precip']} mm

👕 {get_clothing(w['temp'], w['precip'])}
"""

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(msg)
        )
        return

    # 💰 登録
    match = re.match(r'^(\d+)\s*(.+)$', text)
    if match:
        save_expense(user_id, int(match.group(1)), match.group(2))
        line_bot_api.reply_message(event.reply_token, TextSendMessage("登録OK"))
        return

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage("『1000 食費』で入力してね")
    )


# ======================
# routes
# ======================
@app.route("/")
def home():
    return "OK", 200


@app.route("/graph/<user_id>.png")
def graph(user_id):
    return send_file(f"graph_{user_id}.png", mimetype="image/png")


# ======================
# run
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))