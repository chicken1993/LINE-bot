import os
import requests

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")

users = get_all_users()

for user_id in users:

    try:

        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=message)
        )

    except:

        print(f"送信失敗:{user_id}")


message = """
☀️ 天気通知テスト成功！

毎朝通知できるよ👍
"""

url = "https://api.line.me/v2/bot/message/push"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"
}

data = {
    "to": USER_ID,
    "messages": [
        {
            "type": "text",
            "text": message
        }
    ]
}

response = requests.post(
    url,
    headers=headers,
    json=data
)

print(response.status_code)
print(response.text)