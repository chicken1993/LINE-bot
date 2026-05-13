# =========================================================
# Flask + LINE Bot 家計簿アプリ
# 超初心者向けコメント完全版
# =========================================================

# ======================
# Flask関連
# ======================

# Flask
# → PythonでWebアプリを作るライブラリ
from flask import Flask, request, send_file

# ======================
# LINE Bot SDK
# ======================

# LINE Bot操作用
from linebot import LineBotApi, WebhookHandler

# LINEメッセージ関連クラス
from linebot.models import *

# ======================
# Python標準ライブラリ
# ======================

# defaultdict
# → 存在しないキーでも自動で0を入れてくれる辞書
from collections import defaultdict

# 現在日時取得
from datetime import datetime

# カレンダー機能
import calendar

# os → ファイル操作
# re → 正規表現
# traceback → エラー表示
# requests → API通信
import os, re, traceback, requests

# ======================
# .env読み込み
# ======================

# .envファイルの内容を使えるようにする
from dotenv import load_dotenv

# .env読み込み実行
load_dotenv()

# ======================
# PostgreSQL
# ======================

# PostgreSQL接続
import psycopg2

# DB接続プール
from psycopg2.pool import SimpleConnectionPool

# ======================
# グラフ描画
# ======================

import matplotlib

# Renderなどサーバー環境用設定
matplotlib.use("Agg")

# グラフ描画
import matplotlib.pyplot as plt

# フォント
from matplotlib import font_manager

# ======================
# Google OCR
# ======================

# Google Vision API
from google.cloud import vision

# =========================================================
# 日本語フォント設定
# =========================================================

# フォント情報を入れる変数
font_prop = None

# 現在のフォルダ取得
BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

# フォントファイル場所
FONT_PATH = os.path.join(
    BASE_DIR,
    "fonts",
    "ipaexg.ttf"
)

try:

    # フォント存在確認
    if os.path.exists(FONT_PATH):

        # matplotlibへ追加
        font_manager.fontManager.addfont(
            FONT_PATH
        )

        # フォント読み込み
        font_prop = font_manager.FontProperties(
            fname=FONT_PATH
        )

        # matplotlibへ設定
        plt.rcParams["font.family"] = (
            font_prop.get_name()
        )

        # マイナス文字化け防止
        plt.rcParams["axes.unicode_minus"] = False

        print("✅ 日本語フォントOK")

    else:

        # フォント無い時
        plt.rcParams["font.family"] = "DejaVu Sans"

except:

    # エラー時
    plt.rcParams["font.family"] = "DejaVu Sans"

# =========================================================
# Flask初期化
# =========================================================

# Flaskアプリ作成
app = Flask(__name__)

# .envから取得
CHANNEL_ACCESS_TOKEN = os.getenv(
    "CHANNEL_ACCESS_TOKEN"
)

CHANNEL_SECRET = os.getenv(
    "CHANNEL_SECRET"
)

BASE_URL = os.getenv(
    "BASE_URL"
)

DATABASE_URL = os.getenv(
    "DATABASE_URL"
)

# LINE Bot API操作用
line_bot_api = LineBotApi(
    CHANNEL_ACCESS_TOKEN
)

# LINE Webhook受信用
handler = WebhookHandler(
    CHANNEL_SECRET
)

# DB接続プール
pool = SimpleConnectionPool(

    # 最小接続数
    1,

    # 最大接続数
    10,

    # DB URL
    dsn=DATABASE_URL,

    # SSL接続
    sslmode="require"
)

# OCR月間上限
OCR_LIMIT = 20

# =========================================================
# DB接続
# =========================================================

# DB接続取得
def get_conn():

    return pool.getconn()

# DB接続返却
def put_conn(conn):

    pool.putconn(conn)

# =========================================================
# DB初期化
# =========================================================

