# =====================================================
# LINE 家計簿Bot 完全版（Render安定 + PDF + グラフ + 天気 + 朝通知）
# =====================================================

from flask import Flask, request, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    TextSendMessage,
    ImageSendMessage,
    TemplateSendMessage,
    ConfirmTemplate,
    MessageAction,
    MessageEvent,
    TextMessage,
    ImageMessage
)
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

# ======================
# PDF
# ======================
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# ======================
# matplotlib
# ======================
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "ipaexg.ttf")

if os.path.exists(FONT_PATH):
    font_manager.fontManager.addfont(FONT_PATH)
    font_prop = font_manager.FontProperties(fname=FONT_PATH)
    plt.rcParams["font.family"] = font_prop.get_name()
else:
    plt.rcParams["font.family"] = "DejaVu Sans"

plt.rcParams["axes.unicode_minus"] = False


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
# 天気（今いる場所ベース）
# ======================
def get_weather():
    try:
        url = "https://wttr.in/?format=3"
        return requests.get(url, timeout=5).text.strip()
    except:
        return "天気取得失敗"


# ======================
# PUSH通知（🔥追加）
# ======================
def push_weather(user_id):
    try:
        weather = get_weather()
        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=f"🌅 朝の天気\n{weather}")
        )
    except:
        print("push失敗")


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
# ユーザー登録
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
# データ保存
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
# データ取得
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


def get_month_detail(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT amount, category, created_at
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
        ORDER BY created_at DESC
        """, (user_id,))
        return cur.fetchall()
    finally:
        cur.close()
        put_conn(conn)


# ======================
# 明細
# ======================
def build_statement(user_id):
    rows = get_month_detail(user_id)
    if not rows:
        return "今月データなし"

    total = sum(r[0] for r in rows)
    cat = defaultdict(int)

    lines = ["📄 今月の明細", "----------------"]

    for amount, category, created_at in rows:
        lines.append(f"{created_at.strftime('%m/%d')} | {category} | {amount}円")
        cat[category] += amount

    lines.append("----------------")
    lines.append("📊 カテゴリ別")

    for k, v in cat.items():
        lines.append(f"{k}: {v}円")

    lines.append("----------------")
    lines.append(f"合計: {total}円")

    return "\n".join(lines)


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

    month_label = datetime.now().strftime("%Y/%m")

    plt.figure(figsize=(6, 6))
    plt.pie(list(data.values()), labels=list(data.keys()), autopct="%1.1f%%")
    plt.title(f"{month_label} 支出")

    path = f"graph_{user_id}.png"
    plt.savefig(path)
    plt.close()

    return path


# ======================
# PDF
# ======================
def create_pdf(user_id):
    rows = get_month_detail(user_id)
    if not rows:
        return None

    os.makedirs("reports", exist_ok=True)

    path = f"reports/report_{user_id}.pdf"
    c = canvas.Canvas(path, pagesize=A4)

    y = 800
    total = 0

    month_label = datetime.now().strftime("%Y/%m")

    c.drawString(50, y, f"{month_label} 明細")
    y -= 40

    for amount, category, created_at in rows:
        line = f"{created_at.strftime('%m/%d')} {category} {amount}円"
        c.drawString(50, y, line)
        y -= 20
        total += amount

        if y < 50:
            c.showPage()
            y = 800

    y -= 30
    c.drawString(50, y, f"合計: {total}円")

    c.save()
    return path


# ======================
# LINE webhook
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
# TEXT処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    if text == "天気":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(get_weather())
        )
        return

    if text == "今月":
        rows = get_month_data(user_id)
        total = sum(a for _, a in rows)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"今月合計：{total}円")
        )
        return

    if text == "集計":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(build_statement(user_id))
        )
        return

    if text == "PDF出力":
        path = create_pdf(user_id)
        if not path:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("データなし")
            )
            return

        url = f"{BASE_URL}/report/{user_id}.pdf"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"PDFできた👇\n{url}")
        )
        return


# ======================
# cron用（🔥追加）
# ======================
@app.route("/cron/weather", methods=["GET"])
def cron_weather():

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        users = cur.fetchall()

        for (user_id,) in users:
            push_weather(user_id)

        return "OK", 200
    finally:
        cur.close()
        put_conn(conn)


# ======================
# ROUTES
# ======================
@app.route("/")
def home():
    return "OK", 200


@app.route("/graph/<user_id>.png")
def graph(user_id):
    return send_file(f"graph_{user_id}.png", mimetype="image/png")


@app.route("/report/<user_id>.pdf")
def report(user_id):
    return send_file(
        f"reports/report_{user_id}.pdf",
        mimetype="application/pdf"
    )


# ======================
# RUN
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))