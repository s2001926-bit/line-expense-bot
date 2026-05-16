import os
import json
from datetime import datetime
from collections import defaultdict

from flask import Flask, request, abort

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import gspread
from google.oauth2.service_account import Credentials


app = Flask(__name__)

LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
MASTER_SHEET_ID = os.getenv("MASTER_SHEET_ID")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def get_gspread_client():
    service_account_info = json.loads(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    )

    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=SCOPES
    )

    return gspread.authorize(creds)


gc = get_gspread_client()


def get_month_title():
    now = datetime.now()
    return f"{now.year}/{now.month}"


def get_master_sheet():
    master = gc.open_by_key(MASTER_SHEET_ID)

    try:
        ws = master.worksheet("users")
    except:
        ws = master.add_worksheet(
            title="users",
            rows=1000,
            cols=10
        )
        ws.append_row([
            "user_id",
            "name",
            "spreadsheet_id",
            "created_at"
        ])

    return ws


def find_user(user_id):
    ws = get_master_sheet()
    records = ws.get_all_records()

    for index, row in enumerate(records, start=2):
        if row["user_id"] == user_id:
            return {
                "row": index,
                "name": row["name"],
                "spreadsheet_id": row["spreadsheet_id"]
            }

    return None


def setup_record_sheet(sheet):
    headers = ["日期", "項目", "金額", "備註"]
    current = sheet.row_values(1)

    if current != headers:
        sheet.clear()
        sheet.append_row(headers)


def create_user_spreadsheet(user_id, name):
    title = f"{name}_記帳本"
    spreadsheet = gc.create(title)

    if ADMIN_EMAIL:
        spreadsheet.share(
            ADMIN_EMAIL,
            perm_type="user",
            role="writer"
        )

    month_title = get_month_title()

    sheet = spreadsheet.sheet1
    sheet.update_title(month_title)
    setup_record_sheet(sheet)

    ws = get_master_sheet()
    ws.append_row([
        user_id,
        name,
        spreadsheet.id,
        datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    ])

    return spreadsheet.id


def get_or_create_month_sheet(spreadsheet):
    month_title = get_month_title()

    try:
        sheet = spreadsheet.worksheet(month_title)
    except:
        sheet = spreadsheet.add_worksheet(
            title=month_title,
            rows=1000,
            cols=10
        )
        setup_record_sheet(sheet)

    return sheet


def record_expense(spreadsheet_id, item, amount, note=""):
    spreadsheet = gc.open_by_key(spreadsheet_id)
    sheet = get_or_create_month_sheet(spreadsheet)

    today = datetime.now().strftime("%Y/%m/%d")

    sheet.append_row([
        today,
        item,
        amount,
        note
    ])


def monthly_summary(spreadsheet_id):
    spreadsheet = gc.open_by_key(spreadsheet_id)
    sheet = get_or_create_month_sheet(spreadsheet)

    records = sheet.get_all_records()

    total = 0
    item_sum = defaultdict(int)

    for row in records:
        try:
            amount = int(row["金額"])
            item = row["項目"]

            total += amount
            item_sum[item] += amount

        except:
            pass

    if total == 0:
        return "本月目前沒有記帳資料。"

    lines = [
        f"本月總支出：{total} 元",
        "",
        "項目統計："
    ]

    for item, amount in item_sum.items():
        lines.append(f"{item}：{amount} 元")

    return "\n".join(lines)


@app.route("/", methods=["GET"])
def home():
    return "LINE Expense Bot is running."


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    user = find_user(user_id)

    if text.startswith("設定名稱"):
        name = text.replace("設定名稱", "").strip()

        if not name:
            reply_text = "請輸入：設定名稱 你的名字"

        elif user:
            reply_text = f"你已經建立過記帳本：{user['name']}_記帳本"

        else:
            create_user_spreadsheet(user_id, name)
            reply_text = f"已建立專屬記帳本：{name}_記帳本"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

        return

    if not user:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請先輸入：設定名稱 你的名字"
            )
        )

        return

    if text == "本月":
        reply_text = monthly_summary(user["spreadsheet_id"])

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

        return

    parts = text.split()

    if len(parts) < 2:
        reply_text = (
            "格式錯誤\n"
            "例如：晚餐全家 70"
        )

    else:
        amount = parts[-1]
        item = " ".join(parts[:-1])
        note = ""

        if not amount.isdigit():
            reply_text = (
                "金額必須放最後面\n"
                "例如：晚餐全家 70"
            )

        else:
            record_expense(
                user["spreadsheet_id"],
                item,
                int(amount),
                note
            )

            reply_text = f"已記錄：{item} {amount}元"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_text)
    )


if __name__ == "__main__":
    app.run(debug=True)
