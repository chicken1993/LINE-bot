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
# 環境変数（APIキーなど）
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
# ★① LINE Botの認証設定
# ======================
# LINE Developersで取得したトークンとシークレットを読み込む
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

# LINE API操作用
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

# Webhook受信用（ここにLINEのメッセージが届く）
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# ★② データベース接続
# ======================
def get_conn():
    """
    SupabaseなどのPostgreSQLに接続する
    """
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

    # 家計簿テーブル
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

# アプリ起動時に実行
init_db()

# ======================
# データ保存
# ======================
def save_expense(user_id, amount, category):
    """
    支出を保存
    """
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
# データ削除（最新1件）
# ======================
def delete_last_expense(user_id, amount):
    """
    同じ金額の最新データを1件削除
    """
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

# ======================
# 履歴取得（最新5件）
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
# 合計取得
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
# ★③ LINEからのリクエスト受信口
# ======================
@app.route("/callback", methods=['POST'])
def callback():
    """
    LINEからメッセージが送られるとここに来る
    """
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    try:
        # handlerがメッセージ内容を解析
        handler.handle(body, signature)
    except Exception as e:
        print("エラー:", e)
        return "ERROR", 500

    return 'OK', 200

# ======================
# カテゴリ文字の掃除
# ======================
def clean_category(text):
    """
    「ラーメンに」「ラーメン買った」などを
    → 「ラーメン」にする
    """
    remove_words = ["に", "で", "を", "入れて", "使った", "購入", "買った"]

    for w in remove_words:
        text = text.replace(w, "")

    return text.strip()

# ======================
# ★メイン処理（ここが脳みそ）
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text
    user_id = event.source.user_id

    try:
        # 入力を整形
        text_clean = text.strip()
        text_clean = text_clean.replace("　", " ")
        text_clean = text_clean.replace("\n", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # ======================
        # UI：家計簿メニュー
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
        # 履歴表示
        # ======================
        elif "履歴" in text:
            history = get_history(user_id)

            if history:
                msg = "直近5件👇\n"
                for c, a in history:
                    msg += f"{c} {a}円\n"
            else:
                msg = "履歴なし"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=msg)
            )
            return

        # ======================
        # 削除（-900）
        # ======================
        match_del = re.search(r'-\s*(\d+)', text_clean)
        if match_del:
            amount = int(match_del.group(1))
            delete_last_expense(user_id, amount)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"{amount}円削除したよ")
            )
            return

        # ======================
        # 入力（ラーメンに900円）
        # ======================
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

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )