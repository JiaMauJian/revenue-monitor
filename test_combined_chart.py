"""
測試用：抓 2408 南亞科真實月營收，產圖後組合 header + chart 輸出 charts/test_combined.png
"""

import io
import re
import time
import requests
import urllib3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from matplotlib import font_manager
from bs4 import BeautifulSoup
from io import StringIO
import pandas as pd
import os
from pathlib import Path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── 中文字型 ──────────────────────────────────────────────────
def _setup_cjk_font():
    _repo_font = Path(__file__).parent / "fonts" / "NotoSansTC-Regular.ttf"
    candidates = [str(_repo_font), "C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/mingliu.ttc"]
    for path in candidates:
        if os.path.exists(path):
            font_manager.fontManager.addfont(path)
            prop = font_manager.FontProperties(fname=path)
            matplotlib.rcParams["font.family"] = prop.get_name()
            return

_setup_cjk_font()

# ── 抓營收（同 revenue_checker.py）───────────────────────────
_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st10_ifrs"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://mopsov.twse.com.tw/mops/web/t05st10_ifrs",
    "Content-Type": "application/x-www-form-urlencoded",
}


def fetch_revenue(stock_id: str, is_new=True, year="", month="") -> tuple:
    payload = {
        "step": "1", "firstin": "1", "off": "1",
        "queryName": "co_id", "inpuType": "co_id",
        "TYPEK": "all", "co_id": stock_id,
        "isnew": "true" if is_new else "false",
        "year": year, "month": month,
    }
    try:
        resp = requests.post(_URL, headers=_HEADERS, data=payload, verify=False, timeout=20)
        resp.encoding = "utf-8"
        if "t05st10_ifrs_form" in resp.text and "詳細資料" in resp.text:
            time.sleep(3)
            payload["step"] = "2"
            resp = requests.post(_URL, headers=_HEADERS, data=payload, verify=False, timeout=20)
            resp.encoding = "utf-8"
        if "查詢過於頻繁" in resp.text or resp.status_code == 403:
            return "BLOCKED", "IP 遭封鎖"
        if "查詢無資料" in resp.text:
            return None, "查無資料"
        soup = BeautifulSoup(resp.text, "html.parser")
        date_el = soup.find("td", string=lambda x: x and "民國" in x)
        date_text = date_el.get_text(strip=True) if date_el else ""

        # BeautifulSoup 抓備註（pandas read_html 會漏掉 th+td 混排的備註列）
        note = ""
        for th in soup.find_all("th", string=lambda x: x and "備註" in x):
            td = th.find_next_sibling("td")
            if td:
                note = td.get_text(strip=True)
                break

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
                if note:
                    res["備註"] = note
                return res, date_text
        return None, "解析表格失敗"
    except Exception as e:
        return None, f"連線錯誤：{e}"


def fetch_with_mom(stock_id: str) -> tuple:
    data, date_text = fetch_revenue(stock_id, is_new=True)
    if data == "BLOCKED" or data is None:
        return data, date_text
    date_nums = re.findall(r"\d+", date_text)
    if not date_nums:
        return data, date_text
    curr_y, curr_m = int(date_nums[0]), int(date_nums[1])
    prev_y = curr_y - 1 if curr_m == 1 else curr_y
    prev_m = 12       if curr_m == 1 else curr_m - 1
    time.sleep(3)
    prev_data, _ = fetch_revenue(stock_id, is_new=False, year=str(prev_y), month=f"{prev_m:02d}")
    this_val = data.get("本月", 0)
    mom = 0.0
    if isinstance(prev_data, dict) and prev_data.get("本月", 0) > 0:
        mom = (this_val - prev_data["本月"]) / prev_data["本月"] * 100
    data["MoM"]   = round(mom, 2)
    data["year"]  = curr_y
    data["month"] = curr_m
    return data, date_text


# ── 主流程 ────────────────────────────────────────────────────
STOCK_ID   = "2408"
STOCK_NAME = "南亞科"
STOCK_TYPE = "長期強勢型"

