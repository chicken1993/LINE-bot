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
    MessageAction
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
# 支出DB
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

def get_recent_expenses(user_id, limit=10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, category, amount
        FROM expenses
        WHERE user_id=%s
        ORDER BY created_at DESC
        LIMIT %s
    """, (user_id, limit))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

# ======================
# 予算
# ======================
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

def get_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT monthly_budget FROM budgets WHERE user_id=%s", (user_id,))
    r = cur.fetchone()
    cur.close()
    conn.close()
    return r[0] if r else None

def delete_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM budgets WHERE user_id=%s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

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

    plt.figure(figsize=(6,6))

    if not data:
        plt.text(0.5, 0.5, "データなし", ha='center')
    else:
        labels = [d[0] for d in data]
        values = [d[1] for d in data]
        plt.pie(values, labels=labels, autopct="%1.1f%%",
                textprops={"fontproperties": font_prop})

    plt.axis('equal')

    img = io.BytesIO()
    plt.savefig(img, format="png")
    plt.close()
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")

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
        # 支出開始
        # ======================
        if text == "支出入力":
            set_state(user_id, "expense_category")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("カテゴリ選択👇\n食費/交通費/娯楽/その他")
            )
            return

        # ======================
        # カテゴリ選択
        # ======================
        if state and state[0] == "expense_category":

            category_map = {
                "食費": "food",
                "交通費": "transport",
                "娯楽": "entertainment",
                "その他": "other"
            }

            if text in category_map:
                set_state(user_id, "expense_amount", category_map[text])

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("金額いくら？")
                )
                return

        # ======================
        # 金額入力
        # ======================
        if state and state[0] == "expense_amount":

            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))
                category = state[1]

                save_expense(user_id, amount, category)
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{amount}円登録したよ✅")
                )
                return

        # ======================
        # 今月
        # ======================
        if text in ["今月", "今月合計"]:
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            msg = f"今月：{total}円"

            if budget:
                msg += f"\n予算：{budget}円\n残り：{budget - total}円"
            else:
                msg += "\n予算未設定"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )
            return

        # ======================
        # 予算
        # ======================
        if text == "予算設定":
            set_state(user_id, "budget_input")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("予算いくら？")
            )
            return

        if state and state[0] == "budget_input":
            match = re.search(r'(\d+)', text)
            if match:
                set_budget(user_id, int(match.group(1)))
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("予算設定OK")
                )
                return

        if text == "予算削除":
            delete_budget(user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("削除した")
            )
            return

        # ======================
        # 削除メニュー
        # ======================
        if text == "削除":
            set_state(user_id, "delete_menu")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("直前1個 / 履歴10個 / 今月すべて消去")
            )
            return

        if state and state[0] == "delete_menu":

            if text == "直前1個":
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

                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("削除した")
                )
                return

            if text == "履歴10個":
                rows = get_recent_expenses(user_id, 10)
                msg = "\n".join([f"{r[1]} {r[2]}円" for r in rows])

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(msg if msg else "なし")
                )
                return

            if text == "今月すべて消去":
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
                conn.commit()
                cur.close()
                conn.close()

                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("全部消した")
                )
                return

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
        # fallback
        # ======================
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("メニューから選んでね")
        )

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("エラー")
        )

# ======================
# 起動
# ======================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))