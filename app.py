# ======================
# Flask / LINE Bot 家計簿（完全統合版）
# OCR + 今月 + グラフ + 予算 + リセット
# ======================

from flask import Flask, request, Response
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
import os, re, io, traceback

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.pool import SimpleConnectionPool

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager

# ===== OCR =====
from google.cloud import vision

# ======================
# 日本語フォント
# ======================
font_prop = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONT_PATH = os.path.join(BASE_DIR, "fonts", "ipaexg.ttf")

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

# ======================
# DB
# ======================
pool = SimpleConnectionPool(
    1,
    10,
    dsn=DATABASE_URL,
    sslmode="require"
)

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

    # 支出
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            amount INTEGER,
            category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 予算
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
# DB操作
# ======================
def save_expense(user_id, amount, category):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO expenses
        (user_id, amount, category)
        VALUES (%s, %s, %s)
        """,
        (user_id, amount, category)
    )

    conn.commit()

    cur.close()

    put_conn(conn)

def get_month_total(user_id):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)
        = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    total = cur.fetchone()[0]

    cur.close()

    put_conn(conn)

    return total

def set_budget(user_id, amount):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO budgets (user_id, amount)
        VALUES (%s, %s)

        ON CONFLICT (user_id)

        DO UPDATE SET
        amount = EXCLUDED.amount
    """, (user_id, amount))

    conn.commit()

    cur.close()

    put_conn(conn)

def get_budget(user_id):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute("""
        SELECT amount
        FROM budgets
        WHERE user_id=%s
    """, (user_id,))

    result = cur.fetchone()

    cur.close()

    put_conn(conn)

    if result:
        return result[0]

    return None

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

def extract_max_price(text):

    numbers = re.findall(r'\d{2,6}', text)

    if not numbers:
        return None

    return max(map(int, numbers))

# ======================
# グラフ
# ======================
@app.route("/chart/<user_id>")
def chart(user_id):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
    """, (user_id,))

    data = cur.fetchall()

    cur.close()

    put_conn(conn)

    plt.figure(figsize=(6,6))

    if not data:

        plt.text(
            0.5,
            0.5,
            "データなし",
            ha='center',
            fontproperties=font_prop if font_prop else None
        )

    else:

        labels = [str(d[0]) for d in data]

        values = [d[1] for d in data]

        plt.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            textprops={
                "fontproperties": font_prop
            } if font_prop else {}
        )

    plt.tight_layout()

    img = io.BytesIO()

    plt.savefig(img, format="png")

    plt.close()

    img.seek(0)

    return Response(
        img.getvalue(),
        mimetype="image/png"
    )

# ======================
# Webhook
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

@app.route("/")
def home():
    return "OK"

# ======================
# テキスト処理
# ======================
@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip().replace("　", " ")

    user_id = event.source.user_id

    try:

        # ======================
        # OCR OK
        # ======================
        if text.startswith("OK_"):

            parts = text.split("_")

            amount = int(parts[1])

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

        # ======================
        # ヘルプ
        # ======================
        if text in ["ヘルプ", "使い方", "はじめて"]:

            msg = """【使い方】

■ 手入力
1000 食費

■ OCR
レシート画像送信📸

■ 今月確認
今月

■ 円グラフ
グラフ

■ 予算設定
予算 30000

■ 全削除
リセット
"""

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )

            return

        # ======================
        # 今月
        # ======================
        if text in ["今月", "今月合計"]:

            total = get_month_total(user_id)

            budget = get_budget(user_id)

            msg = f"【今月の支出】\n合計：{total}円\n"

            if budget:

                remain = budget - total

                msg += f"残り：{remain}円\n"

                if total > budget:
                    msg += "⚠️予算オーバー"

            else:

                msg += "予算未設定\n"

            msg += "\nグラフ → 円グラフ表示📊"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )

            return

        # ======================
        # グラフ
        # ======================
        if text == "グラフ":

            url = f"{BASE_URL}/chart/{user_id}"

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(url, url)
            )

            return

        # ======================
        # 予算
        # ======================
        budget_match = re.match(
            r'予算\s*(\d+)',
            text
        )

        if budget_match:

            amount = int(
                budget_match.group(1)
            )

            set_budget(user_id, amount)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    f"予算を{amount}円に設定👍"
                )
            )

            return

        # ======================
        # リセット
        # ======================
        if text == "リセット":

            conn = get_conn()

            cur = conn.cursor()

            cur.execute("""
                DELETE FROM expenses
                WHERE user_id=%s
            """, (user_id,))

            conn.commit()

            cur.close()

            put_conn(conn)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("全データ削除したよ")
            )

            return

        # ======================
        # 通常入力
        # ======================
        match = re.match(
            r'^(\d+)(円)?\s*(.+)$',
            text
        )

        if match:

            amount = int(match.group(1))

            category = match.group(3).strip()

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

        # ======================
        # その他
        # ======================
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                "「使い方」と送ると説明出るよ👍"
            )
        )

    except:

        print(traceback.format_exc())

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("エラー")
        )

# ======================
# OCR画像処理
# ======================
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id

    try:

        message_content = line_bot_api.get_message_content(
            event.message.id
        )

        image_bytes = message_content.content

        # OCR
        text = detect_text_from_image(
            image_bytes
        )

        print("===== OCR RESULT =====")

        print(text)

        # 金額抽出
        amount = extract_max_price(text)

        if not amount:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    "金額読み取れなかった😢"
                )
            )

            return

        # 確認ボタン
        flex = TemplateSendMessage(
            alt_text="確認",
            template=ConfirmTemplate(
                text=f"{amount}円で登録する？",
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
        port=int(os.environ.get("PORT", 10000))
    )