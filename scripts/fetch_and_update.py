# -*- coding: utf-8 -*-
"""
GitHub Actions에서 실행되는 슬랙 데이터 자동 업데이트 스크립트.
환경변수 SLACK_TOKEN 필요 (xoxp- 사용자 토큰 권장).
"""
import json, re, sys, os, time, requests
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

TOKEN = os.environ.get('SLACK_TOKEN', '')
if not TOKEN:
    print("ERROR: SLACK_TOKEN environment variable not set")
    sys.exit(1)

print(f"Token: {TOKEN[:10]}...")

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
KST = timezone(timedelta(hours=9))

USER_MAP = {
    "U0AJ4HXGG9L": "건희", "U0AJKJBRNP3": "준태",
    "U0AK0UW8XG8": "윤하", "U0AJA945KE0": "지수", "U0AMWE87M0B": "유민",
}

CHANNELS = {
    "C0AJ38GC9HB": "00-announcement",
    "C0AM3GBFPGU": "01-pb_마성의팍스",
    "C0AJ6NYLKT4": "contents_2_차세린",
    "C0ALWFZQMTP": "02-카카오엔터_삼성",
    "C0ALWG29TB7": "03-카카오엔터_웹툰_쫀냐미_고라니",
    "C0AM3GKNCN8": "04-최고심_5월pb",
    "C0ALY0XQ40N": "05-잠재_기타",
    "C0AJU22MXMJ": "contents_민수달",
    "C0APK7RF82J": "07-발주-임가공",
    "C0AJ0DKH22F": "onf_주간회의",
    "C0AJ4HXRD5L": "onf_00_product",
    "C0AK0V0C7BJ": "onf_dev-ai",
    "C0AMC45RJ3U": "06-자사몰project",
    "C0AQ0V3H4JV": "onf_01_태자llm",
    "C0AQ0VDEC4R": "onf_02_태자감정엔진",
    "C0ARZGQTPFZ": "08-주간회의history",
    "C0ALD61LV18": "onf_legal",
    "C0AHRGP7CVD": "onf_archiving",
    "C0AJ087F3B5": "onf_uiux",
    "C0AN5UAE2EN": "TAEJA DM",
    "C0ALJEPNQ6B": "못리-상품",
    "C0ALZU1NDMG": "team1-팬덤상품",
}

EMOJI_MAP = {
    "white_check_mark": "✅", "+1": "👍", "eyes": "👀", "heart": "❤️",
    "thumbsup": "👍", "heavy_check_mark": "✅", "100": "💯",
    "raised_hands": "🙌", "pray": "🙏", "fire": "🔥", "tada": "🎉",
    "ok_hand": "👌", "clap": "👏", "muscle": "💪", "thinking_face": "🤔",
}

SKIP_SUB = {"channel_join", "channel_leave", "channel_name", "channel_purpose",
            "channel_topic", "channel_archive", "channel_unarchive"}


def get_user(uid):
    if uid in USER_MAP:
        return USER_MAP[uid]
    try:
        r = requests.get("https://slack.com/api/users.info", headers=HEADERS, params={"user": uid}, timeout=5)
        d = r.json()
        if d.get("ok"):
            p = d["user"].get("profile", {})
            name = p.get("display_name") or p.get("real_name") or uid
            USER_MAP[uid] = name
            return name
    except:
        pass
    return uid


def clean(text):
    def repl_user(m):
        return "@" + get_user(m.group(1))
    text = re.sub(r'<@(U[A-Z0-9]+)>', repl_user, text)
    text = re.sub(r'<!subteam\^[A-Z0-9]+>', '@팀', text)
    text = re.sub(r'<!here>', '@here', text)
    text = re.sub(r'<!channel>', '@channel', text)
    text = re.sub(r'<(https?://[^|>]+)\|([^>]+)>', r'\2', text)
    text = re.sub(r'<(https?://[^>]+)>', r'\1', text)
    text = re.sub(r'<mailto:([^|>]+)\|([^>]+)>', r'\2', text)
    text = re.sub(r'<#C[A-Z0-9]+\|([^>]+)>', r'#\1', text)
    text = re.sub(r'<#C[A-Z0-9]+>', '', text)
    return text.strip()


