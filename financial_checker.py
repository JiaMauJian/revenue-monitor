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
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from line_notify import send_line_message
from revenue_checker import fetch_revenue as fetch_mops_revenue
from chart import build_fin_combined, save_quarterly_chart, get_chart_url, fetch_ttm_eps, fetch_quarter_revenue, fetch_month_revenue
import sys

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
SLEEP_SEC  = 6   # 每次 MOPS 請求之間的間隔秒數
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

DEBUG = "--debug" in sys.argv
DEBUG_STOCKS = {}

for i, arg in enumerate(sys.argv):
    if arg == "--stock" and i + 1 < len(sys.argv):
        for s in sys.argv[i + 1].split(","):
            DEBUG_STOCKS[s] = STOCKS.get(s, {"name": s, "type": ""})  # 從 STOCKS 取名稱

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
        if resp.status_code >= 500:
            return "ERROR"
        if "查詢無資料" in resp.text:
            return []

        soup    = BeautifulSoup(resp.text, "html.parser")
        results = []

        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue

            title_text = tds[4].get_text(strip=True)
            # 有些公司標題寫「財報」（如 6187 萬潤）；非財報公告由內文解析時排除
            if "財務報告" not in title_text and "財報" not in title_text:
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
        return "ERROR"


# ── 取得財報詳細頁面 ─────────────────────────────────────
def fetch_report_detail(report_payload: dict) -> str:
    try:
        resp = requests.post(URL_FIN, headers=HEADERS, data=report_payload, verify=False, timeout=20)
        resp.encoding = "utf-8"
        if "查詢過於頻繁" in resp.text or resp.status_code == 403:
            print(f"     🛑 fetch_report_detail：IP 遭封鎖")
            return ""
        if resp.status_code >= 500:
            print(f"     ❌ fetch_report_detail：伺服器錯誤 HTTP {resp.status_code}")
            return ""
        return resp.text
    except Exception as e:
        print(f"     ❌ fetch_report_detail 錯誤：{e}")
        return ""


# ── 解析累計財務原始數字 ─────────────────────────────────
# 財報公告（第 31 款）內文欄位固定，例如：
#   4.1月1日累計至本期止營業收入(仟元):1,774,103
#   8.1月1日累計至本期止本期淨利(淨損) (仟元):243,781
#   10.1月1日累計至本期止基本每股盈餘(損失) (元):2.76
FIN_FIELDS = {
    "revenue":   "營業收入",
    "gross":     "營業毛利",
    "operating": "營業利益",
    "net":       "本期淨利",
    "parent":    "歸屬於母公司業主淨利",
    "eps":       "基本每股盈餘",
}


def parse_raw_financials(html: str) -> dict:
    """解析財報公告的累計數字；不是財報公告（找不到營收欄位）回傳 {}。"""
    text   = BeautifulSoup(html, "html.parser").get_text("\n")
    result = {}
    for key, label in FIN_FIELDS.items():
        m = re.search(r"累計至本期止" + label + r"[^:：\n]*[:：]\s*(\(?-?[\d,]+(?:\.\d+)?\)?)", text)
        if not m:
            continue
        raw = m.group(1).replace(",", "")
        neg = raw.startswith("(") and raw.endswith(")")
        val = float(raw.strip("()"))
        result[key] = -val if neg else val
    if not result.get("revenue"):
        return {}
    for key in FIN_FIELDS:
        result.setdefault(key, 0)
    return result


# ── 驗證單季數字 ─────────────────────────────────────────
def validate_single(single: dict, cumulative: dict, monthly_rev: float | None) -> list:
    """回傳問題清單；空清單代表通過。主要防止把累計數字當成單季。"""
    problems = []
    rev = single["revenue"]
    if rev <= 0:
        return [f"單季營收 {rev:,.0f} 不大於 0"]

    # 1. 單季營收應等於該季三個月月營收合計
    if monthly_rev:
        diff = (rev - monthly_rev) / monthly_rev
        if abs(diff) > 0.03:
            problems.append(f"單季營收 {rev:,.0f} 與月營收合計 {monthly_rev:,.0f} 相差 {diff:+.1%}")

    # 2. 毛利不可大於營收
    if single["gross"] > rev:
        problems.append(f"毛利 {single['gross']:,.0f} 大於營收 {rev:,.0f}")

    # 3. 單季 EPS ≈ 單季母公司淨利 × 累計EPS / 累計母公司淨利
    parent, eps = single.get("parent", 0), single.get("eps", 0)
    if parent and cumulative.get("parent") and cumulative.get("eps"):
        expected = parent * cumulative["eps"] / cumulative["parent"]
        if abs(eps - expected) > max(0.05, abs(expected) * 0.1):
            problems.append(f"單季 EPS {eps:.2f} 與淨利推算的 {expected:.2f} 不符")

    return problems


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


