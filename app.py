# ======================
# Flask / LINE Bot 家計簿（UI強化版）
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
    put_conn(conn)

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
    put_conn(conn)

    plt.figure(figsize=(6,6))

    if not data:
        plt.text(0.5, 0.5, "データなし", ha='center')
    else:
        labels = [d[0] for d in data]
        values = [d[1] for d in data]
        plt.pie(values, labels=labels, autopct="%1.1f%%",
                textprops={"fontproperties": font_prop} if font_prop else None)

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

        # ======================
        # 初回ガイド
        # ======================
        if text in ["はじめて", "start", "初回"]:
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(
                    alt_text="初回ガイド",
                    contents={
                        "type": "bubble",
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "spacing": "md",
                            "contents": [
                                {"type": "text","text": "👋 ようこそ","weight": "bold","size": "lg"},
                                {"type": "text","text": "👇下のメニューから操作してね","wrap": True},
                                {"type": "text","text": "迷ったら「ヘルプ」と送信","wrap": True,"color": "#666666"}
                            ]
                        }
                    }
                )
            )
            return

        # ======================
        # ヘルプ
        # ======================
        if text in ["ヘルプ", "使い方", "help"]:
            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(
                    alt_text="ヘルプ",
                    contents={
                        "type": "bubble",
                        "body": {
                            "type": "box",
                            "layout": "vertical",
                            "spacing": "md",
                            "contents": [
                                {"type": "text","text": "📘 使い方","weight": "bold","size": "xl"},
                                {"type": "text","text": "①家計簿\n②今月\n③予算\n④グラフ\n⑤削除","wrap": True},
                                {"type": "separator"},
                                {"type": "text","text": "迷ったらメニュー👇","color": "#666666"}
                            ]
                        }
                    }
                )
            )
            return

        # ===== グラフ =====
        if text in ["グラフ"]:
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                [
                    ImageSendMessage(url, url),
                    TextSendMessage("👇他の操作はメニューから")
                ]
            )
            return

        # ===== 今月 =====
        if text in ["今月"]:
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            msg = f"今月：{total}円"
            if budget:
                msg += f"\n予算：{budget}円\n残り：{budget-total}円"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg + "\n👇メニューから操作")
            )
            return

        # ===== 入力 =====
        if text in ["家計簿"]:
            set_state(user_id, "category")

            message = TemplateSendMessage(
                alt_text="カテゴリ",
                template=ButtonsTemplate(
                    title="支出入力",
                    text="カテゴリ選択",
                    actions=[
                        MessageAction(label="食費", text="食費"),
                        MessageAction(label="交通費", text="交通費"),
                        MessageAction(label="娯楽", text="娯楽"),
                        MessageAction(label="その他", text="その他"),
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, message)
            return

        if state and state[0] == "category":
            set_state(user_id, "amount", text)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(f"{text}いくら？"))
            return

        if state and state[0] == "amount":
            match = re.search(r'(\d+)', text)
            if match:
                save_expense(user_id, int(match.group(1)), state[1])
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("登録OK 👍\n👇メニューへ")
                )
                return

        # ===== fallback =====
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("👇下のメニューから選んでね")
        )

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))