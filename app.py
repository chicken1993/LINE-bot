# ======================
# Flask
# ======================
from flask import Flask, request, send_file

# ======================
# LINE Bot SDK
# ======================
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageSendMessage, FlexSendMessage
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
from datetime import datetime

# ======================
# グラフ
# ======================
import matplotlib
matplotlib.use('Agg')

import matplotlib.pyplot as plt
import io

load_dotenv()

app = Flask(__name__)

# ======================
# LINE認証
# ======================
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# DB接続
# ======================
def get_conn():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        sslmode="require"
    )

# ======================
# 初期化（expenses）
# ======================
def init_db():
    try:
        conn = get_conn()
        cur = conn.cursor()

        # 支出テーブル
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                amount INTEGER,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ユーザーテーブル（初回判定）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                is_first BOOLEAN DEFAULT TRUE
            )
        """)

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print("DB init error:", e)

init_db()

# ======================
# 初回チェック①
# ======================
def is_first_user(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT is_first FROM users WHERE user_id=%s", (user_id,))
    result = cur.fetchone()

    cur.close()
    conn.close()

    if result is None:
        return True
    return result[0]

# ======================
# 初回登録②
# ======================
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

# ======================
# 初回メッセージ④
# ======================
def send_first_message(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🎉ようこそ！\nまずは『メニュー』って送ってね💰"
        )
    )

# ======================
# 保存
# ======================
def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s, %s, %s)",
        (user_id, amount, category)
    )

    conn.commit()
    cur.close()
    conn.close()

# ======================
# 今月合計
# ======================
def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(amount)
        FROM expenses
        WHERE user_id = %s
        AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return total or 0

# ======================
# 円グラフ
# ======================
def create_pie_chart(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
    """, (user_id,))

    data = cur.fetchall()

    cur.close()
    conn.close()

    if not data:
        return None

    labels = [row[0] for row in data]
    sizes = [row[1] for row in data]

    plt.figure(figsize=(6,6))
    plt.pie(sizes, autopct='%1.1f%%', startangle=90)
    plt.legend(labels, loc="best")
    plt.title("支出グラフ")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close()
    buf.seek(0)

    return buf

# ======================
# グラフURL
# ======================
@app.route("/chart/<user_id>")
def chart(user_id):
    img = create_pie_chart(user_id)

    if img:
        return send_file(img, mimetype='image/png')
    return "no data"

# ======================
# メニュー
# ======================
def send_menu(event):

    flex_message = FlexSendMessage(
        alt_text="家計簿メニュー",
        contents={
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "💰 家計簿メニュー",
                        "weight": "bold",
                        "size": "lg"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "action": {"type": "message", "label": "💰入力", "text": "家計簿"}
                    },
                    {
                        "type": "button",
                        "action": {"type": "message", "label": "📊グラフ", "text": "グラフ"}
                    },
                    {
                        "type": "button",
                        "action": {"type": "message", "label": "📅今月", "text": "今月"}
                    }
                ]
            }
        }
    )

    line_bot_api.reply_message(event.reply_token, flex_message)

# ======================
# LINE受信
# ======================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    handler.handle(body, signature)
    return 'OK', 200

# ======================
# メイン処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:

        # ======================
        # ① 初回判定（追加）
        # ======================
        if is_first_user(user_id):
            mark_user_init(user_id)
            send_first_message(event)
            return

        # ======================
        # メニュー
        # ======================
        if text == "メニュー":
            send_menu(event)
            return

        # 今月
        if text == "今月":
            total = get_month_total(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"今月の支出：{total}円")
            )
            return

        # グラフ
        if text == "グラフ":
            image_url = f"https://line-bot-ujj2.onrender.com/chart/{user_id}"

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=image_url,
                    preview_image_url=image_url
                )
            )
            return

        # 支出入力
        match = re.search(r'(.+?)[にで]?(\d+)', text)

        if match:
            category = match.group(1)
            price = int(match.group(2))

            save_expense(user_id, price, category)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"{category} {price}円記録")
            )
            return

        # デフォルト
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="『メニュー』って送ってね")
        )

    except Exception as e:
        print("handler error:", e)

# ======================
# 起動
# ======================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)