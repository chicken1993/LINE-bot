# ======================
# Flask（Webサーバー）
# ======================
from flask import Flask, request, Response

# ======================
# LINE Bot SDK
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
import os
import re
import io
import traceback
from dotenv import load_dotenv

# ======================
# DB
# ======================
import psycopg2

# ======================
# グラフ
# ======================
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ======================
# 初期設定
# ======================
load_dotenv()
app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")
BASE_URL = os.getenv("BASE_URL")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# =========================================================
# DB接続
# =========================================================
def get_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"), sslmode="require")

# =========================================================
# DB初期化（★予算テーブル追加済み）
# =========================================================
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

    # 👇 予算テーブル
    cur.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            user_id TEXT PRIMARY KEY,
            monthly_budget INTEGER
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

init_db()

# =========================================================
# カテゴリ分類
# =========================================================
def classify_category(text):
    rules = [
        ("食費", ["コンビニ", "セブン", "ファミマ", "ローソン", "ご飯"]),
        ("交通費", ["電車", "バス"]),
        ("娯楽", ["ゲーム", "映画"]),
        ("通信費", ["スマホ", "wifi"]),
    ]

    for category, keywords in rules:
        for kw in keywords:
            if kw in text:
                return category

    return "その他"

# =========================================================
# DB処理
# =========================================================
def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s,%s,%s)",
        (user_id, amount, category)
    )

    conn.commit()
    cur.close()
    conn.close()

def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

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
# 👇 予算関連
# =========================================================
def set_budget(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO budgets (user_id, monthly_budget)
        VALUES (%s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET monthly_budget = %s
    """, (user_id, amount, amount))

    conn.commit()
    cur.close()
    conn.close()

def get_budget(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT monthly_budget FROM budgets WHERE user_id=%s
    """, (user_id,))

    r = cur.fetchone()

    cur.close()
    conn.close()

    return r[0] if r else None

# =========================================================
# リッチメニュー
# =========================================================
def create_rich_menu():
    return RichMenu(
        size=RichMenuSize(width=2500, height=1686),
        selected=True,
        name="家計簿メニュー",
        chat_bar_text="メニュー",
        areas=[
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=0, width=1250, height=843),
                action=MessageAction(label="入力", text="家計簿")
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=1250, y=0, width=1250, height=843),
                action=MessageAction(label="グラフ", text="グラフ")
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=0, y=843, width=1250, height=843),
                action=MessageAction(label="今月", text="今月")
            ),
            RichMenuArea(
                bounds=RichMenuBounds(x=1250, y=843, width=1250, height=843),
                action=MessageAction(label="取り消し", text="取り消し")
            ),
        ]
    )

def setup_rich_menu():
    try:
        rich_menu = create_rich_menu()
        rich_menu_id = line_bot_api.create_rich_menu(rich_menu)

        with open("menu.jpg", "rb") as f:
            line_bot_api.set_rich_menu_image(rich_menu_id, "image/jpeg", f)

        line_bot_api.set_default_rich_menu(rich_menu_id)

        return rich_menu_id
    except:
        print(traceback.format_exc())

def set_user_menu(user_id):
    try:
        rich_menu_id = setup_rich_menu()
        if rich_menu_id:
            line_bot_api.link_rich_menu_to_user(user_id, rich_menu_id)
    except:
        print(traceback.format_exc())

# =========================================================
# グラフ
# =========================================================
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
    conn.close()

    if not data:
        return Response("no data", status=404)

    labels = [d[0] for d in data]
    values = [d[1] for d in data]

    plt.figure(figsize=(6,6))
    plt.pie(values, labels=labels, autopct="%1.1f%%")

    img = io.BytesIO()
    plt.savefig(img, format="png")
    plt.close()
    img.seek(0)

    return Response(img.getvalue(), mimetype="image/png")

# =========================================================
# Webhook
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
# メイン処理
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:
        set_user_menu(user_id)

        # ======================
        # 👇 予算設定
        # ======================
        if text.startswith("予算"):
            match = re.search(r'(\d+)', text)

            if match:
                amount = int(match.group(1))
                set_budget(user_id, amount)

                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(f"予算：{amount}円に設定しました")
                )
            else:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage("例：予算 50000")
                )
            return

        if text == "家計簿":
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage("例：コンビニ500円")
            )
            return

        # ======================
        # 👇 今月＋残り＋警告
        # ======================
        if text == "今月":
            total = get_month_total(user_id)
            budget = get_budget(user_id)

            if budget:
                remain = budget - total
                msg = f"今月：{total}円\n残り：{remain}円"

                if remain < 0:
                    msg += "\n⚠️ 予算オーバー！"
                elif total > budget * 0.8:
                    msg += "\n⚠️ 使いすぎ注意"
            else:
                msg = f"今月：{total}円\n※予算未設定"

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(msg)
            )
            return

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

        # ======================
        # 支出記録
        # ======================
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