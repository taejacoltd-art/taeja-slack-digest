# -*- coding: utf-8 -*-
"""
data/*.json 의 채널별 summary/messages 를 새 헤드라인+불릿 프롬프트로 일괄 재생성.

사용법:
  python scripts/regenerate_summaries.py --date 2026-04-15   # 드라이런 (1일치만)
  python scripts/regenerate_summaries.py --all               # 전체 재처리

규칙:
  - 실행 전 data/ 통째로 ../data_backup_YYYYMMDD/ 로 백업
  - 각 날짜의 channels[*].fullLog 로 analyze_channel 재호출
  - summary, messages 덮어씀 / todos 는 절대 건드리지 않음 (누적 보존)
  - 5채널마다 중간 저장
  - 실패 채널 retry 큐 (한번 더)
"""
import json, os, sys, shutil, time, argparse
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

# .env 로드 (analyze_with_llm import 전에 환경변수 세팅 필요)
_ROOT_FOR_ENV = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_ROOT_FOR_ENV, '.env')
if os.path.exists(_env_path):
    with open(_env_path, 'r', encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _k, _v = _line.split('=', 1)
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:
                os.environ[_k] = _v

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analyze_with_llm import analyze_channel, CHANNELS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'data')
BACKUP_PARENT = os.path.dirname(ROOT)  # SLACK/
KST = timezone(timedelta(hours=9))


def make_backup():
    stamp = datetime.now(KST).strftime('%Y%m%d_%H%M')
    backup = os.path.join(BACKUP_PARENT, f'data_backup_{stamp}')
    if os.path.exists(backup):
        print(f'[backup] 이미 존재: {backup}')
        return backup
    print(f'[backup] {DATA_DIR} -> {backup}')
    shutil.copytree(DATA_DIR, backup)
    return backup


def regenerate_one_date(date, save_every=5):
    path = os.path.join(DATA_DIR, f'{date}.json')
    if not os.path.exists(path):
        print(f'  [skip] no data: {date}')
        return 0, 0, []

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    channels = data.get('channels', {})
    if not channels:
        return 0, 0, []

    success = 0
    fail = 0
    retry_queue = []
    processed = 0

    for ch_id, ch_data in list(channels.items()):
        ch_name = CHANNELS.get(ch_id, ch_id)
        full_log = ch_data.get('fullLog', [])
        if not full_log:
            continue

        print(f'  [{date}] #{ch_name} ({len(full_log)} msgs) ... ', end='', flush=True)
        try:
            result = analyze_channel(ch_name, date, full_log)
        except Exception as e:
            print(f'EXCEPTION {e}')
            retry_queue.append((ch_id, ch_name))
            fail += 1
            continue

        if not result:
            print('FAIL (None)')
            retry_queue.append((ch_id, ch_name))
            fail += 1
            continue

        if result.get('summary'):
            ch_data['summary'] = result['summary']
        if result.get('messages'):
            ch_data['messages'] = result['messages']
        # todos 는 절대 손대지 않음 (누적 보존)
        success += 1
        processed += 1
        print('OK')

        if processed % save_every == 0:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    # 재시도
    if retry_queue:
        print(f'  [{date}] retry {len(retry_queue)} channels...')
        time.sleep(2)
        still_failed = []
        for ch_id, ch_name in retry_queue:
            full_log = channels[ch_id].get('fullLog', [])
            try:
                result = analyze_channel(ch_name, date, full_log)
                if result and result.get('summary'):
                    channels[ch_id]['summary'] = result['summary']
                    if result.get('messages'):
                        channels[ch_id]['messages'] = result['messages']
                    success += 1
                    fail -= 1
                    print(f'    retry OK: #{ch_name}')
                else:
                    still_failed.append(ch_name)
            except Exception as e:
                still_failed.append(f'{ch_name} ({e})')
        retry_queue = still_failed

    # 최종 저장
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return success, fail, retry_queue


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='특정 날짜만 (YYYY-MM-DD)')
    ap.add_argument('--all', action='store_true', help='index.json 전체 재처리')
    ap.add_argument('--no-backup', action='store_true', help='백업 생략 (테스트용)')
    args = ap.parse_args()

    if not args.date and not args.all:
        print('--date YYYY-MM-DD 또는 --all 중 하나를 지정하세요')
        sys.exit(1)

    if args.all and not args.no_backup:
        make_backup()
    elif args.date and not args.no_backup:
        # 단일 날짜도 백업 권장
        make_backup()

    if args.date:
        dates = [args.date]
    else:
        idx_path = os.path.join(DATA_DIR, 'index.json')
        with open(idx_path, 'r', encoding='utf-8') as f:
            dates = json.load(f)

    total_success = 0
    total_fail = 0
    all_failed = []

    for i, date in enumerate(dates, 1):
        print(f'\n=== [{i}/{len(dates)}] {date} ===')
        s, f, failed = regenerate_one_date(date)
        total_success += s
        total_fail += f
        if failed:
            all_failed.extend([(date, ch) for ch in failed])

    print(f'\n{"="*50}')
    print(f'완료: 성공 {total_success}, 실패 {total_fail}')
    if all_failed:
        print(f'\n끝까지 실패한 채널 ({len(all_failed)}개):')
        for d, ch in all_failed:
            print(f'  - {d} / {ch}')


if __name__ == '__main__':
    main()
