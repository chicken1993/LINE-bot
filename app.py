from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
import os
from dotenv import load_dotenv
import json

# .env読み込み
load_dotenv()

app = Flask(__name__)

FILE_NAME = "kakeibo.json"

def load_data():
    try:
        with open(FILE_NAME, "r") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(FILE_NAME, "w") as f:
        json.dump(data, f)

@app.route("/")
def home():
    return "Hello from LINE bot!"

# 環境変数から取得
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except Exception as e:
        print("エラー内容:", e)
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text
    print("受信:", text)

    # データ読み込み
    data = load_data()

    if text == "合計":
        total = sum(data[user_id])
        reply_text = f"合計：{total}円"

    else:
        try:
            name, price = text.split()
            price = int(price)

            # データ追加
            # ユーザー初回なら作る
if user_id not in data:
    data[user_id] = []

data[user_id].append(price)

            # 保存
            save_data(data)

            reply_text = f"{name} を {price}円で記録したよ！"

        except:
            reply_text = "『ラーメン 900』みたいに入力してね"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)