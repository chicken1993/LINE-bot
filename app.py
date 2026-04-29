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
# 環境変数（秘密情報）
# ======================
import os
from dotenv import load_dotenv

# ======================
# 文字処理・DB
# ======================
import re
import psycopg2
from datetime import datetime

load_dotenv()

app = Flask(__name__)

# ======================
# LINEの認証情報
# ======================
# LINE公式のトークン（送信権限）
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
# LINEのWebhook認証キー
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# ユーザーの状態管理
# ======================
# 例：入力モード中 / 削除モード中 を保存
user_mode = {}

# ======================
# DB接続（PostgreSQL）
# ======================
def get_conn():
    # データベースに接続する関数
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        sslmode="require"
    )

# ======================
# DB初期化（テーブル作成）
# ======================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # 支出データを保存するテーブル
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
# 支出を保存する
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
# 安全な削除（最近5件から選ぶ）
# ======================
def delete_expense_by_index(user_id, index):
    conn = get_conn()
    cur = conn.cursor()

    # 最新5件を取得
    cur.execute("""
        SELECT id FROM expenses
        WHERE user_id=%s
        ORDER BY id DESC
        LIMIT 5
    """, (user_id,))

    rows = cur.fetchall()

    # ユーザーが選んだ番号のデータを削除
    if 0 <= index < len(rows):
        delete_id = rows[index][0]
        cur.execute("DELETE FROM expenses WHERE id=%s", (delete_id,))
        conn.commit()

    cur.close()
    conn.close()

# ======================
# 最近5件の履歴取得
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
# 全体の合計
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

    # データがないときは0円
    return total if total else 0

# ======================
# ① 今月の合計
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
# ② カテゴリ別ランキング
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
# 全削除（リセット）
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
# ③ PUSH送信（自動通知）
# ======================
def push_message(user_id, text):
    # LINEから強制的にメッセージを送る
    line_bot_api.push_message(
        user_id,
        TextSendMessage(text=text)
    )

# ======================
# LINEからの通信受付
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
# 入力の整形（文字クリーニング）
# ======================
def clean_category(text):
    # いらない単語を削除してカテゴリ化
    remove_words = ["に", "で", "を", "入れて", "使った", "購入", "買った"]
    for w in remove_words:
        text = text.replace(w, "")
    return text.strip()

# ======================
# メイン処理（LINEから来たメッセージ処理）
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text
    user_id = event.source.user_id

    try:
        # 文字の正規化（全角→半角など）
        text_clean = text.strip().replace("　", " ").replace("\n", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # ======================
        # 入力モード（支出登録中）
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
                reply_text = "例：ラーメンに900円"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        # ======================
        # 削除モード
        # ======================
        if user_mode.get(user_id) == "delete":
            if text.isdigit():
                index = int(text) - 1
                delete_expense_by_index(user_id, index)
                reply_text = "削除したよ"
            else:
                reply_text = "数字で選んでね"

            user_mode[user_id] = None
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            return

        # ======================
        # メニュー表示
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
        # 入力開始
        # ======================
        elif "入力" in text:
            user_mode[user_id] = "input"
            reply_text = "例：ラーメンに900円"

        # ======================
        # 削除開始
        # ======================
        elif "削除" in text:
            user_mode[user_id] = "delete"

            history = get_history(user_id)
            if history:
                msg = "番号選んで👇\n"
                for i, (c, a) in enumerate(history, 1):
                    msg += f"{i}. {c} {a}円\n"
            else:
                msg = "履歴なし"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            return

        # ======================
        # 履歴表示
        # ======================
        elif "履歴" in text:
            history = get_history(user_id)
            reply_text = "\n".join([f"{c} {a}円" for c, a in history]) if history else "履歴なし"

        # ======================
        # 合計
        # ======================
        elif "合計" in text:
            total = get_total(user_id)
            reply_text = f"合計は {total}円"

        # ======================
        # 今月
        # ======================
        elif "今月" in text:
            total = get_month_total(user_id)
            reply_text = f"今月は {total}円"

        # ======================
        # ランキング
        # ======================
        elif "ランキング" in text:
            ranks = get_category_rank(user_id)
            reply_text = "カテゴリランキング👇\n"
            for c, t in ranks:
                reply_text += f"{c}: {t}円\n"

        # ======================
        # リセット
        # ======================
        elif "リセット" in text:
            reset_data(user_id)
            reply_text = "リセットしたよ"

        # ======================
        # PUSHテスト
        # ======================
        elif "通知" in text:
            push_message(user_id, "これはPUSH通知だよ")
            return

        else:
            reply_text = "『家計簿』でメニュー出るよ！"

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