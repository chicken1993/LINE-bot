# ======================
# Flask / LINE Bot 家計簿（OCR完全統合版）
# ======================

from flask import Flask, request, Response
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
import os, re, io, traceback, json

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# ===== OCR（Google Vision）=====
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
# 初期化
# ======================
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL, sslmode="require")

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

# ======================
# DB
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

# ======================
# OCR関数
# ======================
def detect_text_from_image(image_content):
    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_content)
    response = client.text_detection(image=image)
    texts = response.text_annotations

    if texts:
        return texts[0].description
    return ""

def extract_max_price(text):
    numbers = re.findall(r'\d{2,6}', text)
    if not numbers:
        return None
    return max(map(int, numbers))

# ======================
# LINE
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
# テキスト処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    # YESボタン押された時
    if text.startswith("OK_"):
        parts = text.split("_")
        amount = int(parts[1])
        save_expense(user_id, amount, "レシート")
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"{amount}円 登録したよ👍")
        )
        return

    # 通常入力
    match = re.match(r'^(\d+)\s*(.+)$', text)
    if match:
        amount = int(match.group(1))
        category = match.group(2)
        save_expense(user_id, amount, category)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(f"{category}:{amount}円 登録OK")
        )
        return

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage("入力：1000 食費 または レシート送信")
    )

# ======================
# 画像処理（ここが神機能）
# ======================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id

    try:
        message_content = line_bot_api.get_message_content(event.message.id)
        image_bytes = message_content.content

        # OCR実行
        text = detect_text_from_image(image_bytes)

        # 金額抽出
        amount = extract_max_price(text)

        if not amount:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("金額読み取れなかった😢")
            )
            return

        # 確認ボタン
        flex = TemplateSendMessage(
            alt_text="確認",
            template=ConfirmTemplate(
                text=f"{amount}円で登録する？",
                actions=[
                    MessageAction(label="はい", text=f"OK_{amount}"),
                    MessageAction(label="いいえ", text="キャンセル")
                ]
            )
        )

        line_bot_api.reply_message(event.reply_token, flex)

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("OCRエラー")
        )

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))