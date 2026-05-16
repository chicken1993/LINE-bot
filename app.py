# =====================================================
# LINE 家計簿Bot 完全版（明細・集計・天気統合）
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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from google.cloud import vision

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
# 天気
# =====================================================

def get_weather():
    try:
        city = "Tokyo"
        url = f"https://wttr.in/{city}?format=3"
        res = requests.get(url, timeout=5)
        return res.text
    except:
        return "天気取得失敗"

# =====================================================
# DB
# =====================================================

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

# =====================================================
# DB初期化
# =====================================================

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
        CREATE TABLE IF NOT EXISTS ocr_logs (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
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

        conn.commit()

    finally:
        cur.close()
        put_conn(conn)

init_db()

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

# =====================================================
# 今月合計
# =====================================================

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

# =====================================================
# 今月明細
# =====================================================

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

# =====================================================
# 明細書
# =====================================================

def build_statement(user_id):
    rows = get_month_detail(user_id)
    total = get_month_total(user_id)

    if not rows:
        return "今月データなし"

    lines = ["📄 今月の明細書", "----------------"]

    cat = defaultdict(int)

    for amount, category, created_at in rows:
        date = created_at.strftime("%m/%d")
        lines.append(f"{date} | {category} | {amount}円")
        cat[category] += amount

    lines.append("----------------")
    lines.append("📊 カテゴリ別")

    for k, v in cat.items():
        lines.append(f"{k}: {v}円")

    lines.append("----------------")
    lines.append(f"合計: {total}円")

    return "\n".join(lines)

# =====================================================
# グラフ
# =====================================================

def create_graph(user_id):
    rows = get_month_detail(user_id)

    if not rows:
        return None

    data = defaultdict(int)

    for a, c, _ in rows:
        data[c] += a

    plt.figure(figsize=(6, 6))
    plt.pie(list(data.values()), labels=list(data.keys()), autopct="%1.1f%%")
    plt.title("今月の支出")

    path = f"graph_{user_id}.png"
    plt.savefig(path)
    plt.close()

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

def extract_store(text):
    for line in text.split("\n"):
        if "TEL" in line:
            continue
        if "合計" in line:
            continue
        if len(line) > 3:
            return line
    return "不明"

# =====================================================
# callback
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
# テキスト処理
# =====================================================

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    if text == "使い方":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                "1000 食費\nグラフ\n集計\nレポート\n天気"
            )
        )
        return

    # 天気
    if text == "天気":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(get_weather())
        )
        return

    # 集計
    if text == "集計":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(build_statement(user_id))
        )
        return

    # レポート
    if text == "レポート":
        msg = build_statement(user_id)
        create_graph(user_id)

        url = f"{BASE_URL}/graph/{user_id}.png"

        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(msg),
                ImageSendMessage(
                    original_content_url=url,
                    preview_image_url=url
                )
            ]
        )
        return

    # グラフ
    if text == "グラフ":
        if not create_graph(user_id):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("データなし")
            )
            return

        url = f"{BASE_URL}/graph/{user_id}.png"

        line_bot_api.reply_message(
            event.reply_token,
            ImageSendMessage(
                original_content_url=url,
                preview_image_url=url
            )
        )
        return

    # 手入力
    match = re.match(r'^(\d+)\s*(.+)$', text)

    if match:
        save_expense(user_id, int(match.group(1)), match.group(2))
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("登録OK👍")
        )
        return

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage("『1000 食費』で入力")
    )

# =====================================================
# OCR
# =====================================================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id

    try:
        msg = line_bot_api.get_message_content(event.message.id)
        img = msg.content

        text = detect_text(img)
        amount = extract_price(text)
        store = extract_store(text)

        if not amount:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("読み取れない")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TemplateSendMessage(
                alt_text="確認",
                template=ConfirmTemplate(
                    text=f"{store}\n{amount}円で登録？",
                    actions=[
                        MessageAction(label="はい", text=f"OK_{amount}"),
                        MessageAction(label="いいえ", text="キャンセル")
                    ]
                )
            )
        )

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("OCRエラー")
        )

# =====================================================
# RUN
# =====================================================

@app.route("/")
def home():
    return "OK", 200

@app.route("/graph/<user_id>.png")
def graph(user_id):
    return send_file(f"graph_{user_id}.png", mimetype="image/png")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))