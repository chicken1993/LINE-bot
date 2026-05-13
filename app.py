# ======================
# Flask / LINE Bot 家計簿（予算警告＋安全天気版）
# ======================

from flask import Flask, request, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.models import *

from collections import defaultdict
from datetime import datetime
import calendar
import os, re, traceback, requests
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager

from google.cloud import vision

# ======================
# 日本語フォント
# ======================

font_prop = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONT_PATH = os.path.join(BASE_DIR, "fonts", "ipaexg.ttf")

try:
    if os.path.exists(FONT_PATH):
        font_manager.fontManager.addfont(FONT_PATH)
        font_prop = font_manager.FontProperties(fname=FONT_PATH)
        plt.rcParams["font.family"] = font_prop.get_name()
        plt.rcParams["axes.unicode_minus"] = False
        print("✅ 日本語フォントOK")
    else:
        plt.rcParams["font.family"] = "DejaVu Sans"

except:
    plt.rcParams["font.family"] = "DejaVu Sans"

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

OCR_LIMIT = 20

# ======================
# DB接続
# ======================

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ocr_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            user_id TEXT PRIMARY KEY,
            amount INTEGER
        )
    """)

    conn.commit()
    cur.close()
    put_conn(conn)

init_db()

# ======================
# 保存系
# ======================

def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO expenses (user_id, amount, category)
        VALUES (%s,%s,%s)
    """, (user_id, amount, category))

    conn.commit()
    cur.close()
    put_conn(conn)

def delete_latest(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM expenses
        WHERE id = (
            SELECT id FROM expenses
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT 1
        )
    """, (user_id,))

    conn.commit()
    cur.close()
    put_conn(conn)

def delete_month(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    conn.commit()
    cur.close()
    put_conn(conn)

def get_recent_expenses(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, category, amount
        FROM expenses
        WHERE user_id=%s
        ORDER BY created_at DESC
        LIMIT 10
    """, (user_id,))

    rows = cur.fetchall()

    cur.close()
    put_conn(conn)

    return rows

def delete_by_id(expense_id, user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM expenses
        WHERE id=%s AND user_id=%s
    """, (expense_id, user_id))

    conn.commit()
    cur.close()
    put_conn(conn)

# ======================
# 予算
# ======================

def save_budget(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO budgets (user_id, amount)
        VALUES (%s,%s)
        ON CONFLICT (user_id)
        DO UPDATE SET amount=EXCLUDED.amount
    """, (user_id, amount))

    conn.commit()
    cur.close()
    put_conn(conn)

def get_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT amount FROM budgets WHERE user_id=%s", (user_id,))
    row = cur.fetchone()

    cur.close()
    put_conn(conn)

    return row[0] if row else None

def delete_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM budgets WHERE user_id=%s", (user_id,))

    conn.commit()
    cur.close()
    put_conn(conn)

# ======================
# 今月データ
# ======================

def get_month_data(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, amount
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    rows = cur.fetchall()

    cur.close()
    put_conn(conn)

    return rows

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

    plt.figure(figsize=(6,6))
    plt.pie(data.values(), labels=data.keys(), autopct="%1.1f%%")
    plt.title("今月の支出")

    path = "graph.png"
    plt.savefig(path)
    plt.close()

    return path

# ======================
# OCR
# ======================

def detect_text_from_image(image_content):
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_content)

    response = client.text_detection(image=image)
    texts = response.text_annotations

    return texts[0].description if texts else ""

def extract_max_price(text):
    nums = re.findall(r'\d{2,6}', text)
    return max(map(int, nums)) if nums else None

# ======================
# 🌤 天気（ここが修正ポイント）
# ======================

def get_weather_text(code):
    if code == 0:
        return "晴れ"
    elif code in [1,2,3]:
        return "くもり"
    elif code in [45,48]:
        return "霧"
    elif code >= 51:
        return "雨"
    return "晴れ"

def fetch_weather(city, lat, lon):

    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            "&hourly=temperature_2m,precipitation_probability,weathercode"
            "&timezone=Asia%2FTokyo"
        )

        data = requests.get(url, timeout=5).json()

        max_t = data["daily"]["temperature_2m_max"][0]
        min_t = data["daily"]["temperature_2m_min"][0]
        rain = data["daily"]["precipitation_probability_max"][0]

        h_t = data["hourly"]["temperature_2m"]
        h_r = data["hourly"]["precipitation_probability"]
        h_c = data["hourly"]["weathercode"]

        def safe(arr, i):
            return arr[i] if arr and len(arr) > i else None

        m_weather = get_weather_text(safe(h_c, 9)) if safe(h_c,9) is not None else "不明"
        a_weather = get_weather_text(safe(h_c,15)) if safe(h_c,15) is not None else "不明"

        text = (
            f"📍{city}\n"
            f"午前：{m_weather} / {safe(h_t,9)}℃ / {safe(h_r,9)}%\n"
            f"午後：{a_weather} / {safe(h_t,15)}℃ / {safe(h_r,15)}%\n"
            f"最高：{max_t}℃ 最低：{min_t}℃\n"
            f"降水：{rain}%"
        )

        if rain >= 50:
            text += "\n☔ 傘持っていこう"

        return text

    except Exception as e:
        print("weather error:", e)
        return f"📍{city}\n取得失敗"

# ======================
# ROUTE
# ======================

@app.route("/")
def home():
    return "OK"

@app.route("/graph.png")
def graph():
    return send_file("graph.png", mimetype="image/png")

@app.route("/weather")
def send_weather():

    user_id = "Ucae4b4a79830d56a8bf4d63159763afd"

    shizuoka = fetch_weather("静岡市", 34.9769, 138.3831)
    numazu = fetch_weather("沼津市", 35.0956, 138.8635)

    msg = f"☀️ 今日の天気\n\n{shizuoka}\n\n{numazu}"

    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=msg)
    )

    return "ok"

# ======================
# LINE TEXT
# ======================

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:

        if text == "グラフ":
            path = create_graph(user_id)
            if not path:
                line_bot_api.reply_message(event.reply_token, TextSendMessage("データなし"))
                return

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=f"{BASE_URL}/graph.png",
                    preview_image_url=f"{BASE_URL}/graph.png"
                )
            )
            return

        if text == "今月":
            rows = get_month_data(user_id)
            total = sum(a for _, a in rows) if rows else 0

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"今月合計：{total}円")
            )
            return

        # （他の機能はそのまま残ってる想定）

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))

# ======================
# OCR IMAGE
# ======================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id

    try:

        content = line_bot_api.get_message_content(event.message.id)
        text = detect_text_from_image(content.content)

        amount = extract_max_price(text)

        if not amount:
            line_bot_api.reply_message(event.reply_token, TextSendMessage("金額なし"))
            return

        line_bot_api.reply_message(
            event.reply_token,
            ConfirmTemplate(
                text=f"{amount}円で登録？",
                actions=[
                    MessageAction(label="はい", text=f"OK_{amount}"),
                    MessageAction(label="いいえ", text="キャンセル")
                ]
            )
        )

    except:
        line_bot_api.reply_message(event.reply_token, TextSendMessage("OCRエラー"))

# ======================
# RUN
# ======================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))