# ── 抓股價 ───────────────────────────────────────────────
def fetch_price(stock_id: str, stock_name: str) -> float | None:
    try:
        resp = requests.post(
            "https://huodalife.azurewebsites.net/Chart1.aspx/GetPrice",
            json={
                "input_option_stock_name": stock_name,
                "input_option_stock_num":  stock_id,
            },
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("d", {})
        if isinstance(raw, str):
            raw = json.loads(raw)
        prices = raw[1]
        if len(prices) < 49:
            print(f"     ⚠️  股價資料尚未更新（{len(prices)}/49），略過")
            return None
        price = prices[-1]
        if price not in (None, ""):
            return float(price)
    except Exception as e:
        print(f"     ❌ 抓股價失敗：{stock_id} {e}")
    return None


# ── 注意股公告 ───────────────────────────────────────────
def check_attention_stock(stock_id: str, name: str, state: dict, stock_type: str = "") -> bool:
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

        state_key      = f"{stock_id}_attention"
        is_first_check = state_key not in state
        today_str      = datetime.now().strftime("%Y%m%d")

        for tr in reversed(soup.find_all("tr")):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue

            title_text = tds[4].get_text(strip=True)
            if "注意交易資訊" not in title_text and "注意股" not in title_text:
                continue
            if "轉換公司債" in title_text:  # 含「無擔保轉換公司債」，內文只有可轉債資訊、沒有自結數字
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
            notified = state.get(state_key, [])

            if not DEBUG and key in notified:
                print(f"     ✅ 注意股已通知過：{spoke_date}")
                continue

            # 新加入監控的股票：今天以前的舊公告直接標記已讀，不發送（避免補發整批歷史公告）
            if not DEBUG and is_first_check and spoke_date < today_str:
                notified.append(key)
                state[state_key] = notified
                print(f"     ⏭️  新股票，略過今天以前的舊公告：{spoke_date}")
                continue

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

            time.sleep(SLEEP_SEC)
            html  = fetch_report_detail(detail_payload)
            soup2 = BeautifulSoup(html, "html.parser")
            pre   = soup2.find("pre", style=lambda s: s and "text-align" in s)
            if not pre:
                continue

            content = pre.get_text().strip()
            if "公司債相關資訊" in content:
                # 可轉債的注意交易公告（標題不一定寫「轉換公司債」），沒有自結數字，不發
                notified.append(key)
                state[state_key] = notified
                print(f"     ⏭️  可轉債公告，略過：{spoke_date}")
                return True
            msg     = parse_attention_summary(name, stock_id, spoke_date, content, stock_type)

            send_line_message(msg, mode="push" if DEBUG else "broadcast")

            notified.append(key)
            state[state_key] = notified
            print(f"     🔔 注意股公告已發送：{spoke_date}")
            return True

        # 第一次檢查完成就建立紀錄（即使沒有任何公告），之後不再視為新股票，
        # 避免日後的公告因「今天以前」被當成舊公告略過
        if not DEBUG and is_first_check:
            state.setdefault(state_key, [])
            return True  # 讓 main 存檔

    except Exception as e:
        print(f"     ❌ check_attention_stock 錯誤：{e}")

    return False


# ── 注意股公告解析（正規表達式）───────────────────────────
# 各公司表格寫法不一，但都是「科目(單位)  最近一月  ...  最近一季 ...」，
# 每列第一個非百分比數字就是最近一月。以 115/08–11 全市場 253 份公告驗證。
_ATTN_NUM    = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?%?")
# 每個欄位依序嘗試的科目名稱（淨利優先用「歸屬母公司」，沒有才用本期 / 稅後淨利）
_ATTN_FIELDS = {
    "revenue":  ["營業收入"],
    "pretax":   ["稅前"],
    "aftertax": ["歸屬", "本期淨", "稅後淨"],
    "eps":      ["每股"],
}


def _attn_first_number(s: str):
    """取字串中第一個不是百分比的數字（括號視為負數）。"""
    s = re.sub(r"\(?註\s*\d+\)?", "", s)
    for m in _ATTN_NUM.finditer(s):
        tok = m.group()
        if tok.endswith("%") or s[m.end():m.end() + 1] == "%":
            continue
        tok = tok.rstrip("%")
        neg = tok.startswith("(") and tok.endswith(")")
        v   = float(tok.strip("()").replace(",", ""))
        return -abs(v) if neg else v
    return None


def _attn_scale(label: str, section: str) -> float:
    """換算成百萬元的倍數：先看科目名稱，再看整段的單位說明；都沒寫就當百萬元。"""
    for txt in (label, section):
        if re.search(r"仟元|千元", txt):
            return 1 / 1000
        if "百萬" in txt:
            return 1
        if "億" in txt:
            return 100
    return 1


def parse_attention_numbers(content: str) -> dict:
    """解析注意股公告的最近一月自結數字。金額單位百萬元，抓不到的欄位不放。"""
    m       = re.search(r"3\s*\.(.*?)(?:\n\s*4\s*\.|\Z)", content, re.S)
    section = m.group(1) if m else content
    lines   = section.split("\n")
    out     = {}

    # 表頭第一個「115年7月」或「(115/07)」就是最近一月（只認同一行內，避免直排表頭跨行誤判）
    pm = re.search(r"(\d{3})[ \t]*年[ \t]*0?(\d{1,2})[ \t]*月|(?<![\d/])(\d{3})/(\d{1,2})(?![\d/])", section)
    if pm:
        y, mth = (pm.group(1), pm.group(2)) if pm.group(1) else (pm.group(3), pm.group(4))
        out["period"] = (int(y), int(mth))

    for key, label_pats in _ATTN_FIELDS.items():
        v = None
        for label_pat in label_pats:
            for i, line in enumerate(lines):
                lm = re.search(label_pat, line)
                if not lm:
                    continue
                label, v = line, _attn_first_number(line[lm.end():])
                # 科目名稱跨行（如「歸屬母公司\n 業主淨利\n（百萬元）  194」）：往下最多再看兩行
                for nxt in lines[i + 1:i + 3]:
                    if v is not None:
                        break
                    label, v = label + nxt, _attn_first_number(nxt)
                if v is not None:
                    out[key] = v if key == "eps" else v * _attn_scale(label, section)
                    break
            if v is not None:
                break
    return out


def parse_attention_summary(name: str, stock_id: str, spoke_date: str, content: str, stock_type: str = "") -> str:

    year_roc = int(spoke_date[:4]) - 1911
    date_fmt = f"{year_roc}年{spoke_date[4:6]}月{spoke_date[6:8]}日"
    header   = f"⚠️ 注意股公告\n\n【{name} {stock_id}】{date_fmt}\n類型　　　 {stock_type}\n"

    # 去掉 4. 之後
    cut = re.search(r"^(.*?)\n4\.", content, re.DOTALL)
    if cut:
        content = cut.group(1).strip()

    d   = parse_attention_numbers(content)
    rev = d.get("revenue")
    if not rev or "period" not in d:
        print(f"     ℹ️  注意股公告抓不到營收或月份，改發原文")
        return header + content

    # 用月營收 API 交叉核對，對不上就發原文（公司自結數有時本身有誤或單位未標示）
    py, pm  = d["period"]
    monthly = fetch_month_revenue(name, stock_id, py, pm)
    if monthly is None:
        # 月營收 API 通常比公開資訊觀測站晚幾天更新，查不到就改查觀測站
        time.sleep(SLEEP_SEC)
        mops, _ = fetch_mops_revenue(stock_id, is_new=False, year=str(py), month=f"{pm:02d}")
        if isinstance(mops, dict) and mops.get("本月"):
            monthly = mops["本月"]
            print(f"     ℹ️  月營收 API 尚無 {py}/{pm:02d}，改用觀測站資料核對")
    if monthly is None:
        print(f"     ℹ️  查不到 {py}/{pm:02d} 月營收，無法核對，改發原文")
        return header + content
    monthly /= 1000  # 千元 → 百萬元
    if abs(rev - monthly) > max(monthly * 0.05, 0.6):
        if abs(rev / 1000 - monthly) <= max(monthly * 0.05, 0.6):
            # 公告未標單位、實際為仟元
            d = {k: (v / 1000 if k in ("revenue", "pretax", "aftertax") else v) for k, v in d.items()}
            rev = d["revenue"]
        else:
            print(f"     ⚠️  注意股營收 {rev:,.1f} 百萬與月營收 {monthly:,.1f} 百萬不符，改發原文")
            return header + content

    pre, aft, eps = d.get("pretax"), d.get("aftertax"), d.get("eps")
    period_fmt    = f"{py}年{pm:02d}月"

    rev_bil = rev / 100         # 百萬元 → 億元

    lines = [f"【{name} {stock_id}】{date_fmt}（{period_fmt}自結）", f"類型　　　 {stock_type}"]
    lines.append(f"營收　　　 {rev_bil:,.1f} 億元")
    if pre is not None:
        lines.append(f"稅前利益率 {pre/rev*100:.1f}%")
    if aft is not None:
        lines.append(f"淨利率　　 {aft/rev*100:.1f}%")
    if eps is not None:
        lines.append(f"ＥＰＳ　　 {eps:.2f} 元")
    if eps is not None and eps > 0:
        price = fetch_price(stock_id, name)
        if price:
            annual_eps = round(eps * 12, 2)
            per        = round(price / annual_eps, 1)
            print(f"     [DEBUG] 股價={price}  EPS={eps}  年化EPS(×12)={annual_eps}  本益比={per}")
            lines.append(f"年化本益比 {per}")

    return "⚠️ 注意股公告\n\n" + "\n".join(lines)


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


def format_msg(name: str, stock_id: str, year: int, display_season: str, ratios: dict,
               stock_type: str = "", eps: float = 0, price: float | None = None,
               ttm_eps: float | None = None) -> str:
    rev_bil = ratios.get("revenue", 0) / 100000
    lines = [
        f"📋 財務報表新公告\n",
        f"【{name} {stock_id}】{year}年 {display_season}（單季）",
        f"類型　　　 {stock_type}",
        f"營收　　　 {rev_bil:,.0f} 億元",
        f"毛利率　　 {ratios.get('gross', 0):.1f}%",
        f"營業利益率 {ratios.get('operating', 0):.1f}%",
        f"淨利率　　 {ratios.get('net', 0):.1f}%",
    ]
    if eps is not None:
        lines.append(f"ＥＰＳ　　 {eps:.2f} 元")  # 虧損也顯示負 EPS；本益比只在獲利時計算
    if eps and eps > 0:
        if price:
            if ttm_eps and ttm_eps > 0:
                per = round(price / ttm_eps, 1)
                lines.append(f"近四季本益比 {per}")
            else:
                annual_eps = round(eps * 4, 2)
                per        = round(price / annual_eps, 1)
                lines.append(f"年化本益比 {per}")
    return "\n".join(lines)


def _period_order(k: str) -> tuple:
    parts = k.split("_")
    try:
        y = int(parts[-2])
        q = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}.get(parts[-1], 0)
        return (y, q)
    except (IndexError, ValueError):
        return (0, 0)


