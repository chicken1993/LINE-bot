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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS category_budgets (
            user_id TEXT,
            category TEXT,
            budget INTEGER,
            PRIMARY KEY (user_id, category)
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
# 支出
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

# ======================
# カテゴリ予算
# ======================
def get_category_budget(user_id, category):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT budget FROM category_budgets
        WHERE user_id=%s AND category=%s
    """, (user_id, category))
    r = cur.fetchone()
    cur.close()
    conn.close()
    return r[0] if r else None

def set_category_budget(user_id, category, amount):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO category_budgets (user_id, category, budget)
        VALUES (%s,%s,%s)
        ON CONFLICT (user_id, category)
        DO UPDATE SET budget=%s
    """, (user_id, category, amount, amount))
    conn.commit()
    cur.close()
    conn.close()

# ======================
# 80%アラート
# ======================
def check_budget_alert(user_id, category):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s AND category=%s
        AND DATE_TRUNC('month', created_at)=DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id, category))

    used = cur.fetchone()[0]
    cur.close()
    conn.close()

    budget = get_category_budget(user_id, category)
    if not budget:
        return None

    ratio = used / budget

    if ratio >= 1:
        return f"🚨 {category}予算超過"
    if ratio >= 0.8:
        return f"⚠️ {category}予算80%超え"

    return None

# ======================
# Flex UI
# ======================
def build_dashboard(total, budget):

    percent = int((total / budget) * 100) if budget else 0

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "今月ダッシュボード", "weight": "bold", "size": "lg"},
                {"type": "text", "text": f"使用: {total}円"},
                {"type": "text", "text": f"予算: {budget if budget else '未設定'}円"},
                {"type": "text", "text": f"進捗: {percent}%"}
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "button", "action": {"type": "message", "label": "支出入力", "text": "家計簿"}},
                {"type": "button", "action": {"type": "message", "label": "グラフ", "text": "グラフ"}}
            ]
        }
    }

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
# メイン
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip().replace(" ", "").replace("　", "")
    user_id = event.source.user_id
    state = get_state(user_id)

    try:

        # ======================
        # 今月（Flex）
        # ======================
        if text in ["今月", "今月合計"]:
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            bubble = build_dashboard(total, budget)

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="今月", contents=bubble)
            )
            return

        # ======================
        # 予算
        # ======================
        if text == "予算設定":
            set_state(user_id, "budget_input")
            line_bot_api.reply_message(event.reply_token, TextSendMessage("予算いくら？"))
            return

        if state and state[0] == "budget_input":
            amount = int(re.search(r'\d+', text).group())
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO budgets (user_id, monthly_budget)
                VALUES (%s,%s)
                ON CONFLICT (user_id)
                DO UPDATE SET monthly_budget=%s
            """, (user_id, amount, amount))
            conn.commit()
            cur.close()
            conn.close()

            clear_state(user_id)
            line_bot_api.reply_message(event.reply_token, TextSendMessage("予算更新OK"))
            return

        if text == "予算削除":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM budgets WHERE user_id=%s", (user_id,))
            conn.commit()
            cur.close()
            conn.close()

            line_bot_api.reply_message(event.reply_token, TextSendMessage("予算削除OK"))
            return

        # ======================
        # 支出入力
        # ======================
        if text in ["家計簿", "支出入力"]:
            set_state(user_id, "category")
            line_bot_api.reply_message(event.reply_token, TextSendMessage("食費・交通費・娯楽・その他"))
            return

        if state and state[0] == "category":
            set_state(user_id, "amount", text)
            line_bot_api.reply_message(event.reply_token, TextSendMessage("金額は？"))
            return

        if state and state[0] == "amount":
            amount = int(re.search(r'\d+', text).group())
            category = state[1]

            save_expense(user_id, amount, category)

            alert = check_budget_alert(user_id, category)
            if alert:
                line_bot_api.push_message(user_id, TextSendMessage(alert))

            clear_state(user_id)

            line_bot_api.reply_message(event.reply_token, TextSendMessage("登録OK"))
            return

        # ======================
        # グラフ
        # ======================
        if text in ["グラフ", "グラフ📊"]:
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(event.reply_token, ImageSendMessage(url, url))
            return

        line_bot_api.reply_message(event.reply_token, TextSendMessage("メニュー使ってね"))

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))