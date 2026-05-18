"""
實際打 API 測試 fetch_price，印出完整 JSON 結構
"""

import json
import requests

STOCKS = [
    ("3006", "晶豪科",   50,  2000),
]

for sid, name, lo, hi in STOCKS:
    print(f"\n{'='*50}")
    print(f"{sid} {name}")
    try:
        resp = requests.post(
            "https://huodalife.azurewebsites.net/Chart1.aspx/GetPrice",
            json={"input_option_stock_name": name, "input_option_stock_num": sid},
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        raw = resp.json()
        d = raw.get("d", {})
        if isinstance(d, str):
            d = json.loads(d)
        print(f"d[0] (labels): {d[0] if len(d) > 0 else 'N/A'}")
        print(f"d[1] (prices): {d[1] if len(d) > 1 else 'N/A'}")
        price = d[1][-1] if len(d) > 1 and d[1] else None
        if price is None or price == "":
            print(f"❌ 股價：None")
        elif float(price) < lo or float(price) > hi:
            print(f"⚠️  股價 {price} 超出合理範圍 [{lo}, {hi}]")
        else:
            print(f"✅ 股價：{price}")
    except Exception as e:
        print(f"❌ 錯誤：{e}")
