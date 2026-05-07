# -*- coding: utf-8 -*-
"""
룰 기반 채널 summary + todos 추출 — LLM 호출 없이 동작.
Anthropic API 크레딧 불필요. 분 단위 즉시 처리.

용법:
  python scripts/analyze_rules.py                  # TODAY (KST) 처리
  python scripts/analyze_rules.py --date 2026-05-07
  python scripts/analyze_rules.py --all             # data/*.json 전체 재처리
  python scripts/analyze_rules.py --since 2026-05-01

규칙:
  - todos: 부탁/확인/공유/전달 + 마감 키워드 또는 멘션이 들어간 메시지를 todo로 채택
  - assignee: @이름 멘션 우선 / @here·@channel = 8명 전원 / 기본값 '준태'
  - 중복(text 같음) 스킵, id는 기존 todos 뒤에 이어붙임
  - summary: 헤드라인(발화자·메시지수) + 핵심 메시지 0~3개 (HTML)
  - 사용자 수동 수정 보존: data 파일에 _manual=true 키 있으면 채널 스킵
"""
import json, os, re, sys, glob, argparse
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'data')
KST = timezone(timedelta(hours=9))

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

CHANNEL_TO_PROJECT = {
    "01-pb_마성의팍스": "마성의팍스",
    "02-카카오엔터_삼성": "삼성와펜",
    "03-카카오엔터_웹툰_쫀냐미_고라니": "쫀냐미",
    "04-최고심_5월pb": "최고심",
    "07-발주-임가공": "발주",
    "contents_2_차세린": "차세린",
    "contents_민수달": "민수달",
    "06-자사몰project": "자사몰",
    "onf_01_태자llm": "태자LLM",
    "onf_02_태자감정엔진": "감정엔진",
}

REQUEST_KEYWORDS = [
    '부탁드립니다', '부탁드려요', '부탁드림', '부탁해요', '부탁해주세요',
    '확인부탁', '확인 부탁', '확인 좀', '확인해주세요', '확인해주실', '체크부탁', '체크해주세요',
    '전달부탁', '전달 부탁', '전달해주세요',
    '공유부탁', '공유 부탁', '공유해주세요', '공유드립니다',
    '회신부탁', '회신 부탁', '답변부탁', '답변 부탁',
    '검토부탁', '검토 부탁', '리뷰 부탁',
    '진행부탁', '처리부탁',
]
DEADLINE_KEYWORDS = [
    'ASAP', 'asap', '급함', '급해요', '오늘까지', '내일까지',
    '이번주까지', '금주내', '금일내', '내일 오전', '내일 오후',
]
DONE_HINTS = [
    '완료', '발송완료', '전달드림', '전달완료', '확인완료', '처리완료', '전달했', '보냈', '드렸',
]


def normalize_text(t: str) -> str:
    return re.sub(r'\s+', ' ', t or '').strip()


def find_mentioned_assignees(text: str):
    if not text:
        return []
    if '@here' in text or '@channel' in text or '@팀' in text:
        return list(VALID_ASSIGNEES)
    found = []
    for name in VALID_ASSIGNEES:
        if f'@{name}' in text:
            found.append(name)
    return found


def is_request(text: str) -> bool:
    if not text:
        return False
    if any(k in text for k in REQUEST_KEYWORDS):
        return True
    if any(k in text for k in DEADLINE_KEYWORDS):
        return True
    return False


def looks_done(text: str) -> bool:
    return any(k in (text or '') for k in DONE_HINTS)


def extract_todos_for_channel(ch_id, ch_name, full_log, existing_texts):
    todos = []
    project = CHANNEL_TO_PROJECT.get(ch_name, '')
    for m in full_log:
        text = normalize_text(m.get('text', ''))
        if not text or text == '[이미지]':
            continue
        if text.startswith('↳ '):
            continue
        if not is_request(text):
            continue
        if looks_done(text):
            continue
        if text in existing_texts:
            continue

        mentioned = find_mentioned_assignees(text)
        if mentioned:
            assignee = mentioned[0] if len(mentioned) == 1 else mentioned
        else:
            sender = m.get('sender', '')
            others = [n for n in VALID_ASSIGNEES if n != sender]
            assignee = '준태' if '준태' in others else (others[0] if others else '준태')

        todos.append({
            'assignee': assignee,
            'channel': ch_name,
            'channel_id': ch_id,
            'project': project,
            'text': text[:300],
            'source_ts': m.get('ts', ''),
            'done': False,
        })
        existing_texts.add(text)
    return todos


