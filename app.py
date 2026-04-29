# ======================
# Flask（Webサーバー）
# ======================
from flask import Flask, request

# ======================
# LINE Bot SDK
# ======================
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    TemplateSendMessage, ButtonsTemplate, MessageAction
)

# ======================
# 環境変数
# ======================
import os
from dotenv import load_dotenv

# ======================
# 文字処理・DB
# ======================
import re
import psycopg2
from threading import Timer

load_dotenv()

app = Flask(__name__)

# ======================
# LINE認証
# ======================
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# モード管理
# ======================
user_mode = {}

# ======================
# 💰 ① 予算管理（ユーザーごと）
# ======================
user_budget = {}

def set_budget(user_id, amount):
    user_budget[user_id] = amount

def get_budget(user_id):
    return user_budget.get(user_id, 30000)  # デフォルト3万

# ======================
# DB接続
# ======================
def get_conn():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        sslmode="require"
    )

# ======================
# 初期化
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

    conn.commit()
    cur.close()
    conn.close()

init_db()

# ======================
# 保存
# ======================
def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s, %s, %s)",
        (user_id, amount, category)
    )

    conn.commit()
    cur.close()
    conn.close()

# ======================
# 削除
# ======================
def delete_expense_by_index(user_id, index):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id FROM expenses
        WHERE user_id=%s
        ORDER BY id DESC
        LIMIT 5
    """, (user_id,))

    rows = cur.fetchall()

    if 0 <= index < len(rows):
        delete_id = rows[index][0]
        cur.execute("DELETE FROM expenses WHERE id=%s", (delete_id,))
        conn.commit()

    cur.close()
    conn.close()

# ======================
# 履歴
# ======================
def get_history(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, amount
        FROM expenses
        WHERE user_id=%s
        ORDER BY id DESC
        LIMIT 5
    """, (user_id,))

    rows = cur.fetchall()

    cur.close()
    conn.close()
    return rows

# ======================
# 合計
# ======================
def get_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT SUM(amount) FROM expenses WHERE user_id=%s", (user_id,))
    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return total if total else 0

# ======================
# 今月
# ======================
def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(amount)
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return total if total else 0

# ======================
# ランキング
# ======================
def get_category_rank(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
        ORDER BY SUM(amount) DESC
        LIMIT 5
    """, (user_id,))

    rows = cur.fetchall()

    cur.close()
    conn.close()
    return rows

# ======================
# リセット
# ======================
def reset_data(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
    conn.commit()

    cur.close()
    conn.close()

# ======================
# PUSH
# ======================
def push_message(user_id, text):
    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=text)
    )

# ======================
# ② カテゴリ自動分類
# ======================
def auto_category(text):
    if any(w in text for w in ["コンビニ", "セブン", "ローソン"]):
        return "コンビニ"
    if any(w in text for w in ["ラーメン", "ごはん", "昼飯"]):
        return "食費"
    if any(w in text for w in ["電車", "バス"]):
        return "交通費"
    return "その他"

# ======================
# 🌅 ③ 毎朝通知
# ======================
def morning_push():
    for user_id in user_budget.keys():
        total = get_month_total(user_id)
        push_message(user_id, f"おはよう☀️ 今月支出：{total}円")

    Timer(86400, morning_push).start()

morning_push()

# ======================
# Webhook
# ======================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print(e)
        return "ERROR", 500

    return 'OK', 200

# ======================
# メイン
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text
    user_id = event.source.user_id

    try:
        text_clean = text.strip().replace("　", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # ======================
        # 💰 予算設定コマンド
        # 例：予算50000
        # ======================
        if text.startswith("予算"):
            amount = int(re.sub(r"\D", "", text))
            set_budget(user_id, amount)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"予算を{amount}円に設定したよ！")
            )
            return

        # ======================
        # ① 入力自由化
        # ======================
        match = re.search(r'(.+?)[にで]?(\d+)\s*円?', text_clean)

        if match:
            raw_category = match.group(1)
            price = int(match.group(2))

            category = auto_category(raw_category)

            save_expense(user_id, price, category)

            # ======================
            # 🚨 予算チェック（自動）
            # ======================
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            if total > budget:
                push_message(user_id, f"⚠️予算超え！{total}円 / {budget}円")

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"{category} {price}円記録したよ！")
            )
            return

        # ======================
        # メニュー
        # ======================
        if "家計簿" in text:
            reply = TemplateSendMessage(
                alt_text="menu",
                template=ButtonsTemplate(
                    title="家計簿",
                    text="選んで",
                    actions=[
                        MessageAction(label="削除", text="削除"),
                        MessageAction(label="履歴", text="履歴"),
                        MessageAction(label="合計", text="合計")
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, reply)
            return

        # ======================
        # 削除
        # ======================
        if "削除" in text:
            user_mode[user_id] = "delete"

            history = get_history(user_id)
            msg = "番号👇\n"
            for i, (c, a) in enumerate(history, 1):
                msg += f"{i}. {c} {a}円\n"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        if user_mode.get(user_id) == "delete":
            if text.isdigit():
                delete_expense_by_index(user_id, int(text)-1)
                reply_text = "削除した"
            else:
                reply_text = "数字で"

            user_mode[user_id] = None

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        # ======================
        # その他
        # ======================
        if "履歴" in text:
            history = get_history(user_id)
            reply_text = "\n".join([f"{c} {a}円" for c, a in history]) or "なし"

        elif "合計" in text:
            reply_text = f"{get_total(user_id)}円"

        elif "今月" in text:
            reply_text = f"{get_month_total(user_id)}円"

        elif "ランキング" in text:
            ranks = get_category_rank(user_id)
            reply_text = "\n".join([f"{c}:{t}円" for c, t in ranks])

        elif "リセット" in text:
            reset_data(user_id)
            reply_text = "リセットした"

        elif "通知" in text:
            push_message(user_id, "テスト通知")
            return

        else:
            reply_text = "そのまま金額送ってOK"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    except Exception as e:
        print(e)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="エラー"))