print(f"抓取 {STOCK_ID} {STOCK_NAME} 月營收...")
data, date_text = fetch_with_mom(STOCK_ID)

if data in ("BLOCKED", None):
    print(f"抓取失敗：{date_text}")
    exit(1)

rev      = data.get("本月", 0)
mom_rate = data.get("MoM", 0)
yoy_rate = data.get("增減百分比", 0)
month    = data.get("month", 0)
year_roc = data.get("year", 0)
note     = data.get("備註", "")
rev_date = f"{year_roc + 1911}/{month:02d}"
rev_bil  = rev / 100_000  # 千元 → 億元

print(f"  公告日期：{date_text}")
print(f"  營收：{rev_bil:.2f} 億元  MoM：{mom_rate:+.2f}%  YoY：{yoy_rate:+.2f}%")
if note:
    print(f"  備註：{note}")

# 產圖（用 chart.py 的 build_chart）
from chart import build_chart, build_quarterly_chart
print("產生圖表...")
chart_bytes = build_chart(
    STOCK_NAME, STOCK_ID,
    latest_rev={"date": rev_date, "value": rev, "yoy": yoy_rate},
    stock_type=STOCK_TYPE,
)
if not chart_bytes:
    print("圖表生成失敗")
    exit(1)

chart_img = mpimg.imread(io.BytesIO(chart_bytes))

# ── 輔助 ──────────────────────────────────────────────────────
def _fmt_rate(v: float) -> str:
    return f"{v:,.1f}" if abs(v) >= 1000 else f"{v:.1f}"

def _rate_color(v: float) -> str:
    return "#009933" if v < 0 else "#CC0000"

# ── 組合圖 ────────────────────────────────────────────────────
height_ratios = [0.45, 0.95, 0.40, 5.0] if note else [0.45, 0.95, 5.0]
n_rows = 4 if note else 3

fig = plt.figure(figsize=(8, 8.5))
fig.patch.set_facecolor("white")
gs = fig.add_gridspec(n_rows, 1, height_ratios=height_ratios, hspace=0)

# Row 0：月份標題
ax_title = fig.add_subplot(gs[0])
ax_title.set_facecolor("white")
ax_title.axis("off")
ax_title.text(0.5, 0.5, f"民國{year_roc}年{month}月營收",
              transform=ax_title.transAxes,
              ha="center", va="center",
              fontsize=16, fontweight="bold", color="black")
ax_title.axhline(y=0, color="#CCCCCC", linewidth=0.8)

# 計算 chart 嵌入 aspect="equal" 後的水平 letterbox 比例
_ch_px, _cw_px = chart_img.shape[:2]
_chart_subplot_h = 5.0 / sum(height_ratios) * 8.5
_display_w_ratio = _chart_subplot_h * (_cw_px / _ch_px) / 8.0
_lb = max(0.0, (1.0 - _display_w_ratio) / 2.0)
_rng = 1.0 - 2 * _lb
_col_xs  = [_lb + _rng / 6, 0.50, 1.0 - _lb - _rng / 6]
_sep_xs  = [_lb + _rng / 3, 1.0 - _lb - _rng / 3]

# Row 1：3 欄統計
ax_stats = fig.add_subplot(gs[1])
ax_stats.set_facecolor("white")
ax_stats.axis("off")
ax_stats.set_xlim(0, 1)
ax_stats.set_ylim(0, 1)

headers    = ["營收(億元)", "月增率(%)", "年增率(%)"]
values     = [f"{rev_bil:.2f}", _fmt_rate(mom_rate), _fmt_rate(yoy_rate)]
val_colors = ["black", _rate_color(mom_rate), _rate_color(yoy_rate)]

for x, hdr in zip(_col_xs, headers):
    ax_stats.text(x, 0.78, hdr, ha="center", va="top",
                  fontsize=12, color="#555555", transform=ax_stats.transAxes)
