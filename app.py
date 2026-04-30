# ======================
# Flask（Webサーバー）
# ======================
from flask import Flask, request, send_file

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
# 環境変数
# ======================
import os
from dotenv import load_dotenv

# ======================
# DB
# ======================
import re
import psycopg2

# ======================
# グラフ
# ======================
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io

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
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        sslmode="require"
    )


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
# ユーザー初回判定
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
# リッチメニュー作成
# =========================================================
def create_rich_menu():
    return RichMenu(
        size={"width": 2500, "height": 1686},
        selected=True,
        name="家計簿UI",
        chat_bar_text="メニュー",
        areas=[
            RichMenuArea(bounds={"x":0,"y":0,"w":1250,"h":843},
                         action=MessageAction(label="入力", text="家計簿")),
            RichMenuArea(bounds={"x":1250,"y":0,"w":1250,"h":843},
                         action=MessageAction(label="グラフ", text="グラフ")),
            RichMenuArea(bounds={"x":0,"y":843,"w":1250,"h":843},
                         action=MessageAction(label="今月", text="今月")),
            RichMenuArea(bounds={"x":1250,"y":843,"w":1250,"h":843},
                         action=MessageAction(label="メニュー", text="メニュー"))
        ]
    )


# =========================================================
# ⭐リッチメニューID管理
# =========================================================
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


# =========================================================
# ⭐① 画像設定（追加）
# =========================================================
def set_rich_menu_image(rich_menu_id):
    with open("menu.jpg", "rb") as f:
        line_bot_api.set_rich_menu_image(
            rich_menu_id,
            "image/jpeg",
            f
        )


# =========================================================
# ⭐② 完全版リッチメニュー作成
# =========================================================
def setup_rich_menu_once():
    try:
        existing = get_rich_menu_id()

        if existing:
            return existing

        rich_menu = create_rich_menu()
        rich_menu_id = line_bot_api.create_rich_menu(rich_menu)

        set_rich_menu_image(rich_menu_id)  # ★重要

        save_rich_menu_id(rich_menu_id)

        return rich_menu_id

    except Exception as e:
        print("setup rich menu error:", e)
        return None


# =========================================================
# ユーザー紐付け
# =========================================================
def set_user_rich_menu(user_id):
    try:
        rich_menu_id = setup_rich_menu_once()

        if rich_menu_id:
            line_bot_api.link_rich_menu_to_user(user_id, rich_menu_id)

    except Exception as e:
        print("link error:", e)


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
# 今月
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
# メッセージ
# =========================================================
def send_first_message(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage("🎉ようこそ！メニューから使えるよ")
    )


# =========================================================
# webhook
# =========================================================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers["X-Line-Signature"]

    handler.handle(body, signature)
    return "OK"


# =========================================================
# メイン
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:

        # 初回
        if is_first_user(user_id):
            mark_user_init(user_id)
            set_user_rich_menu(user_id)
            send_first_message(event)
            return

        if text == "今月":
            total = get_month_total(user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"今月：{total}円")
            )
            return

        if text == "グラフ":
            url = f"https://line-bot-ujj2.onrender.com/chart/{user_id}"

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        match = re.search(r'(.+?)[にで]?(\d+)', text)

        if match:
            save_expense(user_id, int(match.group(2)), match.group(1))

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("記録OK")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("メニュー使ってね")
        )

    except Exception as e:
        print(e)


# =========================================================
# 起動
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))