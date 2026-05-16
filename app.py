# =====================================================
# LINE 家計簿Bot 完全版
# Flask + PostgreSQL + OCR + グラフ + 天気
# =====================================================

# =====================================================
# Flask関連
# =====================================================

from flask import Flask, request, send_file

# LINE Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.models import *
from linebot.models.events import FollowEvent

# =====================================================
# Python標準ライブラリ
# =====================================================

import os
import re
import traceback
import requests

from collections import defaultdict

# =====================================================
# .env 読み込み
# Render環境変数を使う
# =====================================================

from dotenv import load_dotenv
load_dotenv()

# =====================================================
# PostgreSQL
# =====================================================

import psycopg2
from psycopg2.pool import SimpleConnectionPool

# =====================================================
# matplotlib
# グラフ作成ライブラリ
# =====================================================

import matplotlib

# サーバー環境(Render)ではGUI不要
matplotlib.use("Agg")

# グラフ描画
import matplotlib.pyplot as plt

# フォント管理
from matplotlib import font_manager

# =====================================================
# Google Vision OCR
# =====================================================

from google.cloud import vision

# =====================================================
# 日本語フォント設定
# =====================================================

# app.py の場所取得
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# フォントファイル場所
FONT_PATH = os.path.join(
    BASE_DIR,
    "fonts",
    "ipaexg.ttf"
)

try:

    # フォント存在チェック
    if os.path.exists(FONT_PATH):

        # matplotlib にフォント追加
        font_manager.fontManager.addfont(FONT_PATH)

        # フォント読み込み
        font_prop = font_manager.FontProperties(
            fname=FONT_PATH
        )

        # matplotlib 全体へ適用
        plt.rcParams["font.family"] = font_prop.get_name()

        # マイナス文字化け防止
        plt.rcParams["axes.unicode_minus"] = False

        print("✅ 日本語フォントOK")

    else:

        # フォント無い場合
        plt.rcParams["font.family"] = "DejaVu Sans"

        print("⚠️ フォントなし")

except Exception as e:

    print("フォントエラー:", e)

    plt.rcParams["font.family"] = "DejaVu Sans"

# =====================================================
# Flask開始
# =====================================================

app = Flask(__name__)

# =====================================================
# 環境変数取得
# =====================================================

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")
DATABASE_URL = os.getenv("DATABASE_URL")

# =====================================================
# LINE Bot 初期化
# =====================================================

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

handler = WebhookHandler(CHANNEL_SECRET)

# =====================================================
# PostgreSQL接続プール
# =====================================================

pool = SimpleConnectionPool(
    1,
    10,
    dsn=DATABASE_URL,
    sslmode="require"
)

# OCR月間制限
OCR_LIMIT = 20

# =====================================================
# DB接続取得
# =====================================================

def get_conn():
    return pool.getconn()

# =====================================================
# DB接続返却
# =====================================================

def put_conn(conn):
    pool.putconn(conn)

# =====================================================
# DB初期化
# テーブル自動作成
# =====================================================

def init_db():

    conn = get_conn()

    try:

        cur = conn.cursor()

        # 支出テーブル
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                amount INTEGER,
                category TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # OCRログ
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ocr_logs (
                id SERIAL PRIMARY KEY,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ユーザー保存
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

# 起動時DB作成
init_db()

# =====================================================
# 友達追加時
# =====================================================

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

# =====================================================
# 支出保存
# =====================================================

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

# =====================================================
# OCR回数取得
# 今月OCRを何回使ったか数える
# =====================================================

def get_monthly_ocr_count(user_id):

    # DB接続取得
    conn = get_conn()

    try:

        # SQL実行用
        cur = conn.cursor()

        # 今月のOCR回数取得
        cur.execute("""
            SELECT COUNT(*)
            FROM ocr_logs
            WHERE user_id=%s
            AND DATE_TRUNC('month', created_at)
                = DATE_TRUNC('month', CURRENT_DATE)
        """, (user_id,))

        # 件数取得
        count = cur.fetchone()[0]

        # 回数返す
        return count

    finally:

        # DB終了
        cur.close()

        # 接続返却
        put_conn(conn)

# =====================================================
# OCRログ保存
# OCR使用履歴を保存
# =====================================================

def save_ocr_log(user_id):

    conn = get_conn()

    try:

        cur = conn.cursor()

        # OCR使用履歴追加
        cur.execute("""
            INSERT INTO ocr_logs (user_id)
            VALUES (%s)
        """, (user_id,))

        conn.commit()

    finally:

        cur.close()
        put_conn(conn)

# =====================================================
# 今月データ取得
# =====================================================

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

# =====================================================
# 円グラフ作成
# =====================================================

def create_graph(user_id):

    rows = get_month_data(user_id)

    # データ無し
    if not rows:
        return None

    # カテゴリごと合計
    data = defaultdict(int)

    for category, amount in rows:
        data[category] += amount

    labels = list(data.keys())
    values = list(data.values())

    # グラフサイズ
    plt.figure(figsize=(6, 6))

    # 円グラフ
    plt.pie(
        values,
        labels=labels,
        autopct="%1.1f%%"
    )

    plt.title("今月の支出")

    # 保存ファイル名
    graph_path = f"graph_{user_id}.png"

    # 保存
    plt.savefig(graph_path)

    # メモリ解放
    plt.close()

    return graph_path

# =====================================================
# OCR
# =====================================================

def detect_text_from_image(image_content):

    client = vision.ImageAnnotatorClient()

    image = vision.Image(content=image_content)

    response = client.text_detection(image=image)

    texts = response.text_annotations

    if texts:
        return texts[0].description

    return ""

# =====================================================
# 金額抽出
# =====================================================

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

# =====================================================
# callback
# LINE Webhook
# =====================================================

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

# =====================================================
# Render動作確認
# =====================================================

@app.route("/")
def home():
    return "OK", 200

# =====================================================
# グラフ画像取得
# =====================================================

@app.route("/graph/<user_id>.png")
def graph(user_id):

    return send_file(
        f"graph_{user_id}.png",
        mimetype="image/png"
    )

# =====================================================
# テキスト処理
# =====================================================

@handler.add(MessageEvent, message=TextMessage)
def handle_text(event):

    text = event.message.text.strip()

    user_id = event.source.user_id

    try:

        # グラフ表示
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

        # 未対応
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="『1000 食費』で入力してね！"
            )
        )

    except:

        print(traceback.format_exc())

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("エラー")
        )

# =====================================================
# OCR画像処理
# =====================================================

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    user_id = event.source.user_id

    try:

        current_count = get_monthly_ocr_count(
            user_id
        )

        # OCR制限
        if current_count >= OCR_LIMIT:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text=f"今月OCR上限😢"
                )
            )

            return

        # 画像取得
        message_content = line_bot_api.get_message_content(
            event.message.id
        )

        image_bytes = message_content.content

        # OCR実行
        text = detect_text_from_image(
            image_bytes
        )

        # 金額抽出
        amount = extract_max_price(text)

        if not amount:

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="金額読み取れなかった😢"
                )
            )

            return

        # OCRログ保存
        save_ocr_log(user_id)

        # 確認メッセージ
        confirm = TemplateSendMessage(
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
            confirm
        )

    except:

        print(traceback.format_exc())

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("OCRエラー")
        )

# =====================================================
# 起動
# =====================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", 10000)
        )
    )