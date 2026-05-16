# =====================================================
# LINE 家計簿Bot 完全版
# Flask + PostgreSQL + OCR + グラフ + 店舗 + 予算
# =====================================================

from flask import Flask, request, send_file

from linebot import LineBotApi, WebhookHandler
from linebot.models import *
from linebot.models.events import FollowEvent

import os
import re
import traceback
from collections import defaultdict

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

pool = SimpleConnectionPool(
    1, 10,
    dsn=DATABASE_URL,
    sslmode="require"
)

OCR_LIMIT = 20

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

        # ⭐予算テーブル追加
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
# 予算設定・取得
# =====================================================

def set_budget(user_id, amount):

    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
        INSERT INTO budgets (user_id, amount)
        VALUES (%s, %s)
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

        cur.execute("""
        SELECT amount FROM budgets WHERE user_id=%s
        """, (user_id,))

        row = cur.fetchone()
        return row[0] if row else None

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
        VALUES (%s, %s, %s)
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

# =====================================================
# OCR
# =====================================================

def get_monthly_ocr_count(user_id):

    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
        SELECT COUNT(*)
        FROM ocr_logs
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))

        return cur.fetchone()[0]

    finally:
        cur.close()
        put_conn(conn)


def save_ocr_log(user_id):

    conn = get_conn()
    try:
        cur = conn.cursor()

        cur.execute("""
        INSERT INTO ocr_logs (user_id)
        VALUES (%s)
        """, (user_id,))

        conn.commit()

    finally:
        cur.close()
        put_conn(conn)

# =====================================================
# OCR処理
# =====================================================

def detect_text(image_bytes):

    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)

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
            return line.strip()

    return "不明"

# =====================================================
# グラフ
# =====================================================

def create_graph(user_id):

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

        rows = cur.fetchall()

    finally:
        cur.close()
        put_conn(conn)

    if not rows:
        return None

    data = defaultdict(int)

    for c, a in rows:
        data[c] += a

    plt.figure(figsize=(6, 6))
    plt.pie(list(data.values()), labels=list(data.keys()), autopct="%1.1f%%")
    plt.title("今月の支出")

    path = f"graph_{user_id}.png"
    plt.savefig(path)
    plt.close()

    return path

# =====================================================
# callback（安定版）
# =====================================================

@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, signature)

    except Exception:
        print(traceback.format_exc())
        print("Webhook error")

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

    # ---------------------
    # 使い方
    # ---------------------
    if text == "使い方":

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                "使い方👇\n"
                "1000 食費 → 手入力\n"
                "グラフ → 可視化\n"
                "予算 50000 → 予算設定\n"
                "残り → 予算残額"
            )
        )
        return

    # ---------------------
    # 予算設定
    # ---------------------
    if text.startswith("予算"):

        amount = int(re.findall(r'\d+', text)[0])
        set_budget(user_id, amount)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"予算 {amount}円セットOK👍")
        )
        return

    # ---------------------
    # 残り
    # ---------------------
    if text == "残り":

        budget = get_budget(user_id)
        used = get_month_total(user_id)

        if not budget:
            msg = "予算まだ未設定"
        else:
            msg = f"残り {(budget - used)}円"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(msg)
        )
        return

    # ---------------------
    # OK登録（OCR）
    # ---------------------
    if text.startswith("OK_"):

        amount = int(text.split("_")[1])
        save_expense(user_id, amount, "レシート")

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("登録OK👍")
        )
        return

    # ---------------------
    # グラフ
    # ---------------------
    if text == "グラフ":

        path = create_graph(user_id)

        if not path:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("データなし")
            )
            return

        url = f"{BASE_URL}/graph/{user_id}.png"

        line_bot_api.reply_message(
            event.reply_token,
            ImageSendMessage(url, url)
        )
        return

    # ---------------------
    # 手入力
    # ---------------------
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

        if get_monthly_ocr_count(user_id) >= OCR_LIMIT:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("OCR上限😢")
            )
            return

        msg = line_bot_api.get_message_content(event.message.id)
        img = msg.content

        text = detect_text(img)

        amount = extract_price(text)
        store = extract_store(text)

        if not amount:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("読み取れない😢")
            )
            return

        save_ocr_log(user_id)

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
# 起動
# =====================================================

@app.route("/")
def home():
    return "OK", 200

@app.route("/graph/<user_id>.png")
def graph(user_id):
    return send_file(f"graph_{user_id}.png", mimetype="image/png")

# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))