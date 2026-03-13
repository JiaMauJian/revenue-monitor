"""
台股財務報表監控 - GitHub Actions 版
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

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 設定 ─────────────────────────────────────────────────
def load_stocks() -> dict:
    path = Path("stocks.json")
    if not path.exists():
        print("  ⚠️  找不到 stocks.json")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

STOCKS     = load_stocks()
STATE_FILE = "last_fin_state.json"  # 與月營收狀態分開
URL_FIN    = "https://mopsov.twse.com.tw/mops/web/ajax_t05st01"
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer":    "https://mopsov.twse.com.tw/mops/web/t05st01",
    "Content-Type": "application/x-www-form-urlencoded",
}

# 目前民國年（用於查詢）
ROC_YEAR = datetime.now().year - 1911


# ── 工具函式 ─────────────────────────────────────────────
def safe_div(a, b) -> float:
    """安全除法，避免除以零"""
    try:
        if b and float(b) != 0:
            return float(a) / float(b) * 100
    except Exception:
        pass
    return 0.0


def clean_num(val) -> float:
    """清理數字字串，移除逗號與空白"""
    try:
        return float(str(val).replace(",", "").replace(" ", "").strip())
    except Exception:
        return 0.0


# ── 步驟一：取得財報連結清單 ─────────────────────────────
def fetch_report_list(stock_id: str, year: int) -> list[dict]:
    """
    回傳該股票當年度所有財報連結，格式：
    [{"title": "合併財務報告", "season": "Q1", "url": "..."}]
    """
    payload = {
        "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
        "keyword4": "", "code1": "", "TYPEK2": "", "checkbtn": "",
        "queryName": "co_id", "inpuType": "co_id", "TYPEK": "all",
        "co_id": stock_id, "year": str(year), "month": "", "b_date": "", "e_date": "",
    }

    try:
        resp = requests.post(URL_FIN, headers=HEADERS, data=payload, verify=False, timeout=20)
        resp.encoding = "utf-8"

        if "查詢過於頻繁" in resp.text or resp.status_code == 403:
            return "BLOCKED"
        if "查詢無資料" in resp.text:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if "合併財務報告" in text or "財務報告" in text:
                href = a["href"]
                # 判斷季別
                season = "年報"
                if "Q1" in text or "第一季" in text or "q1" in href:
                    season = "Q1"
                elif "Q2" in text or "第二季" in text or "q2" in href:
                    season = "Q2"
                elif "Q3" in text or "第三季" in text or "q3" in href:
                    season = "Q3"
                elif "Q4" in text or "第四季" in text or "q4" in href:
                    season = "Q4"

                full_url = href if href.startswith("http") else "https://mopsov.twse.com.tw" + href
                results.append({"title": text, "season": season, "url": full_url})

        return results

    except Exception as e:
        print(f"     ❌ fetch_report_list 錯誤：{e}")
        return []


# ── 步驟二：解析財報頁面，取得三率 ──────────────────────
def parse_financial_ratios(url: str) -> dict:
    """
    進入財報頁面，從損益表解析：
    - 毛利率    = 毛利 / 營業收入
    - 營業利益率 = 營業利益 / 營業收入
    - 淨利率    = 本期淨利 / 營業收入
    回傳 {"gross": float, "operating": float, "net": float} 或 {}
    """
    try:
        resp = requests.get(url, headers=HEADERS, verify=False, timeout=20)
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        dfs  = pd.read_html(StringIO(resp.text))

        revenue    = 0.0
        gross      = 0.0
        operating  = 0.0
        net_income = 0.0

        # 關鍵字對應
        KEYWORDS = {
            "營業收入": ["營業收入合計", "收入合計", "營業收入淨額", "營業收入"],
            "毛利":     ["營業毛利（毛損）", "營業毛利", "毛利"],
            "營業利益": ["營業利益（損失）", "營業利益"],
            "淨利":     ["本期淨利（淨損）", "本期淨利", "淨利"],
        }

        for df in dfs:
            df_str = df.astype(str)
            col0   = df_str.iloc[:, 0].tolist()
            joined = "".join(col0)

            # 只處理損益表（含「營業收入」的表格）
            if "營業收入" not in joined:
                continue

            for _, row in df.iterrows():
                label = str(row.iloc[0]).strip()
                # 取第一個有效數字欄位
                val = 0.0
                for v in row.iloc[1:]:
                    clean = str(v).replace(",", "").replace(" ", "").strip()
                    if re.match(r"^-?\d+(\.\d+)?$", clean):
                        val = float(clean)
                        break

                for key, keywords in KEYWORDS.items():
                    if any(kw in label for kw in keywords):
                        if key == "營業收入" and revenue == 0.0:
                            revenue = val
                        elif key == "毛利" and gross == 0.0:
                            gross = val
                        elif key == "營業利益" and operating == 0.0:
                            operating = val
                        elif key == "淨利" and net_income == 0.0:
                            net_income = val

        if revenue == 0:
            return {}

        return {
            "gross":     round(safe_div(gross,     revenue), 2),
            "operating": round(safe_div(operating, revenue), 2),
            "net":       round(safe_div(net_income, revenue), 2),
        }

    except Exception as e:
        print(f"     ❌ parse_financial_ratios 錯誤：{e}")
        return {}


# ── 狀態管理 ─────────────────────────────────────────────
def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state: dict):
    cleaned = {k: v for k, v in state.items() if k in STOCKS}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2, sort_keys=True)

# ── LINE 通知 ────────────────────────────────────────────
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
        json={"to": user_id, "messages": [{"type": "text", "text": message}]},
        timeout=10,
    )
    print("  ✅ LINE 通知已發送" if resp.status_code == 200 else f"  ❌ 失敗：{resp.status_code} {resp.text}")


# ── 主程式 ───────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  台股財務報表監控")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  查詢年度：民國 {ROC_YEAR} 年")
    print(f"{'='*55}\n")

    state     = load_state()
    has_new   = False

    for stock_id, name in STOCKS.items():
        print(f"  🔍 查詢 {stock_id} {name}...")

        reports = fetch_report_list(stock_id, ROC_YEAR)

        if reports == "BLOCKED":
            print(f"     🛑 IP 被封鎖，停止執行\n")
            break

        if not reports:
            # 也試查上一年度（年報可能在隔年才出）
            reports = fetch_report_list(stock_id, ROC_YEAR - 1)

        if not reports:
            print(f"     ⚠️  查無財報\n")
            continue

        # 已通知過的清單
        notified = state.get(stock_id, [])

        for report in reports:
            key = f"{stock_id}_{ROC_YEAR}_{report['season']}"
            if key in notified:
                print(f"     ✅ 已通知過：{report['season']}")
                continue

            print(f"     🔔 新財報：{report['season']} {report['title']}")
            print(f"       URL：{report['url']}")

            # 解析三率
            time.sleep(random.uniform(1.5, 3.0))
            ratios = parse_financial_ratios(report["url"])

            if ratios:
                gross_m     = ratios.get("gross", 0)
                oper_m      = ratios.get("operating", 0)
                net_m       = ratios.get("net", 0)
                print(f"       毛利率：{gross_m:.1f}%  營業利益率：{oper_m:.1f}%  淨利率：{net_m:.1f}%")

                msg = (
                    f"【{name} {stock_id}】{ROC_YEAR}年 {report['season']}\n"
                    f"毛利率　　 {gross_m:.1f}%\n"
                    f"營業利益率 {oper_m:.1f}%\n"
                    f"淨利率　　 {net_m:.1f}%"
                )
            else:
                print(f"       ⚠️  無法解析財務數字，僅通知有新報表")
                msg = (
                    f"📋 財務報表新公告\n\n"
                    f"【{name} {stock_id}】{ROC_YEAR}年 {report['season']}\n"
                    f"（點此查看報表）\n{report['url']}"
                )

            send_line_message(msg)
            notified.append(key)
            state[stock_id] = notified
            has_new = True

        print()
        time.sleep(random.uniform(2.0, 4.0))

    if has_new:
        save_state(state)
        print("  💾 狀態已更新")
    else:
        print("  ℹ️  本次無新財報")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()