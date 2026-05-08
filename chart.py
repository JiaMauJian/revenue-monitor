"""
營收圖表生成模組
- 抓月營收 + 月均價資料
- 畫綠色長條（月營收）+ 黑色虛線（月均價）
- 存到 charts/{stock_num}_{YYYYMMDD}.png
- 回傳 GitHub raw URL（需 GITHUB_REPOSITORY 環境變數）
"""

import io
import os
import re
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
    _repo_font = Path(__file__).parent / "fonts" / "NotoSansTC-Regular.ttf"
    candidates = [
        str(_repo_font),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJKtc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/mingliu.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            font_manager.fontManager.addfont(path)
            prop = font_manager.FontProperties(fname=path)
            matplotlib.rcParams["font.family"] = prop.get_name()
            return

    # 找不到固定路徑時，搜尋系統已安裝的 CJK 字型
    keywords = ("noto", "cjk", "noto sans cjk", "msjh", "mingliu", "pingfang")
    for f in font_manager.fontManager.ttflist:
        if any(k in f.name.lower() for k in keywords):
            matplotlib.rcParams["font.family"] = f.name
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
        if not isinstance(rev_data, list) or len(rev_data) < 4:
            print(f"     ❌ 圖表生成失敗：Revenue API 格式異常 → {rev_data}")
            return None
        if not isinstance(price_data, list) or len(price_data) < 2:
            print(f"     ❌ 圖表生成失敗：GetPrice API 格式異常 → {price_data}")
            return None

        rev_dates = list(rev_data[2][-months:])
        rev_vals  = [float(v) / 100_000 if v is not None else 0.0 for v in rev_data[3][-months:]]

        def _safe(v):
            f = float(v) if v is not None else None
            return None if f is None or f >= 999999 else f

        # 用 date 做 key，避免長度不一致造成 index 錯位
        # rev_data[6]=YoY%, rev_data[7]=累積YoY%
        _api_dates = list(rev_data[2])
        _yoy_map  = {d: _safe(v) for d, v in zip(_api_dates, rev_data[6] if len(rev_data) > 6 else [])}
        _cumy_map = {d: _safe(v) for d, v in zip(_api_dates, rev_data[7] if len(rev_data) > 7 else [])}

        # 若最新營收日期不在 API 資料中，補到最後
        if latest_rev:
            if not rev_dates or rev_dates[-1] != latest_rev["date"]:
                rev_dates.append(latest_rev["date"])
                rev_vals.append(float(latest_rev["value"]) / 100_000)
            if latest_rev.get("yoy") is not None:
                _yoy_map[latest_rev["date"]] = latest_rev["yoy"]

        # GetPrice: [[dates_N], [prices_N+1], [latest_date]]
        p_month_dates = price_data[0]         # N 個月份
        p_prices      = price_data[1]         # N+1 個價格
        p_latest_date = price_data[2][0] if len(price_data) > 2 and price_data[2] else None  # "2026/04/08"

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

        fig = plt.figure(figsize=(8, 6.0), constrained_layout=True)
        gs_main = fig.add_gridspec(2, 1, height_ratios=[4.0, 2.0], hspace=0.15)
        ax1 = fig.add_subplot(gs_main[0])
        ax2 = ax1.twinx()

        # 右 Y 軸：綠色長條（月營收，最後一個空位不畫）
        x = list(range(len(all_dates)))
        bar_vals = [v if v > 0 else 0.0 for v in all_bars]
        ax2.bar(x, bar_vals, color="#4CAF50", alpha=0.85, width=0.45, zorder=2)
        ax2.set_ylabel("營收（億元）", color="black", fontsize=12)
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
        ax1.set_ylabel("月均價", color="black", fontsize=12)
        ax1.tick_params(axis="y", labelcolor="black")
        ax1.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.3, zorder=1)

        # X 軸：資料多時只顯示年份，少時顯示每月
        if len(all_dates) > 18:
            seen, year_ticks, year_labels = set(), [], []
            for i, d in enumerate(all_dates):
                yr = str(d)[:4]
                if yr not in seen:
                    seen.add(yr)
                    year_ticks.append(i)
                    year_labels.append(yr)
            ax1.set_xticks(year_ticks)
            ax1.set_xticklabels(year_labels, rotation=0, ha="center", fontsize=12, color="black")
        else:
            ax1.set_xticks(x)
            ax1.set_xticklabels(all_dates, rotation=45, ha="right", fontsize=12, color="black")

        title = f"{stock_name}({stock_num})  {stock_type}  營收與月均價" if stock_type else f"{stock_name}({stock_num})營收與月均價"
        ax1.set_title(title, fontsize=16, pad=10, color="black", fontweight="bold")
        ax1.set_xlim(-0.5, len(all_dates) - 0.5)
        ax2.set_xlim(-0.5, len(all_dates) - 0.5)

        # ── 營收明細表（僅用 rev_data API 資料，最新 3 個月，新→舊）
        _raw_dates = list(rev_data[2])
        _raw_vals  = rev_data[3]
        n_show  = min(3, len(_raw_dates))
        start_i = len(_raw_dates) - n_show
        t_dates = list(reversed(_raw_dates[start_i:]))
        t_revs  = list(reversed([float(v) / 100_000 if v is not None else 0.0
                                  for v in _raw_vals[start_i:]]))
        t_yoy   = [_yoy_map.get(d) for d in t_dates]
        t_cumy  = [_cumy_map.get(d) for d in t_dates]
        t_mom_fwd = []
        for i in range(start_i, start_i + n_show):
            prev_v = float(_raw_vals[i - 1]) if i > 0 and _raw_vals[i - 1] is not None else None
            curr_v = float(_raw_vals[i])     if _raw_vals[i] is not None else 0.0
            if prev_v and prev_v > 0:
                t_mom_fwd.append((curr_v - prev_v) / prev_v * 100)
            else:
                t_mom_fwd.append(None)
        t_mom = list(reversed(t_mom_fwd))

        ax_tbl = fig.add_subplot(gs_main[1])
        ax_tbl.set_facecolor("white")
        ax_tbl.axis("off")
        ax_tbl.set_xlim(0, 1)
        ax_tbl.set_ylim(0, 1)

        col_xs   = [0.10, 0.30, 0.50, 0.70, 0.90]
        col_hdrs = ["日期", "月營收\n(億元)", "MoM\n(%)", "YoY\n(%)", "累計YoY\n(%)"]

        def _fmt_r(v):
            if v is None: return "N/A"
            if abs(v) >= 10000: return f"{v:,.0f}"
            if abs(v) >= 1000:  return f"{v:,.1f}"
            return f"{v:.1f}"

        def _rc(v):
            if v is None: return "#888888"
            return "#009933" if v < 0 else "#CC0000"

        ax_tbl.axhline(y=0.97, color="#CCCCCC", linewidth=0.8)
        for x, hdr in zip(col_xs, col_hdrs):
            ax_tbl.text(x, 0.92, hdr, ha="center", va="top",
                        fontsize=16, color="#555555", fontweight="bold",
                        transform=ax_tbl.transAxes)
        ax_tbl.axhline(y=0.60, color="#CCCCCC", linewidth=0.6)

        row_ys = [0.44, 0.25, 0.07]
        for ri, (d, rv, mom, yoy, cumy) in enumerate(zip(t_dates, t_revs, t_mom, t_yoy, t_cumy)):
            y = row_ys[ri]
            ax_tbl.text(col_xs[0], y, d,             ha="center", va="center", fontsize=16, color="black",   transform=ax_tbl.transAxes)
            ax_tbl.text(col_xs[1], y, f"{rv:.2f}",   ha="center", va="center", fontsize=16, color="black",   transform=ax_tbl.transAxes)
            ax_tbl.text(col_xs[2], y, _fmt_r(mom),   ha="center", va="center", fontsize=16, color=_rc(mom),  transform=ax_tbl.transAxes)
            ax_tbl.text(col_xs[3], y, _fmt_r(yoy),   ha="center", va="center", fontsize=16, color=_rc(yoy),  transform=ax_tbl.transAxes)
            ax_tbl.text(col_xs[4], y, _fmt_r(cumy),  ha="center", va="center", fontsize=16, color=_rc(cumy), transform=ax_tbl.transAxes)
            if ri < n_show - 1:
                ax_tbl.axhline(y=y - 0.09, color="#EEEEEE", linewidth=0.5)
        ax_tbl.axhline(y=0.01, color="#CCCCCC", linewidth=0.8)

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"     ❌ 圖表生成失敗：{e}")
        return None


