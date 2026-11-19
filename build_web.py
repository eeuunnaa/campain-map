# -*- coding: utf-8 -*-
"""웹 버전(GitHub Pages)용 지도 만들기 - GitHub Actions 가 6시간마다 자동으로 실행한다.

web_config.json 에 적은 지역들을 각각 만들어 site 폴더에 저장한다.
  첫 번째 지역  -> site/index.html  (웹 주소로 들어가면 바로 보이는 지도)
  두 번째부터   -> site/map2.html, site/map3.html ...
각 지도 왼쪽 위 메뉴에서 다른 지역 지도로 이동할 수 있다.

내 PC에서 시험해 보기:  python build_web.py   (결과: site 폴더, 지도열기.bat 와는 별개)
"""
import html
import os
import pathlib
import sys
import traceback

BASE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import build  # noqa: E402  (같은 폴더의 build.py)

SITE = BASE / 'site'
WEB_CONFIG = BASE / 'web_config.json'


def placeholder(path, region, error):
    """지도 만들기에 실패한 지역은 안내 페이지라도 남긴다 (메뉴 링크가 깨지지 않게)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{html.escape(region)} 체험단 마감지도</title>'
        '<body style="font-family:system-ui,\'Malgun Gothic\',sans-serif;padding:32px;line-height:1.6;background:#f3f3f1;color:#0b0b0b">'
        f'<h2>"{html.escape(region)}" 지도를 이번에는 만들지 못했어요</h2>'
        f'<p>다음 자동 갱신 때 다시 시도합니다.</p><p style="color:#666">사유: {html.escape(str(error))[:300]}</p>'
        '<p><a href="index.html">첫 번째 지역 지도로 가기</a></p></body></html>',
        encoding='utf-8')


def main():
    cfg = build.load_json(WEB_CONFIG, {})
    regions = []
    for r in cfg.get('regions') or [build.DEFAULT_KEYWORD]:
        r = build.normalize_keyword(str(r))
        if build.valid_keyword(r):
            regions.append(r)
        else:
            build.log(f'!! web_config.json 의 지역 이름이 올바르지 않아 건너뜀: {r!r}')
    if not regions:
        sys.exit('web_config.json 에 만들 지역이 없어요. 예: {"regions": ["대구"], "days": 7}')
    days = cfg.get('days') if cfg.get('days') in build.WINDOW_CHOICES else build.DEFAULT_WINDOW
    try:  # 화면에 보여줄 갱신 주기(시간). 실제 주기는 .github/workflows/update-map.yml 의 cron
        every_hours = max(1, int(cfg.get('every_hours') or 6))
    except (TypeError, ValueError):
        every_hours = 6

    try:
        limit = max(1, int(cfg.get('max_regions') or 8))
    except (TypeError, ValueError):
        limit = 8
    if len(regions) > limit:
        build.log(f'!! 지역이 {len(regions)}곳이라 앞의 {limit}곳만 만들어요 (web_config.json 의 max_regions)')
        regions = regions[:limit]
    # GitHub 에서 돌 때 저장소 이름(아이디/저장소). 지도 화면의 '지역 요청' 버튼이 이 저장소에 이슈를 연다.
    repo = os.environ.get('GITHUB_REPOSITORY') or str(cfg.get('repo') or '') or None

    files = ['index.html'] + [f'map{i}.html' for i in range(2, len(regions) + 1)]
    menu = [{'name': r, 'file': f} for r, f in zip(regions, files)]
    ok = 0
    for region, file in zip(regions, files):
        build.log(f'\n========== {region} -> site/{file} ==========')
        try:
            build.main(region, days, out=SITE / file, site={'regions': menu, 'current': file, 'everyHours': every_hours,
                                                          'repo': repo, 'maxRegions': limit})
            ok += 1
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # noqa: BLE001 - SystemExit 포함, 한 지역 실패가 전체를 막지 않게
            if not isinstance(e, SystemExit):
                traceback.print_exc()
            build.log(f'!! {region} 실패: {e}')
            placeholder(SITE / file, region, e)
    (SITE / '.nojekyll').write_text('', encoding='utf-8')
    build.log(f'\n웹 지도 {ok}/{len(regions)}개 완료 -> {SITE}')
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
