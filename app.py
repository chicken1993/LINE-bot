# ======================
# Flask（Webサーバー）
# ======================
from flask import Flask, request, Response

# ======================
# LINE Bot SDK（LINEとやり取りするためのライブラリ）
# ======================
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageSendMessage,
    RichMenu, RichMenuArea, RichMenuBounds, MessageAction, RichMenuSize
)

# ======================
# 基本ライブラリ
# ======================
import os          # 環境変数を扱う
import re          # 文字から数字を取り出す
import io          # 画像データを一時保存
import traceback   # エラー表示
from dotenv import load_dotenv  # .envを読み込む

# ======================
# データベース（PostgreSQL）
# ======================
import psycopg2

# ======================
# グラフ作成
# ======================
import matplotlib
matplotlib.use("Agg")  # サーバーでも動くようにする
import matplotlib.pyplot as plt

# ======================
# 初期設定
# ======================
load_dotenv()  # .env読み込み
app = Flask(__name__)  # Flask起動

# LINEの秘密キー読み込み
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")  # RenderのURL

# LINE API接続
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# =========================================================
# DB接続
# =========================================================
def get_conn():
    # PostgreSQLに接続
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

# =========================================================
# テーブル作成（初回だけ）
# =========================================================
def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # 支出データ
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            amount INTEGER,
            category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ユーザー（今回はほぼ使ってない）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

# 起動時に実行
init_db()

# =========================================================
# カテゴリ自動判定
# =========================================================
def classify_category(text):
    # キーワードで自動分類
    rules = [
        ("食費", ["コンビニ", "セブン", "ファミマ", "ローソン", "ご飯", "ランチ"]),
        ("交通費", ["電車", "バス", "タクシー"]),
        ("娯楽", ["ゲーム", "映画"]),
        ("通信費", ["スマホ", "wifi"]),
    ]

    for category, keywords in rules:
        for kw in keywords:
            if kw in text:
                return category

    return "その他"

# =========================================================
# データ保存
# =========================================================
def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()

    # DBに保存
    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s,%s,%s)",
        (user_id, amount, category)
    )

    conn.commit()
    cur.close()
    conn.close()

# =========================================================
# 今月の合計
# =========================================================
def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    # 今月だけ合計
    cur.execute("""
        SELECT COALESCE(SUM(amount),0)
        FROM expenses
        WHERE user_id=%s
        AND DATE_TRUNC('month', created_at)=DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return total

# =========================================================
# リッチメニュー作成（LINEの下のボタン）
# =========================================================
def create_rich_menu():
    return RichMenu(
        size=RichMenuSize(width=2500, height=1686),  # ←画像サイズと一致必須
        selected=True,
        name="家計簿メニュー",
        chat_bar_text="メニュー",
        areas=[
            # 左上
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=1250, height=843),
                action=MessageAction(label="入力", text="家計簿")
            ),
            # 右上
            RichMenuArea(
                bounds=RichMenuBounds(x=1250, y=0, width=1250, height=843),
                action=MessageAction(label="グラフ", text="グラフ")
            ),
            # 左下
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=843, width=1250, height=843),
                action=MessageAction(label="今月", text="今月")
            ),
            # 右下
            RichMenuArea(
                bounds=RichMenuBounds(x=1250, y=843, width=1250, height=843),
                action=MessageAction(label="取り消し", text="取り消し")
            ),
        ]
    )

# =========================================================
# メニューをLINEに登録
# =========================================================
def setup_rich_menu():
    try:
        rich_menu = create_rich_menu()

        # LINEにメニュー作成
        rich_menu_id = line_bot_api.create_rich_menu(rich_menu)

        # 画像を設定（menu.jpg）
        with open("menu.jpg", "rb") as f:
            line_bot_api.set_rich_menu_image(rich_menu_id, "image/jpeg", f)

        # 全ユーザーに適用
        line_bot_api.set_default_rich_menu(rich_menu_id)

        return rich_menu_id

    except Exception:
        print(traceback.format_exc())

# =========================================================
# ユーザーにメニューを表示
# =========================================================
def set_user_menu(user_id):
    try:
        rich_menu_id = setup_rich_menu()

        if rich_menu_id:
            # このユーザーにメニューを紐付け
            line_bot_api.link_rich_menu_to_user(user_id, rich_menu_id)
    except:
        print(traceback.format_exc())

# =========================================================
# グラフ表示URL
# =========================================================
@app.route("/chart/<user_id>")
def chart(user_id):

    conn = get_conn()
    cur = conn.cursor()

    # カテゴリごとの合計
    cur.execute("""
        SELECT category, SUM(amount)
        FROM expenses
        WHERE user_id=%s
        GROUP BY category
    """, (user_id,))

    data = cur.fetchall()

    cur.close()
    conn.close()

    if not data:
        return Response("no data", status=404)

    labels = [d[0] for d in data]
    values = [d[1] for d in data]

    # 円グラフ作成
    plt.figure(figsize=(6,6))
    plt.pie(values, labels=labels, autopct="%1.1f%%")

    # 画像化
    img = io.BytesIO()
    plt.savefig(img, format="png")
    plt.close()
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")

# =========================================================
# LINEからのリクエスト受け取り
# =========================================================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature")

    try:
        handler.handle(body, signature)
    except:
        print(traceback.format_exc())

    return "OK"

# =========================================================
# メイン処理（ここが一番重要）
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()  # ユーザーの入力
    user_id = event.source.user_id     # ユーザーID

    try:
        # 毎回メニューを表示（テスト用）
        set_user_menu(user_id)

        # メニュー説明
        if text == "家計簿":
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("例：コンビニ500円")
            )
            return

        # 今月合計
        if text == "今月":
            total = get_month_total(user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"今月：{total}円")
            )
            return

        # グラフ表示
        if text == "グラフ":
            url = f"{BASE_URL}/chart/{user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=url,
                    preview_image_url=url
                )
            )
            return

        # 直前削除
        if text == "取り消し":
            conn = get_conn()
            cur = conn.cursor()

            cur.execute("""
                DELETE FROM expenses
                WHERE id = (
                    SELECT id FROM expenses
                    WHERE user_id=%s
                    ORDER BY created_at DESC
                    LIMIT 1
                )
            """, (user_id,))

            conn.commit()
            cur.close()
            conn.close()

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("削除OK")
            )
            return

        # 数字があれば支出として記録
        match = re.search(r'(\d+)', text)

        if match:
            amount = int(match.group(1))
            category = classify_category(text)

            save_expense(user_id, amount, category)

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(f"{category}：{amount}円 記録OK")
            )
            return

    except:
        print(traceback.format_exc())

# =========================================================
# 起動
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))