def build_quarterly_chart(stock_name: str, stock_num: str, stock_type: str = "",
                           latest_quarter: str | None = None,
                           latest_rates: dict | None = None) -> bytes | None:
    """
    產生季度獲利指標圖表 PNG bytes（毛利率、營業利益率、稅後純益率）。
    API 回傳陣列：index 2 = 季別標籤，8 = 毛利率，9 = 營業利益率，10 = 稅後純益率。

    latest_quarter: 最新季別標籤，格式如 "114Q4"（民國年+季）
    latest_rates:   {"gross": 45.2, "operating": 20.1, "net": 18.3}（%）
                    若 API 尚未收錄此季，會補到圖表最後一點。
    """
    try:
        data = _call("QuarterlyRpt", stock_name, stock_num)

        quarters        = list(data[2])
        gross_rates     = [float(v) if v is not None else None for v in data[8]]
        operating_rates = [float(v) if v is not None else None for v in data[9]]
        net_rates       = [float(v) if v is not None else None for v in data[10]]

        # 若最新季別不在 API 資料中，補到最後
        if latest_quarter and latest_rates and latest_quarter not in quarters:
            quarters.append(latest_quarter)
            gross_rates.append(latest_rates.get("gross"))
            operating_rates.append(latest_rates.get("operating"))
            net_rates.append(latest_rates.get("net"))

        # 只取最後 8 季
        quarters        = quarters[-8:]
        gross_rates     = gross_rates[-8:]
        operating_rates = operating_rates[-8:]
        net_rates       = net_rates[-8:]

        def _fmt_quarter(label: str) -> str:
            m = re.match(r"(\d+)Q(\d)", label)
            if m:
                return f"{int(m.group(1)) + 1911}.{m.group(2)}Q"
            return label

        x_labels = [_fmt_quarter(q) for q in quarters]

        fig, ax = plt.subplots(figsize=(8, 4.8))
        x = list(range(len(quarters)))

        def plot_line(vals, color, marker, label):
            valid = [(i, v) for i, v in enumerate(vals) if v is not None]
            if valid:
                xi, yi = zip(*valid)
                ax.plot(xi, yi, color=color, marker=marker, linewidth=2.0,
                        markersize=6, label=label)

        plot_line(gross_rates,      "#1f77b4", "D", "毛利率")
        plot_line(operating_rates,  "#d62728", "s", "營業利益率")
        plot_line(net_rates,        "#ff7f0e", "^", "(稅後)純益率")

        ax.axhline(0, color="gray", linewidth=0.8, linestyle="--", alpha=0.5)
        ax.set_xticks(x)
        ax.set_xticklabels(x_labels, rotation=0, ha="center", fontsize=10)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.yaxis.tick_right()
        ax.yaxis.set_label_position("right")
        title = f"{stock_name}({stock_num})  {stock_type}  獲利指標(季)" if stock_type else f"{stock_name}({stock_num})獲利指標(季)"
        ax.set_title(title, fontsize=13, fontweight="bold", pad=10)
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"     ❌ 季報圖表生成失敗：{e}")
        return None


