# ======================
# Flask / LINE Bot 家計簿
# ======================

from flask import Flask, request, Response
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageSendMessage, TemplateSendMessage,
    ButtonsTemplate, MessageAction, FlexSendMessage
)

import os, re, io, traceback
from dotenv import load_dotenv

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# ======================
# 初期化
# ======================
load_dotenv()
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# DBプール
# ======================
pool = SimpleConnectionPool(1, 10, dsn=DATABASE_URL, sslmode="require")

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

# ======================
# フォント
# ======================
try:
    font_prop = fm.FontProperties(fname="ipaexg.ttf")
except:
    font_prop = None

# ======================
# DB初期化
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
    put_conn(conn)

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
    put_conn(conn)

def get_state(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT step, category FROM user_states WHERE user_id=%s", (user_id,))
    r = cur.fetchone()

    cur.close()
    put_conn(conn)

    return r

def clear_state(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM user_states WHERE user_id=%s", (user_id,))

    conn.commit()
    cur.close()
    put_conn(conn)

# ======================
# DB操作
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
    put_conn(conn)

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
    put_conn(conn)

    return total

def get_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT monthly_budget FROM budgets WHERE user_id=%s", (user_id,))
    r = cur.fetchone()

    cur.close()
    put_conn(conn)

    return r[0] if r else None

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

@app.route("/")
def home():
    return "OK"

# ======================
# メイン処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip().replace(" ", "").replace("　", "")
    user_id = event.source.user_id
    state = get_state(user_id)

    try:

        # ===== 今月（★追加済み）=====
        if text in ["今月", "今月合計"]:

            total = get_month_total(user_id)
            budget = get_budget(user_id)

            if budget:
                usage_rate = int((total / budget) * 100) if budget > 0 else 0
                msg = (
                    f"今月合計：{total}円\n"
                    f"上限：{budget}円\n"
                    f"使用金額：{total}円\n"
                    f"使用率：{usage_rate}%"
                )
            else:
                msg = (
                    f"今月合計：{total}円\n"
                    f"上限：未設定\n"
                    f"使用金額：{total}円"
                )

            message = TemplateSendMessage(
                alt_text="今月メニュー",
                template=ButtonsTemplate(
                    title="今月",
                    text=msg,
                    actions=[
                        MessageAction(label="上限金額変更", text="上限金額変更"),
                        MessageAction(label="金額変更なし", text="金額変更なし")
                    ]
                )
            )

            line_bot_api.reply_message(event.reply_token, message)
            return

        # ===== 上限金額変更 =====
        if text == "上限金額変更":
            set_state(user_id, "budget")

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("月の上限金額を入力してね（例：50000）")
            )
            return

        # ===== budget設定 =====
        if state and state[0] == "budget":
            match = re.search(r'(\d+)', text)
            if match:
                budget = int(match.group(1))

                conn = get_conn()
                cur = conn.cursor()

                cur.execute("""
                    INSERT INTO budgets (user_id, monthly_budget)
                    VALUES (%s, %s)
                    ON CONFLICT (user_id)
                    DO UPDATE SET monthly_budget=%s
                """, (user_id, budget, budget))

                conn.commit()
                cur.close()
                put_conn(conn)

                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"上限を{budget}円に設定したよ")
                )
                return

        # ===== ここから下は既存処理（省略せずそのまま残してOK）=====
        # ※あなたの元コードの「カテゴリ・金額・削除系」はここにそのまま残す

        # ===== カテゴリ =====
        if state and state[0] == "category":
            set_state(user_id, "amount", text)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(f"{text}いくら？"))
            return

        # ===== 金額 =====
        if state and state[0] == "amount":
            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))
                category = state[1]

                save_expense(user_id, amount, category)
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{category}:{amount}円 登録OK")
                )
                return

        line_bot_api.reply_message(event.reply_token, TextSendMessage("メニューから選んで"))

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))