for x, val, col in zip(_col_xs, values, val_colors):
    ax_stats.text(x, 0.50, val, ha="center", va="top",
                  fontsize=20, fontweight="bold", color=col,
                  transform=ax_stats.transAxes)

for sep_x in _sep_xs:
    ax_stats.axvline(x=sep_x, ymin=0.05, ymax=0.95, color="#CCCCCC", linewidth=0.8)
ax_stats.axhline(y=0, color="#CCCCCC", linewidth=0.8)

# Row 2：備註（有才顯示）
if note:
    ax_note = fig.add_subplot(gs[2])
    ax_note.set_facecolor("white")
    ax_note.axis("off")
    ax_note.text(0.5, 0.55, note,
                 transform=ax_note.transAxes,
                 ha="center", va="center",
                 fontsize=12, color="#1a6bbf")
    ax_note.axhline(y=0, color="#CCCCCC", linewidth=0.8)

# 最後一列：圖表
ax_chart = fig.add_subplot(gs[-1])
ax_chart.set_facecolor("white")
ax_chart.imshow(chart_img, aspect="equal")
ax_chart.axis("off")

out_path = Path("charts/test_combined.png")
out_path.parent.mkdir(exist_ok=True)
plt.savefig(str(out_path), dpi=120, bbox_inches="tight", facecolor="white")
plt.close(fig)
print(f"done: {out_path}")

# ── 財報組合圖 ────────────────────────────────────────────────
from financial_checker import (
    fetch_report_list, fetch_report_detail, parse_raw_financials,
    calc_ratios, find_report, PREV_SEASON, DISPLAY_SEASON, ROC_YEAR,
)

