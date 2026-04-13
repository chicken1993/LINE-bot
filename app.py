@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id  # ←追加！
    text = event.message.text
    print("受信:", text, "ユーザー:", user_id)

    # データ読み込み
    data = load_data()

    # ユーザー初回なら作る
    if user_id not in data:
        data[user_id] = []

    if text == "合計":
        total = sum(data[user_id])
        reply_text = f"合計：{total}円"

    else:
        try:
            name, price = text.split()
            price = int(price)

            # データ追加
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