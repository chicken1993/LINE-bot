# =====================================================
# LINE 家計簿Bot 完全統合版（生活OS）
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

from google.cloud import vision

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4


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

        cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            user_id TEXT PRIMARY KEY,
            amount INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS ocr_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.commit()

    finally:
        cur.close()
        put_conn(conn)

init_db()


# ======================
# 天気＋服装
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
        return "☔ 傘＋レインコート"
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
# 予算
# ======================
def set_budget(user_id, amount):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO budgets (user_id, amount)
        VALUES (%s,%s)
        ON CONFLICT (user_id)
        DO UPDATE SET amount = EXCLUDED.amount
        """, (user_id, amount))
        conn.commit()
    finally:
        cur.close()
        put_conn(conn)


def get_budget(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT amount FROM budgets WHERE user_id=%s", (user_id,))
        r = cur.fetchone()
        return r[0] if r else None
    finally:
        cur.close()
        put_conn(conn)


def get_month_total(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
        = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))
        return cur.fetchone()[0]
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
# グラフ
# ======================
def create_graph(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT category, amount FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
        = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))
        rows = cur.fetchall()
    finally:
        cur.close()
        put_conn(conn)

    if not rows:
        return None

    data = defaultdict(int)
    for c, a in rows:
        data[c] += a

    plt.figure()
    plt.pie(list(data.values()), labels=list(data.keys()), autopct="%1.1f%%")
    plt.title("今月支出")

    path = f"graph_{user_id}.png"
    plt.savefig(path)
    plt.close()

    return path


# ======================
# PDF
# ======================
def create_pdf(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT amount, category, created_at FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
        = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))
        rows = cur.fetchall()
    finally:
        cur.close()
        put_conn(conn)

    if not rows:
        return None

    os.makedirs("reports", exist_ok=True)
    path = f"reports/report_{user_id}.pdf"

    c = canvas.Canvas(path, pagesize=A4)
    y = 800
    total = 0

    for a, cat, t in rows:
        c.drawString(50, y, f"{t.strftime('%m/%d')} {cat} {a}円")
        y -= 20
        total += a

    c.drawString(50, y-20, f"合計: {total}円")
    c.save()

    return path


# ======================
# webhook
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


# ======================
# メイン処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text
    user_id = event.source.user_id


    # 🌤 天気＋服装
    if text == "天気":
        w = get_weather()

        if not w:
            line_bot_api.reply_message(event.reply_token, TextSendMessage("天気失敗"))
            return

        msg = f"""🌤 天気

🌡 {w['temp']}°C（体感 {w['feels']}）
🌧 {w['precip']}mm

👕 {get_clothing(w['temp'], w['precip'])}
"""

        line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
        return


    # 💰 予算
    if text.startswith("予算"):
        amount = int(re.findall(r"\d+", text)[0])
        set_budget(user_id, amount)
        line_bot_api.reply_message(event.reply_token, TextSendMessage("予算OK"))
        return


    if text == "残り":
        budget = get_budget(user_id)
        used = get_month_total(user_id)

        msg = "未設定" if not budget else f"残り {budget-used}円"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
        return


    # 📊 グラフ
    if text == "グラフ":
        path = create_graph(user_id)
        if not path:
            line_bot_api.reply_message(event.reply_token, TextSendMessage("なし"))
            return

        url = f"{BASE_URL}/graph/{user_id}.png"
        line_bot_api.reply_message(event.reply_token, ImageSendMessage(url, url))
        return


    # 📄 PDF
    if text == "PDF":
        path = create_pdf(user_id)
        if not path:
            line_bot_api.reply_message(event.reply_token, TextSendMessage("なし"))
            return

        url = f"{BASE_URL}/report/{user_id}.pdf"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(url))
        return


    # 💰 手入力
    match = re.match(r"(\d+)\s*(.+)", text)
    if match:
        save_expense(user_id, int(match.group(1)), match.group(2))
        line_bot_api.reply_message(event.reply_token, TextSendMessage("OK"))
        return


    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage("使い方：1000 食費 / 天気 / 予算50000 / 残り / グラフ / PDF")
    )


# ======================
# routes
# ======================
@app.route("/")
def home():
    return "OK"

@app.route("/graph/<uid>.png")
def graph(uid):
    return send_file(f"graph_{uid}.png")

@app.route("/report/<uid>.pdf")
def report(uid):
    return send_file(f"reports/report_{uid}.pdf")


# ======================
# run
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))