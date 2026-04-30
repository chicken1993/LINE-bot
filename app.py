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
    RichMenu, RichMenuArea, RichMenuBounds, MessageAction
)

# ======================
# 基本
# ======================
import os
import re
import io
import time
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

# ======================
# 初期化
# ======================
load_dotenv()
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

# 🔥 これが先！！
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


# =========================================================
# DB接続
# =========================================================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")


# =========================================================
# メイン処理（ここに書く）
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text
    user_id = event.source.user_id

    print("DEBUG TEXT:", repr(text))

    try:

        if "今月" in text:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("今月チェック")
            )
            return

        if "グラフ" in text:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("グラフ表示")
            )
            return

        if "メニュー" in text:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("メニュー表示")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("⚠️認識できません")
        )

    except Exception as e:
        print("error:", e)


# =========================================================
# Webhook
# =========================================================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Webhook error:", e)

    return "OK"


# =========================================================
# 起動
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))