
# ======================
# Flask / LINE Bot 家計簿 完全統合版
# ======================

from flask import Flask, request, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
from linebot.models.events import FollowEvent

import os
import re
import traceback
import requests

from collections import defaultdict

import matplotlib.pyplot as plt
from matplotlib import font_manager



from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")


from google.cloud import vision

# ======================
# 日本語フォント
# ======================

font_prop = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONT_PATH = os.path.join(
    BASE_DIR,
    "fonts",
    "ipaexg.ttf"
)

try:

    if os.path.exists(FONT_PATH):

        font_manager.fontManager.addfont(FONT_PATH)

        font_prop = font_manager.FontProperties(
            fname=FONT_PATH
        )

        plt.rcParams["font.family"] = font_prop.get_name()
        plt.rcParams["axes.unicode_minus"] = False

        print("✅ 日本語フォントOK")

    else:

        plt.rcParams["font.family"] = "DejaVu Sans"

except:

    plt.rcParams["font.family"] = "DejaVu Sans"

# ======================
# Flask
# ======================

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

pool = SimpleConnectionPool(
    1,
    10,
    dsn=DATABASE_URL,
    sslmode="require"
)

OCR_LIMIT = 20

# ======================
# DB
# ======================

def get_conn():
    return pool.getconn()

def put_conn(conn):
    pool.putconn(conn)

# ======================
# DB初期化
# ======================

def init_db():

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                amount INTEGER,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS ocr_logs (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                user_id TEXT PRIMARY KEY,
                amount INTEGER
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

    finally:

        cur.close()
        put_conn(conn)

init_db()

# ======================
# Follow
# ======================

@handler.add(FollowEvent)
def handle_follow(event):

    user_id = event.source.user_id

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO users (user_id)
            VALUES (%s)
            ON CONFLICT DO NOTHING
        """, (user_id,))

        conn.commit()

    finally:

        cur.close()
        put_conn(conn)

# ======================
# 全ユーザー
# ======================

def get_all_users():

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT user_id
            FROM users
        """)

        rows = cur.fetchall()

        return [r[0] for r in rows]

    finally:

        cur.close()
        put_conn(conn)

# ======================
# 保存
# ======================

def save_expense(user_id, amount, category):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO expenses (
                user_id,
                amount,
                category
            )
            VALUES (%s,%s,%s)
        """, (
            user_id,
            amount,
            category
        ))

        conn.commit()

    finally:

        cur.close()
        put_conn(conn)

# ======================
# 最新削除
# ======================

def delete_latest(user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            DELETE FROM expenses
            WHERE id = (
                SELECT id
                FROM expenses
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT 1
            )
            RETURNING id
        """, (user_id,))

        deleted = cur.fetchone()

        conn.commit()

        return deleted is not None

    finally:

        cur.close()
        put_conn(conn)

# ======================
# 今月削除
# ======================

def delete_month(user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            DELETE FROM expenses
            WHERE user_id=%s
            AND DATE_TRUNC('month', created_at)
                = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))

        conn.commit()

    finally:

        cur.close()
        put_conn(conn)

# ======================
# 履歴取得
# ======================

def get_recent_expenses(user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT id, category, amount
            FROM expenses
            WHERE user_id=%s
            ORDER BY created_at DESC
            LIMIT 10
        """, (user_id,))

        return cur.fetchall()

    finally:

        cur.close()
        put_conn(conn)

# ======================
# ID削除
# ======================

def delete_by_id(expense_id, user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            DELETE FROM expenses
            WHERE id=%s
            AND user_id=%s
        """, (
            expense_id,
            user_id
        ))

        conn.commit()

    finally:

        cur.close()
        put_conn(conn)

# ======================
# OCR回数
# ======================

def get_monthly_ocr_count(user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)
            FROM ocr_logs
            WHERE user_id=%s
            AND DATE_TRUNC('month', created_at)
                = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))

        return cur.fetchone()[0]

    finally:

        cur.close()
        put_conn(conn)

# ======================
# OCRログ
# ======================

