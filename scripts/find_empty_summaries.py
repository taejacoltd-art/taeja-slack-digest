# -*- coding: utf-8 -*-
"""
data/*.json 스캔 → summary가 비어있는 채널 목록 추출.
동적 prompt md 파일 생성 (Claude Code Base Action용).

산출물:
  scripts/_regenerate_prompt.md  (동적 prompt)
  empty_targets.json             (탐지 결과 백업)

사용:
  python scripts/find_empty_summaries.py
"""
import json, os, sys, glob

sys.stdout.reconfigure(encoding='utf-8')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'data')
PROMPT_OUT = os.path.join(ROOT, 'scripts', '_regenerate_prompt.md')
TARGETS_OUT = os.path.join(ROOT, 'empty_targets.json')

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


def find_targets():
    targets = []
    for fp in sorted(glob.glob(os.path.join(DATA_DIR, '2026-*.json'))):
        date = os.path.basename(fp).replace('.json', '')
        with open(fp, 'r', encoding='utf-8') as f:
            d = json.load(f)
        for ch_id, ch in d.get('channels', {}).items():
            if ch.get('_manual'):
                continue
            full_log = ch.get('fullLog', [])
            if not full_log:
                continue
            if not ch.get('summary'):
                targets.append({
                    'date': date,
                    'channel_id': ch_id,
                    'channel_name': CHANNELS.get(ch_id, ch_id),
                    'msg_count': len(full_log),
                })
    return targets


def build_prompt(targets):
    if not targets:
        return "# 작업 없음\n\n빈 summary 채널이 없습니다. 아무 작업도 하지 마세요.\n"

    lines = [
        "# 빈 summary 채널 백필",
        "",
        "당신은 슬랙 다이제스트 자동화 도구입니다. 외부 설명 없이 작업만 하세요.",
        "",
        "## 작업 대상",
        "",
        "아래 (날짜, 채널) 쌍의 summary가 비어 있습니다. 각 항목에 대해:",
        "",
        "1. `data/{date}.json` 파일을 Read.",
        "2. `channels[{channel_id}].fullLog` 배열을 읽어 분석.",
        "3. `channels[{channel_id}].summary` (HTML, 아래 규칙), `channels[{channel_id}].messages` (핵심 최대 5개), `channels[{channel_id}].hasNew=true` 채우기.",
        "4. 새 todos 추출 → 파일의 `todos` 배열에 append (id = max(기존 id)+1부터, 동일 text 중복 스킵).",
        "5. Edit으로 같은 파일에 저장.",
        "",
        "**대상 목록 (총 {n}건):**".format(n=len(targets)),
        "",
        "| 날짜 | 채널 ID | 채널명 | 메시지 수 |",
        "|------|---------|--------|-----------|",
    ]
    for t in targets:
        lines.append(f"| {t['date']} | `{t['channel_id']}` | #{t['channel_name']} | {t['msg_count']} |")

    lines += [
        "",
        "## summary HTML 규칙",
        "",
        '- 형식: `<div class="sum-headline">한줄 헤드라인</div><ul class="sum-bullets"><li>...</li></ul>`',
        '- 헤드라인: 누가/무엇을 결정/언제 — 한 줄(100자 이내). 발화자는 `<span class="person">이름</span>` 표기, 핵심 수치·날짜·업체명·금액 반드시 포함.',
        '- 불릿: 액션·수치·결정사항만 0~3개. 각 불릿도 한 줄 짧게. 발화자는 `<span class="person">이름</span>`.',
        '- 불릿이 0개면 `<ul>` 자체 생략 (헤드라인만).',
        '- 잡담/이모지만 있는 채널은 헤드라인 1줄로 끝.',
        '- 서술형 미사여구 금지. HTML 외 줄바꿈 금지.',
        "",
        "## todos 규칙",
        "",
        "- 담당자(assignee): 5명 중 (건희, 준태, 윤하, 지수, 유민). 다른 이름은 todo 대상 X.",
        '- `@here`/`@channel`이면 5명 전부 array. 복수 멘션이면 array.',
        '- "부탁드립니다/확인부탁/전달부탁/공유부탁" 등 명시적 요청 → todo.',
        '- 마감 키워드("오늘까지","내일","ASAP","급함","X일까지")는 text에 포함.',
        '- 완료된 사안(파일/링크 전달 확인)은 todos에 추가 X.',
        '- source_ts: fullLog의 ts 값 그대로 한 개 복사. 추정·생성·변형 금지. fullLog에 없는 ts면 빈 문자열.',
        '- 기존 todos[*].text와 동일한 항목은 추가 X (중복 스킵).',
        '- 새 id는 max(기존 id) + 1부터 순차. text는 200자 이내.',
        "",
        "## todo 항목 형식",
        "",
        "```json",
        "{",
        '  "id": <자동 누적>,',
        '  "assignee": "준태" 또는 ["준태","윤하"],',
        '  "channel": "<채널명>",',
        '  "channel_id": "C00...",',
        '  "project": "마성의팍스 / 삼성와펜 / 차세린 / 최고심 / 발주 / 자사몰 / 등",',
        '  "text": "할일 내용 (마감일 포함)",',
        '  "source_ts": "<원 메시지의 ts 그대로>",',
        '  "done": false',
        "}",
        "```",
        "",
        "## 출력",
        "",
        "추가 설명·보고 없이 파일 저장만 하고 작업을 끝내세요.",
        "",
    ]
    return "\n".join(lines)


def main():
    targets = find_targets()
    with open(TARGETS_OUT, 'w', encoding='utf-8') as f:
        json.dump(targets, f, ensure_ascii=False, indent=2)
    prompt = build_prompt(targets)
    with open(PROMPT_OUT, 'w', encoding='utf-8') as f:
        f.write(prompt)
    print(f"Empty summary targets: {len(targets)}")
    for t in targets:
        print(f"  {t['date']} | {t['channel_name']:25s} | {t['msg_count']} msgs")
    print(f"\nPrompt: {PROMPT_OUT}")
    print(f"Targets: {TARGETS_OUT}")


if __name__ == '__main__':
    main()
