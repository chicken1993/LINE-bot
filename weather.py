import os
import requests

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")

USER_ID = "Ucae4b4a79830d56a8bf4d63159763afd"

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