def save_ocr_log(user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO ocr_logs (user_id)
            VALUES (%s)
        """, (user_id,))

        conn.commit()

    finally:

        cur.close()
        put_conn(conn)

# ======================
# 今月データ
# ======================

def get_month_data(user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT category, amount
            FROM expenses
            WHERE user_id=%s
            AND DATE_TRUNC('month', created_at)
                = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))

        return cur.fetchall()

    finally:

        cur.close()
        put_conn(conn)

# ======================
# グラフ
# ======================

def create_graph(user_id):

    import matplotlib.pyplot as plt
    from matplotlib import font_manager


    import matplotlib
    matplotlib.use("Agg")

    if os.path.exists(FONT_PATH):

        font_manager.fontManager.addfont(FONT_PATH)

        font_prop = font_manager.FontProperties(
            fname=FONT_PATH
        )

        plt.rcParams["font.family"] = font_prop.get_name()
        plt.rcParams["axes.unicode_minus"] = False

    rows = get_month_data(user_id)

    if not rows:
        return None

    data = defaultdict(int)

    for category, amount in rows:
        data[category] += amount

    labels = list(data.keys())
    values = list(data.values())

    plt.figure(figsize=(6,6))

    plt.pie(
        values,
        labels=labels,
        autopct="%1.1f%%"
    )

    plt.title("今月の支出")

    graph_path = f"graph_{user_id}.png"

    plt.savefig(graph_path)

    plt.close()

    return graph_path


# ======================
# OCR
# ======================

def detect_text_from_image(image_content):

    client = vision.ImageAnnotatorClient()

    image = vision.Image(content=image_content)

    response = client.text_detection(image=image)

    texts = response.text_annotations

    if texts:
        return texts[0].description

    return ""

# ======================
# 金額抽出
# ======================

def extract_max_price(text):

    patterns = [
        r'合計\s*¥?(\d+)',
        r'TOTAL\s*¥?(\d+)',
        r'税込\s*¥?(\d+)',
        r'¥\s*(\d+)'
    ]

    candidates = []

    for pattern in patterns:

        matches = re.findall(
            pattern,
            text,
            re.IGNORECASE
        )

        candidates.extend(matches)

    if candidates:
        return max(map(int, candidates))

    numbers = re.findall(r'\d{2,6}', text)

    if not numbers:
        return None

    return max(map(int, numbers))

# ======================
# 天気
# ======================

def get_weather_text(code):

    if code == 0:
        return "晴れ"

    elif code in [1,2,3]:
        return "くもり"

    elif code in [45,48]:
        return "霧"

    return "雨"

def fetch_weather(city, lat, lon):

    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&hourly=temperature_2m,precipitation_probability,weathercode"
        "&timezone=Asia%2FTokyo"
    )

    data = requests.get(url, timeout=5).json()

    daily = data.get("daily", {})
    hourly = data.get("hourly", {})

    max_list = daily.get("temperature_2m_max")
    min_list = daily.get("temperature_2m_min")
    rain_list = daily.get("precipitation_probability_max")

    h_t = hourly.get("temperature_2m")
    h_c = hourly.get("weathercode")

    if not all([max_list, min_list, rain_list, h_t, h_c]):
        return "⚠️ 天気情報を取得できません"


    max_t = max_list[0]
    min_t = min_list[0]
    rain = rain_list[0]

    return (
        f"📍{city}\n"
        f"午前：{get_weather_text(h_c[9])} / {h_t[9]}℃\n"
        f"午後：{get_weather_text(h_c[15])} / {h_t[15]}℃\n"
        f"最高：{max_t}℃\n"
        f"最低：{min_t}℃\n"
        f"降水確率：{rain}%"
        + ("\n☔ 傘必要" if rain >= 50 else "")
    )



# ======================
# callback
# ======================

@app.route("/callback", methods=["POST"])
def callback():

    body = request.get_data(as_text=True)

    signature = request.headers.get(
        "X-Line-Signature"
    )

    try:

        handler.handle(body, signature)

    except:

        print(traceback.format_exc())

    return "OK"

# ======================
# home
# ======================

@app.route("/")
def home():
    return "OK", 200
    

# ======================
# graph
# ======================

@app.route("/graph/<user_id>.png")
def graph(user_id):

    return send_file(
        f"graph_{user_id}.png",
        mimetype="image/png"
    )

# ======================
# weather push
# ======================

@app.route("/weather")
def send_weather():

    users = get_all_users()

    msg = (
        "☀️ 今日の天気\n\n"
        + fetch_weather("静岡市", 34.9769, 138.3831)
        + "\n\n"
        + fetch_weather("沼津市", 35.0956, 138.8635)
    )

    for u in users:

        try:

            line_bot_api.push_message(
                u,
                TextSendMessage(text=msg)
            )

        except:
            pass

    return "sent"

# ======================
# TEXT
# ======================

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()

    user_id = event.source.user_id

    try:

        # OCR登録
        if text.startswith("OK_"):

            amount = int(
                text.split("_")[1]
            )

            save_expense(
                user_id,
                amount,
                "レシート"
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=f"{amount}円 登録したよ👍"
                )
            )

            return

        # グラフ
        if text == "グラフ":

            graph_path = create_graph(user_id)

            if not graph_path:

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("データなし")
                )

                return

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=f"{BASE_URL}/graph/{user_id}.png",
                    preview_image_url=f"{BASE_URL}/graph/{user_id}.png"
                )
            )

            return

        # 今月
        if text == "今月":

            rows = get_month_data(user_id)

            total = sum(
                amount for _, amount in rows
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=f"今月合計：{total}円"
                )
            )

            return


        # ======================
        # 使い方
        # ======================

        if text == "使い方":

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=(
                        "使い方を説明するね👇\n\n"

                        "・『予算』と入力\n"
                        "→ 予算設定＆確認や取り消しができるよ\n\n"

                        "・『1000 食費』を入力\n"
                        "→ 支出登録ができるよ\n\n"

                        "・『今月』と入力\n"
                        "→ 今月の使用金額の確認や\n"
                        "設定額に対しての残額を確認できるよ\n\n"

                        "・『グラフ』と入力\n"
                        "→ 円グラフを表示して何に\n"
                        "一番使ってるか確認できるよ\n\n"

                        "・『リセット』と入力\n"
                        "→ 履歴削除ができるよ\n\n"

                        "📸 レシート送信もOK\n"
                        "レシート画像を送信してね！\n"
                        "僕がレシートの画像を判断して\n"
                        "入力金額を確認するよ🤖\n"
                        "結構間違えるから修正をお願い\n"
                        "することもあるよ💦\n\n"

                        "⚠️ 無料プランのため\n"
                        "初回返信は1分前後かかります。\n"
                        "返信が来ない場合は、1分後再度\n"
                        "同じメッセージを送信してください。"
                    )
                )
            )

            return

        # リセット
        if text == "リセット":

            buttons = TemplateSendMessage(
                alt_text="削除メニュー",
                template=ButtonsTemplate(
                    title="削除メニュー",
                    text="どれを削除する？",
                    actions=[

                        MessageAction(
                            label="直前の入力1件",
                            text="削除_最新"
                        ),

                        MessageAction(
                            label="履歴10件から選択",
                            text="削除_履歴"
                        ),

                        MessageAction(
                            label="今月データ全部",
                            text="削除_今月"
                        )
                    ]
                )
            )

            line_bot_api.reply_message(
                event.reply_token,
                buttons
            )

            return

        # 最新削除
        if text == "削除_最新":

            confirm = TemplateSendMessage(
                alt_text="確認",
                template=ConfirmTemplate(
                    text="最新1件を削除する？",
                    actions=[

                        MessageAction(
                            label="はい",
                            text="実行_削除最新"
                        ),

                        MessageAction(
                            label="いいえ",
                            text="キャンセル"
                        )
                    ]
                )
            )

            line_bot_api.reply_message(
                event.reply_token,
                confirm
            )

            return

        # 今月削除
        if text == "削除_今月":

            confirm = TemplateSendMessage(
                alt_text="確認",
                template=ConfirmTemplate(
                    text="今月データ全部削除する？",
                    actions=[

                        MessageAction(
                            label="はい",
                            text="実行_削除今月"
                        ),

                        MessageAction(
                            label="いいえ",
                            text="キャンセル"
                        )
                    ]
                )
            )

            line_bot_api.reply_message(
                event.reply_token,
                confirm
            )

            return

        # 履歴表示
        if text == "削除_履歴":

            rows = get_recent_expenses(user_id)

            if not rows:

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("履歴なし")
                )

                return

            columns = []

            for expense_id, category, amount in rows:

                columns.append(
                    CarouselColumn(
                        title="削除確認",
                        text=f"{category}:{amount}円",
                        actions=[
                            MessageAction(
                                label="この履歴を削除",
                                text=f"確認削除_{expense_id}"
                            )
                        ]
                    )
                )

            carousel = TemplateSendMessage(
                alt_text="履歴削除",
                template=CarouselTemplate(
                    columns=columns
                )
            )

            line_bot_api.reply_message(
                event.reply_token,
                carousel
            )

            return

        # 履歴削除確認
        if text.startswith("確認削除_"):

            expense_id = text.split("_")[1]

            confirm = TemplateSendMessage(
                alt_text="確認",
                template=ConfirmTemplate(
                    text="この履歴を削除する？",
                    actions=[

                        MessageAction(
                            label="はい",
                            text=f"実行削除_{expense_id}"
                        ),

                        MessageAction(
                            label="いいえ",
                            text="キャンセル"
                        )
                    ]
                )
            )

            line_bot_api.reply_message(
                event.reply_token,
                confirm
            )

            return

        # 履歴削除実行
        if text.startswith("実行削除_"):

            expense_id = text.split("_")[1]

            delete_by_id(
                expense_id,
                user_id
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "履歴削除したよ🗑️"
                )
            )

            return

        # 最新削除実行
        if text == "実行_削除最新":

            success = delete_latest(user_id)

            msg = (
                "最新1件削除したよ🗑️"
                if success
                else "削除データなし"
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )

            return

        # 今月削除実行
        if text == "実行_削除今月":

            delete_month(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "今月データ全部削除したよ🗑️"
                )
            )

            return

                # 通常入力
        match = re.match(
            r'^(\d+)\s*(.+)$',
            text
        )

        if match:

            amount = int(match.group(1))
            category = match.group(2)

            save_expense(
                user_id,
                amount,
                category
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=f"{category}:{amount}円 登録OK👍"
                )
            )

            return

        # 未対応入力
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text=(
                    "対応してない入力だよ😢\n\n"
                    "『使い方』と入力してね！"
                )
            )
        )

    except:

        print(traceback.format_exc())

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("エラー")
        )
# ======================
# OCR画像
# ======================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id

    try:

        current_count = get_monthly_ocr_count(
            user_id
        )

        if current_count >= OCR_LIMIT:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=(
                        f"今月OCR上限😢\n"
                        f"月{OCR_LIMIT}回まで"
                    )
                )
            )

            return

        message_content = line_bot_api.get_message_content(
            event.message.id
        )

        image_bytes = message_content.content

        text = detect_text_from_image(
            image_bytes
        )

        amount = extract_max_price(text)

        if not amount:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="金額読み取れなかった😢"
                )
            )

            return

        save_ocr_log(user_id)

        remain = OCR_LIMIT - (
            current_count + 1
        )

        confirm = TemplateSendMessage(
            alt_text="確認",
            template=ConfirmTemplate(
                text=f"{amount}円で登録する？\n残り:{remain}回",
                actions=[

                    MessageAction(
                        label="はい",
                        text=f"OK_{amount}"
                    ),

                    MessageAction(
                        label="いいえ",
                        text="キャンセル"
                    )
                ]
            )
        )

        line_bot_api.reply_message(
            event.reply_token,
            confirm
        )

    except:

        print(traceback.format_exc())

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("OCRエラー")
        )

# ======================
# 起動
# ======================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", 10000)
        )
    )
