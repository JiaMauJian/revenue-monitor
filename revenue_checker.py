"""
台股月營收監控 - GitHub Actions 版
每月 1~10 號自動執行，偵測到新營收公告時發送 LINE 通知
"""

import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime
from pathlib import Path

# ── 設定：想追蹤的股票清單 ──────────────────────────────
STOCKS = {
    "2330": "台積電",
    "2454": "聯發科",
    "2317": "鴻海",
}

URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st10_ifrs"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://mopsov.twse.com.tw/mops/web/t05st10_ifrs",
    "Content-Type": "application/x-www-form-urlencoded",
}
STATE_FILE = "last_state.json"  # 記錄上次的資料狀態


# ── 爬蟲 ────────────────────────────────────────────────
def fetch_revenue(stock_id: str) -> list[dict]:
    payload = (
        "encodeURIComponent=1&step=1&firstin=1&off=1"
        "&keyword4=&code1=&TYPEK2=&checkbtn="
        "&queryName=co_id&inpuType=co_id&TYPEK=all"
        f"&isnew=true&co_id={stock_id}&year=&month="
    )
    resp = requests.post(URL, headers=HEADERS, data=payload, timeout=15)
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []

    rows = []
    for table in tables:
        header_row = table.find("tr")
        if not header_row:
            continue
        ths = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
        for tr in table.find_all("tr")[1:]:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) >= 2:
                row = dict(zip(ths, tds)) if ths else {"raw": tds}
                rows.append(row)
    return rows


# ── 狀態管理（偵測新資料）───────────────────────────────
def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_latest_month(rows: list[dict]) -> str:
    """取出第一筆資料的年月，作為狀態比對用。"""
    if not rows:
        return ""
    first = rows[0]
    # 欄位名稱可能是「年度」「月份」或合併，取前兩個欄位值串接
    values = list(first.values())
    return "_".join(values[:2])


# ── LINE Notify 通知 ─────────────────────────────────────
def send_line_notify(message: str):
    token = os.environ.get("LINE_NOTIFY_TOKEN", "")
    if not token:
        print("  ⚠️  未設定 LINE_NOTIFY_TOKEN，跳過通知")
        return
    resp = requests.post(
        "https://notify-api.line.me/api/notify",
        headers={"Authorization": f"Bearer {token}"},
        data={"message": message},
        timeout=10,
    )
    if resp.status_code == 200:
        print("  ✅ LINE 通知已發送")
    else:
        print(f"  ❌ LINE 通知失敗：{resp.status_code}")


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
        try:
            rows = fetch_revenue(stock_id)
            if not rows:
                print(f"     ⚠️  查無資料\n")
                continue

            latest = get_latest_month(rows)
            prev   = state.get(stock_id, "")

            if latest != prev:
                print(f"     🔔 新資料！{prev} → {latest}")
                # 印出最新一筆
                for k, v in rows[0].items():
                    if k and v:
                        print(f"       {k}: {v}")
                new_alerts.append(f"\n【{name} {stock_id}】\n" +
                                  "\n".join(f"{k}: {v}" for k, v in rows[0].items() if k and v))
                state[stock_id] = latest
            else:
                print(f"     ✅ 無新資料（最新：{latest}）")
            print()

        except Exception as e:
            print(f"     ❌ 錯誤：{e}\n")

    # 有新資料才發通知
    if new_alerts:
        msg = "\n\n📊 台股月營收新公告！" + "".join(new_alerts)
        send_line_notify(msg)
        save_state(state)
        print("  💾 狀態已更新")
    else:
        print("  ℹ️  本次無新公告")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()
