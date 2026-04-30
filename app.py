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
    ImageSendMessage
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
matplotlib.use('Agg')  # ★重要（Render対策）

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
# 初期化（安全版）
# ======================
def init_db():
    try:
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
        conn.close()

    except Exception as e:
        print("DB init error:", e)

# ★起動時に実行（落ちてもOK）
init_db()

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
# グラフ作成
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

    plt.figure()
    plt.pie(sizes, labels=labels, autopct='%1.1f%%')

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close()  # ★重要
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
        # グラフ
        # ======================
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

        # ======================
        # 支出入力
        # ======================
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

        # ======================
        # デフォルト
        # ======================
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="例：ラーメン900 / グラフ")
        )

    except Exception as e:
        print(e)

# ======================
# Render起動設定
# ======================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)