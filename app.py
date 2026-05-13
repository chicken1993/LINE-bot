# ======================
# Flask / LINE Bot 家計簿 完全版
# ======================

from flask import Flask, request, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
from linebot.models import FollowEvent

from collections import defaultdict
import os, re, traceback, requests
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from google.cloud import vision

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
        CREATE TABLE IF NOT EXISTS budgets (
            user_id TEXT PRIMARY KEY,
            amount INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cur.close()
    put_conn(conn)

init_db()

# ======================
# ユーザー管理
# ======================

@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_id)
        VALUES (%s)
        ON CONFLICT DO NOTHING
    """, (user_id,))

    conn.commit()
    cur.close()
    put_conn(conn)

def get_all_users():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT user_id FROM users")
    rows = cur.fetchall()

    cur.close()
    put_conn(conn)

    return [r[0] for r in rows]

# ======================
# 家計簿
# ======================

def save_expense(user_id, amount, category="その他"):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO expenses (user_id, amount, category)
        VALUES (%s,%s,%s)
    """, (user_id, amount, category))

    conn.commit()
    cur.close()
    put_conn(conn)

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

def detect_text(image_content):
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_content)
    response = client.text_detection(image=image)
    return response.text_annotations[0].description if response.text_annotations else ""

def extract_price(text):
    nums = re.findall(r'\d{2,6}', text)
    return max(map(int, nums)) if nums else None

# ======================
# 天気
# ======================

def get_weather_text(code):
    if code == 0:
        return "晴れ"
    elif code in [1,2,3]:
        return "くもり"
    elif code in [45,48]:
        return "霧"
    return "雨"

def fetch_weather(city, lat, lon):

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

    return (
        f"📍{city}\n"
        f"午前：{get_weather_text(safe(h_c,9))} / {safe(h_t,9)}℃\n"
        f"午後：{get_weather_text(safe(h_c,15))} / {safe(h_t,15)}℃\n"
        f"最高：{max_t}℃ 最低：{min_t}℃\n"
        f"降水：{rain}%"
        + ("\n☔ 傘必要" if rain >= 50 else "")
    )

# ======================
# ROUTES
# ======================

@app.route("/")
def home():
    return "OK"

@app.route("/weather")
def send_weather():

    users = get_all_users()

    msg = "☀️ 今日の天気\n\n" + \
          fetch_weather("静岡市", 34.9769, 138.3831) + "\n\n" + \
          fetch_weather("沼津市", 35.0956, 138.8635)

    for u in users:
        try:
            line_bot_api.push_message(u, TextSendMessage(text=msg))
        except:
            pass

    return "sent"

@app.route("/graph.png")
def graph():
    return send_file("graph.png", mimetype="image/png")

@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    handler.handle(body, request.headers.get("X-Line-Signature", ""))
    return "OK"

# ======================
# LINE TEXT
# ======================

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text
    user_id = event.source.user_id

    if text.isdigit():
        save_expense(user_id, int(text), "手入力")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"{text}円を保存したよ")
        )
        return

    if text == "今月":
        rows = get_month_data(user_id)
        total = sum(a for _, a in rows)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"今月合計：{total}円")
        )
        return

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

# ======================
# IMAGE OCR
# ======================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    try:
        content = line_bot_api.get_message_content(event.message.id)
        text = detect_text(content.content)

        price = extract_price(text)

        if not price:
            line_bot_api.reply_message(event.reply_token, TextSendMessage("金額なし"))
            return

        line_bot_api.reply_message(
            event.reply_token,
            ConfirmTemplate(
                text=f"{price}円で保存する？",
                actions=[
                    MessageAction(label="はい", text=str(price)),
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