def build_revenue_combined(stock_name: str, stock_num: str, stock_type: str,
                            year_roc: int, month: int,
                            rev_bil: float, mom_rate: float, yoy_rate: float,
                            note: str = "", rev: float = 0,
                            rev_date: str = "") -> bytes | None:
    """月營收組合圖：標題 + 統計欄 + (備註) + 月營收圖。"""
    try:
        import matplotlib.image as mpimg

        chart_bytes = build_chart(stock_name, stock_num,
                                  latest_rev={"date": rev_date, "value": rev, "yoy": yoy_rate},
                                  stock_type=stock_type)
        if not chart_bytes:
            return None
        chart_img = mpimg.imread(io.BytesIO(chart_bytes))

        def _fmt_rate(v):
            return f"{v:,.1f}" if abs(v) >= 1000 else f"{v:.1f}"

        def _rate_color(v):
            return "#009933" if v < 0 else "#CC0000"

        height_ratios = [0.45, 0.95, 0.40, 5.0] if note else [0.45, 0.95, 5.0]
        n_rows = 4 if note else 3

        fig = plt.figure(figsize=(8, 8.5))
        fig.patch.set_facecolor("white")
        gs = fig.add_gridspec(n_rows, 1, height_ratios=height_ratios, hspace=0)

        ax_title = fig.add_subplot(gs[0])
        ax_title.set_facecolor("white")
        ax_title.axis("off")
        ax_title.text(0.5, 0.5, f"民國{year_roc}年{month}月營收",
                      transform=ax_title.transAxes,
                      ha="center", va="center",
                      fontsize=16, fontweight="bold", color="black")
        ax_title.axhline(y=0, color="#CCCCCC", linewidth=0.8)

        _ch_px, _cw_px = chart_img.shape[:2]
        _chart_subplot_h = 5.0 / sum(height_ratios) * 8.5
        _display_w_ratio = _chart_subplot_h * (_cw_px / _ch_px) / 8.0
        _lb  = max(0.0, (1.0 - _display_w_ratio) / 2.0)
        _rng = 1.0 - 2 * _lb
        _col_xs = [_lb + _rng / 6, 0.50, 1.0 - _lb - _rng / 6]
        _sep_xs = [_lb + _rng / 3, 1.0 - _lb - _rng / 3]

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

        if note:
            ax_note = fig.add_subplot(gs[2])
            ax_note.set_facecolor("white")
            ax_note.axis("off")
            ax_note.text(0.5, 0.55, note,
                         transform=ax_note.transAxes,
                         ha="center", va="center",
                         fontsize=12, color="#1a6bbf")
            ax_note.axhline(y=0, color="#CCCCCC", linewidth=0.8)

        ax_chart = fig.add_subplot(gs[-1])
        ax_chart.set_facecolor("white")
        ax_chart.imshow(chart_img, aspect="equal")
        ax_chart.axis("off")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"     ❌ 月營收組合圖生成失敗：{e}")
        return None