def build_summary_html(ch_name, full_log):
    if not full_log:
        return ''
    senders = [m.get('sender', '') for m in full_log if m.get('sender')]
    unique_senders = list(dict.fromkeys(senders))
    main_speaker = unique_senders[0] if unique_senders else '?'
    extra = len(unique_senders) - 1
    speaker_label = main_speaker if extra <= 0 else f'{main_speaker} 외 {extra}명'
    headline = f'<span class="person">{speaker_label}</span> · {len(full_log)}건'

    bullets = []
    seen_bullets = set()
    for m in full_log:
        text = normalize_text(m.get('text', ''))
        if not text or text == '[이미지]' or text.startswith('↳ '):
            continue
        if not is_request(text):
            continue
        sender = m.get('sender', '')
        snippet = text if len(text) <= 90 else text[:87] + '...'
        key = (sender, snippet)
        if key in seen_bullets:
            continue
        seen_bullets.add(key)
        bullets.append(f'<li><span class="person">{sender}</span> {snippet}</li>')
        if len(bullets) >= 3:
            break

    if not bullets:
        for m in full_log[:1]:
            text = normalize_text(m.get('text', ''))
            if text and text != '[이미지]':
                snippet = text if len(text) <= 90 else text[:87] + '...'
                bullets.append(f'<li><span class="person">{m.get("sender","?")}</span> {snippet}</li>')

    if bullets:
        return f'<div class="sum-headline">{headline}</div><ul class="sum-bullets">{"".join(bullets)}</ul>'
    return f'<div class="sum-headline">{headline}</div>'


def build_messages_preview(full_log, limit=5):
    msgs = []
    for m in full_log[:limit]:
        msgs.append({
            'sender': m.get('sender', ''),
            'time': m.get('time', ''),
            'text': m.get('text', '')[:300],
        })
    return msgs


def process_one_date(date_str: str) -> dict:
    path = os.path.join(DATA_DIR, f'{date_str}.json')
    if not os.path.exists(path):
        return {'date': date_str, 'skipped': 'no_file'}

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not data.get('todos'):
        data['todos'] = []
    next_id = max((t.get('id', 0) for t in data['todos']), default=0) + 1
    existing_texts = {normalize_text(t.get('text', '')) for t in data['todos']}

    new_todos_count = 0
    summaries_filled = 0
    channels = data.get('channels', {})

    for ch_id, ch_data in channels.items():
        if ch_data.get('_manual'):
            continue
        ch_name = CHANNELS.get(ch_id, ch_id)
        full_log = ch_data.get('fullLog', []) or []
        if not full_log:
            continue

        summary_html = build_summary_html(ch_name, full_log)
        if summary_html:
            ch_data['summary'] = summary_html
            summaries_filled += 1
            ch_data['hasNew'] = True
        ch_data['messages'] = build_messages_preview(full_log)

        new_todos = extract_todos_for_channel(ch_id, ch_name, full_log, existing_texts)
        for t in new_todos:
            t['id'] = next_id
            next_id += 1
            data['todos'].append(t)
            new_todos_count += 1

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        'date': date_str,
        'new_todos': new_todos_count,
        'summaries': f'{summaries_filled}/{len(channels)}',
        'total_todos': len(data['todos']),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='특정 날짜 (YYYY-MM-DD)')
    ap.add_argument('--all', action='store_true', help='data/*.json 전체')
    ap.add_argument('--since', help='이 날짜 포함 이후 전부 (YYYY-MM-DD)')
    args = ap.parse_args()

    if args.all:
        targets = sorted([
            os.path.basename(p).replace('.json', '')
            for p in glob.glob(os.path.join(DATA_DIR, '20*.json'))
        ])
    elif args.since:
        targets = sorted([
            os.path.basename(p).replace('.json', '')
            for p in glob.glob(os.path.join(DATA_DIR, '20*.json'))
            if os.path.basename(p).replace('.json', '') >= args.since
        ])
    elif args.date:
        targets = [args.date]
    else:
        targets = [datetime.now(KST).strftime('%Y-%m-%d')]

    print(f'[analyze_rules] processing {len(targets)} date(s)')
    grand_new = 0
    for d in targets:
        r = process_one_date(d)
        if r.get('skipped'):
            print(f'  {d}: SKIP ({r["skipped"]})')
            continue
        print(f'  {d}: +{r["new_todos"]} todos, summaries {r["summaries"]} (total {r["total_todos"]})')
        grand_new += r['new_todos']

    print(f'[analyze_rules] DONE — new todos: {grand_new}')


if __name__ == '__main__':
    main()