def init_db():

    # DB接続取得
    conn = get_conn()

    # SQL実行用
    cur = conn.cursor()

    # ======================
    # 支出テーブル
    # ======================

    cur.execute("""

        CREATE TABLE IF NOT EXISTS expenses (

            # 自動ID
            id SERIAL PRIMARY KEY,

            # LINEユーザーID
            user_id TEXT,

            # 金額
            amount INTEGER,

            # カテゴリ
            category TEXT,

            # 登録日時
            created_at TIMESTAMP
            DEFAULT CURRENT_TIMESTAMP
        )

    """)

    # ======================
    # OCRログ
    # ======================

    cur.execute("""

        CREATE TABLE IF NOT EXISTS ocr_logs (

            id SERIAL PRIMARY KEY,

            user_id TEXT,

            created_at TIMESTAMP
            DEFAULT CURRENT_TIMESTAMP
        )

    """)

    # ======================
    # 予算
    # ======================

    cur.execute("""

        CREATE TABLE IF NOT EXISTS budgets (

            # user_idを主キー
            user_id TEXT PRIMARY KEY,

            # 予算金額
            amount INTEGER
        )

    """)

    # 保存
    conn.commit()

    # cursor終了
    cur.close()

    # 接続返却
    put_conn(conn)

# 起動時にDB作成
init_db()

# =========================================================
# 支出保存
# =========================================================

def save_expense(user_id, amount, category):

    # DB接続
    conn = get_conn()

    # SQL実行用
    cur = conn.cursor()

    # INSERT
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

    # 保存
    conn.commit()

    # 終了
    cur.close()

    put_conn(conn)

# =========================================================
# 最新データ削除
# =========================================================

def delete_latest(user_id):

    conn = get_conn()
    cur = conn.cursor()

    # 一番新しいデータ削除
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

# =========================================================
# 今月全部削除
# =========================================================

def delete_month(user_id):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute("""

        DELETE FROM expenses

        WHERE user_id=%s

        AND DATE_TRUNC('month', created_at)
        =
        DATE_TRUNC('month', CURRENT_DATE)

    """, (user_id,))

    conn.commit()

    cur.close()

    put_conn(conn)

# =========================================================
# 最近10件取得
# =========================================================

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

    # データ取得
    rows = cur.fetchall()

    cur.close()

    put_conn(conn)

    return rows

# =========================================================
# ID指定削除
# =========================================================

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

# =========================================================
# 予算保存
# =========================================================

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

# =========================================================
# 予算取得
# =========================================================

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

    # データ存在時
    if row:

        return row[0]

    return None

# =========================================================
# 予算削除
# =========================================================

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

# =========================================================
# OCR回数取得
# =========================================================

def get_monthly_ocr_count(user_id):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute("""

        SELECT COUNT(*)

        FROM ocr_logs

        WHERE user_id=%s

        AND DATE_TRUNC('month', created_at)
        =
        DATE_TRUNC('month', CURRENT_DATE)

    """, (user_id,))

    count = cur.fetchone()[0]

    cur.close()

    put_conn(conn)

    return count

# =========================================================
# OCRログ保存
# =========================================================

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

# =========================================================
# 今月データ取得
# =========================================================

def get_month_data(user_id):

    conn = get_conn()

    cur = conn.cursor()

    cur.execute("""

        SELECT category, amount

        FROM expenses

        WHERE user_id=%s

        AND DATE_TRUNC('month', created_at)
        =
        DATE_TRUNC('month', CURRENT_DATE)

    """, (user_id,))

    rows = cur.fetchall()

    cur.close()

    put_conn(conn)

    return rows

# =========================================================
# 円グラフ作成
# =========================================================

def create_graph(user_id):

    # 今月データ取得
    rows = get_month_data(user_id)

    # データ無し
    if not rows:

        return None

    # カテゴリ別合計
    data = defaultdict(int)

    # 集計
    for category, amount in rows:

        data[category] += amount

    # ラベル
    labels = list(data.keys())

    # 金額
    values = list(data.values())

    # グラフサイズ
    plt.figure(figsize=(6,6))

    # 円グラフ
    plt.pie(

        values,

        labels=labels,

        autopct="%1.1f%%"
    )

    # タイトル
    plt.title("今月の支出")

    # 保存ファイル名
    graph_path = "graph.png"

    # 保存
    plt.savefig(graph_path)

    # メモリ解放
    plt.close()

    return graph_path

# =========================================================
# OCR
# =========================================================

def detect_text_from_image(image_content):

    # Google OCRクライアント
    client = vision.ImageAnnotatorClient()

    # 画像作成
    image = vision.Image(
        content=image_content
    )

    # OCR実行
    response = client.text_detection(
        image=image
    )

    # テキスト取得
    texts = response.text_annotations

    # テキスト存在時
    if texts:

        return texts[0].description

    return ""

# =========================================================
# 金額抽出
# =========================================================

