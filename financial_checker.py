"""
台股財務報表監控 - GitHub Actions 版
全部通知單季數字：
  Q1 單季 = Q1 累計
  Q2 單季 = Q2 - Q1
  Q3 單季 = Q3 - Q2
  Q4 單季 = 年報 - Q3
另外監控注意股公告，有新公告時發送完整財務資訊到 LINE
"""

import requests
from bs4 import BeautifulSoup
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
STATE_FILE = "last_fin_state.json"
URL_FIN    = "https://mopsov.twse.com.tw/mops/web/ajax_t05st01"
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer":    "https://mopsov.twse.com.tw/mops/web/t05st01",
    "Content-Type": "application/x-www-form-urlencoded",
}

ROC_YEAR = datetime.now().year - 1911

# 季別對應的前一期（用來相減）
PREV_SEASON = {
    "Q1":  None,
    "Q2":  "Q1",
    "Q3":  "Q2",
    "年報": "Q3",
}

# 通知顯示的季別名稱
DISPLAY_SEASON = {
    "Q1":  "Q1",
    "Q2":  "Q2",
    "Q3":  "Q3",
    "年報": "Q4",
}


# ── 步驟一：取得財報清單 ─────────────────────────────────
def fetch_report_list(stock_id: str, year: int) -> list:
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

        soup    = BeautifulSoup(resp.text, "html.parser")
        results = []

        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue

            title_text = tds[4].get_text(strip=True)
            if "財務報告" not in title_text:
                continue
            if "預計" in title_text or "召開" in title_text:
                continue

            btn = tr.find("input", {"type": "button", "value": "詳細資料"})
            if not btn:
                continue

            onclick = btn.get("onclick", "")

            def extract(key, oc=onclick):
                m = re.search(key + r"\.value=\'([^\']*)\'", oc)
                return m.group(1) if m else ""

            seq_no     = extract("seq_no")
            spoke_time = extract("spoke_time")
            spoke_date = extract("spoke_date")
            co_id      = extract("co_id")
            typek      = extract("TYPEK")

            if not seq_no:
                continue

            # 支援國字和數字季別
            season = "年報"
            if any(k in title_text for k in ["第一季", "第1季", "Q1"]):
                season = "Q1"
            elif any(k in title_text for k in ["第二季", "第2季", "Q2"]):
                season = "Q2"
            elif any(k in title_text for k in ["第三季", "第3季", "Q3"]):
                season = "Q3"

            results.append({
                "title":  title_text,
                "season": season,
                "year":   year,
                "payload": {
                    "step":       "2",
                    "firstin":    "true",
                    "off":        "1",
                    "seq_no":     seq_no,
                    "spoke_time": spoke_time,
                    "spoke_date": spoke_date,
                    "co_id":      co_id,
                    "TYPEK":      typek,
                    "year":       str(year),
                    "month":      "all",
                }
            })

        return list(reversed(results))

    except Exception as e:
        print(f"     ❌ fetch_report_list 錯誤：{e}")
        return []


# ── 取得財報詳細頁面 ─────────────────────────────────────
def fetch_report_detail(report_payload: dict) -> str:
    try:
        resp = requests.post(URL_FIN, headers=HEADERS, data=report_payload, verify=False, timeout=20)
        resp.encoding = "utf-8"
        return resp.text
    except Exception as e:
        print(f"     ❌ fetch_report_detail 錯誤：{e}")
        return ""


# ── 解析累計財務原始數字 ─────────────────────────────────
def parse_raw_financials(html: str) -> dict:
    """回傳累計原始數字（仟元）{"revenue", "gross", "operating", "net"}"""
    try:
        soup = BeautifulSoup(html, "html.parser")
        pre  = soup.find("pre", style=lambda s: s and "text-align" in s)
        if not pre:
            return {}

        text = pre.get_text()

        def extract_item(keyword: str) -> float:
            m = re.search(keyword + r"[^\d-]*([\d,]+)", text)
            return float(m.group(1).replace(",", "")) if m else 0.0

        return {
            "revenue":   extract_item(r"營業收入\(仟元\)"),
            "gross":     extract_item(r"營業毛利"),
            "operating": extract_item(r"營業利益"),
            "net":       extract_item(r"本期淨利"),
        }

    except Exception as e:
        print(f"     ❌ parse_raw_financials 錯誤：{e}")
        return {}


