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
# LINE認証
# ======================
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# ユーザーモード（削除だけ使う）
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
# ④ 安全削除（最新5件からID削除）
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

    cur.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id=%s",
        (user_id,)
    )

    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return total if total else 0

# ======================
# ① 今月合計
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
# ② カテゴリランキング
# ======================
def get_category_rank(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, SUM(amount) as total
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
        ORDER BY total DESC
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

    cur.execute(
        "DELETE FROM expenses WHERE user_id=%s",
        (user_id,)
    )

    conn.commit()
    cur.close()
    conn.close()

# ======================
# ③ PUSH送信
# ======================
def push_message(user_id, text):
    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=text)
    )

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
        text_clean = text.strip().replace("　", " ").replace("\n", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # =====================================================
        # 🔥① どこでも直接入力OK（最重要）
        # 例：ラーメンに900円 / コンビニで300円
        # =====================================================
        match = re.search(r'(.+?)に\s*(\d+)円?', text_clean)

        if match:
            category = clean_category(match.group(1))
            price = int(match.group(2))

            save_expense(user_id, price, category)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"{category}に{price}円記録したよ！")
            )
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
                        MessageAction(label='削除', text='削除'),
                        MessageAction(label='履歴', text='履歴')
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

            msg = "番号選んで👇\n"
            for i, (c, a) in enumerate(history, 1):
                msg += f"{i}. {c} {a}円\n"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        if user_mode.get(user_id) == "delete":
            if text.isdigit():
                delete_expense_by_index(user_id, int(text)-1)
                reply_text = "削除したよ"
            else:
                reply_text = "数字で選んでね"

            user_mode[user_id] = None

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=reply_text)
            )
            return

        # ======================
        # 履歴
        # ======================
        if "履歴" in text:
            history = get_history(user_id)
            reply_text = "\n".join([f"{c} {a}円" for c, a in history]) or "履歴なし"

        # ======================
        # 合計
        # ======================
        elif "合計" in text:
            reply_text = f"合計は {get_total(user_id)}円"

        # ======================
        # 今月
        # ======================
        elif "今月" in text:
            reply_text = f"今月は {get_month_total(user_id)}円"

        # ======================
        # ランキング
        # ======================
        elif "ランキング" in text:
            ranks = get_category_rank(user_id)
            reply_text = "ランキング👇\n"
            for c, t in ranks:
                reply_text += f"{c}: {t}円\n"

        # ======================
        # リセット
        # ======================
        elif "リセット" in text:
            reset_data(user_id)
            reply_text = "リセットしたよ"

        # ======================
        # ③ PUSH通知テスト
        # ======================
        elif "通知" in text:
            push_message(user_id, "これはPUSH通知だよ")
            return

        else:
            reply_text = "そのまま「ラーメンに900円」って送ればOK！"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

    except Exception as e:
        print("🔥エラー:", e)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="エラー発生")
        )