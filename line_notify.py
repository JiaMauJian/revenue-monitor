"""
LINE Messaging API 共用通知模組

支援兩種發送模式：
  - broadcast : 發給所有已加好友的用戶（不需 USER_ID）
  - push      : 發給指定單一用戶（需要 LINE_USER_ID）

環境變數：
  LINE_CHANNEL_TOKEN  必填
  LINE_USER_ID        push 模式時必填；broadcast 時可省略
"""

import os
import requests


def send_line_image(image_url: str, mode: str = "auto") -> bool:
    """發送 LINE 圖片訊息（需公開 HTTPS URL）。"""
    token   = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")

    if not token:
        print("  ⚠️  未設定 LINE_CHANNEL_TOKEN")
        return False

    if mode == "auto":
        actual_mode = "push" if user_id else "broadcast"
    else:
        actual_mode = mode

    if actual_mode == "push" and not user_id:
        actual_mode = "broadcast"

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {token}",
    }
    image_msg = {
        "type":               "image",
        "originalContentUrl": image_url,
        "previewImageUrl":    image_url,
    }

    if actual_mode == "push":
        url  = "https://api.line.me/v2/bot/message/push"
        body = {"to": user_id, "messages": [image_msg]}
    else:
        url  = "https://api.line.me/v2/bot/message/broadcast"
        body = {"messages": [image_msg]}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        if resp.status_code == 200:
            print(f"  [OK] LINE 圖表已發送（{actual_mode}）")
            return True
        print(f"  [ERR] LINE 圖表發送失敗（{actual_mode}）：{resp.status_code} {resp.text}")
        return False
    except Exception as e:
        print(f"  [ERR] LINE 圖表發送例外：{e}")
        return False


def send_line_message(message: str, mode: str = "auto") -> bool:
    """
    發送 LINE 訊息。

    Parameters
    ----------
    message : str
        要發送的文字訊息。
    mode : str
        "broadcast" | "push" | "auto"
        auto = 有 LINE_USER_ID 就用 push，否則用 broadcast。

    Returns
    -------
    bool
        True 表示發送成功，False 表示失敗或未設定 token。
    """
    token   = os.environ.get("LINE_CHANNEL_TOKEN", "")
    user_id = os.environ.get("LINE_USER_ID", "")

    if not token:
        print("  ⚠️  未設定 LINE_CHANNEL_TOKEN")
        return False

    # 決定實際模式
    if mode == "auto":
        actual_mode = "push" if user_id else "broadcast"
    else:
        actual_mode = mode

    if actual_mode == "push" and not user_id:
        print("  ⚠️  push 模式需設定 LINE_USER_ID，自動改用 broadcast")
        actual_mode = "broadcast"

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {token}",
    }

    if actual_mode == "push":
        url  = "https://api.line.me/v2/bot/message/push"
        body = {
            "to":       user_id,
            "messages": [{"type": "text", "text": message}],
        }
    else:
        url  = "https://api.line.me/v2/bot/message/broadcast"
        body = {"messages": [{"type": "text", "text": message}]}

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=10)
        if resp.status_code == 200:
            print(f"  [OK] LINE 通知已發送（{actual_mode}）")
            return True
        else:
            print(f"  [ERR] LINE 發送失敗（{actual_mode}）：{resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"  [ERR] LINE 發送例外：{e}")
        return False
