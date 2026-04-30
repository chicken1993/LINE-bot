# ======================
# Flask（Webサーバー）
# ======================
from flask import Flask, request, send_file

# ======================
# LINE Bot SDK
# ======================
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageSendMessage,
    RichMenu, RichMenuArea, MessageAction
)

# ======================
# 環境変数
# ======================
import os
from dotenv import load_dotenv

# ======================
# DB
# ======================
import re
import psycopg2

# ======================
# グラフ
# ======================
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io

# ======================
# 環境変数読み込み
# ======================
load_dotenv()

app = Flask(__name__)

# ======================
# LINE認証
# ======================
CHANNEL_ACCESS_TOKEN = os.getenv("CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


# =========================================================
# DB接続
# =========================================================
def get_conn():
    return psycopg2.connect(
        os.getenv("DATABASE_URL"),
        sslmode="require"
    )


# =========================================================
# 初期化（テーブル）
# =========================================================
def init_db():
    try:
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

        # ユーザー
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                is_first BOOLEAN DEFAULT TRUE
            )
        """)

        # ⭐追加① app設定（リッチメニュー管理）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print("DB init error:", e)

init_db()


# =========================================================
# 初回判定
# =========================================================
def is_first_user(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT is_first FROM users WHERE user_id=%s", (user_id,))
    result = cur.fetchone()

    cur.close()
    conn.close()

    if result is None:
        return True
    return result[0]


# =========================================================
# 初回登録
# =========================================================
def mark_user_init(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (user_id, is_first)
        VALUES (%s, FALSE)
        ON CONFLICT (user_id) DO UPDATE SET is_first = FALSE
    """, (user_id,))

    conn.commit()
    cur.close()
    conn.close()


# =========================================================
# 初回メッセージ
# =========================================================
def send_first_message(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(
            text="🎉ようこそ！\n下のメニューから操作できるよ👇"
        )
    )


# =========================================================
# リッチメニュー作成
# =========================================================
def create_rich_menu():
    return RichMenu(
        size={"width": 2500, "height": 1686},
        selected=True,
        name="家計簿UI",
        chat_bar_text="メニュー",
        areas=[
            RichMenuArea(
                bounds={"x": 0, "y": 0, "width": 1250, "height": 843},
                action=MessageAction(label="入力", text="家計簿")
            ),
            RichMenuArea(
                bounds={"x": 1250, "y": 0, "width": 1250, "height": 843},
                action=MessageAction(label="グラフ", text="グラフ")
            ),
            RichMenuArea(
                bounds={"x": 0, "y": 843, "width": 1250, "height": 843},
                action=MessageAction(label="今月", text="今月")
            ),
            RichMenuArea(
                bounds={"x": 1250, "y": 843, "width": 1250, "height": 843},
                action=MessageAction(label="メニュー", text="メニュー")
            )
        ]
    )


# =========================================================
# ⭐② リッチメニューID保存
# =========================================================
def save_rich_menu_id(rich_menu_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO app_settings (key, value)
        VALUES ('rich_menu_id', %s)
        ON CONFLICT (key) DO UPDATE SET value = %s
    """, (rich_menu_id, rich_menu_id))

    conn.commit()
    cur.close()
    conn.close()


def get_rich_menu_id():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT value FROM app_settings WHERE key='rich_menu_id'")
    result = cur.fetchone()

    cur.close()
    conn.close()

    return result[0] if result else None


# =========================================================
# ⭐③ 初回のみ作成（重要）
# =========================================================
def setup_rich_menu_once():
    try:
        existing = get_rich_menu_id()

        if existing:
            return existing

        rich_menu = create_rich_menu()
        rich_menu_id = line_bot_api.create_rich_menu(rich_menu)

        save_rich_menu_id(rich_menu_id)

        return rich_menu_id

    except Exception as e:
        print("rich menu setup error:", e)
        return None


# =========================================================
# ⭐④ ユーザーに安全付与
# =========================================================
def set_user_rich_menu(user_id):
    try:
        rich_menu_id = setup_rich_menu_once()

        if not rich_menu_id:
            return

        line_bot_api.link_rich_menu_to_user(user_id, rich_menu_id)

    except Exception as e:
        print("rich menu link error:", e)


# =========================================================
# 支出保存
# =========================================================
def save_expense(user_id, amount, category):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO expenses (user_id, amount, category) VALUES (%s, %s, %s)",
        (user_id, amount, category)
    )

    conn.commit()
    cur.close()
    conn.close()


# =========================================================
# 今月合計
# =========================================================
def get_month_total(user_id):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(amount)
        FROM expenses
        WHERE user_id = %s
        AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', CURRENT_DATE)
    """, (user_id,))

    total = cur.fetchone()[0]

    cur.close()
    conn.close()

    return total or 0


# =========================================================
# グラフ
# =========================================================
def create_pie_chart(user_id):
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
        return None

    labels = [r[0] for r in data]
    sizes = [r[1] for r in data]

    plt.figure(figsize=(6,6))
    plt.pie(sizes, autopct='%1.1f%%', startangle=90)
    plt.legend(labels)
    plt.title("支出グラフ")

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close()
    buf.seek(0)

    return buf


# =========================================================
# グラフAPI
# =========================================================
@app.route("/chart/<user_id>")
def chart(user_id):
    img = create_pie_chart(user_id)
    if img:
        return send_file(img, mimetype='image/png')
    return "no data"


# =========================================================
# LINE受信
# =========================================================
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    handler.handle(body, signature)
    return 'OK', 200


# =========================================================
# メイン処理
# =========================================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    text = event.message.text.strip()
    user_id = event.source.user_id

    try:

        # ⭐⑤ 初回処理（完成形）
        if is_first_user(user_id):
            mark_user_init(user_id)
            set_user_rich_menu(user_id)
            send_first_message(event)
            return

        if text == "メニュー":
            send_first_message(event)
            return

        if text == "今月":
            total = get_month_total(user_id)
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"今月の支出：{total}円")
            )
            return

        if text == "グラフ":
            url = f"https://line-bot-ujj2.onrender.com/chart/{user_id}"

            line_bot_api.reply_message(
                event.reply_token,
                ImageSendMessage(
                    original_content_url=url,
                    preview_image_url=url
                )
            )
            return

        match = re.search(r'(.+?)[にで]?(\d+)', text)

        if match:
            save_expense(user_id, int(match.group(2)), match.group(1))

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="記録したよ")
            )
            return

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="『メニュー』で操作できるよ")
        )

    except Exception as e:
        print("error:", e)


# =========================================================
# 起動
# =========================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))