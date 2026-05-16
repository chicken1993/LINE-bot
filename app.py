# =====================================================
# LINE 家計簿Bot 完全版（Render安定 + PDF + グラフ + 天気）
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
# matplotlib（日本語フォント固定）
# ======================
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ★フォント（ルート or fontsどちらでもOK）
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
# 天気
# ======================
def get_weather():
    try:
        url = "https://wttr.in/Tokyo?format=3"
        return requests.get(url, timeout=5).text
    except:
        return "天気取得失敗"


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
# 今月データ
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
# グラフ（日本語OK + yyyy/mm）
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

    path = f"report_{user_id}.pdf"
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
# OCR
# ======================
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


# ======================
# LINE callback
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
# FOLLOW
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
# TEXT
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
            TemplateSendMessage(
                alt_text="選択",
                template=ConfirmTemplate(
                    text="どっちで見る？",
                    actions=[
                        MessageAction(label="明細", text="明細表示"),
                        MessageAction(label="PDF", text="PDF出力")
                    ]
                )
            )
        )
        return

    if text == "明細表示":
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

        url = f"{BASE_URL}/{path}"
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"PDFできた👇\n{url}")
        )
        return

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
            ImageSendMessage(original_content_url=url, preview_image_url=url)
        )
        return

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


# ======================
# IMAGE OCR
# ======================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id

    try:
        msg = line_bot_api.get_message_content(event.message.id)
        img = msg.content

        text = detect_text(img)
        amount = extract_price(text)

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
                    text=f"{amount}円で登録？",
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
    return send_file(f"report_{user_id}.pdf", mimetype="application/pdf")


# ======================
# RUN
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))