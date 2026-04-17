# Flask
from flask import Flask, request, abort

# LINE Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 環境変数
import os
from dotenv import load_dotenv

# 正規表現
import re

load_dotenv()

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

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

    print("メッセージ:", text)

    try:
        # 前処理
        text_clean = text.strip()
        text_clean = text_clean.replace("　", " ")
        text_clean = text_clean.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

        numbers = re.findall(r"\d+", text_clean)

        # ======================
        # 家計簿っぽい入力
        # ======================
        if numbers:
            price = int(numbers[-1])
            name = re.sub(r"\d+|円", "", text_clean).strip()
            if not name:
                name = "不明"

            reply_text = f"{name} を {price}円としてメモしたよ！（仮）"

        # ======================
        # コマンド系
        # ======================
        elif "合計" in text:
            reply_text = "今はまだ合計機能は準備中だよ！"

        elif "リセット" in text:
            reply_text = "リセット機能はこれから作るよ！"

        elif "予定" in text:
            reply_text = "予定管理はこれから追加予定！"

        elif "野球" in text:
            reply_text = """野球中継はこちら👇
https://www.dazn.com/
https://sports.nhk.or.jp/
"""

        elif "天気" in text:
            reply_text = """天気はこちら👇
https://weather.yahoo.co.jp/
"""

        elif "こんにちは" in text or "やあ" in text:
            reply_text = "こんにちは！秘書としてサポートするよ👍"
        elif "YouTube" in text or "動画" in text:
    reply_text = "YouTubeはこちら👇\nhttps://www.youtube.com/"

elif "ニュース" in text:
    reply_text = "ニュースはこちら👇\nhttps://news.yahoo.co.jp/"

elif "時間" in text:
    import datetime
    now = datetime.datetime.now()
    reply_text = f"今の時間は {now.strftime('%H:%M')} だよ！"

elif "ありがとう" in text:
    reply_text = "どういたしまして👍"

        # ======================
        # デフォルト
        # ======================
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