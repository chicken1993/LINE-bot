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
    FlexSendMessage
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

line_bot_api = LineBotApi(os.getenv("CHANNEL_ACCESS_TOKEN"))
handler = WebhookHandler(os.getenv("CHANNEL_SECRET"))
BASE_URL = os.getenv("BASE_URL")

# ======================
# DB
# ======================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

# ======================
# 初期化DB
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
    conn.close()

init_db()

# ======================
# state
# ======================
def set_state(user_id, step, category=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_states (user_id, step, category)
        VALUES (%s,%s,%s)
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
# expense
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

def get_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT monthly_budget FROM budgets WHERE user_id=%s", (user_id,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    return r[0] if r else None

def get_recent(user_id, limit=10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, category, amount
        FROM expenses
        WHERE user_id=%s
        ORDER BY created_at DESC
        LIMIT %s
    """, (user_id, limit))
    r = cur.fetchall()
    cur.close()
    conn.close()
    return r

# ======================
# 履歴UI
# ======================
def send_history(reply_token, data):
    contents = []

    for d in data:
        contents.append({
            "type": "button",
            "action": {
                "type": "message",
                "label": f"{d[1]} {d[2]}円",
                "text": f"del_{d[0]}"
            }
        })

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "履歴削除", "weight": "bold"}
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": contents
        }
    }

    line_bot_api.reply_message(
        reply_token,
        FlexSendMessage(alt_text="履歴削除", contents=bubble)
    )

# ======================
# webhook
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
# メイン
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip().replace(" ", "").replace("　", "")
    user_id = event.source.user_id
    state = get_state(user_id)

    try:

        # ======================
        # 今月（UI安定版）
        # ======================
        if text in ["今月", "今月合計"]:
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            msg = f"使用：{total}円"

            if budget:
                msg += f"\n予算：{budget}円"
                msg += f"\n残り：{budget - total}円"
            else:
                msg += "\n予算未設定"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )
            return

        # ======================
        # 支出入力（完全修正）
        # ======================
        if text in ["家計簿", "支出入力"]:
            set_state(user_id, "category")

            line_bot_api.reply_message(
                event.reply_token,
                TemplateSendMessage(
                    alt_text="カテゴリ",
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
            )
            return

        if state and state[0] == "category":
            set_state(user_id, "amount", text)
            line_bot_api.reply_message(event.reply_token, TextSendMessage("金額入力して"))
            return

        if state and state[0] == "amount":
            amount = int(re.search(r'\d+', text).group())
            category = state[1]

            save_expense(user_id, amount, category)

            clear_state(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("登録OK")
            )
            return

        # ======================
        # グラフ（単機能化）
        # ======================
        if text in ["グラフ", "グラフ📊"]:
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        # ======================
        # 削除メニュー（修正版）
        # ======================
        if text in ["削除", "取り消し"]:
            line_bot_api.reply_message(
                event.reply_token,
                TemplateSendMessage(
                    alt_text="削除",
                    template=ButtonsTemplate(
                        title="削除",
                        text="選択",
                        actions=[
                            MessageAction(label="直近削除", text="直近削除"),
                            MessageAction(label="履歴削除", text="履歴削除"),
                            MessageAction(label="今月削除", text="今月削除")
                        ]
                    )
                )
            )
            return

        # 直近削除
        if text == "直近削除":
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

        # 履歴
        if text == "履歴削除":
            data = get_recent(user_id, 10)
            send_history(event.reply_token, data)
            return

        # 個別削除
        if text.startswith("del_"):
            did = text.replace("del_", "")

            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM expenses WHERE id=%s", (did,))
            conn.commit()
            cur.close()
            conn.close()

            line_bot_api.reply_message(event.reply_token, TextSendMessage("削除完了"))
            return

        # 今月削除
        if text == "今月削除":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM expenses
                WHERE user_id=%s
                AND DATE_TRUNC('month', created_at)=DATE_TRUNC('month', CURRENT_DATE)
            """, (user_id,))
            conn.commit()
            cur.close()
            conn.close()

            line_bot_api.reply_message(event.reply_token, TextSendMessage("今月削除OK"))
            return

        line_bot_api.reply_message(event.reply_token, TextSendMessage("メニュー使ってね"))

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))