# ── 計算三率 ─────────────────────────────────────────────
def calc_ratios(revenue: float, gross: float, operating: float, net: float) -> dict:
    if revenue == 0:
        return {}
    return {
        "revenue":   revenue,
        "gross":     round(gross     / revenue * 100, 2),
        "operating": round(operating / revenue * 100, 2),
        "net":       round(net       / revenue * 100, 2),
    }


# ── 從清單找指定季別的報告 ───────────────────────────────
def find_report(reports: list, season: str):
    for r in reports:
        if r["season"] == season:
            return r
    return None

# ── 注意股公告 ───────────────────────────────────────────
def check_attention_stock(stock_id: str, name: str, state: dict) -> bool:
    """
    抓最新的注意股公告，若有新的就發 LINE
    回傳 True 表示有新公告
    """
    payload = {
        "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
        "keyword4": "", "code1": "", "TYPEK2": "", "checkbtn": "",
        "queryName": "co_id", "inpuType": "co_id", "TYPEK": "all",
        "co_id": stock_id, "year": str(ROC_YEAR), "month": "", "b_date": "", "e_date": "",
    }

    try:
        resp = requests.post(URL_FIN, headers=HEADERS, data=payload, verify=False, timeout=20)
        resp.encoding = "utf-8"

        if "查詢過於頻繁" in resp.text or resp.status_code == 403:
            return False

        soup = BeautifulSoup(resp.text, "html.parser")

        for tr in reversed(soup.find_all("tr")):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue

            title_text = tds[4].get_text(strip=True)
            if "注意交易資訊" not in title_text and "注意股" not in title_text:
                continue
            if "可轉換公司債" in title_text:
                continue

            btn = tr.find("input", {"type": "button", "value": "詳細資料"})
            if not btn:
                continue

            onclick = btn.get("onclick", "")

            def extract(key, oc=onclick):
                m = re.search(key + r"\.value=\'([^\']*)\'", oc)
                return m.group(1) if m else ""

            seq_no     = extract("seq_no")
            spoke_date = extract("spoke_date")
            spoke_time = extract("spoke_time")
            co_id      = extract("co_id")
            typek      = extract("TYPEK")

            if not seq_no:
                continue

            key      = f"{stock_id}_attention_{spoke_date}_{seq_no}"
            notified = state.get(f"{stock_id}_attention", [])

            if key in notified:
                print(f"     ✅ 注意股已通知過：{spoke_date}")
                continue

            # 抓詳細內容
            detail_payload = {
                "step":       "2",
                "firstin":    "true",
                "off":        "1",
                "seq_no":     seq_no,
                "spoke_time": spoke_time,
                "spoke_date": spoke_date,
                "co_id":      co_id,
                "TYPEK":      typek,
                "year":       str(ROC_YEAR),
                "month":      "all",
            }

            time.sleep(random.uniform(1.0, 2.0))
            html  = fetch_report_detail(detail_payload)
            soup2 = BeautifulSoup(html, "html.parser")
            pre   = soup2.find("pre", style=lambda s: s and "text-align" in s)
            if not pre:
                continue

            content = pre.get_text().strip()

            # 去掉 4. 之後的所有內容
            match = re.search(r"^(.*?)\n4\.", content, re.DOTALL)
            if match:
                content = match.group(1).strip()

            # 嘗試解析最近一月數字
            content = pre.get_text().strip()
            msg = parse_attention_summary(name, stock_id, spoke_date, content)

            #print(msg)
            send_line_message(msg)
            notified.append(key)
            state[f"{stock_id}_attention"] = notified
            print(f"     🔔 注意股公告已發送：{spoke_date}")
            return True

    except Exception as e:
        print(f"     ❌ check_attention_stock 錯誤：{e}")

    return False

