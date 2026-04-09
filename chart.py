"""
營收圖表生成模組
- 抓月營收 + 月均價資料
- 畫綠色長條（月營收）+ 黑色虛線（月均價）
- 存到 charts/{stock_num}_{YYYYMMDD}.png
- 回傳 GitHub raw URL（需 GITHUB_REPOSITORY 環境變數）
"""

import io
import os
import json
from datetime import datetime
from pathlib import Path

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib import font_manager

CHARTS_DIR = Path("charts")

# ── 中文字型 ────────────────────────────────────────────────
def _setup_cjk_font():
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/mingliu.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            prop = font_manager.FontProperties(fname=path)
            matplotlib.rcParams["font.family"] = prop.get_name()
            return

_setup_cjk_font()

API_BASE = "https://huodalife.azurewebsites.net/Chart1.aspx"
HEADERS  = {"Content-Type": "application/json"}


def _call(endpoint: str, stock_name: str, stock_num: str) -> dict:
    resp = requests.post(
        f"{API_BASE}/{endpoint}",
        json={
            "input_option_stock_name": stock_name,
            "input_option_stock_num":  stock_num,
        },
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json().get("d", {})
    if isinstance(raw, str):
        raw = json.loads(raw)
    return raw


def build_chart(stock_name: str, stock_num: str, months: int = 48,
                latest_rev: dict | None = None, stock_type: str = "") -> bytes | None:
    """
    產生圖表 PNG bytes，失敗時回傳 None。
    latest_rev: {"date": "2026/03", "value": 18170000}（千元單位）
                代表最新一筆還未進 API 的營收，會附加到圖表最後。
    """
    try:
        rev_data   = _call("Revenue",  stock_name, stock_num)
        price_data = _call("GetPrice", stock_name, stock_num)

        # Revenue: [stock_num, stock_name, [dates], [values_千元]]
        rev_dates = list(rev_data[2][-months:])
        rev_vals  = [float(v) / 100_000 if v is not None else 0.0 for v in rev_data[3][-months:]]

        # 若最新營收日期不在 API 資料中，補到最後
        if latest_rev:
            if not rev_dates or rev_dates[-1] != latest_rev["date"]:
                rev_dates.append(latest_rev["date"])
                rev_vals.append(float(latest_rev["value"]) / 100_000)

        # GetPrice: [[dates_N], [prices_N+1], [latest_date]]
        p_month_dates = price_data[0]         # N 個月份
        p_prices      = price_data[1]         # N+1 個價格
        p_latest_date = price_data[2][0] if price_data[2] else None  # "2026/04/08"

        # 對齊月均價到營收日期
        price_map = dict(zip(p_month_dates, p_prices[:len(p_month_dates)]))
        aligned_prices = [
            (float(price_map[d]) if price_map.get(d) not in (None, "") else None)
            for d in rev_dates
        ]

        # 加上最新股價那一點（"2026/04/08"）
        all_dates = list(rev_dates)
        all_bars  = list(rev_vals)
        all_prices = list(aligned_prices)
        if p_latest_date and len(p_prices) > len(p_month_dates):
            latest_price = float(p_prices[-1]) if p_prices[-1] not in (None, "") else None
            all_dates.append(p_latest_date)
            all_bars.append(0.0)          # 最新日期沒有營收資料
            all_prices.append(latest_price)

        fig, ax1 = plt.subplots(figsize=(11, 5))
        ax2 = ax1.twinx()

        # 右 Y 軸：綠色長條（月營收，最後一個空位不畫）
        x = list(range(len(all_dates)))
        bar_vals = [v if v > 0 else 0.0 for v in all_bars]
        ax2.bar(x, bar_vals, color="#4CAF50", alpha=0.85, zorder=2)
        ax2.set_ylabel("營收（億元）", color="black", fontsize=10)
        ax2.tick_params(axis="y", labelcolor="black")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}"))
        ax2.set_ylim(bottom=0)

        # 左 Y 軸：黑色虛線（月均價）；畫在最上層
        ax1.set_zorder(ax2.get_zorder() + 1)
        ax1.patch.set_visible(False)
        valid = [(i, p) for i, p in enumerate(all_prices) if p is not None]
        if valid:
            px_idx, px_vals = zip(*valid)
            ax1.plot(px_idx, px_vals, color="black", linestyle="--", linewidth=3.0)
        ax1.set_ylabel("月均價（元）", color="black", fontsize=10)
        ax1.tick_params(axis="y", labelcolor="black")
        ax1.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.3, zorder=1)

        # X 軸：每個刻度都顯示
        ax1.set_xticks(x)
        ax1.set_xticklabels(all_dates, rotation=45, ha="right", fontsize=7, color="black")

        title = f"{stock_name}({stock_num})  {stock_type}  營收與月均價" if stock_type else f"{stock_name}({stock_num})營收與月均價"
        ax1.set_title(title, fontsize=13, pad=10, color="black", fontweight="bold")
        ax1.set_xlim(-0.5, len(all_dates) - 0.5)
        ax2.set_xlim(-0.5, len(all_dates) - 0.5)
        fig.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"     ❌ 圖表生成失敗：{e}")
        return None


def save_chart(stock_num: str, image_bytes: bytes) -> str | None:
    """
    存圖到 charts/{stock_num}_{YYYYMMDD}.png。
    先刪除同股票的舊圖，再存新圖。
    回傳檔名（如 2408_20260409.png），失敗回傳 None。
    """
    try:
        CHARTS_DIR.mkdir(exist_ok=True)

        # 刪除同股票舊圖
        for old in CHARTS_DIR.glob(f"{stock_num}_*.png"):
            old.unlink()

        filename = f"{stock_num}_{datetime.now().strftime('%Y%m%d')}.png"
        (CHARTS_DIR / filename).write_bytes(image_bytes)
        print(f"     ✅ 圖表已存：charts/{filename}")
        return filename

    except Exception as e:
        print(f"     ❌ 圖表儲存失敗：{e}")
        return None


def get_chart_url(filename: str) -> str:
    """
    回傳 GitHub raw URL。
    需要環境變數 GITHUB_REPOSITORY（格式：owner/repo）。
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        return ""
    return f"https://raw.githubusercontent.com/{repo}/main/charts/{filename}"


def cleanup_removed_charts(stocks: dict):
    """刪除 charts/ 裡已不在 stocks 清單中的圖片。"""
    if not CHARTS_DIR.exists():
        return
    for f in CHARTS_DIR.glob("*.png"):
        stock_num = f.stem.split("_")[0]
        if stock_num not in stocks:
            f.unlink()
            print(f"  🗑️  已移除舊圖表：{f.name}")
