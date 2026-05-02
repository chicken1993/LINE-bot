# ======================
# Flask（サーバー）
# ======================
from flask import Flask, request, Response

# ======================
# LINE Bot SDK
# ======================
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageSendMessage,
    TemplateSendMessage, ButtonsTemplate,
    MessageAction
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
import matplotlib.font_manager as fm

# 日本語フォント（同じフォルダに置く）
font_path = "ipaexg.ttf"
font_prop = fm.FontProperties(fname=font_path)

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

valid_categories = ["食費", "交通費", "娯楽", "その他"]

# ======================
# DB接続
# ======================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

# ======================
# DB初期化
# ======================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # 支出
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            amount INTEGER,
            category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 状態
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_states (
            user_id TEXT PRIMARY KEY,
            step TEXT,
            category TEXT
        )
    """)

    # 🔥 予算テーブル追加
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

# ======================
# 状態管理
# ======================
def set_state(user_id, step, category=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_states (user_id, step, category)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET step=%s, category=%s
    """, (user_id, step, category, step, category))
    conn.commit()
    cur.close()
    conn.close()

def get_state(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT step, category FROM user_states WHERE user_id=%s", (user_id,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    return r

def clear_state(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM user_states WHERE user_id=%s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

# ======================
# DB処理
# ======================
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

# 🔥 予算保存
def set_budget(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO budgets (user_id, monthly_budget)
        VALUES (%s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET monthly_budget=%s
    """, (user_id, amount, amount))

    conn.commit()
    cur.close()
    conn.close()

# 🔥 予算取得
def get_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT monthly_budget FROM budgets WHERE user_id=%s", (user_id,))
    r = cur.fetchone()

    cur.close()
    conn.close()
    return r[0] if r else None

# ======================
# UI
# ======================
def send_category_menu(reply_token):
    message = TemplateSendMessage(
        alt_text="カテゴリ選択",
        template=ButtonsTemplate(
            title="カテゴリ選択",
            text="どのカテゴリ？",
            actions=[
                MessageAction(label="🍜 食費", text="食費"),
                MessageAction(label="🚃 交通費", text="交通費"),
                MessageAction(label="🎮 娯楽", text="娯楽"),
                MessageAction(label="📦 その他", text="その他"),
            ]
        )
    )
    line_bot_api.reply_message(reply_token, message)

# ======================
# グラフ
# ======================
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
    plt.pie(
        values,
        labels=labels,
        autopct="%1.1f%%",
        startangle=90,
        textprops={"fontproperties": font_prop}
    )
    plt.axis('equal')

    img = io.BytesIO()
    plt.savefig(img, format="png", bbox_inches="tight")
    plt.close()
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")

# ======================
# Webhook
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
# メイン処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:
        state = get_state(user_id)

        # ======================
        # 今月（🔥ここ進化）
        # ======================
        if text == "今月":
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            if budget:
                remain = budget - total
                msg = f"今月：{total}円\n残り：{remain}円"
            else:
                msg = f"今月：{total}円\n※予算未設定"

            message = TemplateSendMessage(
                alt_text="今月情報",
                template=ButtonsTemplate(
                    title="今月の状況",
                    text=msg,
                    actions=[
                        MessageAction(label="💰 予算を設定", text="予算設定"),
                        MessageAction(label="❌ 閉じる", text="キャンセル")
                    ]
                )
            )

            line_bot_api.reply_message(event.reply_token, message)
            return

        # ======================
        # 予算設定スタート
        # ======================
        if text == "予算設定":
            set_state(user_id, "budget")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("予算いくら？（例：50000）")
            )
            return

        # ======================
        # 状態処理
        # ======================
        if state:
            step, category = state

            # 予算入力
            if step == "budget":
                match = re.search(r'(\d+)', text)
                if match:
                    amount = int(match.group(1))
                    set_budget(user_id, amount)
                    clear_state(user_id)

                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(f"予算 {amount}円 に設定したよ✅")
                    )
                else:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage("数字で入力してね")
                    )
                return

        # ======================
        # 家計簿
        # ======================
        if text == "家計簿":
            set_state(user_id, "category")
            send_category_menu(event.reply_token)
            return

        # ======================
        # グラフ
        # ======================
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

        # fallback
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("『家計簿』と送ると入力できるよ")
        )

    except:
        print(traceback.format_exc())

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))