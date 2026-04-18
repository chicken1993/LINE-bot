# Flask
from flask import Flask, request

# LINE Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    TemplateSendMessage, ButtonsTemplate, MessageAction
)

# 環境変数
import os
from dotenv import load_dotenv

# 正規表現
import re

# DB
import psycopg2

load_dotenv()

app = Flask(__name__)

# ======================
# ★① handler初期化（超重要）
# ======================
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# ★② DB接続
# ======================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            amount INTEGER,
            category TEXT
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

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

def get_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    print("検索USER_ID:", user_id)  # ★④デバッグ

    cur.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id=%s",
        (user_id,)
    )

    total = cur.fetchone()[0]

    print("DB結果:", total)  # ★④デバッグ

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
# ★③ callback（LINE必須）
# ======================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    print("受信:", body)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("エラー:", e)
        return "ERROR", 500

    return 'OK', 200

# ======================
# メッセージ処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text
    user_id = event.source.user_id

    print("USER_ID:", user_id)
    print("受信:", text, "ユーザー:", user_id)

    try:
        text_clean = text.strip()
        text_clean = text_clean.replace("　", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # 映画
        if "映画" in text and "YouTube" not in text and "UNEXT" not in text:
            reply = TemplateSendMessage(
                alt_text='映画を見るサービスを選んでね',
                template=ButtonsTemplate(
                    title='映画を見る',
                    text='どこで見る？',
                    actions=[
                        MessageAction(label='YouTube', text='映画 YouTube'),
                        MessageAction(label='U-NEXT', text='映画 UNEXT')
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, reply)
            return

        elif "映画 YouTube" in text:
            reply_text = "YouTubeの映画はこちら👇\nhttps://www.youtube.com/results?search_query=映画"

        elif "映画 UNEXT" in text:
            reply_text = "U-NEXTはこちら👇\nhttps://video.unext.jp/"

        # コマンド
        elif "合計" in text:
            total = get_total(user_id)
            print("TOTAL:", total)
            reply_text = f"合計は {total}円だよ！"

        elif "リセット" in text:
            reset_data(user_id)
            reply_text = "データをリセットしたよ！"

        elif "こんにちは" in text or "やあ" in text:
            reply_text = "こんにちは！秘書としてサポートするよ👍"

        elif "天気" in text:
            reply_text = "天気はこちら👇\nhttps://weather.yahoo.co.jp/"

        elif "野球" in text:
            reply_text = "野球はこちら👇\nhttps://sports.nhk.or.jp/"

        elif "ニュース" in text:
            reply_text = "ニュースはこちら👇\nhttps://news.yahoo.co.jp/"

        elif "時間" in text:
            import datetime
            now = datetime.datetime.now()
            reply_text = f"今の時間は {now.strftime('%H:%M')} だよ！"

        elif "ありがとう" in text:
            reply_text = "どういたしまして👍"

        elif "予定" in text:
            reply_text = "予定管理はこれから追加予定！"

# ======================
# 家計簿
# ======================
else:
    # 「名前 + 数字」のセットで抽出
    items = re.findall(r'([^\d\s]+)\s*(\d+)', text_clean)

    total_added = 0

    for name, price in items:
        price = int(price)

        if not name:
            name = "不明"

        category = "その他"

        print("保存:", user_id, name, price)
        save_expense(user_id, price, category)

        total_added += price

    if total_added > 0:
        reply_text = f"{total_added}円分まとめて記録したよ！"
    else:
        reply_text = "ごめん、まだ対応してない内容だよ💦"
 
    except Exception as e:
        print("🔥エラー:", e)
        reply_text = "エラーが起きた💦"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )