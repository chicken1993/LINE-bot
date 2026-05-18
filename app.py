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

        # expenses
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                amount INTEGER,
                category TEXT,
                store TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

        # store列追加（既存DB対策）
        try:

            cur.execute("""
                ALTER TABLE expenses
                ADD COLUMN store TEXT
            """)

            conn.commit()

        except:

            conn.rollback()

        # OCR logs
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ocr_logs (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # budgets
        cur.execute("""
            CREATE TABLE IF NOT EXISTS budgets (
                user_id TEXT PRIMARY KEY,
                amount INTEGER
            )
        """)

        # users
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

def save_expense(
    user_id,
    amount,
    category,
    store=None
):

    conn = get_conn()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO expenses (
                user_id,
                amount,
                category,
                store
            )
            VALUES (%s,%s,%s,%s)
        """, (
            user_id,
            amount,
            category,
            store
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
            SELECT id, category, amount, store
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

    response = client.document_text_detection(
        image=image
    )

    texts = response.text_annotations

    if texts:
        return texts[0].description

    return ""

# ======================
# カテゴリ判定
# ======================

CATEGORY_RULES = {

    "食費": [
        "セブン",
        "ファミマ",
        "ローソン",
        "イオン",
        "スーパー",
        "マックスバリュ",
        "ドンキ"
    ],

    "交通": [
        "ENEOS",
        "JR",
        "PASMO",
        "Suica"
    ],

    "日用品": [
        "マツキヨ",
        "ウエルシア",
        "ツルハ"
    ]
}

def classify_category(store_name):

    for category, keywords in CATEGORY_RULES.items():

        for keyword in keywords:

            if keyword.lower() in store_name.lower():

                return category

    return "その他"

# ======================
# 店舗名抽出
# ======================

def extract_store_name(text):

    lines = text.splitlines()

    ignore_words = [
        "TEL",
        "レシート",
        "領収書",
        "営業時間"
    ]

    for line in lines[:8]:

        line = line.strip()

        if len(line) < 2:
            continue

        if any(w in line for w in ignore_words):
            continue

        if re.search(r'\d{4,}', line):
            continue

        return line

    return "不明"

# ======================
# OCR解析
# ======================

def extract_receipt_info(text):

    lines = text.splitlines()

    total_keywords = [
        "合計",
        "TOTAL",
        "Total",
        "現計",
        "ご利用額",
        "お会計"
    ]

    candidates = []

    for line in lines:

        clean = line.replace(",", "")

        for keyword in total_keywords:

            if keyword.lower() in clean.lower():

                nums = re.findall(
                    r'(\d{2,6})',
                    clean
                )

                for n in nums:

                    candidates.append(int(n))

    if candidates:

        amount = candidates[-1]

    else:

        nums = re.findall(
            r'\d{2,6}',
            text
        )

        amount = (
            max(map(int, nums))
            if nums else None
        )

    store = extract_store_name(text)

    category = classify_category(store)

    return {
        "amount": amount,
        "store": store,
        "category": category
    }

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

            parts = text.split("_")

            amount = int(parts[1])

            category = parts[2]

            store = "_".join(parts[3:])

            save_expense(
                user_id,
                amount,
                category,
                store
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=(
                        f"登録したよ👍\n\n"
                        f"店舗：{store}\n"
                        f"カテゴリ：{category}\n"
                        f"金額：{amount}円"
                    )
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

        # 履歴削除
        if text == "削除_履歴":

            rows = get_recent_expenses(user_id)

            if not rows:

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("履歴なし")
                )

                return

            columns = []

            for expense_id, category, amount, store in rows:

                columns.append(
                    CarouselColumn(
                        title="削除確認",
                        text=f"{store or category}:{amount}円",
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

        # 通常入力
        match = re.match(
            r'^(\d+)\s*(.+)$',
            text
        )

        if match:

            amount = int(match.group(1))

            category = match.group(2).strip()

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

        # 未対応
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="『使い方』と入力してね！"
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

        print(text)

        info = extract_receipt_info(text)

        amount = info["amount"]
        store = info["store"]
        category = info["category"]

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
                text=(
                    f"店舗：{store}\n"
                    f"カテゴリ：{category}\n"
                    f"金額：{amount}円\n\n"
                    f"登録する？\n"
                    f"残り:{remain}回"
                ),
                actions=[

                    MessageAction(
                        label="はい",
                        text=f"OK_{amount}_{category}_{store}"
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