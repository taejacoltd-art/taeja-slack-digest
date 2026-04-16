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

대화:
{conversation}

위 대화를 분석하여 아래 형식의 JSON으로 응답하세요. 다른 설명 없이 JSON만.

{{
  "summary": "HTML 형식 2~3문장 요약. 발화자는 <span class=\\"person\\">이름</span>으로 표시. 수치/날짜/금액/업체명/결정사항 반드시 포함. 핵심 결론 중심.",
  "messages": [
    {{"sender": "이름", "time": "HH:MM", "text": "원문"}},
    ...최대 5개 핵심 메시지...
  ],
  "todos": [
    {{
      "assignee": "이름" 또는 ["이름1","이름2"],
      "channel": "{channel_name}",
      "project": "프로젝트명 (예: 마성의팍스, 삼성와펜, 차세린 등)",
      "text": "할일 내용 (마감일 포함)",
      "done": false
    }}
  ]
}}

규칙:
- 담당자는 이 8명 중에서: 건희, 규민, 준태, 윤하, 지수, 유민, 성민, 수혁
- @here/@channel이면 8명 전부, 복수 멘션이면 array
- "부탁드립니다/확인부탁/전달부탁/공유부탁" 등은 전부 todo
- 마감 키워드("오늘까지","내일","ASAP","급함","X일까지")는 text에 포함
- 완료된 사안(파일/링크 전달 확인)은 todos에 추가하지 말 것
- 잡담/이모지만 있는 채널은 summary는 한 줄, todos는 [], messages는 1~2개만
- 한국어
"""


def analyze_channel(channel_name, date, full_log):
    if not full_log:
        return None

    # 대화 텍스트 만들기
    conversation = "\n".join([
        f"[{m['time']}] {m['sender']}: {m['text']}"
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

            new_todo = {
                "id": next_id,
                "assignee": final_assignee,
                "channel": todo.get('channel', ch_name),
                "project": todo.get('project', ''),
                "text": todo['text'],
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
