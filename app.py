# =========================================================
# メイン処理（完成版）
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text
    user_id = event.source.user_id

    # ======================
    # 🔥 デバッグログ
    # ======================
    print("DEBUG TEXT:", repr(text))

    try:

        # ======================
        # 初回
        # ======================
        if is_first_user(user_id):
            mark_user_init(user_id)
            set_user_rich_menu(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("🎉ようこそ！")
            )
            return

        # ======================
        # 今月
        # ======================
        if "今月" in text:
            total = get_month_total(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"今月：{total}円")
            )
            return

        # ======================
        # グラフ
        # ======================
        if "グラフ" in text:
            url = f"https://line-bot-1-gizk.onrender.com/chart/{user_id}"

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=url,
                    preview_image_url=url
                )
            )
            return

        # ======================
        # メニュー
        # ======================
        if "メニュー" in text:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("入力→記録\nグラフ→分析\n今月→合計")
            )
            return

        # ======================
        # 金額入力
        # ======================
        match = re.search(r'(.+?)[にで]?(\d+)', text)

        if match:
            category = classify_category(text)
            amount = int(match.group(2))

            save_expense(user_id, amount, category)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("記録OK")
            )
            return

        # ======================
        # 🔥 フォールバック
        # ======================
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("⚠️コマンドが認識できません\n「メニュー」と送ってください")
        )

    except Exception as e:
        print("error:", e)