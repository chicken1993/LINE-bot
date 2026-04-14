import sqlite3

# DB初期化
def init_db():
    conn = sqlite3.connect("kakeibo.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            amount INTEGER
        )
    """)
    conn.commit()
    conn.close()

# 保存
def save_expense(user_id, amount):
    conn = sqlite3.connect("kakeibo.db")
    c = conn.cursor()
    c.execute(
        "INSERT INTO expenses (user_id, amount) VALUES (?, ?)",
        (user_id, amount)
    )
    conn.commit()
    conn.close()

# 合計取得
def get_total(user_id):
    conn = sqlite3.connect("kakeibo.db")
    c = conn.cursor()
    c.execute(
        "SELECT SUM(amount) FROM expenses WHERE user_id=?",
        (user_id,)
    )
    total = c.fetchone()[0]
    conn.close()
    return total if total else 0

# LINE処理
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text
    print("受信:", text, "ユーザー:", user_id)

    if text == "合計":
        total = get_total(user_id)
        reply_text = f"合計：{total}円"

    else:
        try:
            name, price = text.split()
            price = int(price)

            # DBに保存
            save_expense(user_id, price)

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

# 起動時
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)