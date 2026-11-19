# -*- coding: utf-8 -*-
"""폰에서 보낸 '지역 요청'(GitHub 이슈)을 web_config.json 에 반영한다. GitHub Actions 가 실행한다.

이슈 제목 형식
  지역 추가: 부산
  지역 빼기: 포항        ('지역 삭제:' 도 됨)

환경 변수 (워크플로가 넣어 줌)
  TITLE  - 이슈 제목 (외부 입력이므로 코드나 명령으로 실행하지 않고 글자로만 다룬다)
  AUTHOR - 이슈 작성자 아이디
  OWNER  - 저장소 주인 아이디. 주인이 쓴 요청만 처리한다.
결과는 GITHUB_OUTPUT 에 changed(true/false), handled(true/false), message 로 남긴다.
"""
import json
import os
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import build  # noqa: E402

WEB_CONFIG = BASE / 'web_config.json'
DEFAULT_MAX = 8


def output(**kv):
    path = os.environ.get('GITHUB_OUTPUT')
    lines = [f'{k}={str(v).replace(chr(10), " ").replace(chr(13), " ")}' for k, v in kv.items()]
    if path:
        with open(path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


def main():
    title = os.environ.get('TITLE', '')
    author, owner = os.environ.get('AUTHOR', ''), os.environ.get('OWNER', '')
    m = re.match(r'^\s*지역\s*(추가|빼기|삭제)\s*[:：]\s*(.+?)\s*$', title)
    if not m:
        return output(handled='false', changed='false', message='지역 요청 형식이 아니라서 무시했어요.')
    if not author or author != owner:
        # 공개 저장소라 누구나 이슈를 쓸 수 있으므로 주인 요청만 처리한다 (다른 사람 이슈에는 반응하지 않음)
        return output(handled='false', changed='false', message='저장소 주인의 요청이 아니라서 무시했어요.')

    action, region = m.group(1), build.normalize_keyword(m.group(2))
    if not build.valid_keyword(region):
        return output(handled='true', changed='false',
                      message=f'"{region}"은(는) 지역 이름으로 쓸 수 없어요. 한글, 영문, 숫자만 쓰고 여러 곳은 쉼표로 구분하세요.')

    cfg = build.load_json(WEB_CONFIG, {})
    regions = [build.normalize_keyword(str(r)) for r in cfg.get('regions') or []]
    limit = int(cfg.get('max_regions') or DEFAULT_MAX)

    if action == '추가':
        if region in regions:
            return output(handled='true', changed='false', message=f'"{region}"은(는) 이미 지도 메뉴에 있어요.')
        if len(regions) >= limit:
            return output(handled='true', changed='false',
                          message=f'지역은 최대 {limit}곳까지예요. "지역 빼기: 지역이름"으로 하나를 먼저 빼 주세요. '
                                  f'지금 목록: {", ".join(regions)}')
        regions.append(region)
        msg = f'"{region}"을(를) 추가했어요. 10~30분 뒤 지도 왼쪽 위 지역 메뉴에 나타나요.'
    else:
        if region not in regions:
            return output(handled='true', changed='false',
                          message=f'"{region}"은(는) 지도 메뉴에 없어요. 지금 목록: {", ".join(regions)}')
        if len(regions) == 1:
            return output(handled='true', changed='false', message='마지막 남은 지역은 뺄 수 없어요. 다른 지역을 먼저 추가하세요.')
        regions.remove(region)
        msg = f'"{region}"을(를) 뺐어요. 10~30분 뒤 지도 메뉴에서 사라져요.'

    cfg['regions'] = regions
    WEB_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    output(handled='true', changed='true', message=msg + f' 지금 목록: {", ".join(regions)}')


if __name__ == '__main__':
    main()
