# ======================
# Flask / LINE Bot 家計簿（予算警告追加版）
# ======================

from flask import Flask, request, send_file
from linebot import LineBotApi, WebhookHandler
from linebot.models import *

from collections import defaultdict
from datetime import datetime
import calendar
import os, re, traceback, requests
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager

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
# DB接続
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

    conn.commit()

    cur.close()
    put_conn(conn)

init_db()

# ======================
# 保存
# ======================

def save_expense(user_id, amount, category):

    conn = get_conn()
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

    cur.close()
    put_conn(conn)

# ======================
# 最新削除
# ======================

def delete_latest(user_id):

    conn = get_conn()
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
    """, (user_id,))

    conn.commit()

    cur.close()
    put_conn(conn)

# ======================
# 今月削除
# ======================

def delete_month(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    conn.commit()

    cur.close()
    put_conn(conn)

# ======================
# 履歴10件取得
# ======================

def get_recent_expenses(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, category, amount
        FROM expenses
        WHERE user_id=%s
        ORDER BY created_at DESC
        LIMIT 10
    """, (user_id,))

    rows = cur.fetchall()

    cur.close()
    put_conn(conn)

    return rows

# ======================
# ID削除
# ======================

def delete_by_id(expense_id, user_id):

    conn = get_conn()
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

    cur.close()
    put_conn(conn)

# ======================
# 予算保存
# ======================

def save_budget(user_id, amount):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO budgets (
            user_id,
            amount
        )
        VALUES (%s,%s)

        ON CONFLICT (user_id)
        DO UPDATE SET amount=EXCLUDED.amount
    """, (
        user_id,
        amount
    ))

    conn.commit()

    cur.close()
    put_conn(conn)

# ======================
# 予算取得
# ======================

def get_budget(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT amount
        FROM budgets
        WHERE user_id=%s
    """, (user_id,))

    row = cur.fetchone()

    cur.close()
    put_conn(conn)

    if row:
        return row[0]

    return None

# ======================
# 予算削除
# ======================

def delete_budget(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        DELETE FROM budgets
        WHERE user_id=%s
    """, (user_id,))

    conn.commit()

    cur.close()
    put_conn(conn)

# ======================
# OCR回数
# ======================

def get_monthly_ocr_count(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM ocr_logs
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    count = cur.fetchone()[0]

    cur.close()
    put_conn(conn)

    return count

# ======================
# OCRログ
# ======================

def save_ocr_log(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO ocr_logs (user_id)
        VALUES (%s)
    """, (user_id,))

    conn.commit()

    cur.close()
    put_conn(conn)

# ======================
# 今月データ
# ======================

def get_month_data(user_id):

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT category, amount
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
            = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    rows = cur.fetchall()

    cur.close()
    put_conn(conn)

    return rows

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

    graph_path = "graph.png"

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

    numbers = re.findall(r'\d{2,6}', text)

    if not numbers:
        return None

    return max(map(int, numbers))

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

    return "OK"


@app.route("/weather")
def send_weather():
    try:

        user_id = "Ucae4b4a79830d56a8bf4d63159763afd"

        # ======================
        # 天気コード変換
        # ======================

        def get_weather_text(code):

            if code == 0:
                return "晴れ"

            elif code in [1, 2, 3]:
                return "くもり"

            elif code in [45, 48]:
                return "霧"

            elif code >= 51:
                return "雨"

            return "晴れ"

        # ======================
        # 都市ごとの取得
        # ======================

        def fetch_weather(city, latitude, longitude):

            url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={latitude}"
                f"&longitude={longitude}"
                "&daily=weathercode,"
                "temperature_2m_max,"
                "temperature_2m_min,"
                "precipitation_probability_max"
                "&hourly=weathercode"
                "&timezone=Asia%2FTokyo"
            )

            response = requests.get(url)

            data = response.json()

            # 日別

            max_temp = data["daily"][
                "temperature_2m_max"
            ][0]

            min_temp = data["daily"][
                "temperature_2m_min"
            ][0]

            rain = data["daily"][
                "precipitation_probability_max"
            ][0]

            # hourly取得

            hourly_codes = data["hourly"][
                "weathercode"
            ]

            # 9時 = 午前
            morning_code = hourly_codes[9]

            # 15時 = 午後
            afternoon_code = hourly_codes[15]

            morning_weather = get_weather_text(
                morning_code
            )

            afternoon_weather = get_weather_text(
                afternoon_code
            )

            text = (
                f"📍{city}\n"
                f"午前：{morning_weather}\n"
                f"午後：{afternoon_weather}\n"
                f"最高：{max_temp}℃\n"
                f"最低：{min_temp}℃\n"
                f"降水確率：{rain}%"
            )

            if rain >= 50:

                text += "\n☔ 傘推奨"

            return text

        # ======================
        # 静岡市
        # ======================

        shizuoka = fetch_weather(
            "静岡市",
            34.9769,
            138.3831
        )

        # ======================
        # 沼津市
        # ======================

        numazu = fetch_weather(
            "沼津市",
            35.0956,
            138.8635
        )

        # ======================
        # LINE送信
        # ======================

        message = (
            "☀️ 今日の天気\n\n"
            f"{shizuoka}\n\n"
            f"{numazu}"
        )

        line_bot_api.push_message(
            user_id,
            TextSendMessage(text=message)
        )

        return "weather sent"

    except Exception as e:

        print(traceback.format_exc())

        return str(e)


