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

# 🔥 日本語回避（文字化け防止）
plt.rcParams['font.family'] = 'DejaVu Sans'

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

# 内部キー（ズレ防止）
CMD_INPUT = "START_INPUT"
CMD_GRAPH = "SHOW_GRAPH"
CMD_MONTH = "SHOW_MONTH"
CMD_DELETE = "DELETE_LAST"

valid_categories = ["食費", "交通費", "光熱費", "通信費", "娯楽", "その他"]

# =========================================================
# DB
# =========================================================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

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

def delete_last(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM expenses
        WHERE id = (
            SELECT id FROM expenses
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT 1
        )
    """, (user_id,))

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
# UI
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
# 🔥 リッチメニュー（ズレ防止版）
# =========================================================
def create_rich_menu():
    return RichMenu(
        size=RichMenuSize(width=2500, height=1686),
        selected=True,
        name="menu",
        chat_bar_text="メニュー",
        areas=[
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=1250, height=843),
                action=MessageAction(label="支出入力", text=CMD_INPUT)
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=1250, y=0, width=1250, height=843),
                action=MessageAction(label="グラフ", text=CMD_GRAPH)
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=843, width=1250, height=843),
                action=MessageAction(label="今月", text=CMD_MONTH)
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=1250, y=843, width=1250, height=843),
                action=MessageAction(label="取り消し", text=CMD_DELETE)
            ),
        ]
    )

def setup_rich_menu():
    try:
        rich_menu = create_rich_menu()
        rich_menu_id = line_bot_api.create_rich_menu(rich_menu)

        with open("menu.jpg", "rb") as f:
            line_bot_api.set_rich_menu_image(rich_menu_id, "image/jpeg", f)

        line_bot_api.set_default_rich_menu(rich_menu_id)
        return rich_menu_id
    except:
        print(traceback.format_exc())

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

    # 🔥 文字化け回避
    labels = [str(d[0]) for d in data]
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
# メイン処理
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:
        print("入力:", text)
        print("状態:", user_states.get(user_id))

        # ======================
        # コマンド
        # ======================
        if text == CMD_INPUT:
            user_states.pop(user_id, None)
            user_states[user_id] = {"step": "category"}
            send_category_menu(event.reply_token)
            return

        if text == CMD_GRAPH:
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        if text == CMD_MONTH:
            total = get_month_total(user_id)
            budget = get_budget(user_id)
            msg = f"今月：{total}円"
            if budget:
                msg += f"\n残り：{budget-total}円"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        if text == CMD_DELETE:
            delete_last(user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("削除OK")
            )
            return

        # ======================
        # 状態処理
        # ======================
        if user_id in user_states:

            if user_states[user_id]["step"] == "category":

                if text not in valid_categories:
                    send_category_menu(event.reply_token)
                    return

                user_states[user_id] = {
                    "step": "amount",
                    "category": text
                }

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{text}ですね！金額入力してね")
                )
                return

            if user_states[user_id]["step"] == "amount":

                match = re.search(r'(\d+)', text)

                if match:
                    amount = int(match.group(1))
                    category = user_states[user_id]["category"]

                    save_expense(user_id, amount, category)
                    user_states.pop(user_id, None)

                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage(f"{category}：{amount}円 登録完了")
                    )
                else:
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage("数字で入力してね")
                    )
                return

        # fallback
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("メニューから操作してね")
        )

    except:
        print(traceback.format_exc())

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))