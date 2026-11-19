# -*- coding: utf-8 -*-
"""지금까지 찾아둔 위치/주소 기록(cache)을 seed 폴더로 복사한다.

seed 는 GitHub(웹 버전) 첫 실행이나 다른 PC 첫 실행 때 처음부터 다 찾지 않도록 쓰는 초기값.
 - 카카오 검색 결과는 넣지 않는다 (카카오 API 결과를 공개 저장소에 올리지 않기 위해)
 - '요청 제한으로 쉬는 중' 같은 일시적 상태도 넣지 않는다
사용법: python make_seed.py
"""
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent
SRC, DST = BASE / 'cache', BASE / 'seed'


def main():
    DST.mkdir(exist_ok=True)
    geo = json.loads((SRC / 'geocode.json').read_text(encoding='utf-8'))
    geo = {k: v for k, v in geo.items() if not k.startswith(('kakao:', 'nominatim:'))}
    (DST / 'geocode.json').write_text(json.dumps(geo, ensure_ascii=False), encoding='utf-8')
    pages = json.loads((SRC / 'pages_v2.json').read_text(encoding='utf-8'))
    (DST / 'pages.json').write_text(json.dumps(pages, ensure_ascii=False), encoding='utf-8')
    print(f'seed 저장: 위치 {len(geo)}개, 주소 {len(pages)}개 -> {DST}')


if __name__ == '__main__':
    main()
