# Flask
from flask import Flask, request, abort

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
import sqlite3

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# DB初期化
# ======================

def init_db():
    conn = sqlite3.connect("expenses.db")
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            amount INTEGER,
            category TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# 保存
def save_expense(user_id, amount, category):
    conn = sqlite3.connect("expenses.db")
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (?, ?, ?)",
        (user_id, amount, category)
    )

    conn.commit()
    conn.close()

# 合計
def get_total(user_id):
    conn = sqlite3.connect("expenses.db")
    cur = conn.cursor()

    cur.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id=?",
        (user_id,)
    )

    total = cur.fetchone()[0]
    conn.close()

    return total if total else 0

# リセット
def reset_data(user_id):
    conn = sqlite3.connect("expenses.db")
    cur = conn.cursor()

    cur.execute("DELETE FROM expenses WHERE user_id=?", (user_id,))

    conn.commit()
    conn.close()

# ======================
# ルーティング
# ======================

@app.route("/")
def home():
    return "Hello from LINE bot!"

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

    print("受信:", text, "ユーザー:", user_id)

    try:
        text_clean = text.strip()
        text_clean = text_clean.replace("　", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        # ======================
        # 映画
        # ======================
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

        # ======================
        # コマンド
        # ======================
        elif "合計" in text:
            total = get_total(user_id)
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
            numbers = re.findall(r"\d+", text_clean)

            if numbers:
                price = int(numbers[-1])
                name = re.sub(r"\d+|円", "", text_clean).strip()
                if not name:
                    name = "不明"

                category = "その他"

                save_expense(user_id, price, category)

                reply_text = f"{name} を {price}円で記録したよ！"

            else:
                reply_text = "ごめん、まだ対応してない内容だよ💦"

    except Exception as e:
        print("🔥エラー:", e)
        reply_text = "エラーが起きた💦"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )

# ======================
# 起動
# ======================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)