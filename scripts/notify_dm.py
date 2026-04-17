# -*- coding: utf-8 -*-
"""대시보드 업데이트 결과를 준태 Slack DM으로 전송."""
import json, os, sys, re, requests
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime('%Y-%m-%d')
NOW = datetime.now(KST).strftime('%H:%M')

TOKEN = os.environ.get('SLACK_TOKEN', '')
TARGET_USER = 'U0AJKJBRNP3'  # 준태
DASHBOARD_URL = 'https://taejacoltd-art.github.io/taeja-slack-digest/'

if not TOKEN:
    print("ERROR: SLACK_TOKEN not set")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json; charset=utf-8"}

CHANNELS = {
    "C0AJ38GC9HB": "00-announcement", "C0AM3GBFPGU": "01-pb_마성의팍스",
    "C0AJ6NYLKT4": "contents_2_차세린", "C0ALWFZQMTP": "02-카카오엔터_삼성",
    "C0ALWG29TB7": "03-카카오엔터_웹툰", "C0AM3GKNCN8": "04-최고심_5월pb",
    "C0ALY0XQ40N": "05-잠재_기타", "C0AJU22MXMJ": "contents_민수달",
    "C0APK7RF82J": "07-발주-임가공", "C0AJ0DKH22F": "onf_주간회의",
    "C0AJ4HXRD5L": "onf_00_product", "C0AK0V0C7BJ": "onf_dev-ai",
    "C0AMC45RJ3U": "06-자사몰project", "C0AQ0V3H4JV": "onf_01_태자llm",
    "C0AQ0VDEC4R": "onf_02_태자감정엔진", "C0ARZGQTPFZ": "08-주간회의history",
    "C0ALD61LV18": "onf_legal", "C0AHRGP7CVD": "onf_archiving",
    "C0AJ087F3B5": "onf_uiux", "C0AN5UAE2EN": "TAEJA DM",
}


def strip_html(s):
    return re.sub(r'<[^>]+>', '', s or '').strip()


def main():
    today_path = os.path.join(DATA_DIR, f"{TODAY}.json")
    if not os.path.exists(today_path):
        print(f"No data for {TODAY}, skip DM")
        return

    with open(today_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    todos = [t for t in data.get('todos', []) if not t.get('done')]
    channels = data.get('channels', {})

    lines = [f"📊 *대시보드 업데이트* ({TODAY} {NOW} KST)"]

    active_channels = [(cid, ch) for cid, ch in channels.items()
                       if ch.get('summary') or (ch.get('fullLog') and len(ch['fullLog']) > 0)]

    if active_channels:
        lines.append(f"\n💬 *활성 채널 {len(active_channels)}개*")
        for cid, ch in active_channels:
            name = CHANNELS.get(cid, cid)
            msg_count = len(ch.get('fullLog', []))
            summary = strip_html(ch.get('summary', ''))[:100]
            lines.append(f"• #{name} ({msg_count}건)")
            if summary:
                lines.append(f"   └ {summary}{'...' if len(strip_html(ch.get('summary',''))) > 100 else ''}")
    else:
        lines.append("\n💬 오늘 활성 채널 없음")

    if todos:
        lines.append(f"\n📋 *미완료 할일 {len(todos)}건*")
        for t in todos[:8]:
            a = t.get('assignee', '')
            a_str = '/'.join(a) if isinstance(a, list) else a
            lines.append(f"• [{a_str}] {t.get('text','')[:80]}")
        if len(todos) > 8:
            lines.append(f"  ... 외 {len(todos)-8}건")

    lines.append(f"\n🔗 {DASHBOARD_URL}")

    text = "\n".join(lines)

    r = requests.post("https://slack.com/api/chat.postMessage",
                      headers=HEADERS,
                      json={"channel": TARGET_USER, "text": text, "mrkdwn": True},
                      timeout=10)
    d = r.json()
    if d.get("ok"):
        print(f"DM sent ({len(text)} chars)")
    else:
        print(f"ERROR: {d.get('error')}")
        sys.exit(1)


if __name__ == '__main__':
    main()
