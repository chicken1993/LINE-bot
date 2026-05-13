# ======================
# 全ユーザー天気通知（コピペ専用）
# ======================

from linebot.models import TextSendMessage
import traceback

message = """
☀️ 天気通知テスト成功！

毎朝通知できるよ👍
"""

try:
    users = get_all_users()

    for user_id in users:

        try:
            line_bot_api.push_message(
                user_id,
                TextSendMessage(text=message)
            )
            print(f"送信成功: {user_id}")

        except Exception:
            print(f"送信失敗: {user_id}")
            print(traceback.format_exc())

except Exception:
    print("ユーザー取得エラー")
    print(traceback.format_exc())