def parse_attention_summary(name: str, stock_id: str, spoke_date: str, content: str) -> str:
    """
    從注意股公告內文解析最近一月數字，組成濃縮格式
    找不到就退回完整內文
    """
    # 去掉 4. 之後的所有內容
    cut = re.search(r"^(.*?)\n4\.", content, re.DOTALL)
    if cut:
        content = cut.group(1).strip()

    # 找最近一月區塊
    month_match = re.search(r"最近一月.+?(?=\n單位:|$)", content, re.DOTALL)

    # 20260310 → 115/03/10
    year_roc = int(spoke_date[:4]) - 1911
    date_fmt = f"{year_roc}/{spoke_date[4:6]}/{spoke_date[6:8]}"
    header   = f"⚠️ 注意股公告\n\n【{name} {stock_id}】{date_fmt}\n"

    if not month_match:
        return header + "\n" + content

    block = month_match.group(0)

    def get_val(keyword: str) -> float:
        m = re.search(keyword + r"\s+([\d,.()]+)", block)
        if not m:
            return None
        raw = m.group(1).replace(",", "").replace("(", "-").replace(")", "")
        try:
            return float(raw)
        except Exception:
            return None

    revenue  = get_val(r"營業收入")
    pretax   = get_val(r"稅前淨利")
    aftertax = get_val(r"歸屬母公司業主淨利")
    eps      = get_val(r"每股盈餘\(元\)")

    period_match = re.search(r"\(([^)]+自結數[^)]*)\)", block)
    period       = period_match.group(1) if period_match else ""
    period_short = re.sub(r"\d+年(\d+月.+)", r"\1", period)

    if revenue and pretax and aftertax and eps is not None:
        rev_b   = revenue  / 100
        pre_b   = pretax   / 100
        aft_b   = aftertax / 100
        pre_pct = round(pre_b / rev_b * 100, 2)
        aft_pct = round(aft_b / rev_b * 100, 2)
        summary = (
            f"{period_short} "
            f"營收{rev_b:.2f}／稅前{pre_b:.2f}={pre_pct:.1f}%。"
            f"營收{rev_b:.2f}／稅後{aft_b:.2f}={aft_pct:.1f}% "
            f"EPS={eps:.2f}"
        )
        return header + summary
    else:
        return header + "\n" + content

# ── 狀態管理 ─────────────────────────────────────────────
def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    cleaned = {}
    for k, v in state.items():
        if k in STOCKS:
            cleaned[k] = v
        elif k.endswith("_attention"):
            stock_id = k.replace("_attention", "")
            if stock_id in STOCKS:
                cleaned[k] = v
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


def format_msg(name: str, stock_id: str, year: int, display_season: str, ratios: dict) -> str:
    rev_bil = ratios.get("revenue", 0) / 100000
    return (
        f"📋 財務報表新公告\n\n"
        f"【{name} {stock_id}】{year}年 {display_season}（單季）\n"
        f"營收　　　 {rev_bil:,.0f} 億元\n"
        f"毛利率　　 {ratios.get('gross', 0):.1f}%\n"
        f"營業利益率 {ratios.get('operating', 0):.1f}%\n"
        f"淨利率　　 {ratios.get('net', 0):.1f}%"
    )

