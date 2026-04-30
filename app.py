# ======================
# Flask
# ======================
from flask import Flask, request, Response

# ======================
# LINE Bot SDK
# ======================
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageSendMessage,
    RichMenu, RichMenuArea, MessageAction
)

# ======================
# 基本
# ======================
import os
import re
import io
import time
from dotenv import load_dotenv

# ======================
# DB
# ======================
import psycopg2

# ======================
# グラフ
# ======================
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ======================
# 初期化
# ======================
load_dotenv()
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


# =========================================================
# DB接続
# =========================================================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")


# =========================================================
# 初期テーブル
# =========================================================
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
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            is_first BOOLEAN DEFAULT TRUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()


# =========================================================
# 初回判定
# =========================================================
def is_first_user(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT is_first FROM users WHERE user_id=%s", (user_id,))
    r = cur.fetchone()

    cur.close()
    conn.close()

    return True if r is None else r[0]


def mark_user_init(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_id, is_first)
        VALUES (%s, FALSE)
        ON CONFLICT (user_id) DO UPDATE SET is_first = FALSE
    """, (user_id,))

    conn.commit()
    cur.close()
    conn.close()


# =========================================================
# カテゴリ分類（強化版）
# =========================================================
def classify_category(text):

    rules = {
        "食費": ["コンビニ", "ご飯", "ランチ", "カフェ", "ラーメン", "外食"],
        "交通費": ["電車", "バス", "タクシー", "ガソリン"],
        "娯楽": ["ゲーム", "映画", "カラオケ", "遊び"],
        "日用品": ["シャンプー", "洗剤", "日用品"],
        "通信費": ["スマホ", "WiFi", "通信"]
    }

    for category, keywords in rules.items():
        for kw in keywords:
            if kw in text:
                return category

    return "その他"


# =========================================================
# 支出保存
# =========================================================
def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s,%s,%s)",
        (user_id, amount, category)
    )

    conn.commit()
    cur.close()
    conn.close()


# =========================================================
# 今月合計
# =========================================================
def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)=DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    r = cur.fetchone()[0]

    cur.close()
    conn.close()

    return r


# =========================================================
# リッチメニュー
# =========================================================
def create_rich_menu():
    return RichMenu(
        size={"width": 2500, "height": 1686},
        selected=True,
        name="家計簿UI",
        chat_bar_text="メニュー",
        areas=[
            RichMenuArea(bounds={"x":0,"y":0,"width":1250,"height":843},
                         action=MessageAction(label="入力", text="家計簿")),
            RichMenuArea(bounds={"x":1250,"y":0,"width":1250,"height":843},
                         action=MessageAction(label="グラフ", text="グラフ")),
            RichMenuArea(bounds={"x":0,"y":843,"width":1250,"height":843},
                         action=MessageAction(label="今月", text="今月")),
            RichMenuArea(bounds={"x":1250,"y":843,"width":1250,"height":843},
                         action=MessageAction(label="メニュー", text="メニュー"))
        ]
    )


def set_rich_menu_image(rich_menu_id):
    try:
        path = os.path.join(os.path.dirname(__file__), "menu.jpg")

        with open(path, "rb") as f:
            line_bot_api.set_rich_menu_image(
                rich_menu_id,
                "image/jpeg",
                f
            )
    except Exception as e:
        print("image set error:", e)


def setup_rich_menu_once():
    try:
        existing = get_rich_menu_id()
        if existing:
            return existing

        rich_menu = create_rich_menu()
        rich_menu_id = line_bot_api.create_rich_menu(rich_menu)

        time.sleep(1)
        set_rich_menu_image(rich_menu_id)

        save_rich_menu_id(rich_menu_id)

        return rich_menu_id

    except Exception as e:
        print("setup rich menu error:", e)
        return None


def save_rich_menu_id(rich_menu_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO app_settings (key, value)
        VALUES ('rich_menu_id', %s)
        ON CONFLICT (key) DO UPDATE SET value = %s
    """, (rich_menu_id, rich_menu_id))

    conn.commit()
    cur.close()
    conn.close()


def get_rich_menu_id():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT value FROM app_settings WHERE key='rich_menu_id'")
    r = cur.fetchone()

    cur.close()
    conn.close()

    return r[0] if r else None


def set_user_rich_menu(user_id):
    try:
        rich_menu_id = setup_rich_menu_once()
        if rich_menu_id:
            line_bot_api.link_rich_menu_to_user(user_id, rich_menu_id)
    except Exception as e:
        print("link error:", e)


# =========================================================
# グラフ生成（LINE対応100%）
# =========================================================
@app.route("/chart/<user_id>")
def chart(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
        ORDER BY SUM(amount) DESC
    """, (user_id,))

    data = cur.fetchall()

    cur.close()
    conn.close()

    if not data:
        return "no data"

    labels = [d[0] for d in data]
    values = [d[1] for d in data]

    plt.figure(figsize=(6,6))
    plt.pie(values, labels=labels, autopct="%1.1f%%")
    plt.title("支出グラフ")

    img = io.BytesIO()
    plt.savefig(img, format="png")
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")


# =========================================================
# Flaskルート
# =========================================================
@app.route("/")
def home():
    return "OK"


@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Webhook error:", e)

    return "OK"


# =========================================================
# メイン処理
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:

        if is_first_user(user_id):
            mark_user_init(user_id)
            set_user_rich_menu(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("🎉ようこそ！")
            )
            return

        if text == "今月":
            total = get_month_total(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"今月：{total}円")
            )
            return

        if text == "グラフ":
            url = f"https://line-bot-1-gizk.onrender.com/chart/{user_id}"

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        match = re.search(r'(.+?)[にで]?(\d+)', text)

        if match:
            category = classify_category(text)
            amount = int(match.group(2))

            save_expense(user_id, amount, category)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("記録OK")
            )
            return

    except Exception as e:
        print("error:", e)


# =========================================================
# 起動
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))