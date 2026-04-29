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

load_dotenv()

app = Flask(__name__)

# ======================
# LINE設定
# ======================
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# ユーザーモード管理
# ======================
user_mode = {}

# ======================
# DB接続
# ======================
def get_conn():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        sslmode="require"
    )

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
# DB操作
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

def delete_last_expense(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM expenses
        WHERE id = (
            SELECT id FROM expenses
            WHERE user_id=%s AND amount=%s
            ORDER BY id DESC LIMIT 1
        )
    """, (user_id, amount))

    conn.commit()
    cur.close()
    conn.close()

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

def get_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id=%s",
        (user_id,)
    )

    total = cur.fetchone()[0]

    cur.close()
    conn.close()
    return total if total else 0

def reset_data(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "DELETE FROM expenses WHERE user_id=%s",
        (user_id,)
    )

    conn.commit()
    cur.close()
    conn.close()

# ======================
# callback
# ======================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("エラー:", e)
        return "ERROR", 500

    return 'OK', 200

# ======================
# カテゴリ整形
# ======================
def clean_category(text):
    remove_words = ["に", "で", "を", "入れて", "使った", "購入", "買った"]
    for w in remove_words:
        text = text.replace(w, "")
    return text.strip()

# ======================
# メイン処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text
    user_id = event.source.user_id

    try:
        text_clean = text.strip()
        text_clean = text_clean.replace("　", " ")
        text_clean = text_clean.replace("\n", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # ======================
        # 入力モード
        # ======================
        if user_mode.get(user_id) == "input":
            match = re.search(r'(.+?)に\s*(\d+)円?', text_clean)

            if match:
                category = clean_category(match.group(1))
                price = int(match.group(2))

                save_expense(user_id, price, category)
                reply_text = f"{category}に{price}円記録したよ！"
                user_mode[user_id] = None
            else:
                reply_text = "形式：ラーメンに900円"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        # ======================
        # 削除モード
        # ======================
        if user_mode.get(user_id) == "delete":
            if text.isdigit():
                index = int(text) - 1
                history = get_history(user_id)

                if 0 <= index < len(history):
                    category, amount = history[index]
                    delete_last_expense(user_id, amount)
                    reply_text = f"{category} {amount}円削除したよ"
                else:
                    reply_text = "番号が違うよ"
            else:
                reply_text = "数字で選んでね"

            user_mode[user_id] = None

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        # ======================
        # メニュー
        # ======================
        if "家計簿" in text:
            reply = TemplateSendMessage(
                alt_text='家計簿メニュー',
                template=ButtonsTemplate(
                    title='家計簿',
                    text='何する？',
                    actions=[
                        MessageAction(label='入力', text='入力'),
                        MessageAction(label='削除', text='削除'),
                        MessageAction(label='履歴', text='履歴')
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, reply)
            return

        # ======================
        # 入力モード開始
        # ======================
        elif "入力" in text:
            user_mode[user_id] = "input"
            line_bot_api.reply_message(event.reply_token,
                TextSendMessage(text="何を入力する？（例：ラーメンに900円）"))
            return

        # ======================
        # 削除モード開始
        # ======================
        elif "削除" in text:
            user_mode[user_id] = "delete"

            history = get_history(user_id)
            if history:
                msg = "削除する番号を選んで👇\n"
                for i, (c, a) in enumerate(history, 1):
                    msg += f"{i}. {c} {a}円\n"
            else:
                msg = "履歴なし"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        # ======================
        # 履歴
        # ======================
        elif "履歴" in text:
            history = get_history(user_id)

            if history:
                msg = "直近5件👇\n"
                for c, a in history:
                    msg += f"{c} {a}円\n"
            else:
                msg = "履歴なし"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        # ======================
        # 合計
        # ======================
        elif "合計" in text:
            total = get_total(user_id)
            reply_text = f"合計は {total}円"

        # ======================
        # リセット
        # ======================
        elif "リセット" in text:
            reset_data(user_id)
            reply_text = "リセットしたよ"

        else:
            reply_text = "『家計簿』って送るとメニュー出るよ！"

    except Exception as e:
        print("🔥エラー:", e)
        reply_text = "エラー発生"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))