# ── 主程式 ───────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  台股財務報表監控")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  查詢年度：民國 {ROC_YEAR} 年 + {ROC_YEAR - 1} 年")
    print(f"{'='*55}\n")

    state   = load_state()
    has_new = False

    for stock_id, name in STOCKS.items():
        print(f"  🔍 查詢 {stock_id} {name}...")

        # ── 財報監控 ──────────────────────────────────────
        all_reports = []
        for year in [ROC_YEAR, ROC_YEAR - 1]:
            result = fetch_report_list(stock_id, year)
            if result == "BLOCKED":
                print(f"     🛑 IP 被封鎖，停止執行\n")
                return
            all_reports.extend(result)
            time.sleep(random.uniform(1.0, 2.0))

        if not all_reports:
            print(f"     ⚠️  查無財報")
        else:
            # 只取最新一筆
            report  = all_reports[0]
            year    = report["year"]
            season  = report["season"]

            # 年報實際報導的是上一年度的 Q4
            display_year = year - 1 if season == "年報" else year
            display_s    = DISPLAY_SEASON.get(season, season)

            key      = f"{stock_id}_{display_year}_{display_s}"
            notified = state.get(stock_id, [])

            if key in notified:
                print(f"     ✅ 已通知過：{display_year}年 {display_s}")
            else:
                print(f"     🔔 新財報：{display_year}年 {display_s}（單季）")

                time.sleep(random.uniform(1.5, 3.0))
                html     = fetch_report_detail(report["payload"])
                curr_raw = parse_raw_financials(html) if html else {}

                if not curr_raw or curr_raw.get("revenue", 0) == 0:
                    print(f"       ⚠️  無法解析財務數字")
                    send_line_message(
                        f"📋 財務報表新公告\n\n"
                        f"【{name} {stock_id}】{display_year}年 {display_s}\n"
                        f"（無法解析財務數字）"
                    )
                    notified.append(key)
                    state[stock_id] = notified
                    has_new = True
                else:
                    prev_season = PREV_SEASON.get(season)

                    if prev_season is None:
                        single_raw = curr_raw
                    else:
                        if season == "年報":
                            prev_report = find_report(
                                [r for r in all_reports if r["year"] == year - 1], "Q3"
                            ) or find_report(all_reports, "Q3")
                        else:
                            prev_report = find_report(
                                [r for r in all_reports if r["year"] == year], prev_season
                            )

                        if not prev_report:
                            print(f"       ⚠️  找不到 {prev_season}，無法計算單季")
                            send_line_message(
                                f"📋 財務報表新公告\n\n"
                                f"【{name} {stock_id}】{display_year}年 {display_s}\n"
                                f"（找不到前期資料，無法計算單季）"
                            )
                            notified.append(key)
                            state[stock_id] = notified
                            has_new = True
                        else:
                            time.sleep(random.uniform(1.5, 3.0))
                            prev_html = fetch_report_detail(prev_report["payload"])
                            prev_raw  = parse_raw_financials(prev_html) if prev_html else {}

                            if not prev_raw or prev_raw.get("revenue", 0) == 0:
                                print(f"       ⚠️  前期 {prev_season} 資料解析失敗，改用累計")
                                single_raw = curr_raw
                            else:
                                single_raw = {
                                    "revenue":   curr_raw["revenue"]   - prev_raw["revenue"],
                                    "gross":     curr_raw["gross"]     - prev_raw["gross"],
                                    "operating": curr_raw["operating"] - prev_raw["operating"],
                                    "net":       curr_raw["net"]       - prev_raw["net"],
                                }

                            ratios = calc_ratios(
                                single_raw["revenue"],
                                single_raw["gross"],
                                single_raw["operating"],
                                single_raw["net"],
                            )

                            rev_b = single_raw["revenue"] / 100000
                            print(f"       營收：{rev_b:,.0f}億  毛利率：{ratios.get('gross',0):.1f}%  營業利益率：{ratios.get('operating',0):.1f}%  淨利率：{ratios.get('net',0):.1f}%")
                            send_line_message(format_msg(name, stock_id, display_year, display_s, ratios))

                            notified.append(key)
                            state[stock_id] = notified
                            has_new = True

        # ── 注意股公告監控 ────────────────────────────────
        time.sleep(random.uniform(1.0, 2.0))
        if check_attention_stock(stock_id, name, state):
            has_new = True

        print()
        time.sleep(random.uniform(2.0, 4.0))

    if has_new:
        save_state(state)
        print("  💾 狀態已更新")
    else:
        print("  ℹ️  本次無新財報或公告")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()