print("\n抓取季報資料...")
try:
    _all_rpts = []
    for _yr in [ROC_YEAR, ROC_YEAR - 1]:
        _res = fetch_report_list(STOCK_ID, _yr)
        if _res in ("BLOCKED", "ERROR"):
            raise ValueError(f"財報清單抓取失敗：{_res}")
        _all_rpts.extend(_res)
        time.sleep(3)

    if not _all_rpts:
        raise ValueError("查無財報")

    _rpt     = _all_rpts[0]
    _year    = _rpt["year"]
    _season  = _rpt["season"]
    _disp_yr = _year - 1 if _season == "年報" else _year
    _disp_s  = DISPLAY_SEASON.get(_season, _season)
    print(f"  最新財報：民國{_disp_yr}年 {_disp_s}")

    time.sleep(3)
    _html     = fetch_report_detail(_rpt["payload"])
    _curr_raw = parse_raw_financials(_html) if _html else {}
    if not _curr_raw or _curr_raw.get("revenue", 0) == 0:
        raise ValueError("無法解析財務數字")

    _prev_s = PREV_SEASON.get(_season)
    _single = None
    if _prev_s is None:
        _single = _curr_raw
    else:
        if _season == "年報":
            _prev_rpt = find_report([r for r in _all_rpts if r["year"] == _year - 1], "Q3") \
                     or find_report(_all_rpts, "Q3")
        else:
            _prev_rpt = find_report([r for r in _all_rpts if r["year"] == _year], _prev_s)
        if _prev_rpt:
            time.sleep(3)
            _prev_html = fetch_report_detail(_prev_rpt["payload"])
            _prev_raw  = parse_raw_financials(_prev_html) if _prev_html else {}
            if _prev_raw and _prev_raw.get("revenue", 0) > 0:
                _single = {k: _curr_raw[k] - _prev_raw.get(k, 0)
                           for k in ["revenue", "gross", "operating", "net"]}
                _single["eps"] = _curr_raw.get("eps", 0) - _prev_raw.get("eps", 0)
            else:
                _single = _curr_raw
        else:
            _single = _curr_raw

    ratios   = calc_ratios(_single["revenue"], _single["gross"], _single["operating"], _single["net"])
    latest_q = f"{_disp_yr}{_disp_s}"   # e.g. "115Q1"
    print(f"  毛利率：{ratios.get('gross',0):.1f}%  營業利益率：{ratios.get('operating',0):.1f}%  淨利率：{ratios.get('net',0):.1f}%")

    def _q_title(label):
        m = re.match(r"(\d+)Q(\d)", label)
        return f"民國{m.group(1)}年Q{m.group(2)} 季報" if m else label

    def _fmtr(v):
        return f"{v:.1f}" if v is not None else "N/A"

    def _frc(v):
        if v is None: return "#888888"
        return "#009933" if v < 0 else "#CC0000"

    fin_bytes = build_quarterly_chart(
        STOCK_NAME, STOCK_ID, stock_type=STOCK_TYPE,
        latest_quarter=latest_q,
        latest_rates=ratios,
    )
    if not fin_bytes:
        raise ValueError("季報圖表生成失敗")
    fin_img = mpimg.imread(io.BytesIO(fin_bytes))

    fin_hr  = [0.45, 0.95, 3.5]
    fin_fig = plt.figure(figsize=(8, 7))
    fin_fig.patch.set_facecolor("white")
    fin_gs  = fin_fig.add_gridspec(3, 1, height_ratios=fin_hr, hspace=0)

    # Row 0：標題
    ax_ft = fin_fig.add_subplot(fin_gs[0])
    ax_ft.set_facecolor("white")
    ax_ft.axis("off")
    ax_ft.text(0.5, 0.5, _q_title(latest_q),
               transform=ax_ft.transAxes,
               ha="center", va="center",
               fontsize=17, fontweight="bold", color="black")
    ax_ft.axhline(y=0, color="#CCCCCC", linewidth=0.8)

    # 計算 letterbox 讓 stats 欄位對齊圖表寬度
    _fh, _fw   = fin_img.shape[:2]
    _fs_h      = 3.5 / sum(fin_hr) * 7.0
    _flb       = max(0.0, (1.0 - _fs_h * (_fw / _fh) / 8.0) / 2.0)
    _frng      = 1.0 - 2 * _flb
    _fcx       = [_flb + _frng / 6, 0.50, 1.0 - _flb - _frng / 6]
    _fsx       = [_flb + _frng / 3, 1.0 - _flb - _frng / 3]

    # Row 1：3 欄統計
    ax_fs = fin_fig.add_subplot(fin_gs[1])
    ax_fs.set_facecolor("white")
    ax_fs.axis("off")
    ax_fs.set_xlim(0, 1)
    ax_fs.set_ylim(0, 1)

    f_hdrs = ["毛利率(%)", "營業利益率(%)", "稅後純益率(%)"]
    f_vals = [_fmtr(ratios.get("gross")), _fmtr(ratios.get("operating")), _fmtr(ratios.get("net"))]
    f_cols = [_frc(ratios.get("gross")),  _frc(ratios.get("operating")),  _frc(ratios.get("net"))]

    for x, h in zip(_fcx, f_hdrs):
        ax_fs.text(x, 0.78, h, ha="center", va="top",
                   fontsize=12, color="#555555", transform=ax_fs.transAxes)
    for x, v, c in zip(_fcx, f_vals, f_cols):
        ax_fs.text(x, 0.50, v, ha="center", va="top",
                   fontsize=20, fontweight="bold", color=c,
                   transform=ax_fs.transAxes)

    for sx in _fsx:
        ax_fs.axvline(x=sx, ymin=0.05, ymax=0.95, color="#CCCCCC", linewidth=0.8)
    ax_fs.axhline(y=0, color="#CCCCCC", linewidth=0.8)

    # Row 2：圖表
    ax_fc = fin_fig.add_subplot(fin_gs[2])
    ax_fc.set_facecolor("white")
    ax_fc.imshow(fin_img, aspect="equal")
    ax_fc.axis("off")

    fin_path = Path("charts/test_fin_combined.png")
    plt.savefig(str(fin_path), dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fin_fig)
    print(f"done: {fin_path}")

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"季報圖表失敗：{e}")
