import requests

LINE_CHANNEL_ACCESS_TOKEN = "ここにLINEのチャネルトークン"
USER_ID = "ここにあなたのUSER_ID"

url = "https://api.line.me/v2/bot/message/push"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
}

data = {
    "to": USER_ID,
    "messages": [
        {
            "type": "text",
            "text": "☀️ 天気通知テスト成功！"
        }
    ]
}

response = requests.post(url, headers=headers, json=data)

print(response.status_code)
print(response.text)