def process_msg(msg):
    sub = msg.get("subtype", "")
    if sub in SKIP_SUB:
        return None
    bot_id = msg.get("bot_id", "")
    if bot_id:
        bp = msg.get("bot_profile", {})
        bn = bp.get("name", "")
        if any(s in bn for s in ["Google Calendar", "Slackbot"]):
            return None

    uid = msg.get("user", "")
    ts = msg.get("ts", "")
    text = msg.get("text", "")
    if not ts:
        return None

    dt = datetime.fromtimestamp(float(ts), tz=KST)
    date = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%H:%M")

    sender = get_user(uid) if uid else "bot"
    cleaned = clean(text)

    for f in msg.get("files", []):
        fn = f.get("name", "file")
        cleaned += f" [파일: {fn}]"

    for rx in msg.get("reactions", []):
        emoji = rx.get("name", "")
        ec = EMOJI_MAP.get(emoji, f":{emoji}:")
        users = [get_user(u) for u in rx.get("users", [])]
        cleaned += f" [{ec} {','.join(users)}]"

    if not cleaned and not msg.get("files"):
        cleaned = "[이미지]"

    return {"sender": sender, "time": time_str, "text": cleaned, "date": date, "ts": ts, "_ts": float(ts)}


def main():
    total_thread_replies = 0
    total_msgs = 0

    for ch_id, ch_name in CHANNELS.items():
        print(f"\n== #{ch_name} ==")

        all_msgs = []
        cursor = None
        while True:
            params = {"channel": ch_id, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            r = requests.get("https://slack.com/api/conversations.history",
                             headers=HEADERS, params=params, timeout=10)
            d = r.json()
            if not d.get("ok"):
                print(f"  ERROR: {d.get('error')}")
                break
            all_msgs.extend(d.get("messages", []))
            if not d.get("has_more"):
                break
            cursor = d.get("response_metadata", {}).get("next_cursor")
            time.sleep(0.3)

        print(f"  Raw: {len(all_msgs)}")

        by_date = defaultdict(list)

        for msg in all_msgs:
            result = process_msg(msg)
            if not result:
                continue

            result["text"] = re.sub(r'\s*\[쓰레드 \d+개\]', '', result["text"])
            by_date[result["date"]].append(result)

            reply_count = msg.get("reply_count", 0)
            thread_ts = msg.get("thread_ts")
            if reply_count > 0 and thread_ts and thread_ts == msg.get("ts"):
                try:
                    params2 = {"channel": ch_id, "ts": thread_ts, "limit": 200}
                    r2 = requests.get("https://slack.com/api/conversations.replies",
                                      headers=HEADERS, params=params2, timeout=10)
                    d2 = r2.json()
                    if d2.get("ok"):
                        replies = d2.get("messages", [])[1:]
                        for reply in replies:
                            rr = process_msg(reply)
                            if rr:
                                rr["text"] = "↳ " + rr["text"]
                                by_date[rr["date"]].append(rr)
                                total_thread_replies += 1
                    time.sleep(0.2)
                except Exception as e:
                    print(f"  Thread error: {e}")

        ch_total = 0
        for date in by_date:
            by_date[date].sort(key=lambda m: m["_ts"])
            for m in by_date[date]:
                del m["_ts"]
                del m["date"]
            ch_total += len(by_date[date])

        total_msgs += ch_total
        print(f"  Final: {ch_total} msgs")

        for date, msgs in by_date.items():
            jp = os.path.join(DATA_DIR, f"{date}.json")
            if os.path.exists(jp):
                with open(jp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {"channels": {}, "todos": []}

            if ch_id in data.get("channels", {}):
                data["channels"][ch_id]["fullLog"] = msgs
            else:
                data["channels"][ch_id] = {"fullLog": msgs}

            with open(jp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        time.sleep(0.3)

    all_dates = sorted(set(
        f.replace('.json', '') for f in os.listdir(DATA_DIR)
        if re.match(r'^\d{4}-\d{2}-\d{2}\.json$', f)
    ))
    with open(os.path.join(DATA_DIR, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(all_dates, f, ensure_ascii=False, indent=2)

    print(f"\n=== DONE ===")
    print(f"Total msgs: {total_msgs}")
    print(f"Thread replies: {total_thread_replies}")
    print(f"Dates: {len(all_dates)}")


if __name__ == '__main__':
    main()
