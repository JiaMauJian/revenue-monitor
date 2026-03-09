"""
台股月營收監控 - GitHub Actions 版
每月 1~10 號自動執行，偵測到新營收公告時發送 LINE 通知
"""

import requests
from bs4 import BeautifulSoup
from io import StringIO
import pandas as pd
import urllib3
import json
import os
import re
import time
import random
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # 自動讀取 .env 檔

# 隱藏 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 設定：想追蹤的股票清單 ──────────────────────────────
def load_stocks() -> dict:
    path = Path("stocks.json")
    if not path.exists():
        print("  ⚠️  找不到 stocks.json")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

STOCKS = load_stocks()
URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st10_ifrs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer":    "https://mopsov.twse.com.tw/mops/web/t05st10_ifrs",
    "Content-Type": "application/x-www-form-urlencoded",
}
STATE_FILE = "last_state.json"


def fetch_revenue(stock_id: str, is_new=True, year="", month="") -> tuple:
    """
    回傳 (data_dict, date_text) 或 ("BLOCKED", reason) 或 (None, reason)
    data_dict 包含：本月、去年同期、增減百分比、MoM
    """
    payload = {
        "step": "1", "firstin": "1", "off": "1",
        "queryName": "co_id", "inpuType": "co_id",
        "TYPEK": "all", "co_id": stock_id,
        "isnew": "true" if is_new else "false",
        "year": year, "month": month,
    }

    try:
        resp = requests.post(URL, headers=HEADERS, data=payload, verify=False, timeout=20)
        resp.encoding = "utf-8"

        # --- 新增：處理投控公司中間頁面 ---
        if "t05st10_ifrs_form" in resp.text and "詳細資料" in resp.text:
            # 這是投控公司頁面，我們需要發送第二次 Request (Step 2)
            # 抓取第一個按鈕對應的 co_id (通常就是母公司)
            payload["step"] = "2"
            # 針對 1702 這類標的，維持原本的 co_id 即可，但 step 改成 2 就能穿透進入
            resp = requests.post(URL, headers=HEADERS, data=payload, verify=False, timeout=20)
            resp.encoding = "utf-8"

        if "查詢過於頻繁" in resp.text or resp.status_code == 403:
            return "BLOCKED", "IP 遭封鎖"
        if "查詢無資料" in resp.text:
            return None, "查無資料"

        # 解析公告日期
        soup = BeautifulSoup(resp.text, "html.parser")
        date_el = soup.find("td", string=lambda x: x and "民國" in x)
        date_text = date_el.get_text(strip=True) if date_el else ""

        # 用 pandas 解析表格
        dfs = pd.read_html(StringIO(resp.text))
        for df in dfs:
            df_str = df.astype(str)
            if "本月" not in "".join(df_str.iloc[:, 0].tolist()):
                continue
            res = {}
            for _, row in df.iterrows():
                row_list = row.tolist()
                for key in ["本月", "去年同期", "增減百分比"]:
                    if key in str(row_list[0]):
                        for val in row_list[1:]:
                            clean = str(val).replace(",", "").replace("%", "").strip()
                            if re.match(r"^-?\d+(\.\d+)?$", clean):
                                res[key] = float(clean)
                                break
            if res:
                return res, date_text

        return None, "解析表格失敗"

    except Exception as e:
        return None, f"連線錯誤：{e}"


def fetch_with_mom(stock_id: str) -> tuple:
    """抓本月 + 上月，計算 MoM，回傳完整 data_dict"""
    # 1. 抓本月
    data, date_text = fetch_revenue(stock_id, is_new=True)
    if data == "BLOCKED" or data is None:
        return data, date_text

    # 2. 解析年月
    date_nums = re.findall(r"\d+", date_text)
    if not date_nums:
        return data, date_text
    curr_y, curr_m = int(date_nums[0]), int(date_nums[1])
    prev_y = curr_y - 1 if curr_m == 1 else curr_y
    prev_m = 12       if curr_m == 1 else curr_m - 1

    # 3. 抓上月（短暫延遲）
    time.sleep(random.uniform(1.0, 2.0))
    prev_data, _ = fetch_revenue(stock_id, is_new=False, year=str(prev_y), month=f"{prev_m:02d}")

    # 4. 計算 MoM
    this_val = data.get("本月", 0)
    mom = 0.0
    if isinstance(prev_data, dict) and prev_data.get("本月", 0) > 0:
        mom = (this_val - prev_data["本月"]) / prev_data["本月"] * 100
    data["MoM"] = round(mom, 2)
    data["year"] = curr_y
    data["month"] = curr_m

    return data, date_text


# ── 狀態管理 ─────────────────────────────────────────────
def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── LINE Notify 通知 ─────────────────────────────────────
def send_line_message(message: str):
    token   = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")
    if not token or not user_id:
        print("  ⚠️  未設定 LINE 環境變數")
        return
    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={
            "to": user_id,
            "messages": [{"type": "text", "text": message}]
        },
        timeout=10,
    )
    print("  ✅ LINE 通知已發送" if resp.status_code == 200 else f"  ❌ 失敗：{resp.status_code} {resp.text}")


# ── 主程式 ───────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  台股月營收監控")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*55}\n")

    state = load_state()
    new_alerts = []

    for stock_id, name in STOCKS.items():
        print(f"  🔍 查詢 {stock_id} {name}...")

        data, date_text = fetch_with_mom(stock_id)

        if data == "BLOCKED":
            print(f"     🛑 IP 被封鎖，停止執行\n")
            break

        if data is None:
            print(f"     ⚠️  {date_text}\n")
            continue

        state_key = f"{data.get('year')}_{data.get('month')}"
        prev_key  = state.get(stock_id, "")

        if state_key != prev_key:
            yoy = data.get("增減百分比", 0)
            mom = data.get("MoM", 0)
            rev = data.get("本月", 0)
            print(f"     🔔 新公告！{date_text}")
            print(f"       YoY：{yoy:+.2f}%　MoM：{mom:+.2f}%")

            # 每間公司單獨發一則 LINE 訊息
            msg = (
                f"📊 持股月營收新公告\n\n"
                f"【{name} {stock_id}】{date_text}\n"
                f"YoY：{yoy:+.2f}%　MoM：{mom:+.2f}%"
            )
            send_line_message(msg)

            new_alerts.append(stock_id)
            state[stock_id] = state_key
        else:
            print(f"     ✅ 無新資料（最新：{state_key}）")

        print()
        time.sleep(random.uniform(2.0, 4.0))  # 每檔間隔，避免被封

    if new_alerts:
        save_state(state)
        print("  💾 狀態已更新")
    else:
        print("  ℹ️  本次無新公告")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()