# ======================
# graph
# ======================

@app.route("/graph.png")
def graph():

    return send_file(
        "graph.png",
        mimetype="image/png"
    )

# ======================
# テキスト
# ======================

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()

    user_id = event.source.user_id


    print("USER_ID:", user_id)


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
                    f"{amount}円 登録したよ👍"
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
                    original_content_url=f"{BASE_URL}/graph.png",
                    preview_image_url=f"{BASE_URL}/graph.png"
                )
            )

            return

        # 今月
        if text == "今月":

            rows = get_month_data(user_id)

            if not rows:

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("今月データなし")
                )

                return

            total = sum(
                amount for _, amount in rows
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    f"今月合計：{total}円"
                )
            )

            return

        # ======================
        # 予算メニュー
        # ======================

        if text == "予算":

            buttons = TemplateSendMessage(
                alt_text="予算メニュー",
                template=ButtonsTemplate(
                    title="予算メニュー",
                    text="何をする？",
                    actions=[

                        MessageAction(
                            label="予算を追加する",
                            text="設定予算"
                        ),

                        MessageAction(
                            label="予算の確認",
                            text="予算確認"
                        ),

                        MessageAction(
                            label="予算リセット",
                            text="予算リセット"
                        )
                    ]
                )
            )

            line_bot_api.reply_message(
                event.reply_token,
                buttons
            )

            return

        # ======================
        # 予算設定開始
        # ======================

        if text == "設定予算":

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "予算金額を入力してね👇\n"
                    "例：50000"
                )
            )

            return

        # ======================
        # 予算確認
        # ======================

        if text == "予算確認":

            budget = get_budget(user_id)

            if not budget:

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(
                        "予算未設定だよ"
                    )
                )

                return

            rows = get_month_data(user_id)

            total = sum(
                amount for _, amount in rows
            )

            remain = budget - total

            # ======================
            # 残り日数
            # ======================

            now = datetime.now()

            last_day = calendar.monthrange(
                now.year,
                now.month
            )[1]

            remain_days = last_day - now.day

            if remain_days <= 0:
                remain_days = 1

            # ======================
            # 1日使える金額
            # ======================

            per_day = int(remain / remain_days)

            # ======================
            # 超過警告
            # ======================

            warning = ""

            if remain < 0:

                warning = "\n⚠️予算オーバー"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    f"💰今月予算：{budget}円\n"
                    f"📉使用額：{total}円\n"
                    f"💵残り：{remain}円\n"
                    f"📅残り日数：{remain_days}日\n"
                    f"🪙1日あと：{per_day}円使える"
                    f"{warning}"
                )
            )

            return

        # ======================
        # 予算リセット
        # ======================

        if text == "予算リセット":

            delete_budget(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "予算リセットしたよ🗑️"
                )
            )

            return

        # ======================
        # リセットメニュー
        # ======================

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

            actions = []

            for expense_id, category, amount in rows:

                actions.append(
                    MessageAction(
                        label=f"{category}:{amount}円",
                        text=f"確認削除_{expense_id}"
                    )
                )

            buttons = TemplateSendMessage(
                alt_text="履歴削除",
                template=ButtonsTemplate(
                    title="履歴10件",
                    text="削除する項目を選んでね",
                    actions=actions[:4]
                )
            )

            line_bot_api.reply_message(
                event.reply_token,
                buttons
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

            delete_latest(user_id)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "最新1件削除したよ🗑️"
                )
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

        # ======================
        # 予算入力
        # ======================

        if re.fullmatch(r'\d{3,8}', text):

            save_budget(
                user_id,
                int(text)
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    f"予算 {text}円 を設定したよ👍"
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

            return

        # ======================
        # 予算入力
        # ======================

        if re.fullmatch(r'\d{3,8}', text):

            save_budget(
                user_id,
                int(text)
            )

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    f"予算 {text}円 を設定したよ👍"
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
                    f"{category}:{amount}円 登録OK👍"
                )
            )

            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                "入力例👇\n"
                "1000 食費\n"
                "今月\n"
                "グラフ\n"
                "予算\n"
                "リセット\n"
                "使い方\n"
                "レシート送信📸"
            )
        )

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
                    f"{category}:{amount}円 登録OK👍"
                )
            )

            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                "入力例👇\n"
                "1000 食費\n"
                "今月\n"
                "グラフ\n"
                "予算\n"
                "リセット\n"
                "レシート送信📸"
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
                    f"今月OCR上限😢\n"
                    f"月{OCR_LIMIT}回まで"
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

        save_ocr_log(user_id)

        amount = extract_max_price(text)

        if not amount:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "金額読み取れなかった😢"
                )
            )

            return

        remain = OCR_LIMIT - (
            current_count + 1
        )

        flex = TemplateSendMessage(
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
            flex
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
