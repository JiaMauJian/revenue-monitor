"""
豁達人生部落格新文章監控 - GitHub Actions 版
監控 https://huodalife.pixnet.net/blog 有無新文章，有則發 LINE 通知

HTML 結構（實測確認）：
  <div class="article" id="article-{article_id}">
    <li class="publish">  ← 日期
    <li class="title" data-article-link="{url}">
      <h2><a href="{url}">{title}</a></h2>
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── 設定 ─────────────────────────────────────────────────
BLOG_SITE  = "huodalife"
BLOG_NAME  = "豁達人生"
BLOG_URL   = f"https://{BLOG_SITE}.pixnet.net/blog"
STATE_FILE = "last_blog_state.json"
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer":    BLOG_URL,
}


# 英文月份 → 數字
MONTH_MAP = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
    "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
    "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


# ── 爬首頁文章列表 ───────────────────────────────────────
def fetch_latest_articles() -> list:
    """
    爬部落格首頁，解析文章列表
    回傳：[{"id": str, "title": str, "link": str, "date": str}, ...]

    過濾規則：
    - Pixnet 新格式 article id 為 18 位時間戳；舊格式（約 10 位）為置頂文章，略過
    """
    try:
        resp = requests.get(BLOG_URL, headers=HEADERS, timeout=20)
        resp.encoding = "utf-8"

        if resp.status_code != 200:
            print(f"     ❌ HTTP {resp.status_code}")
            return []

        soup   = BeautifulSoup(resp.text, "html.parser")
        result = []

        for div in soup.select("div.article[id^='article-']"):
            # 取 article id（去掉前綴 "article-"）
            art_id = div.get("id", "").replace("article-", "")
            if not art_id:
                continue

            # 過濾置頂文章：新格式 id 為 18 位時間戳，舊格式較短
            if len(art_id) < 15:
                continue

            # 標題與連結
            title_li = div.select_one("li.title")
            if not title_li:
                continue
            a_tag = title_li.select_one("h2 a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            link  = a_tag.get("href", BLOG_URL)

            # 日期（span 格式：Mar / 13 / 2026 / 16:37）
            pub_li = div.select_one("li.publish")
            date   = ""
            if pub_li:
                month = pub_li.select_one("span.month")
                day   = pub_li.select_one("span.date")
                year  = pub_li.select_one("span.year")
                t     = pub_li.select_one("span.time")
                if month and day and year:
                    m_str  = MONTH_MAP.get(month.get_text(strip=True), month.get_text(strip=True))
                    d_str  = day.get_text(strip=True).zfill(2)
                    date   = f"{year.get_text(strip=True)}/{m_str}/{d_str}"
                    if t:
                        date += f" {t.get_text(strip=True)}"

            result.append({
                "id":    art_id,
                "title": title,
                "link":  link,
                "date":  date,
            })

        return result

    except Exception as e:
        print(f"     ❌ fetch_latest_articles 錯誤：{e}")
        return []


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
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    if not token:
        print("  ⚠️  未設定 LINE 環境變數")
        return
    resp = requests.post(
        "https://api.line.me/v2/bot/message/broadcast",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={"messages": [{"type": "text", "text": message}]},
        timeout=10,
    )
    print("  ✅ LINE 通知已發送" if resp.status_code == 200 else f"  ❌ 失敗：{resp.status_code} {resp.text}")


# ── 主程式 ───────────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  {BLOG_NAME} 部落格新文章監控")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*55}\n")

    state   = load_state()
    has_new = False

    print(f"  🔍 查詢 {BLOG_NAME} 最新文章...")
    articles = fetch_latest_articles()

    if not articles:
        print("  ⚠️  無法取得文章列表，本次跳過\n")
        return

    print(f"  📄 取得 {len(articles)} 篇文章")

    notified_ids = set(state.get("notified_ids", []))
    new_articles = [a for a in articles if a["id"] not in notified_ids]

    if not new_articles:
        print(f"  ✅ 無新文章（最新：{articles[0]['title'][:25]}...）")
    else:
        # article id 是時間戳，由小到大 = 由舊到新，確保通知順序正確
        new_articles.sort(key=lambda a: a["id"])

        for art in new_articles:
            print(f"  🔔 新文章：{art['title']}")
            date_str = f"\n日期：{art['date']}" if art["date"] else ""
            msg = (
                f"📝 {BLOG_NAME} 新文章\n\n"
                f"{art['title']}{date_str}\n"
                f"{art['link']}"
            )
            send_line_message(msg)
            notified_ids.add(art["id"])
            has_new = True
            time.sleep(0.5)

    if has_new:
        state["notified_ids"] = list(notified_ids)
        save_state(state)
        print("  💾 狀態已更新")
    else:
        print("  ℹ️  本次無新文章")

    print(f"\n{'='*55}\n")


if __name__ == "__main__":
    main()
