@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip().replace(" ", "").replace("　", "")
    user_id = event.source.user_id
    state = get_state(user_id)

    try:

        # ======================
        # 📊 今月
        # ======================
        if text in ["今月", "今月合計"]:
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            msg = f"今月：{total}円"

            if budget:
                msg += f"\n予算：{budget}円"
                msg += f"\n残り：{budget - total}円"
            else:
                msg += "\n※予算未設定"

            line_bot_api.reply_message(
                event.reply_token,
                TemplateSendMessage(
                    alt_text="今月メニュー",
                    template=ButtonsTemplate(
                        title="今月データ",
                        text=msg,
                        actions=[
                            MessageAction(label="✏️予算変更", text="予算設定"),
                            MessageAction(label="🗑予算削除", text="予算削除")
                        ]
                    )
                )
            )
            return

        # ======================
        # 💰 予算
        # ======================
        if text == "予算設定":
            set_state(user_id, "budget_input")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("新しい予算いくら？（数字だけ）")
            )
            return

        if state and state[0] == "budget_input":
            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))
                set_budget(user_id, amount)
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"予算を{amount}円に設定したよ✅")
                )
            return

        if text == "予算削除":
            delete_budget(user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("予算削除したよ🗑")
            )
            return

        # ======================
        # 🧾 家計簿入力（追加）
        # ======================
        if text == "家計簿入力":
            set_state(user_id, "expense_category")
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("カテゴリ選んで👇\n食費\n交通費\n娯楽\nその他")
            )
            return

        # カテゴリ → 金額
        if state and state[0] == "expense_category":
            category = text
            set_state(user_id, "expense_amount", category)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"{category}の金額いくら？（数字だけ）")
            )
            return

        # 金額保存
        if state and state[0] == "expense_amount":
            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))
                category = state[1]

                save_expense(user_id, amount, category)
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{category} {amount}円を保存したよ✅")
                )
            return

        # ======================
        # 🗑 取り消しメニュー
        # ======================
        if text == "取り消し":
            set_state(user_id, "delete_menu")

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "どれ消す？\n"
                    "1️⃣ 直前1件\n"
                    "2️⃣ 履歴10件から選択\n"
                    "3️⃣ 今月すべて削除"
                )
            )
            return

        # 取り消し処理
        if state and state[0] == "delete_menu":

            # 直前1件
            if text == "1":
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("""
                    DELETE FROM expenses
                    WHERE id = (
                        SELECT id FROM expenses
                        WHERE user_id=%s
                        ORDER BY created_at DESC
                        LIMIT 1
                    )
                """, (user_id,))
                conn.commit()
                cur.close()
                conn.close()

                clear_state(user_id)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("直前1件削除したよ🗑")
                )
                return

            # 履歴10件
            if text == "2":
                rows = get_recent_expenses(user_id, 10)

                if not rows:
                    clear_state(user_id)
                    line_bot_api.reply_message(
                        event.reply_token,
                        TextSendMessage("履歴ないよ")
                    )
                    return

                msg = "削除したいIDを送って👇\n\n"
                msg += "\n".join([f"{r[0]}: {r[1]} {r[2]}円" for r in rows])

                set_state(user_id, "delete_select")

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(msg)
                )
                return

            # 今月全削除
            if text == "3":
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
                conn.commit()
                cur.close()
                conn.close()

                clear_state(user_id)
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("今月すべて削除したよ🔥")
                )
                return

        # ======================
        # ID削除
        # ======================
        if state and state[0] == "delete_select":

            conn = get_conn()
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM expenses WHERE id=%s AND user_id=%s",
                (text, user_id)
            )
            conn.commit()
            cur.close()
            conn.close()

            clear_state(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("削除したよ🗑")
            )
            return

        # ======================
        # 📊 グラフ
        # ======================
        if text in ["グラフ", "グラフ📊"]:
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        # ======================
        # fallback
        # ======================
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("メニューから選んでね👇")
        )

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("エラー出た😇")
        )