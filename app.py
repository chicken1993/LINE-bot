from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = "Sw87dewMvMr62IdT9ajyxDiKS7QaN9+RJTftPuEAbTB5OFS2wU7J49MeHT71UlPXpsyWn9PaSfdMyN17C+fZxrujGyY7K53pBOYGDQxvXoBN1CFMpJWYY2bGlFcBdnBYxuYBmLWWhX0FbS20PdHsZQdB04t89/1O/w1cDnyilFU="
CHANNEL_SECRET = "9893629cbabdb7270d0163b095bbe665"

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except:
        abort(400)

    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    print("受信:", event.message.text)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="受け取ったよ！")]
            )
        )

if __name__ == "__main__":
    app.run(port=5000)