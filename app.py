# ======================
# Flask / LINE Bot 家計簿（最終安定版・完全修正）
# ======================

from flask import Flask, request, Response
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
import os, re, io, traceback

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ======================
# フォント安全対応（ここが重要）
# ======================
try:
    plt.rcParams["font.family"] = "IPAexGothic"
except:
    plt.rcParams["font.family"] = "DejaVu Sans"

# ======================
# 初期化
# ======================
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
        CREATE TABLE IF NOT EXISTS budgets (
            user_id TEXT PRIMARY KEY
        )
    """)

    # ★ カラム補完（重要）
    cur.execute("""
        ALTER TABLE budgets
        ADD COLUMN IF NOT EXISTS amount INTEGER
    """)

    conn.commit()
    cur.close()
    put_conn(conn)

init_db()

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
    cur.execute("SELECT amount FROM budgets WHERE user_id=%s", (user_id,))
    result = cur.fetchone()
    cur.close()
    put_conn(conn)
    return result[0] if result else None

def set_budget(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO budgets (user_id, amount)
        VALUES (%s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET amount = EXCLUDED.amount
    """, (user_id, amount))
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
        plt.text(0.5, 0.5, "No Data", ha='center')
    else:
        labels = [str(d[0]) for d in data]  # ←安全化
        values = [d[1] for d in data]
        plt.pie(values, labels=labels, autopct="%1.1f%%")

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

    text = event.message.text.strip().replace("　", " ")
    user_id = event.source.user_id

    try:

        if text in ["はじめて", "使い方", "ヘルプ"]:
            msg = """【使い方】
①「1000 食費」で即登録
②「今月」で合計確認
③「グラフ」で内訳チェック
④「予算 30000」で上限設定
"""
            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        budget_match = re.match(r'予算\s*(\d+)', text)
        if budget_match:
            amount = int(budget_match.group(1))
            set_budget(user_id, amount)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"予算を{amount}円に設定したよ👍")
            )
            return

        if text in ["今月", "今月合計"]:
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            msg = f"【今月の支出】\n合計：{total}円\n"

            if budget:
                remain = budget - total
                msg += f"残り：{remain}円\n"
                if total > budget:
                    msg += "⚠️ 予算オーバー"
            else:
                msg += "※予算未設定\n"

            msg += "\n「グラフ」で内訳見れるよ📊"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        if text == "グラフ":
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(event.reply_token, ImageSendMessage(url, url))
            return

        if text == "リセット":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
            conn.commit()
            cur.close()
            put_conn(conn)

            line_bot_api.reply_message(event.reply_token, TextSendMessage("データ削除しました"))
            return

        quick = re.match(r'^(\d+)(円)?\s*(.+)$', text)

        if quick:
            amount = int(quick.group(1))
            category = quick.group(3).strip()

            save_expense(user_id, amount, category)

            msg = f"{category}:{amount}円 登録OK👍"

            if amount < 500:
                msg += "\n節約ナイス！"
            elif amount > 3000:
                msg += "\nちょっと使いすぎかも？"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        if not re.match(r'\d+', text):
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("はじめての方は「使い方」と送ってね👍")
            )
            return

        line_bot_api.reply_message(event.reply_token, TextSendMessage("「1000 食費」で入力できるよ"))

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))