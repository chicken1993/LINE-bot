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
    RichMenu, RichMenuArea, RichMenuBounds, MessageAction, RichMenuSize,
    TemplateSendMessage, ButtonsTemplate
)

# ======================
# 基本
# ======================
import os
import re
import io
import traceback
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
BASE_URL = os.getenv("BASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# 状態管理
# ======================
user_states = {}

# =========================================================
# DB接続
# =========================================================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

# =========================================================
# DB初期化
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
        CREATE TABLE IF NOT EXISTS budgets (
            user_id TEXT PRIMARY KEY,
            monthly_budget INTEGER
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# =========================================================
# DB処理
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

def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)=DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    return total

def set_budget(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO budgets (user_id, monthly_budget)
        VALUES (%s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET monthly_budget = %s
    """, (user_id, amount, amount))

    conn.commit()
    cur.close()
    conn.close()

def get_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT monthly_budget FROM budgets WHERE user_id=%s", (user_id,))
    r = cur.fetchone()

    cur.close()
    conn.close()
    return r[0] if r else None

# =========================================================
# カテゴリUI
# =========================================================
def send_category_menu(reply_token):
    message = TemplateSendMessage(
        alt_text="カテゴリ選択",
        template=ButtonsTemplate(
            title="カテゴリ選択",
            text="どのカテゴリ？",
            actions=[
                MessageAction(label="🍜 食費", text="食費"),
                MessageAction(label="🚃 交通費", text="交通費"),
                MessageAction(label="💡 光熱費", text="光熱費"),
                MessageAction(label="📱 通信費", text="通信費"),
                MessageAction(label="🎮 娯楽", text="娯楽"),
                MessageAction(label="📦 その他", text="その他"),
            ]
        )
    )
    line_bot_api.reply_message(reply_token, message)

# =========================================================
# グラフ
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
    """, (user_id,))

    data = cur.fetchall()
    cur.close()
    conn.close()

    if not data:
        return Response("no data", status=404)

    labels = [d[0] for d in data]
    values = [d[1] for d in data]

    plt.figure(figsize=(6,6))
    plt.pie(values, labels=labels, autopct="%1.1f%%")

    img = io.BytesIO()
    plt.savefig(img, format="png")
    plt.close()
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")

# =========================================================
# Webhook
# =========================================================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, signature)
    except:
        print(traceback.format_exc())

    return "OK"

# =========================================================
# メイン処理（🔥完全版）
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:
        # ======================
        # 🔥 キャンセル（最優先）
        # ======================
        if text == "キャンセル":
            user_states.pop(user_id, None)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("入力をキャンセルしました")
            )
            return

        # ======================
        # 🔥 コマンド（状態より優先）
        # ======================
        if text == "家計簿":
            user_states[user_id] = {"step": "category"}
            send_category_menu(event.reply_token)
            return

        if text == "グラフ":
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=url,
                    preview_image_url=url
                )
            )
            return

        if text == "今月":
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            if budget:
                remain = budget - total
                msg = f"今月：{total}円\n残り：{remain}円"

                if remain < 0:
                    msg += "\n⚠️ 予算オーバー！"
                elif total > budget * 0.8:
                    msg += "\n⚠️ 使いすぎ注意"
            else:
                msg = f"今月：{total}円\n※予算未設定"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )
            return

        if text.startswith("予算"):
            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))
                set_budget(user_id, amount)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"予算：{amount}円に設定しました")
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("例：予算 50000")
                )
            return

        # ======================
        # 🔥 状態処理
        # ======================
        if user_id in user_states:

            # カテゴリ選択
            if user_states[user_id]["step"] == "category":
                category = text

                user_states[user_id] = {
                    "step": "amount",
                    "category": category
                }

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{category}ですね！金額入力してね")
                )
                return

            # 金額入力
            if user_states[user_id]["step"] == "amount":
                match = re.search(r'(\d+)', text)

                if match:
                    amount = int(match.group(1))
                    category = user_states[user_id]["category"]

                    save_expense(user_id, amount, category)
                    user_states.pop(user_id, None)

                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(f"{category}：{amount}円 登録完了✅")
                    )
                else:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage("数字で入力してね（例: 500）")
                    )
                return

        # ======================
        # 🔥 フォールバック
        # ======================
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("『家計簿』と送ると入力スタートできるよ！")
        )

    except:
        print(traceback.format_exc())

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))