def build_fin_combined(stock_name: str, stock_num: str, stock_type: str,
                        latest_q: str, ratios: dict) -> bytes | None:
    """季報組合圖：標題 + 統計欄 + 季報圖。"""
    try:
        import matplotlib.image as mpimg

        fin_bytes = build_quarterly_chart(stock_name, stock_num, stock_type,
                                          latest_quarter=latest_q,
                                          latest_rates=ratios)
        if not fin_bytes:
            return None
        fin_img = mpimg.imread(io.BytesIO(fin_bytes))

        def _q_title(label):
            m = re.match(r"(\d+)Q(\d)", label)
            return f"民國{m.group(1)}年Q{m.group(2)} 季報" if m else label

        def _fmtr(v):
            return f"{v:.1f}" if v is not None else "N/A"

        def _frc(v):
            if v is None: return "#888888"
            return "#009933" if v < 0 else "#CC0000"

        fin_hr  = [0.45, 0.95, 3.5]
        fin_fig = plt.figure(figsize=(8, 7))
        fin_fig.patch.set_facecolor("white")
        fin_gs  = fin_fig.add_gridspec(3, 1, height_ratios=fin_hr, hspace=0)

        ax_ft = fin_fig.add_subplot(fin_gs[0])
        ax_ft.set_facecolor("white")
        ax_ft.axis("off")
        ax_ft.text(0.5, 0.5, _q_title(latest_q),
                   transform=ax_ft.transAxes,
                   ha="center", va="center",
                   fontsize=17, fontweight="bold", color="black")
        ax_ft.axhline(y=0, color="#CCCCCC", linewidth=0.8)

        _fh, _fw = fin_img.shape[:2]
        _fs_h    = 3.5 / sum(fin_hr) * 7.0
        _flb     = max(0.0, (1.0 - _fs_h * (_fw / _fh) / 8.0) / 2.0)
        _frng    = 1.0 - 2 * _flb
        _fcx     = [_flb + _frng / 6, 0.50, 1.0 - _flb - _frng / 6]
        _fsx     = [_flb + _frng / 3, 1.0 - _flb - _frng / 3]

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

        ax_fc = fin_fig.add_subplot(fin_gs[2])
        ax_fc.set_facecolor("white")
        ax_fc.imshow(fin_img, aspect="equal")
        ax_fc.axis("off")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
        plt.close(fin_fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"     ❌ 季報組合圖生成失敗：{e}")
        return None


def save_chart(stock_num: str, image_bytes: bytes) -> str | None:
    """
    存圖到 charts/{stock_num}_{YYYYMMDD}.png。
    先刪除同股票的舊月營收圖（不動季報圖），再存新圖。
    回傳檔名（如 2408_20260409.png），失敗回傳 None。
    """
    try:
        CHARTS_DIR.mkdir(exist_ok=True)

        # 只刪除月營收圖（檔名格式：stock_num_數字.png），不動季報圖
        for old in CHARTS_DIR.glob(f"{stock_num}_[0-9]*.png"):
            old.unlink()

        filename = f"{stock_num}_{datetime.now().strftime('%Y%m%d')}.png"
        (CHARTS_DIR / filename).write_bytes(image_bytes)
        print(f"     ✅ 圖表已存：charts/{filename}")
        return filename

    except Exception as e:
        print(f"     ❌ 圖表儲存失敗：{e}")
        return None


def save_quarterly_chart(stock_num: str, image_bytes: bytes) -> str | None:
    """
    存季報圖到 charts/{stock_num}_q{YYYYMMDD}.png。
    先刪除同股票的舊季報圖，再存新圖。
    回傳檔名，失敗回傳 None。
    """
    try:
        CHARTS_DIR.mkdir(exist_ok=True)

        for old in CHARTS_DIR.glob(f"{stock_num}_q*.png"):
            old.unlink()

        filename = f"{stock_num}_q{datetime.now().strftime('%Y%m%d')}.png"
        (CHARTS_DIR / filename).write_bytes(image_bytes)
        print(f"     ✅ 季報圖表已存：charts/{filename}")
        return filename

    except Exception as e:
        print(f"     ❌ 季報圖表儲存失敗：{e}")
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