def extract_max_price(text):

    # 2〜6桁数字抽出
    numbers = re.findall(
        r'\d{2,6}',
        text
    )

    # 無ければNone
    if not numbers:

        return None

    # 最大値返す
    return max(map(int, numbers))

# =========================================================
# callback
# =========================================================

@app.route("/callback", methods=["POST"])

def callback():

    # LINEから来たデータ
    body = request.get_data(
        as_text=True
    )

    # 署名取得
    signature = request.headers.get(
        "X-Line-Signature"
    )

    try:

        # LINEイベント処理
        handler.handle(
            body,
            signature
        )

    except:

        # エラー表示
        print(
            traceback.format_exc()
        )

    return "OK"

# =========================================================
# home
# =========================================================

@app.route("/")

def home():

    return "OK"

# =========================================================
# graph画像表示
# =========================================================

@app.route("/graph.png")

def graph():

    return send_file(

        "graph.png",

        mimetype="image/png"
    )

# =========================================================
# テキスト受信
# =========================================================

@handler.add(
    MessageEvent,
    message=TextMessage
)

def handle_text(event):

    # メッセージ取得
    text = event.message.text.strip()

    # ユーザーID取得
    user_id = event.source.user_id

    print("USER_ID:", user_id)

    try:

        # =================================================
        # OCR登録
        # =================================================

        if text.startswith("OK_"):

            # OK_500 → 500取得
            amount = int(
                text.split("_")[1]
            )

            # DB保存
            save_expense(

                user_id,

                amount,

                "レシート"
            )

            # LINE返信
            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    f"{amount}円 登録したよ👍"
                )
            )

            return

        # =================================================
        # 今月合計
        # =================================================

        if text == "今月":

            rows = get_month_data(user_id)

            # データ無し
            if not rows:

                line_bot_api.reply_message(

                    event.reply_token,

                    TextSendMessage(
                        "今月データなし"
                    )
                )

                return

            # 合計計算
            total = sum(

                amount

                for _, amount in rows
            )

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    f"今月合計：{total}円"
                )
            )

            return

        # =================================================
        # 通常入力
        # 例:
        # 1000 食費
        # =================================================

        match = re.match(

            r'^(\d+)\s*(.+)$',

            text
        )

        if match:

            # 金額
            amount = int(
                match.group(1)
            )

            # カテゴリ
            category = match.group(2)

            # DB保存
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

        # =================================================
        # 不明入力
        # =================================================

        line_bot_api.reply_message(

            event.reply_token,

            TextSendMessage(

                "入力例👇\n"

                "1000 食費\n"

                "今月\n"

                "グラフ\n"

                "予算\n"

                "リセット"
            )
        )

    except:

        # エラー表示
        print(traceback.format_exc())

        line_bot_api.reply_message(

            event.reply_token,

            TextSendMessage("エラー")
        )

# =========================================================
# 画像受信
# =========================================================

@handler.add(
    MessageEvent,
    message=ImageMessage
)

def handle_image(event):

    # ユーザーID
    user_id = event.source.user_id

    try:

        # OCR使用回数取得
        current_count = get_monthly_ocr_count(
            user_id
        )

        # 上限チェック
        if current_count >= OCR_LIMIT:

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(

                    f"今月OCR上限😢\n"

                    f"月{OCR_LIMIT}回まで"
                )
            )

            return

        # 画像取得
        message_content = (
            line_bot_api.get_message_content(
                event.message.id
            )
        )

        # バイナリ取得
        image_bytes = message_content.content

        # OCR実行
        text = detect_text_from_image(
            image_bytes
        )

        # OCRログ保存
        save_ocr_log(user_id)

        # 金額抽出
        amount = extract_max_price(text)

        # 金額無し
        if not amount:

            line_bot_api.reply_message(

                event.reply_token,

                TextSendMessage(
                    "金額読み取れなかった😢"
                )
            )

            return

        # 残り回数
        remain = OCR_LIMIT - (
            current_count + 1
        )

        # 確認画面
        flex = TemplateSendMessage(

            alt_text="確認",

            template=ConfirmTemplate(

                text=(
                    f"{amount}円で登録する？\n"
                    f"残り:{remain}回"
                ),

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

        # LINE返信
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

# =========================================================
# Flask起動
# =========================================================

if __name__ == "__main__":

    app.run(

        # 外部アクセス許可
        host="0.0.0.0",

        # Render用PORT
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )