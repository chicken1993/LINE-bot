# =====================================================
# LINE 家計簿Bot 完全統合版
# Flask + PostgreSQL + OCR + グラフ + 予算 + 天気 + 服装 + 朝通知
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

# PDF
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# matplotlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# =====================================================
# font
# =====================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BASE_DIR, "ipaexg.ttf")

if os.path.exists(FONT_PATH):
    font_manager.fontManager.addfont(FONT_PATH)
    font_prop = font_manager.FontProperties(fname=FONT_PATH)
    plt.rcParams["font.family"] = font_prop.get_name()
else:
    plt.rcParams["font.family"] = "DejaVu Sans"

plt.rcParams["axes.unicode_minus"] = False

# =====================================================
# Flask
# =====================================================
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL, sslmode="require")

OCR_LIMIT = 20


# =====================================================
# DB
# =====================================================
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


# =====================================================
# 天気 + 服装
# =====================================================
def get_weather_detail():
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
        return "🧥 厚手アウター"

    if temp <= 15:
        return "🧥 薄手コート"

    if temp <= 20:
        return "👕 長袖"

    if temp <= 25:
        return "👕 半袖"

    return "🩳 夏服"


# =====================================================
# PUSH通知
# =====================================================
def push_weather(user_id):
    try:
        w = get_weather_detail()
        if not w:
            return

        msg = f"""🌅 朝の天気

🌡 {w['temp']}°C（体感 {w['feels']}°C）
🌧 {w['precip']}mm

👕 {get_clothing(w['temp'], w['precip'])}
"""

        line_bot_api.push_message(user_id, TextSendMessage(msg))

    except:
        print("push失敗")


# =====================================================
# 予算
# =====================================================
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


# =====================================================
# 支出
# =====================================================
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
        ORDER BY created_at DESC
        """, (user_id,))
        return cur.fetchall()
    finally:
        cur.close()
        put_conn(conn)


# =====================================================
# グラフ
# =====================================================
def create_graph(user_id):
    rows = get_month_data(user_id)
    if not rows:
        return None

    data = defaultdict(int)
    for c, a in rows:
        data[c] += a

    plt.figure(figsize=(6,6))
    plt.pie(list(data.values()), labels=list(data.keys()), autopct="%1.1f%%")

    path = f"graph_{user_id}.png"
    plt.savefig(path)
    plt.close()

    return path


# =====================================================
# PDF
# =====================================================
def create_pdf(user_id):
    rows = get_month_detail(user_id)
    if not rows:
        return None

    os.makedirs("reports", exist_ok=True)
    path = f"reports/report_{user_id}.pdf"

    c = canvas.Canvas(path, pagesize=A4)

    y = 800
    total = 0

    c.drawString(50, y, "明細")
    y -= 40

    for amount, category, created_at in rows:
        c.drawString(50, y, f"{created_at.strftime('%m/%d')} {category} {amount}円")
        y -= 20
        total += amount

    c.drawString(50, y-30, f"合計: {total}円")
    c.save()

    return path


# =====================================================
# OCR
# =====================================================
def detect_text(img):
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=img)
    res = client.text_detection(image=image)
    return res.text_annotations[0].description if res.text_annotations else ""


def extract_price(text):
    nums = re.findall(r'\d{2,6}', text)
    return max(map(int, nums)) if nums else None


def save_ocr_log(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO ocr_logs (user_id) VALUES (%s)", (user_id,))
        conn.commit()
    finally:
        cur.close()
        put_conn(conn)


def get_ocr_count(user_id):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
        SELECT COUNT(*) FROM ocr_logs
        WHERE user_id=%s
        """, (user_id,))
        return cur.fetchone()[0]
    finally:
        cur.close()
        put_conn(conn)


# =====================================================
# webhook
# =====================================================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, signature)
    except:
        print(traceback.format_exc())

    return "OK"


# =====================================================
# Follow
# =====================================================
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


# =====================================================
# TEXT
# =====================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text
    user_id = event.source.user_id

    # 天気
    if text == "天気":
        w = get_weather_detail()
        if not w:
            return line_bot_api.reply_message(event.reply_token, TextSendMessage("失敗"))

        msg = f"""🌤 天気

🌡 {w['temp']}°C
🌧 {w['precip']}mm

👕 {get_clothing(w['temp'], w['precip'])}
"""

        return line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(msg)
        )

    # 予算
    if text.startswith("予算"):
        amount = int(re.findall(r'\d+', text)[0])
        set_budget(user_id, amount)
        return line_bot_api.reply_message(event.reply_token, TextSendMessage("OK"))

    # 残り
    if text == "残り":
        b = get_budget(user_id)
        u = get_month_total(user_id)
        msg = "未設定" if not b else f"残り {b-u}円"
        return line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))

    # PDF
    if text == "PDF出力":
        path = create_pdf(user_id)
        url = f"{BASE_URL}/report/{user_id}.pdf"
        return line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"PDF👇\n{url}")
        )

    # 手入力
    m = re.match(r'^(\d+)\s*(.+)$', text)
    if m:
        save_expense(user_id, int(m.group(1)), m.group(2))
        return line_bot_api.reply_message(event.reply_token, TextSendMessage("OK"))


# =====================================================
# cron
# =====================================================
@app.route("/cron/weather")
def cron_weather():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        users = cur.fetchall()

        for (u,) in users:
            push_weather(u)

        return "OK", 200
    finally:
        cur.close()
        put_conn(conn)


# =====================================================
# routes
# =====================================================
@app.route("/")
def home():
    return "OK"

@app.route("/graph/<user_id>.png")
def graph(user_id):
    return send_file(f"graph_{user_id}.png", mimetype="image/png")

@app.route("/report/<user_id>.pdf")
def report(user_id):
    return send_file(f"reports/report_{user_id}.pdf", mimetype="application/pdf")


# =====================================================
# run
# =====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))