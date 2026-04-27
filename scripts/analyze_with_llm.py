# -*- coding: utf-8 -*-
"""
오늘 날짜의 fullLog를 Claude API로 분석하여:
- summary (HTML, 발화자 표시)
- messages (핵심 3~5개)
- todos (할일 추출, 담당자/마감 포함)
를 생성하고 JSON에 저장.
"""
import json, os, sys, re
from datetime import datetime, timezone, timedelta
from anthropic import Anthropic

sys.stdout.reconfigure(encoding='utf-8')

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime('%Y-%m-%d')

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
if not ANTHROPIC_API_KEY:
    print("ERROR: ANTHROPIC_API_KEY not set")
    sys.exit(1)

client = Anthropic(api_key=ANTHROPIC_API_KEY)
MODEL = "claude-sonnet-4-5"

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

VALID_ASSIGNEES = ['건희', '규민', '준태', '윤하', '지수', '유민', '성민', '수혁']

ANALYSIS_PROMPT = """다음은 슬랙 채널 #{channel_name}의 {date} 대화 전체 로그입니다.
각 메시지는 [HH:MM | ts=타임스탬프] 발화자: 내용 형태로 주어집니다.

대화:
{conversation}

위 대화를 분석하여 아래 형식의 JSON으로 응답하세요. 다른 설명 없이 JSON만.

{{
  "summary": "HTML 헤드라인+불릿 형식. 아래 규칙 엄수.",
  "messages": [
    {{"sender": "이름", "time": "HH:MM", "text": "원문"}}
  ],
  "todos": [
    {{
      "assignee": "이름" 또는 ["이름1","이름2"],
      "channel": "{channel_name}",
      "project": "프로젝트명 (예: 마성의팍스, 삼성와펜, 차세린 등)",
      "text": "할일 내용 (마감일 포함)",
      "source_ts": "이 todo의 근거가 된 메시지의 ts를 위 대화에서 그대로 복사 (없으면 빈 문자열)",
      "done": false
    }}
  ]
}}

[summary HTML 규칙 — 매우 중요]
- 형식: <div class="sum-headline">한줄 헤드라인</div><ul class="sum-bullets"><li>...</li></ul>
- 헤드라인: 누가/무엇을 결정/언제 — 한 줄(100자 이내). 발화자 <span class="person">이름</span> 표기, 핵심 수치·날짜·업체명·금액 반드시 포함
- 불릿: 액션·수치·결정사항만 0~3개. 각 불릿도 한 줄 짧게. 발화자는 <span class="person">이름</span>로 표시
- 불릿이 0개면 <ul> 자체 생략 (헤드라인만)
- 잡담/이모지만 있는 채널은 헤드라인 1줄로 끝
- 서술형 미사여구·"~을 진행했고 ~에 대해 논의했습니다" 같은 길게 늘어진 문장 금지
- HTML 외 \\n 줄바꿈 넣지 말 것

[messages 규칙]
- 최대 5개 핵심 메시지

[todos 규칙]
- 담당자는 8명 중: 건희, 규민, 준태, 윤하, 지수, 유민, 성민, 수혁
- @here/@channel이면 8명 전부, 복수 멘션이면 array
- "부탁드립니다/확인부탁/전달부탁/공유부탁" 등은 전부 todo
- 마감 키워드("오늘까지","내일","ASAP","급함","X일까지")는 text에 포함
- 완료된 사안(파일/링크 전달 확인)은 todos에 추가하지 말 것
- source_ts: 이 todo가 어느 메시지에서 나왔는지, 위 대화의 ts= 값을 그대로 한 개 복사. 추정·생성·변형 금지

전체 한국어.
"""


def analyze_channel(channel_name, date, full_log):
    if not full_log:
        return None

    # 대화 텍스트 만들기 (ts 포함 — todo 매핑용)
    conversation = "\n".join([
        f"[{m['time']} | ts={m.get('ts','')}] {m['sender']}: {m['text']}"
        for m in full_log
    ])

    # 너무 길면 잘라냄 (토큰 절약)
    if len(conversation) > 50000:
        conversation = conversation[:50000] + "\n...(이하 생략)"

    prompt = ANALYSIS_PROMPT.format(
        channel_name=channel_name,
        date=date,
        conversation=conversation,
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        # JSON 추출 (```json 블록 제거)
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            print(f"  [WARN] No JSON found")
            return None

        result = json.loads(m.group(0))
        return result
    except Exception as e:
        print(f"  [ERROR] {e}")
        return None


def main():
    today_path = os.path.join(DATA_DIR, f"{TODAY}.json")
    if not os.path.exists(today_path):
        print(f"No data for {TODAY}")
        return

    with open(today_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data.get('todos'):
        data['todos'] = []

    next_id = 1
    if data['todos']:
        next_id = max((t.get('id', 0) for t in data['todos']), default=0) + 1

    new_todos = []

    for ch_id, ch_data in data.get('channels', {}).items():
        ch_name = CHANNELS.get(ch_id, ch_id)
        full_log = ch_data.get('fullLog', [])
        if not full_log:
            continue

        print(f"\n== Analyzing #{ch_name} ({len(full_log)} msgs) ==")
        result = analyze_channel(ch_name, TODAY, full_log)
        if not result:
            continue

        # summary, messages 업데이트
        if result.get('summary'):
            ch_data['summary'] = result['summary']
        if result.get('messages'):
            ch_data['messages'] = result['messages']
        ch_data['hasNew'] = True

        # todos 추가 (중복 체크: 같은 text는 스킵)
        existing_texts = {t['text'] for t in data['todos']}
        for todo in result.get('todos', []):
            if not todo.get('text'):
                continue
            if todo['text'] in existing_texts:
                continue

            # assignee 정규화
            assignee = todo.get('assignee', '준태')
            if isinstance(assignee, str):
                assignee_list = [a.strip() for a in re.split(r'[,/]', assignee)]
            else:
                assignee_list = assignee
            assignee_list = [a for a in assignee_list if a in VALID_ASSIGNEES]
            if not assignee_list:
                continue
            final_assignee = assignee_list[0] if len(assignee_list) == 1 else assignee_list

            # source_ts 검증: fullLog에 실제 존재하는 ts만 인정
            src_ts = (todo.get('source_ts') or '').strip()
            valid_ts_set = {m.get('ts','') for m in full_log if m.get('ts')}
            if src_ts and src_ts not in valid_ts_set:
                src_ts = ''

            new_todo = {
                "id": next_id,
                "assignee": final_assignee,
                "channel": todo.get('channel', ch_name),
                "channel_id": ch_id,
                "project": todo.get('project', ''),
                "text": todo['text'],
                "source_ts": src_ts,
                "done": False,
            }
            data['todos'].append(new_todo)
            new_todos.append(new_todo)
            existing_texts.add(todo['text'])
            next_id += 1

        print(f"  Summary: {(ch_data.get('summary') or '')[:80]}...")
        print(f"  Todos extracted: {len([t for t in result.get('todos', [])])}")

    with open(today_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n=== DONE ===")
    print(f"New todos: {len(new_todos)}")
    print(f"Total todos: {len(data['todos'])}")


if __name__ == '__main__':
    main()