def get_expected_fin_suffix(now: datetime) -> str:
    """依申報月份推算目前預期最新季別（提前一個月開跑，涵蓋提早公告的情況：
    年報:3月, Q1:4月, Q2:7月, Q3:10月）"""
    month, roc_year = now.month, now.year - 1911
    if month <= 2:
        return f"{roc_year - 1}_Q3"
    elif month == 3:
        return f"{roc_year - 1}_Q4"
    elif month <= 6:
        return f"{roc_year}_Q1"
    elif month <= 9:
        return f"{roc_year}_Q2"
    else:
        return f"{roc_year}_Q3"


# ── 主程式 ───────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  台股財務報表監控")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  查詢年度：民國 {ROC_YEAR} 年 + {ROC_YEAR - 1} 年")
    print(f"{'='*55}\n")

    state          = load_state()
    has_new        = False
    pending_charts = []

    expected_fin_suffix = get_expected_fin_suffix(datetime.now(timezone.utc))

    stocks_to_check = DEBUG_STOCKS if (DEBUG and DEBUG_STOCKS) else STOCKS

    for stock_id, info in stocks_to_check.items():
        name       = info["name"]
        stock_type = info["type"]
        print(f"  🔍 查詢 {stock_id} {name}...")

        notified  = state.get(stock_id, [])
        exp_order = _period_order(f"{stock_id}_{expected_fin_suffix}")
        if not DEBUG and any(_period_order(k) >= exp_order for k in notified):
            print(f"     ⏭️  財報已有最新（{expected_fin_suffix}），跳過")
            time.sleep(SLEEP_SEC)
            if check_attention_stock(stock_id, name, state, stock_type):
                has_new = True
            print()
            time.sleep(SLEEP_SEC)
            continue

        # ── 財報監控 ──────────────────────────────────────
        all_reports = []
        fetch_error = False
        for year in [ROC_YEAR, ROC_YEAR - 1]:
            result = fetch_report_list(stock_id, year)
            if result == "BLOCKED":
                print(f"     🛑 IP 被封鎖，停止執行\n")
                sys.exit(1)
            if result == "ERROR":
                fetch_error = True
                break
            all_reports.extend(result)
            time.sleep(SLEEP_SEC)

        if fetch_error:
            print(f"     ⚠️  連線失敗，等待 45 秒後重試...")
            time.sleep(45)
            all_reports = []
            retry_error = False
            for year in [ROC_YEAR, ROC_YEAR - 1]:
                result = fetch_report_list(stock_id, year)
                if result == "BLOCKED":
                    print(f"     🛑 IP 被封鎖，停止執行\n")
                    sys.exit(1)
                if result == "ERROR":
                    retry_error = True
                    break
                all_reports.extend(result)
                time.sleep(SLEEP_SEC)
            if retry_error:
                print(f"     ❌ 重試仍失敗，本次略過")
                print()
                time.sleep(SLEEP_SEC)
                continue

        if not all_reports:
            print(f"     ⚠️  查無財報")
        else:
            # 由新到舊找第一份內文確實是財報的公告
            # （標題含「財報」的也可能是召集通知、背書保證等，靠內文欄位排除）
            report, curr_raw = None, None
            for cand in all_reports:
                c_year_d = cand["year"] - 1 if cand["season"] == "年報" else cand["year"]
                c_s      = DISPLAY_SEASON.get(cand["season"], cand["season"])
                c_key    = f"{stock_id}_{c_year_d}_{c_s}"
                if not DEBUG and stock_id in state:
                    if c_key in notified:
                        print(f"     ✅ 已通知過：{c_year_d}年 {c_s}")
                        break
                    if any(_period_order(k) > _period_order(c_key) for k in notified):
                        print(f"     ⚠️  已有更新期別，略過 {c_year_d}年 {c_s}")
                        break

                time.sleep(SLEEP_SEC)
                html = fetch_report_detail(cand["payload"])
                if not html:
                    print(f"     ⚠️  無法取得公告內文，本次略過")
                    break
                raw = parse_raw_financials(html)
                if raw:
                    report, curr_raw = cand, raw
                    break
                print(f"     ↪️  非財報公告，略過：{cand['title']}")

            if report:
                year    = report["year"]
                season  = report["season"]

                display_year = year - 1 if season == "年報" else year
                display_s    = DISPLAY_SEASON.get(season, season)
                key          = f"{stock_id}_{display_year}_{display_s}"

                if not DEBUG and stock_id not in state:
                    # 新加入名單的股票：只記錄目前最新期別當基準，不發通知，等下一季新財報
                    print(f"     🆕 新加入名單，記錄基準 {display_year}年 {display_s}，不發通知")
                    state[stock_id] = [key]
                    has_new = True
                else:
                    print(f"     🔔 新財報：{display_year}年 {display_s}（單季）")

                    prev_season = PREV_SEASON.get(season)
                    single_raw  = None

                    if prev_season is None:
                        single_raw = curr_raw
                    else:
                        if season == "年報":
                            prev_cands = [r for r in all_reports if r["year"] == year - 1 and r["season"] == "Q3"]                                       or [r for r in all_reports if r["season"] == "Q3"]
                        else:
                            prev_cands = [r for r in all_reports if r["year"] == year and r["season"] == prev_season]

                        prev_raw = {}
                        for prev_report in prev_cands:
                            time.sleep(SLEEP_SEC)
                            prev_html = fetch_report_detail(prev_report["payload"])
                            if not prev_html:
                                break
                            prev_raw = parse_raw_financials(prev_html)
                            if prev_raw:
                                break

                        if not prev_cands:
                            print(f"       ⚠️  找不到 {prev_season}，無法計算單季，本次略過")
                        elif not prev_raw:
                            # 不可改用累計數字發通知，下次執行再試
                            print(f"       ⚠️  前期 {prev_season} 資料取得或解析失敗，本次略過")
                        else:
                            single_raw = {k: curr_raw[k] - prev_raw[k] for k in FIN_FIELDS}

                    if single_raw:
                        ratios = calc_ratios(
                            single_raw["revenue"],
                            single_raw["gross"],
                            single_raw["operating"],
                            single_raw["net"],
                        )

                        eps      = single_raw.get("eps", 0)
                        price    = fetch_price(stock_id, name)
                        rev_b    = single_raw["revenue"] / 100000
                        latest_q = f"{display_year}{display_s}"
                        print(f"       營收：{rev_b:,.0f}億  毛利率：{ratios.get('gross',0):.1f}%  營業利益率：{ratios.get('operating',0):.1f}%  淨利率：{ratios.get('net',0):.1f}%")

                        # 穩定型：用近四季 TTM EPS 算本益比
                        ttm_eps = None
                        if stock_type == "穩定型" and eps and eps > 0:
                            ttm_eps = fetch_ttm_eps(name, stock_id, latest_q, eps)
                            if ttm_eps:
                                print(f"       近四季EPS：{ttm_eps:.2f}")
                            else:
                                print(f"       ⚠️  無法取得近四季EPS，改用年化")

                        msg = format_msg(name, stock_id, display_year, display_s, ratios, stock_type, eps, price, ttm_eps)

                        # 自動驗證；沒通過就只 push 給自己確認，不 broadcast
                        monthly_rev = fetch_quarter_revenue(name, stock_id, display_year, display_s)
                        problems    = validate_single(single_raw, curr_raw, monthly_rev)
                        if monthly_rev is None:
                            print(f"       ℹ️  月營收資料不齊，略過營收對帳")
                        if problems:
                            print(f"       ⚠️  未通過驗證，僅 push 給自己：{'；'.join(problems)}")
                            warn = "\n".join(f"・{p}" for p in problems)
                            msg  = f"⚠️ 數字未通過自動驗證，請確認\n{warn}\n\n{msg}"
                        else:
                            print(f"       ✅ 驗證通過")
                        print(msg)

                        # 產生季報獲利指標圖表（帶入最新季資料，避免 API 尚未更新）
                        chart_bytes = build_fin_combined(
                            name, stock_id, stock_type,
                            latest_q=latest_q,
                            ratios=ratios,
                            eps=eps,
                            ttm_eps=ttm_eps,
                            price=price,
                        )
                        filename = save_quarterly_chart(stock_id, chart_bytes) if chart_bytes else None
                        url      = get_chart_url(filename) if filename and not DEBUG else ""
                        if DEBUG:
                            print(f"     🖼️  季報圖表已存（本機預覽）：charts/{filename}" if filename else "     ❌ 季報圖表產生失敗")
                        elif url:
                            pending_charts.append({"stock_id": stock_id, "url": url, "message": msg,
                                                   "push_only": bool(problems)})
                        else:
                            # 平常只發圖片；圖表失敗時改用文字只通知自己，避免通知整個消失
                            print(f"     ❌ 季報圖表產生失敗，改用文字 push 給自己")
                            send_line_message(f"❌ 季報圖表產生失敗，此通知未發給其他人\n\n{msg}", mode="push")

                        notified.append(key)
                        state[stock_id] = notified
                        has_new = True

        # ── 注意股公告監控 ────────────────────────────────
        time.sleep(SLEEP_SEC)
        if check_attention_stock(stock_id, name, state, stock_type):
            has_new = True

        print()
        time.sleep(SLEEP_SEC)

    if has_new and not DEBUG:
        save_state(state)
        print("  💾 狀態已更新")
    else:
        print("  ℹ️  本次無新財報或公告")

    if pending_charts:
        Path("pending_charts.json").write_text(
            json.dumps(pending_charts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  📋 待發圖表：{len(pending_charts)} 筆")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()

# 測試請用 stocks.json 內的股票（name 才會正確傳給 chart API）
# python financial_checker.py --debug --stock 2408