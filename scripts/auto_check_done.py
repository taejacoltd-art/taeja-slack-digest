# -*- coding: utf-8 -*-
"""
지난 N일치 미완료 todo를 재스캔, 완료 증거가 명확한 건만
Firebase todos_state에 자동 체크 + data/*.json 에 evidence 기록.

완료 판정 기준 (tight — 셋 중 하나라도 충족 시):
1. 담당자 본인의 명시적 완료 표현 ("완료", "끝냈", "전달드립니다", "공유드립니다",
   "업로드했", "보냈", "드렸", "완성")
2. 담당자가 결과물 파일/링크 공유 ([파일:...] 또는 URL 포함)
3. 요청자/관리자의 명시적 승인 ("OK", "컨펌", "좋습니다") + 담당자 응답 근거

완료 아님 (유지):
- "확인했습니다"만 (내용 확인일 뿐)
- 이모지 리액션만 (👀 등)
- "작업중", "내일 드릴게요" 등 예정/진행중

사용자가 이미 수동 토글한 건 (Firebase 값 존재) 은 건드리지 않음.
"""
import json, os, sys, re, requests
from datetime import datetime, timezone, timedelta
from anthropic import Anthropic

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, 'data')
KST = timezone(timedelta(hours=9))

FB_URL = "https://daily-digest-8b058-default-rtdb.asia-southeast1.firebasedatabase.app"

LOOKBACK_DAYS = 7
MAX_EVALUATIONS = 120

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
if not ANTHROPIC_API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set")
    sys.exit(1)

client = Anthropic(api_key=ANTHROPIC_API_KEY)
MODEL = "claude-haiku-4-5-20251001"

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
}

EVAL_PROMPT = """아래 미완료 todo가 이후 대화에서 실제로 완료되었는지 엄격히 판정하세요.

## Todo
- 생성일: {created}
- 채널: #{channel}
- 담당자: {assignee}
- 내용: {text}

## 생성일 이후 대화
{later_messages}

## 완료 판정 기준 (tight — 하나라도 충족 시 done=true)
1. 담당자 본인의 명시적 완료 표현: "완료", "끝냈", "전달드립니다", "공유드립니다", "업로드했", "보냈", "드렸", "완성"
2. 담당자가 결과물 파일/링크 공유: [파일:...] 또는 URL 포함
3. 요청자/관리자의 명시적 승인: "OK", "컨펌", "좋습니다", "확정" + 담당자 응답 근거

## 완료 아님 (done=false)
- "확인했습니다"만 있고 결과물 없음
- 이모지 리액션만 (👀 등)
- "작업중", "내일 드릴게요" 등 예정/진행중
- 담당자가 아닌 제3자의 발언
- 주제가 다른 대화

불확실하면 false. evidence는 근거 메시지 한 줄 요약(발화자+내용).

## 출력 (JSON만, 다른 설명 금지)
{{"done": true/false, "evidence": "근거 한 줄"}}
"""


def load_firebase_state():
    try:
        r = requests.get(f"{FB_URL}/todos_state.json", timeout=10)
        return r.json() or {}
    except Exception as e:
        print(f"[ERR] FB read: {e}")
        return {}


def set_firebase_done(date_key, todo_id, value=True):
    try:
        r = requests.put(
            f"{FB_URL}/todos_state/{date_key}/{todo_id}.json",
            data=json.dumps(value),
            timeout=10,
        )
        return r.ok
    except Exception as e:
        print(f"[ERR] FB write: {e}")
        return False


def load_date(dk):
    p = os.path.join(DATA_DIR, f"{dk}.json")
    if not os.path.exists(p):
        return None
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_date(dk, data):
    p = os.path.join(DATA_DIR, f"{dk}.json")
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_target_dates():
    today = datetime.now(KST).date()
    return [(today - timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(LOOKBACK_DAYS + 1)]


def channel_id_by_name(name):
    for cid, cn in CHANNELS.items():
        if cn == name:
            return cid
    return None


def collect_later_messages(channel_id, from_date, all_data):
    """채널의 from_date 이후(포함) 메시지 수집, 최대 300줄"""
    lines = []
    sorted_dates = sorted(all_data.keys())
    for dk in sorted_dates:
        if dk < from_date:
            continue
        data = all_data[dk]
        ch = data.get('channels', {}).get(channel_id)
        if not ch:
            continue
        for m in ch.get('fullLog', []):
            lines.append(f"[{dk} {m['time']}] {m['sender']}: {m['text']}")
    return lines[-300:]


def main():
    fb_state = load_firebase_state()
    target_dates = get_target_dates()

    all_data = {}
    for dk in target_dates:
        d = load_date(dk)
        if d:
            all_data[dk] = d

    pending = []
    for dk, data in all_data.items():
        for t in data.get('todos', []):
            if t.get('done'):
                continue
            # 사용자가 이미 Firebase에 값 넣었으면 건드리지 않음 (true/false 모두)
            fb_val = (fb_state.get(dk) or {}).get(str(t['id']))
            if fb_val is not None:
                continue
            pending.append((dk, t))

    print(f"Pending todos: {len(pending)}")
    if len(pending) > MAX_EVALUATIONS:
        print(f"Capping at {MAX_EVALUATIONS}")
        pending = pending[:MAX_EVALUATIONS]

    checked = 0
    evaluated = 0
    for dk, todo in pending:
        ch_name = todo.get('channel', '')
        ch_id = channel_id_by_name(ch_name)
        if not ch_id:
            continue

        later = collect_later_messages(ch_id, dk, all_data)
        if len(later) < 2:
            continue

        assignees = todo['assignee'] if isinstance(todo['assignee'], list) else [todo['assignee']]

        conv = '\n'.join(later)
        if len(conv) > 30000:
            conv = conv[-30000:]

        prompt = EVAL_PROMPT.format(
            created=dk,
            channel=ch_name,
            assignee=', '.join(assignees),
            text=todo['text'],
            later_messages=conv,
        )

        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            evaluated += 1
            text = resp.content[0].text.strip()
            m = re.search(r'\{[\s\S]*\}', text)
            if not m:
                continue
            result = json.loads(m.group(0))

            if result.get('done') is True:
                evidence = result.get('evidence', '자동 판정')[:200]
                if set_firebase_done(dk, todo['id'], True):
                    todo['evidence'] = f"[자동] {evidence}"
                    print(f"  ✓ [{dk}] #{ch_name} id={todo['id']}: {todo['text'][:40]} — {evidence}")
                    checked += 1
        except Exception as e:
            print(f"  [ERR] {dk}/{todo['id']}: {e}")

    for dk, data in all_data.items():
        save_date(dk, data)

    print(f"\n=== DONE ===")
    print(f"Evaluated: {evaluated}")
    print(f"Auto-checked: {checked}")


if __name__ == '__main__':
    main()
