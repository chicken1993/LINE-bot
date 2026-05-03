@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip().replace(" ", "").replace("　", "")
    user_id = event.source.user_id
    state = get_state(user_id)

    try:

        # ===== 初回ガイド =====
        if text in ["はじめて", "使い方", "ヘルプ"]:
            msg = """【使い方】
①「1000 食費」で即登録
②「今月」で合計確認
③「グラフ」で内訳チェック

例👇
1000 コンビニ
500 ラーメン

まずは1件入れてみて👍
"""
            line_bot_api.reply_message(event.reply_token, TextSendMessage(msg))
            return

        # ===== グラフ =====
        if text in ["グラフ", "グラフ📊"]:
            if not BASE_URL:
                line_bot_api.reply_message(event.reply_token, TextSendMessage("BASE_URL未設定"))
                return

            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )
            return

        # ===== 今月（Flex UI）=====
        if text in ["今月", "今月合計"]:
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            remain = budget - total if budget else None

            bubble = {
                "type": "bubble",
                "size": "mega",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "text",
                            "text": "今月の支出",
                            "weight": "bold",
                            "size": "xl"
                        },
                        {
                            "type": "text",
                            "text": f"{total}円",
                            "size": "xxl",
                            "weight": "bold",
                            "color": "#2E7D32"
                        }
                    ]
                }
            }

            if budget:
                status_text = f"残り {remain}円"
                color = "#0288D1"

                if total > budget:
                    status_text = "⚠️ 予算オーバー"
                    color = "#D32F2F"

                bubble["body"]["contents"].append({
                    "type": "text",
                    "text": status_text,
                    "size": "md",
                    "color": color
                })

            else:
                bubble["body"]["contents"].append({
                    "type": "text",
                    "text": "※予算未設定",
                    "size": "sm",
                    "color": "#999999"
                })

            bubble["footer"] = {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "action": {
                            "type": "message",
                            "label": "グラフを見る",
                            "text": "グラフ"
                        }
                    }
                ]
            }

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="今月の支出", contents=bubble)
            )
            return

        # ===== 一発入力 =====
        quick = re.match(r'^(\d+)(円)?(.+)$', text)

        if quick:
            amount = int(quick.group(1))
            category = quick.group(3).strip()

            save_expense(user_id, amount, category)

            msg = f"{category}:{amount}円 登録OK👍"
            if amount < 500:
                msg += "\n節約ナイス！"
            elif amount > 3000:
                msg += "\nちょっと使いすぎかも？"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )
            return

        # ===== 初回誘導 =====
        if not state and text not in ["家計簿", "支出入力", "今月", "グラフ"]:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("はじめての方は「使い方」と送ってね👍")
            )
            return

        # ===== 削除メニュー =====
        if text in ["削除", "取り消し"]:
            message = TemplateSendMessage(
                alt_text="削除メニュー",
                template=ButtonsTemplate(
                    title="削除",
                    text="選択してね",
                    actions=[
                        MessageAction(label="直前削除", text="直前削除"),
                        MessageAction(label="履歴削除", text="履歴削除"),
                        MessageAction(label="全削除", text="全削除")
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, message)
            return

        # ===== 履歴削除 =====
        if text == "履歴削除":
            data = get_recent_expenses(user_id)
            if not data:
                line_bot_api.reply_message(event.reply_token, TextSendMessage("データなし"))
                return

            contents = []
            for d in data:
                contents.append({
                    "type": "button",
                    "action": {
                        "type": "message",
                        "label": f"{d[1]} {d[2]}円",
                        "text": f"削除_{d[0]}"
                    }
                })

            bubble = {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": "履歴削除", "weight": "bold"}
                    ]
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": contents
                }
            }

            line_bot_api.reply_message(
                event.reply_token,
                FlexSendMessage(alt_text="履歴削除", contents=bubble)
            )
            return

        # ===== 削除実行 =====
        if text.startswith("削除_"):
            delete_id = text.replace("削除_", "")

            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM expenses WHERE id=%s", (delete_id,))
            conn.commit()
            cur.close()
            put_conn(conn)

            line_bot_api.reply_message(event.reply_token, TextSendMessage("削除完了"))
            return

        # ===== 直前削除 =====
        if text == "直前削除":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                DELETE FROM expenses
                WHERE id IN (
                    SELECT id FROM expenses
                    WHERE user_id=%s
                    ORDER BY created_at DESC
                    LIMIT 1
                )
            """, (user_id,))
            conn.commit()
            cur.close()
            put_conn(conn)

            line_bot_api.reply_message(event.reply_token, TextSendMessage("削除OK"))
            return

        # ===== 全削除 =====
        if text == "全削除":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM expenses WHERE user_id=%s", (user_id,))
            conn.commit()
            cur.close()
            put_conn(conn)

            line_bot_api.reply_message(event.reply_token, TextSendMessage("全削除"))
            return

        # ===== 入力UI =====
        if text in ["家計簿", "支出入力"]:
            set_state(user_id, "category")

            message = TemplateSendMessage(
                alt_text="カテゴリ",
                template=ButtonsTemplate(
                    title="支出",
                    text="カテゴリ",
                    actions=[
                        MessageAction(label="食費", text="食費"),
                        MessageAction(label="交通費", text="交通費"),
                        MessageAction(label="娯楽", text="娯楽"),
                        MessageAction(label="その他", text="その他"),
                    ]
                )
            )
            line_bot_api.reply_message(event.reply_token, message)
            return

        # ===== カテゴリ選択 =====
        if state and state[0] == "category":
            set_state(user_id, "amount", text)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(f"{text}いくら？"))
            return

        # ===== 金額入力 =====
        if state and state[0] == "amount":
            match = re.search(r'(\d+)', text)
            if match:
                amount = int(match.group(1))
                category = state[1]

                save_expense(user_id, amount, category)
                clear_state(user_id)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"{category}:{amount}円 登録OK")
                )
                return

        # ===== fallback =====
        line_bot_api.reply_message(event.reply_token, TextSendMessage("メニューか「使い方」で確認してね"))

    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage("エラー"))