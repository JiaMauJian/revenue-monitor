"""
手動發送 LINE broadcast 訊息的工具腳本。
用法：python send_msg.py <訊息內容>
"""

import sys
from dotenv import load_dotenv
load_dotenv()
from line_notify import send_line_message

if len(sys.argv) < 2:
    print("用法：python send_msg.py <訊息>")
    sys.exit(1)

msg = " ".join(sys.argv[1:])
send_line_message(msg, mode="broadcast")
