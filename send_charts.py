"""
讀取 pending_charts.json，發送 LINE 圖片通知，完成後刪除檔案。
需在 git push 後執行，確保圖片 URL 已可公開存取。
"""

import json
from pathlib import Path
from dotenv import load_dotenv
from line_notify import send_line_image

load_dotenv()

PENDING_FILE = Path("pending_charts.json")


def main():
    if not PENDING_FILE.exists():
        print("  ℹ️  無待發圖表")
        return

    entries = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    print(f"  📤 發送 {len(entries)} 張圖表...")

    for item in entries:
        stock_id = item.get("stock_id", "")
        url      = item.get("url", "")
        if url:
            send_line_image(url, mode="broadcast")

    PENDING_FILE.unlink()
    print("  ✅ 圖表發送完成")


if __name__ == "__main__":
    main()
