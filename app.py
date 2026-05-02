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
    TemplateSendMessage, ButtonsTemplate,
    MessageAction,
    FlexSendMessage,
    PostbackEvent
)

# ======================
# 基本
# ======================
import os, re, io, traceback
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

font_prop = fm.FontProperties(fname="ipaexg.ttf")

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
# DB
# ======================
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
        CREATE TABLE IF NOT EXISTS user_states (
            user_id TEXT PRIMARY KEY,
            step TEXT,
            category TEXT
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
    """, (user_id,))
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    return total

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
        alt_text="支出入力",
        template=ButtonsTemplate(
            title="支出入力",
            text="カテゴリ選択",
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
    plt.pie(values, labels=labels, autopct="%1.1f%%",
            textprops={"fontproperties": font_prop})
    plt.axis('equal')

    img = io.BytesIO()
    plt.savefig(img, format="png")
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
        # ======================
        # グラフ
        # ======================
        if text == "グラフ":
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        # ======================
        # 今月合計UI
        # ======================
        if text == "今月合計":
            message = TemplateSendMessage(
                alt_text="今月",
                template=ButtonsTemplate(
                    title="今月メニュー",
                    text="選択してね",
                    actions=[
                        MessageAction(label="📊 使用額確認", text="使用額"),
                        MessageAction(label="💰 上限設定", text="予算設定"),
                        MessageAction(label="🗑 上限削除", text="予算削除"),
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, message)
            return

        if text == "使用額":
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            if budget:
                remain = budget - total
                msg = f"使用額：{total}円\n残り：{remain}円"
            else:
                msg = f"使用額：{total}円\n※上限なし"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        if text == "予算設定":
            set_state(user_id, "budget")
            line_bot_api.reply_message(event.reply_token, TextSendMessage("いくら？"))
            return

        if text == "予算削除":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM budgets WHERE user_id=%s", (user_id,))
            conn.commit()
            cur.close()
            conn.close()

            line_bot_api.reply_message(event.reply_token, TextSendMessage("削除OK"))
            return

        # ======================
        # 取り消し
        # ======================
        if text == "取り消し":
            message = TemplateSendMessage(
                alt_text="削除",
                template=ButtonsTemplate(
                    title="削除メニュー",
                    text="選択してね",
                    actions=[
                        MessageAction(label="🧹 直前削除", text="直前削除"),
                        MessageAction(label="📜 履歴削除", text="履歴削除"),
                        MessageAction(label="🔥 全削除", text="全削除"),
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, message)
            return

        if text == "直前削除":
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

            line_bot_api.reply_message(event.reply_token, TextSendMessage("削除OK"))
            return

        if text == "全削除":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
            conn.commit()
            cur.close()
            conn.close()

            line_bot_api.reply_message(event.reply_token, TextSendMessage("全部削除OK"))
            return

        # ======================
        # 支出入力
        # ======================
        if text == "支出入力":
            set_state(user_id, "category")
            send_category_menu(event.reply_token)
            return

        if text in valid_categories:
            set_state(user_id, "amount", text)
            line_bot_api.reply_message(event.reply_token, TextSendMessage("金額入力"))
            return

        state = get_state(user_id)

        if state and state[0] == "amount":
            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))
                category = state[1]

                save_expense(user_id, amount, category)
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{category} {amount}円 登録OK")
                )
                return

        if state and state[0] == "budget":
            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))

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

                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{amount}円に設定OK")
                )
                return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("メニューから選んでね")
        )

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("エラー出た😇")
        )

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))