# -*- coding: utf-8 -*-
"""체험단 마감임박 지도 생성기

reviewtiful.shop 에서 원하는 지역(처음엔 '대구')을 검색해 오늘/내일 마감인 체험단을 모으고,
가게 위치를 지도에 찍은 HTML 을 만든다. (내 PC에서만 보는 로컬 파일)

사용법
  보통은 지도열기.bat 으로 열고, 지도 화면 왼쪽 위에서 지역을 바꾸면 된다.
  python build.py          마지막으로 본 지역으로 다시 만들기 (config.json)
  python build.py 부산      지역을 바꿔서 만들기 (성공하면 다음부터 기본 지역이 됨)

결과: 같은 폴더의 체험단_마감지도.html

위치 정확도
  - 상세 페이지에 주소/좌표가 있는 사이트는 그 주소로 위치를 잡는다.
  - kakao_key.txt (카카오 REST API 키)가 있으면 가게명·주소를 카카오맵으로 검색해 정확히 잡는다.
  - 둘 다 안 되면 제목의 [대구 수성구], [부산/해운대구] 같은 지역 표기로 '대략' 위치를 잡는다.
  - 무료 지오코딩은 OpenStreetMap Nominatim (초당 1회 제한 준수, 결과는 cache 폴더에 저장).
"""
import collections
import datetime
import html
import json
import os
import pathlib
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / '체험단_마감지도.html'
CONFIG = BASE / 'config.json'
CACHE_DIR = BASE / 'cache'
GEO_CACHE = CACHE_DIR / 'geocode.json'
PAGE_CACHE = CACHE_DIR / 'pages_v2.json'
SEED_DIR = BASE / 'seed'  # 다른 PC/GitHub 첫 실행용 초기 캐시 (make_seed.py 로 만든 스냅샷)
VENDOR = BASE / 'vendor'
KAKAO_KEY_FILE = BASE / 'kakao_key.txt'
DEFAULT_KEYWORD = '대구'
WINDOW_CHOICES = (2, 3, 7)  # 마감 기간(일): 오늘~내일 / 3일 / 7일
DEFAULT_WINDOW = 7
# '오늘'은 항상 한국 시간 기준 (GitHub 서버처럼 UTC 로 도는 컴퓨터에서도 날짜가 밀리지 않게)
KST = datetime.timezone(datetime.timedelta(hours=9))


def now_kst():
    return datetime.datetime.now(KST).replace(tzinfo=None)

SEARCH_URL = 'https://reviewtiful.shop/search?keyword='
BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')
GEOCODER_UA = 'campaign-deadline-map/1.0 (personal, low-volume)'

# 이 PC 네트워크의 TLS 검사 인증서가 Python 의 엄격 모드(VERIFY_X509_STRICT)에서만
# 실패해서 그 플래그만 끈다. 인증서 체인 검증 자체는 그대로 유지된다.
CTX = ssl.create_default_context()
CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT

# 회사망(업무시간 차단 정책)에서 막히는 사이트. 갱신할 때마다 이 중 한 곳만 먼저 시험해서
# 막혀 있으면 나머지도 건너뛴다(차단 기록 최소화). 집처럼 안 막힌 곳에서는 전부 가져온다.
WORK_BLOCKED_HOSTS = {'xn--939au0g4vj8sq.net', 'popomon.com', 'www.ringble.co.kr',
                      'www.cloudreview.co.kr'}
# 상세 HTML 에 주소가 없는(로그인 API 등으로만 주는) 사이트 -> 요청해도 소용없음.
NO_ADDR_HOSTS = {'www.revu.net', 'www.reviewnote.co.kr', 'www.mrblog.net',
                 'chvu.co.kr', 'ohmyblog.co.kr', 'ddok.it', 'www.stylec.co.kr',
                 'place.cashnote.kr'}

SIDO_NAMES = ('서울', '부산', '대구', '인천', '광주', '대전', '울산', '세종', '경기', '강원',
              '충북', '충남', '전북', '전남', '경북', '경남', '제주')
SIDO_RE = (r'(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주'
           r'|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도)'
           r'(?:특별자치시|특별자치도|특별시|광역시|도)?')
# 제목 괄호에 이 지명이 있고 검색 지역은 없으면 다른 지역 캠페인으로 보고 뺀다.
OTHER_REGIONS = SIDO_NAMES + ('해운대', '성남', '판교', '분당', '일산', '수원', '용인', '고양',
                              '창원', '포항', '구미', '경산', '속초', '강릉', '청주', '천안',
                              '전주', '강남', '홍대')
FALSE_HITS = {'대구': '(?!뽈|탕|지리|찜|볼|머리|전)'}  # 생선 '대구' 메뉴 이름은 지역이 아님
ADMIN_SUFFIXES = ('광역시', '특별시', '특별자치시', '시', '군', '구', '특별자치도', '도')

# 도 단위로 입력하면 아래 시·군을 각각 검색해서 합친다. (체험단 제목은 보통 [포항/북구]처럼
# 도 이름 없이 시·군 이름만 쓰기 때문에 '경북' 하나로 검색하면 대부분 빠진다.)
PROVINCES = {
    '경기': ('경기도', '수원 성남 의정부 안양 부천 광명 평택 동두천 안산 고양 과천 구리 남양주 오산 시흥 군포 의왕 하남 용인 파주 이천 안성 김포 화성 광주 양주 포천 여주 연천 가평 양평'),
    '강원': ('강원특별자치도', '춘천 원주 강릉 동해 태백 속초 삼척 홍천 횡성 영월 평창 정선 철원 화천 양구 인제 고성 양양'),
    '충북': ('충청북도', '청주 충주 제천 보은 옥천 영동 증평 진천 괴산 음성 단양'),
    '충남': ('충청남도', '천안 공주 보령 아산 서산 논산 계룡 당진 금산 부여 서천 청양 홍성 예산 태안'),
    '전북': ('전북특별자치도', '전주 군산 익산 정읍 남원 김제 완주 진안 무주 장수 임실 순창 고창 부안'),
    '전남': ('전라남도', '목포 여수 순천 나주 광양 담양 곡성 구례 고흥 보성 화순 장흥 강진 해남 영암 무안 함평 영광 장성 완도 진도 신안'),
    '경북': ('경상북도', '포항 경주 김천 안동 구미 영주 영천 상주 문경 경산 의성 청송 영양 영덕 청도 고령 성주 칠곡 예천 봉화 울진 울릉'),
    '경남': ('경상남도', '창원 진주 통영 사천 김해 밀양 거제 양산 의령 함안 창녕 고성 남해 하동 산청 함양 거창 합천'),
    '제주': ('제주특별자치도', '제주 서귀포'),
}
PROVINCE_ALIAS = {k: k for k in PROVINCES}
PROVINCE_ALIAS.update({'경기도': '경기', '강원도': '강원', '강원특별자치도': '강원', '충청북도': '충북',
                       '충청남도': '충남', '전라북도': '전북', '전북특별자치도': '전북', '전라남도': '전남',
                       '경상북도': '경북', '경상남도': '경남', '제주도': '제주', '제주특별자치도': '제주'})
# 다른 도에도 같은 이름이 있는 시·군(경기 광주 ↔ 광주광역시, 강원 고성 ↔ 경남 고성):
# 위치를 못 찾은 캠페인은 어느 쪽인지 몰라서 뺀다.
AMBIGUOUS_CITIES = {'광주', '고성'}
# 여러 도시에 다 있는 구 이름: 이 이름으로 찾은 캠페인은 제목에 상위 시 이름도 있어야 인정
GENERIC_GU = {'중구', '동구', '서구', '남구', '북구'}
# 사이트 지도 스크립트에 박혀 있는 기본 좌표(가게 위치 아님): 서울시청, 카카오맵 기본값(제주)
DEFAULT_COORDS = [(37.5665, 126.9780), (33.450701, 126.570667)]

PREC_LABEL = {
    1: '정확한 위치 (체험단 페이지 주소)',
    2: '정확한 위치 (카카오맵 가게명 검색)',
    3: '도로(길) 기준 · 수백 m 오차',
    4: '큰 도로 기준 · 오차 클 수 있음',
    5: '동네(동·역) 기준 · 대략',
    6: '가게명 검색(OSM) · 확인 필요',
    7: '구·군·시 기준 · 매우 대략',
}


def log(*a):
    print(*a, flush=True)


def fetch(url, referer=None, timeout=20):
    headers = {'User-Agent': BROWSER_UA, 'Accept-Language': 'ko-KR,ko;q=0.9'}
    if referer:
        headers['Referer'] = referer
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=timeout, context=CTX).read()


def is_block_page(text):
    return 'warning.html?user=' in text[:2000]


def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def save_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def normalize_keyword(text):
    """'경산 ,  포항' -> '경산, 포항'"""
    return ', '.join(' '.join(t.split()) for t in (text or '').split(',') if t.strip())


def valid_keyword(text):
    kw = normalize_keyword(text)
    return bool(kw) and re.fullmatch(r'[가-힣A-Za-z0-9 ,]{1,60}', kw) is not None and kw.count(',') < 8


# ---------------------------------------------------------------- 1. 목록 수집
SEARCH_GAP = 3.0  # 검색 사이 간격(초). 너무 빨리 연달아 검색하면 reviewtiful 이 429(요청 과다)로 막는다.


def get_list(kw):
    for attempt in range(4):
        try:
            raw = fetch(SEARCH_URL + urllib.parse.quote(kw)).decode('utf-8', 'replace')
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            try:
                wait = int(e.headers.get('Retry-After') or 0)
            except ValueError:
                wait = 0
            wait = min(max(wait, 20 * (attempt + 1)), 120)
            log(f'   reviewtiful 이 요청이 많다고 해서(429) {wait}초 쉬었다가 다시 시도합니다...')
            time.sleep(wait)
    m = re.search(r'<script id="resultPageData"[^>]*>(.*?)</script>', raw, re.S)
    if not m:
        raise ValueError('검색 결과 데이터(resultPageData)를 찾지 못함 - 사이트 구조가 바뀌었을 수 있어요')
    return json.loads(m.group(1)).get('resultList') or []


def _strip_prefix(b):
    return re.sub(r'^\s*(릴스|클립|인스타|블로그)\s*', '', b.strip())


def kw_in_bracket(b, kw):
    """괄호 안에 검색 지역이 '지명으로' 들어 있는지. 해운대구 속의 '대구' 같은 건 제외."""
    b = _strip_prefix(b)
    for m in re.finditer(re.escape(kw), b):
        prev = b[m.start() - 1] if m.start() else ''
        if not re.match(r'[가-힣]', prev):
            return True
        rest = re.match(r'[가-힣0-9]*', b[m.end():]).group(0)
        if rest.endswith('역'):  # 동대구역, 서대구역 같은 역 이름
            return True
    return False


def is_target(title, kw):
    brackets = re.findall(r'\[([^\]]*)\]', title)
    if any(kw_in_bracket(b, kw) for b in brackets):
        return True
    if any(r in b for b in brackets for r in OTHER_REGIONS if r != kw and kw not in r):
        return False
    # [기자단] 파스쿠찌 대구칠곡3지구점 처럼 지역 괄호가 없는 경우
    rest = re.sub(r'\[[^\]]*\]', ' ', title)
    return re.search(re.escape(kw) + FALSE_HITS.get(kw, ''), rest) is not None


def title_location(title, kw):
    """제목의 지역 괄호에서 (구·군·시, 세부지역) 추출. 예: [대구/감삼동] -> (None, '감삼동')"""
    for b in re.findall(r'\[([^\]]*)\]', title):
        if kw_in_bracket(b, kw):
            s = _strip_prefix(b)
            break
    else:
        return None, None
    admin = sub = None
    for t in re.split(r'[\s/,·]+', s):
        if not t or re.fullmatch(SIDO_RE, t):
            continue
        if t.startswith(kw):
            rest = t[len(kw):]
            if rest in ('',) + ADMIN_SUFFIXES:
                continue
            if len(rest) >= 2 and not rest.endswith('역'):  # '대구중구' -> '중구' ('범어동'은 그대로)
                t = rest
        if len(t) >= 2 and re.search(r'(구|군|시)$', t) and not t.endswith('역'):
            admin = admin or t
        elif sub is None:
            sub = t
    return admin, sub


def store_name(x):
    t = x['title']
    if x['source'] == '놀러와체험단':
        b = re.findall(r'\[([^\]]*)\]', t)
        if b:
            return b[-1].strip()
    name = re.sub(r'\[[^\]]*\]', ' ', t)
    name = re.sub(r'\((인스타|릴스|클립|블로그)[^)]*\)', ' ', name)
    name = re.sub(r'^\s*(릴스|클립)\s*', '', name)
    name = re.sub(r'[_\s]+(릴스|클립)\s*$', '', name.strip())
    name = re.sub(r'\s+-\s+[A-Z]$', '', name)
    name = re.split(r'에서 ', name)[0]
    name = re.sub(r'\s+', ' ', name).strip(' -_')
    return name or t.strip()


# 리뷰 채널: 사이트 태그(note)나 제목에 적힌 표기로 판단
CHANNEL_RULES = [
    ('reels', r'릴스|reels|숏폼'),
    ('clip', r'클립'),
    ('insta', r'인스타|instagram|insta\b'),
    ('blog', r'블로그|blog'),
    ('yt', r'유튜브|youtube|쇼츠|숏츠|shorts'),
    ('tiktok', r'틱톡|tiktok'),
    ('xhs', r'샤오홍슈|小红书'),
]
# 이 사이트들은 릴스·클립 등만 따로 표시하고, 표시가 없으면 블로그 캠페인이다(추정).
BLOG_DEFAULT_SOURCES = {'리뷰노트', '디너의여왕', '뷰티의여왕', '레뷰', '포블로그', '오마이블로그'}


def parse_channels(note, title, source):
    """-> (채널 목록, 추정 여부). 채널: blog, insta, reels, clip, yt, tiktok, xhs, reporter, unknown"""
    text = f'{note} {title}'.lower()
    ch = [k for k, rx in CHANNEL_RULES if re.search(rx, text)]
    guess = False
    if not ch:
        if source in BLOG_DEFAULT_SOURCES:
            ch, guess = ['blog'], True
        else:
            ch = ['unknown']
    if '기자단' in text:
        ch.append('reporter')  # 방문 없이 글만 쓰는 형태
    return ch, guess


def parse_applicants(s):
    m = re.match(r'\s*(\d+)\s*/\s*(\d+)', s or '')
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r'\s*\?\s*/\s*(\d+)', s or '')
    return (None, int(m.group(1))) if m else (None, None)


def parse_amount(*texts):
    """제공내역/태그에 적힌 금액(원)과 포인트(P) 중 가장 큰 값. 예: '3만5천원 체험권' -> 35000"""
    won, pts = [], []
    for t in texts:
        if not t:
            continue
        t = re.sub(r'\s+', '', t)
        for m in re.finditer(r'(\d+(?:\.\d+)?)만(?:(\d)천)?원', t):
            won.append(float(m.group(1)) * 10000 + (int(m.group(2)) * 1000 if m.group(2) else 0))
        for m in re.finditer(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{4,7})원', t):
            won.append(int(m.group(1).replace(',', '')))
        for m in re.finditer(r'(?<![\d만])(\d)천원', t):
            won.append(int(m.group(1)) * 1000)
        for m in re.finditer(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d{3,7})(?:P|포인트)(?![A-Za-z])', t):
            pts.append(int(m.group(1).replace(',', '')))
    ok = lambda v: 1000 <= v <= 5_000_000  # noqa: E731
    won = [int(v) for v in won if ok(v)]
    pts = [v for v in pts if ok(v)]
    return (max(won) if won else None), (max(pts) if pts else None)


# ---------------------------------------------------------- 2. 상세 페이지 주소
ROAD_RE = r'[가-힣]+[0-9]*(?:로|길)(?:[0-9]+번?길)?'
DONG_RE = r'[가-힣]+[0-9]*(?:동|가|리)'
ADDR_RE = re.compile(
    r'(' + SIDO_RE + r')\s*'
    r'((?:[가-힣]+(?:시|군|구)\s+){0,2})'
    r'((?:[가-힣]+(?:읍|면)\s+)?)'
    r'(' + ROAD_RE + r'|' + DONG_RE + r')(?![가-힣])'  # '경북 블로그'의 '블로' 같은 오인 방지
    r'\s*(\d+(?:-\d+)?)?'
    r'(?:[^<"\'\n(]{0,20}\(([가-힣0-9]+(?:동|가))[,)\s])?')
ADDR_KEYS = ('sido', 'sgg', 'town', 'road', 'num', 'paren')
COORD_RE = re.compile(r'(3[3-8]\.\d{4,})["\'\s,:A-Za-z_=]{1,40}?(12[4-9]\.\d{4,}|13[01]\.\d{4,})')
COORD_RE_REV = re.compile(r'(12[4-9]\.\d{4,}|13[01]\.\d{4,})["\'\s,:A-Za-z_=]{1,40}?(3[3-8]\.\d{4,})')


def extract_from_page(text):
    """페이지에서 주소 후보와 좌표 후보를 뽑는다(지역 판단은 나중에)."""
    text = html.unescape(text)
    found = collections.Counter()
    for m in ADDR_RE.finditer(text):
        sido, sgg, town, road, num, paren = m.groups()
        found[(sido, ' '.join(sgg.split()), town.strip(), road, num or '', paren or '')] += 1
    addrs = [dict(zip(ADDR_KEYS, k), count=c) for k, c in found.most_common(5)]
    coords = []
    for rx, rev in ((COORD_RE, False), (COORD_RE_REV, True)):
        for m in rx.finditer(text):
            a, b = float(m.group(1)), float(m.group(2))
            la, ln = (b, a) if rev else (a, b)
            if any(abs(la - dl) < 0.001 and abs(ln - dn) < 0.001 for dl, dn in DEFAULT_COORDS):
                continue
            if [la, ln] not in coords:
                coords.append([la, ln])
            if len(coords) >= 5:
                break
    return {'addrs': addrs, 'coords': coords}


def addr_text(a):
    return ' '.join(p for p in (a['sido'], a['sgg'], a['town'], a['road'], a['num']) if p)


def pick_addr(addrs, kw):
    # 시·군·구가 없는 주소는 본문 글자를 잘못 읽은 경우가 대부분 (세종시는 원래 없음).
    # 검색 지역(kw)이 들어간 주소만 인정: 다른 지역 주소(회사 주소, 추천 캠페인 등)를 가게 주소로 오인하지 않게.
    def in_kw(a):
        return any(t.startswith(kw) for t in [a['sido']] + a['sgg'].split())
    addrs = [a for a in addrs or [] if (a['sgg'] or a['sido'].startswith('세종')) and in_kw(a)]
    if not addrs:
        return None
    return max(addrs, key=lambda a: (a['count'], bool(a['num'])))


def collect_pages(items, state):
    """상세 페이지에서 주소를 뽑아 캐시에 저장. state = {'blocked': set(), 'work_blocked': None}
    (한 번의 갱신 안에서 여러 번 불러도 회사망 차단 시험은 한 번만 하도록 상태를 공유)"""
    cache = load_json(PAGE_CACHE, None) or load_json(SEED_DIR / 'pages.json', {})
    blocked = state['blocked']
    todo = [x for x in items if x['link'] not in cache]
    n = 0
    for x in todo:
        link = x['link']
        host = urllib.parse.urlparse(link).netloc
        if host in NO_ADDR_HOSTS or host in blocked:
            continue
        if host in WORK_BLOCKED_HOSTS and state['work_blocked']:
            continue
        try:
            text = fetch(link).decode('utf-8', 'replace')
        except Exception as e:  # noqa: BLE001 - 한 페이지 실패는 건너뛴다
            log(f'  상세 페이지 실패 ({host}): {type(e).__name__}')
            continue
        if is_block_page(text):
            if host in WORK_BLOCKED_HOSTS:
                state['work_blocked'] = True
                blocked |= WORK_BLOCKED_HOSTS
                log('  회사망 차단 정책이 걸려 있어서 강남맛집·포포몬 등은 이번엔 건너뜀 (막히지 않은 곳에서 갱신하면 주소를 가져와요)')
            else:
                blocked.add(host)
                log(f'  {host}는 지금 네트워크에서 막혀 있어서 이번 갱신에서 건너뛰어요')
            continue
        if host in WORK_BLOCKED_HOSTS:
            state['work_blocked'] = False
        cache[link] = extract_from_page(text)
        n += 1
        if n % 50 == 0:
            save_json(PAGE_CACHE, cache)
            log(f'  상세 페이지 {n}개 확인')
        time.sleep(0.4)
    save_json(PAGE_CACHE, cache)
    return cache


# -------------------------------------------------------------- 3. 지오코딩
class Region:
    """검색 지역의 범위. 지오코딩 결과가 이 범위 밖이면 버린다."""

    def __init__(self, kw, name, lat, lng, south, north, west, east):
        self.kw, self.name, self.lat, self.lng = kw, name, lat, lng
        cy, cx = (south + north) / 2, (west + east) / 2
        # 역·학교처럼 작은 곳은 최소 ±0.05도(약 5km)로 넓힌다
        hy = max((north - south) / 2 + 0.02, 0.05)
        hx = max((east - west) / 2 + 0.02, 0.05)
        self.s, self.n, self.w, self.e = cy - hy, cy + hy, cx - hx, cx + hx

    def contains(self, lat, lng):
        return self.s <= lat <= self.n and self.w <= lng <= self.e

    @property
    def viewbox(self):  # Nominatim: 왼쪽,위,오른쪽,아래
        return f'{self.w:.4f},{self.n:.4f},{self.e:.4f},{self.s:.4f}'

    @property
    def rect(self):  # 카카오: 왼쪽 아래 x,y, 오른쪽 위 x,y
        return f'{self.w:.4f},{self.s:.4f},{self.e:.4f},{self.n:.4f}'


class Geocoder:
    def __init__(self):
        # 캐시가 없으면(다른 PC·GitHub 첫 실행) 함께 배포된 seed 로 시작해서 처음 검색량을 줄인다
        self.cache = load_json(GEO_CACHE, None) or load_json(SEED_DIR / 'geocode.json', {})
        special = ('kakao:', 'region:', 'place:', 'nominatim:')
        # 예전(대구 전용) 버전의 캐시 키를 지역별 키로 옮긴다
        for k in [k for k in self.cache if '|' not in k and not k.startswith(special)]:
            self.cache[k + '|대구'] = self.cache.pop(k)
        # 이전 버그로 'place:…|대구' 처럼 잘못 옮겨진 키를 원래대로
        for k in [k for k in self.cache if k.startswith(('place:', 'nominatim:')) and k.endswith('|대구')]:
            self.cache.setdefault(k[:-len('|대구')], self.cache.pop(k))
        self.last = 0.0
        self.dirty = 0
        self.region = None
        self.paused_logged = False

    # OpenStreetMap(Nominatim)은 무료 공용 서비스라 "요청이 많다(429)/거부(403)"를 받으면
    # 1시간 동안은 아예 묻지 않는다. (계속 두드리면 더 오래 차단될 수 있음) 쉬는 시각은 캐시에 저장.
    NOMINATIM_PAUSE = 3600

    def paused(self):
        until = self.cache.get('nominatim:pause_until') or 0
        if time.time() < until:
            if not self.paused_logged:
                mins = int((until - time.time()) / 60) + 1
                log(f'  OpenStreetMap 위치 검색이 요청 제한 중이라 약 {mins}분 동안은 묻지 않아요 '
                    '(그동안 새 가게는 위치 없음/대략으로 표시, 카카오 키가 있으면 카카오로 찾음)')
                self.paused_logged = True
            return True
        return False

    def _nominatim(self, params):
        if self.paused():
            raise RuntimeError('nominatim paused')
        wait = 1.1 - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        base = {'format': 'jsonv2', 'countrycodes': 'kr', 'accept-language': 'ko', 'limit': 1}
        url = 'https://nominatim.openstreetmap.org/search?' + urllib.parse.urlencode({**base, **params})
        try:
            req = urllib.request.Request(url, headers={'User-Agent': GEOCODER_UA})
            return json.loads(urllib.request.urlopen(req, timeout=20, context=CTX).read())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                self.cache['nominatim:pause_until'] = time.time() + self.NOMINATIM_PAUSE
                self.save()
                log(f'  OpenStreetMap 이 요청 제한({e.code})을 걸어서 1시간 동안 위치 검색을 쉬어요.')
                self.paused_logged = True
            raise
        finally:
            self.last = time.time()

    def _remember(self, key, value):
        self.cache[key] = value
        self.dirty += 1
        if self.dirty % 20 == 0:
            self.save()

    def query(self, q):
        key = f'{q}|{self.region.kw}'
        if key in self.cache:
            return self.cache[key]
        try:
            data = self._nominatim({'q': q, 'viewbox': self.region.viewbox, 'bounded': 1})
        except Exception as e:  # noqa: BLE001
            if not self.paused():
                log(f'  지오코딩 실패: {q} ({type(e).__name__})')
            return None  # 실패는 캐시하지 않음 (다음 실행 때 재시도)
        res = None
        if data:
            d = data[0]
            res = {'lat': float(d['lat']), 'lng': float(d['lon']),
                   'category': d.get('category'), 'type': d.get('type'),
                   'addresstype': d.get('addresstype'), 'name': d.get('display_name', '')[:120]}
            if not self.region.contains(res['lat'], res['lng']):
                res = None
        self._remember(key, res)
        return res

    def lookup(self, q):
        """지명 검색(전국). 이름, 중심, 범위, 종류, 상위 행정구역을 돌려준다."""
        key = 'place:' + q
        if key in self.cache:
            return self.cache[key]
        try:
            data = self._nominatim({'q': q, 'addressdetails': 1})
        except Exception as e:  # noqa: BLE001
            if not self.paused():
                log(f'  지명 검색 실패: {q} ({type(e).__name__})')
            return None
        r = None
        if data:
            d = data[0]
            s, n, w, e = map(float, d['boundingbox'])
            r = {'name': d.get('name') or q, 'lat': float(d['lat']), 'lng': float(d['lon']),
                 'south': s, 'north': n, 'west': w, 'east': e,
                 'category': d.get('category'), 'addresstype': d.get('addresstype'),
                 'address': {k: v for k, v in (d.get('address') or {}).items() if not k.startswith(('ISO', 'country', 'post'))}}
        self._remember(key, r)
        return r

    def resolve(self, word, ctx=''):
        """'경산' -> 경산시, '대구' -> 대구광역시 처럼 행정구역 이름을 우선으로 찾는다."""
        tail = f', {ctx}' if ctx else ''
        # 1) 입력이 이미 행정구역 이름이면 그대로 (수성구, 범어동, 하양읍)
        if re.search(r'(시|군|구|도|동|읍|면|리|가)$', word):
            r = self.lookup(word + tail)
            if r and r['category'] == 'boundary' and r['name'] == word:
                return r
        # 2) '청도' -> 청도군, '경산' -> 경산시 처럼 붙여서 확인. 이름이 정확히 같을 때만 인정
        #    ('청도'는 '도'로 끝나지만 도 이름이 아니고, '청도시'로 찾으면 '청도읍'이 나오는 경우도 거른다)
        if not re.search(r'(역|로|길)$', word):
            for s in ADMIN_SUFFIXES:
                r = self.lookup(word + s + tail)
                if r and r['category'] == 'boundary' and r['name'] == word + s:
                    return r
        return self.lookup(word + tail)

    def save(self):
        save_json(GEO_CACHE, self.cache)

    def from_addr(self, a):
        """주소 -> (lat, lng, 정확도)"""
        road = a['road']
        is_road = road.endswith(('로', '길'))
        if is_road:
            r = self.query(addr_text(a))
            if r and (r['category'] == 'building' or r['addresstype'] in ('building', 'house')):
                return r['lat'], r['lng'], 1, r.get('name')
            if r and r['category'] == 'highway':
                if road.endswith('길'):
                    return r['lat'], r['lng'], 3, r.get('name')
                if not a['paren']:
                    # 큰 도로는 길어서 도로 중심점이 가게와 멀 수 있다
                    return r['lat'], r['lng'], 4, r.get('name')
        ctx = ', '.join(p for p in (a['sgg'].split()[-1] if a['sgg'] else '', a['sido']) if p)
        dong = a['paren'] if is_road else road
        if dong:
            for d in dict.fromkeys((dong, re.sub(r'[0-9]+가$', '', dong))):
                r = self.query(f'{d}, {ctx}')
                if r:
                    return r['lat'], r['lng'], 5, r.get('name')
        if a['town']:
            r = self.query(f"{a['town']}, {ctx}")
            if r:
                return r['lat'], r['lng'], 5, r.get('name')
        if a['sgg']:
            r = self.query(f"{a['sgg']}, {a['sido']}")
            if r:
                return r['lat'], r['lng'], 7, r.get('name')
        return None

    def from_title(self, admin, sub, name, kw):
        """제목의 지역 표기/가게명 -> (lat, lng, 정확도). kw 는 검색 문맥 지명(예: 포항)"""
        if sub:
            if re.search(r'(구|군|시|동|읍|면|가|리|역|로|길)$', sub):
                terms = [sub]
            else:  # '수성' -> 수성구, '다사' -> 다사읍 처럼 줄여 쓴 지명
                terms = [sub + s for s in ('구', '군', '시', '동', '읍', '면')] + [sub]
            for t in terms:
                qs = [f'{t}, {admin}, {kw}'] if admin and admin != t else []
                qs.append(f'{t}, {kw}')
                if t == terms[-1]:
                    qs.append(t)
                for q in qs:
                    r = self.query(q)
                    if r:
                        prec = 7 if re.search(r'(구|군|시)$', t) else 5
                        return r['lat'], r['lng'], prec, r.get('name')
        # 가게명으로 OSM 검색은 거의 못 찾아서(작은 가게 정보가 없음), 다른 단서가 하나도 없을 때만 시도
        if name and len(name) >= 3 and not admin and not sub:
            r = self.query(name)
            if r and r['category'] not in ('boundary', 'place', 'highway', 'railway'):
                return r['lat'], r['lng'], 6, r.get('name')
        if admin:
            r = self.query(f'{admin}, {kw}')
            if r:
                return r['lat'], r['lng'], 7, r.get('name')
        return None


def _norm(s):
    return re.sub(r'[^0-9a-zA-Z가-힣]', '', s).lower()


def name_match(name, place):
    """체험단 제목의 가게명과 카카오 검색 결과 장소명이 같은 가게인지 대충 판별"""
    a, b = _norm(name), _norm(place)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    strip_branch = lambda s: _norm(re.sub(r'\s*\S*(점|호점)$', '', s))  # noqa: E731 - 지점명 제거
    ca, cb = strip_branch(name), strip_branch(place)
    return (len(ca) >= 2 and ca in b) or (len(cb) >= 2 and cb in a)


ADMIN_TOKEN = re.compile(r'^[가-힣0-9]+(구|군|시|읍|면|동|가|리)$')
ROAD_TOKEN = re.compile(r'[가-힣0-9](로|길)$')


def _is_region_token(t, region):
    if t == region.name:
        return True
    return t.startswith(region.kw) and t[len(region.kw):] in ('',) + ADMIN_SUFFIXES


def _clean_district(t):
    # '수성구범어동'처럼 붙여 쓴 주소는 구까지만
    m = re.match(r'^([가-힣]+?(구|군))[가-힣0-9]+(동|읍|면|가|리)$', t)
    return m.group(1) if m else t


def district_for(region, texts, admin_hint=None):
    """검색 지역 바로 아래 행정구역 (대구 -> 수성구, 경산 -> 하양읍, 경북 -> 포항시).
    texts: 주소('대구 수성구 범어동 1', 넓은 곳 -> 좁은 곳) 또는
           지도 검색 결과 이름('감삼동, 달서구, 대구광역시, …', 좁은 곳 -> 넓은 곳)"""
    for text in texts:
        if not text:
            continue
        if ',' in text:
            toks = [t.strip() for t in text.split(',')]
            for i, t in enumerate(toks):
                if _is_region_token(t, region):
                    if i and ADMIN_TOKEN.match(toks[i - 1]) and not ROAD_TOKEN.search(toks[i - 1]):
                        return _clean_district(toks[i - 1])
                    break
        else:
            toks = text.split()
            for i, t in enumerate(toks):
                if _is_region_token(t, region):
                    for c in toks[i + 1:]:
                        if ADMIN_TOKEN.match(c) and not ROAD_TOKEN.search(c):
                            return _clean_district(c)
                    break
    if admin_hint and ADMIN_TOKEN.match(admin_hint) and not _is_region_token(admin_hint, region):
        return admin_hint
    return None


class Kakao:
    """카카오 로컬 API (키 있을 때만). 결과는 지오코딩 캐시에 같이 저장."""

    def __init__(self, key, geo):
        self.key, self.geo, self.ok = key, geo, True

    def _get(self, path, params):
        ck = 'kakao:' + path + '?' + urllib.parse.urlencode(params)
        if ck in self.geo.cache:
            return self.geo.cache[ck]
        if not self.ok:
            return None
        url = 'https://dapi.kakao.com' + path + '?' + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={'Authorization': 'KakaoAK ' + self.key})
            docs = json.loads(urllib.request.urlopen(req, timeout=15, context=CTX).read()).get('documents', [])
        except urllib.error.HTTPError as e:
            msg = e.read()[:200].decode('utf-8', 'replace')
            log(f'  카카오 API 오류 {e.code}: {msg}')
            if e.code in (401, 403):
                log('  -> 키가 틀렸거나 카카오 개발자 사이트에서 카카오맵 사용 설정이 꺼져 있어요. 이번 갱신은 카카오 검색 없이 계속해요.')
                self.ok = False
            return None
        except Exception as e:  # noqa: BLE001
            log(f'  카카오 API 실패: {type(e).__name__}')
            return None
        keep = ('place_name', 'address_name', 'road_address_name', 'x', 'y')
        self.geo._remember(ck, [{k: d.get(k) for k in keep} for d in docs[:5]])
        time.sleep(0.05)
        return self.geo.cache[ck]

    def address(self, q):
        for d in self._get('/v2/local/search/address.json', {'query': q, 'size': 1}) or []:
            lat, lng = float(d['y']), float(d['x'])
            if self.geo.region.contains(lat, lng):
                return lat, lng
        return None

    def place(self, name, admin, kw):
        region = self.geo.region
        for q in (f'{kw} {name}', name):
            docs = self._get('/v2/local/search/keyword.json', {'query': q, 'rect': region.rect, 'size': 5})
            for d in docs or []:
                addr = d.get('address_name') or ''
                lat, lng = float(d['y']), float(d['x'])
                if not region.contains(lat, lng):
                    continue
                if admin and admin not in addr:
                    continue
                if not name_match(name, d.get('place_name') or ''):
                    continue
                return lat, lng, d.get('road_address_name') or addr, d.get('place_name'), addr
        return None


# ------------------------------------------------------------- 4. HTML 생성
VENDOR_FILES = {
    'leaflet.css': 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css',
    'leaflet.js': 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js',
    'MarkerCluster.css': 'https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css',
    'leaflet.markercluster.js': 'https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js',
}


def vendor(name):
    p = VENDOR / name
    if not p.exists():
        VENDOR.mkdir(parents=True, exist_ok=True)
        p.write_bytes(fetch(VENDOR_FILES[name]))
    return p.read_text(encoding='utf-8')


def safe_json(obj):
    return json.dumps(obj, ensure_ascii=False).replace('</', '<\\/')


# ------------------------------------------------------- 지역 입력 해석
class Area:
    """동·읍·역처럼 작은 지역의 범위(사각형). 너무 작으면 최소 약 ±800m 로 넓힌다."""

    def __init__(self, name, stem, south, north, west, east):
        self.name, self.stem = name, stem
        cy, cx = (south + north) / 2, (west + east) / 2
        hy = max((north - south) / 2 + 0.003, 0.008)
        hx = max((east - west) / 2 + 0.003, 0.009)
        self.s, self.n, self.w, self.e = cy - hy, cy + hy, cx - hx, cx + hx

    def contains(self, lat, lng):
        return self.s <= lat <= self.n and self.w <= lng <= self.e


class Term:
    """입력한 지역 하나. searches: reviewtiful 에서 검색할 단어들"""

    def __init__(self, label, desc, region, searches, province=False, area=None, parent_kws=()):
        self.label, self.desc, self.region, self.searches = label, desc, region, searches
        self.province, self.area, self.parent_kws = province, area, list(parent_kws)


def stem_of(name):
    """대구광역시 -> 대구, 경산시 -> 경산, 수성구 -> 수성, 범어동 -> 범어 (한 글자가 되면 원래 이름)"""
    s = re.sub(r'(통합특별시|특별자치시|특별자치도|광역시|특별시|시|군|구|동|읍|면)$', '', name)
    return s if len(s) >= 2 else name


def region_from(kw, r):
    return Region(kw, r['name'], r['lat'], r['lng'], r['south'], r['north'], r['west'], r['east'])


def is_city(r):
    if r['category'] != 'boundary' or r['addresstype'] in ('borough', 'town', 'village', 'legal', 'quarter', 'suburb'):
        return False
    return r['addresstype'] in ('city', 'county', 'province', 'state', 'municipality') or \
        re.search(r'(광역시|특별시|특별자치시|시|군)$', r['name']) is not None


def build_terms(text, geo):
    terms = []
    for raw in [t for t in text.split(', ') if t]:
        words = raw.split()
        if len(words) == 1 and words[0] in PROVINCE_ALIAS:  # 경북, 경상북도 ...
            key = PROVINCE_ALIAS[words[0]]
            full, cities = PROVINCES[key]
            r = geo.lookup(full)
            if not r:
                raise SystemExit(f'"{raw}" 범위를 지도에서 찾지 못했어요.')
            cities = cities.split()
            terms.append(Term(raw, f'{full} {len(cities)}개 시·군', region_from(key, r), [key] + cities, province=True))
            continue
        target = words[-1]
        ctx = ', '.join(PROVINCES[PROVINCE_ALIAS[w]][0] if w in PROVINCE_ALIAS else w for w in reversed(words[:-1]))
        r = geo.resolve(target, ctx)
        if not r:
            raise SystemExit(f'"{raw}" 지역을 지도에서 찾지 못했어요. 예: 부산 / 경북 / 대구 범어동 / 경산 하양읍')
        if is_city(r):  # 시·군·광역시
            kw = stem_of(r['name'])
            terms.append(Term(raw, r['name'], region_from(kw, r), [kw]))
            continue
        # 동·읍·면·구·역 등 작은 지역: 상위 시(또는 구)로 검색한 뒤 그 범위 안의 가게만 남긴다
        addr = r.get('address') or {}
        parent = addr.get('city') or addr.get('county') or addr.get('province') or addr.get('state')
        pr = geo.resolve(parent) if parent else None
        if not pr:
            raise SystemExit(f'"{raw}"이(가) 속한 시·군을 찾지 못했어요. "대구 범어동"처럼 앞에 시 이름을 붙여 보세요.')
        borough = addr.get('borough') or addr.get('city_district') or ''
        pkw = stem_of(parent)
        bkw = stem_of(borough) if borough else ''
        parent_kws = [pkw] + ([bkw] if len(bkw) >= 2 and bkw != borough else [])
        own = r['name'] if r['category'] in ('boundary', 'place') else target
        area = Area(own, stem_of(own), r['south'], r['north'], r['west'], r['east'])
        searches = list(dict.fromkeys([parent_kws[-1], area.stem]))
        desc = ' '.join(dict.fromkeys(p for p in (parent, borough, own) if p))  # 대구광역시 수성구 범어동
        terms.append(Term(raw, desc, region_from(pkw, pr), searches, area=area, parent_kws=parent_kws))
    if not terms:
        raise SystemExit('지역을 입력해 주세요.')
    return terms


def keep_store(st, term):
    """작은 지역/중복 이름 시·군 조건에 맞는 가게만 남긴다."""
    located = st.get('lat') is not None
    if term.area:
        in_parent = located or any(is_target(c['title'], k) for c in st['items'] for k in term.parent_kws)
        if not in_parent:
            return False
        text = ' '.join([st['name'], st['area'], st.get('addr') or '', st.get('matched') or ''] +
                        [c['title'] for c in st['items']])
        if term.area.stem in text:
            if not located or st['prec'] >= 6 or not term.area.contains(st['lat'], st['lng']):
                # 이름/주소로 이 동네인 건 확실한데 핀이 구 중심 등에 있으면 동네 중심으로 옮긴다
                st.update(lat=round((term.area.s + term.area.n) / 2, 6), lng=round((term.area.w + term.area.e) / 2, 6),
                          prec=5, precLabel=f'{term.area.name} 안 (정확한 위치 모름)')
            return True
        return located and st['prec'] <= 5 and term.area.contains(st['lat'], st['lng'])
    if term.province and not located and all(c['kw'] in AMBIGUOUS_CITIES for c in st['items']):
        return False
    return True


def main(keyword=None, window=None, out=None, site=None):
    """목록 수집 -> 주소 수집 -> 좌표 -> HTML 저장. 만든 HTML 의 생성 시각(ISO)을 돌려준다.

    window: 마감 기간(일). 2 = 오늘~내일, 3, 7. 기간이 길면 오늘·내일 가게를 먼저 처리해서
    중간 결과 지도를 한 번 저장하고(partial), 나머지 날짜까지 끝나면 다시 저장한다.
    out: 저장할 HTML 경로 (기본: 체험단_마감지도.html)
    site: 웹 버전(GitHub Pages)용 정보 {'regions': [{'name', 'file'}], 'current', 'everyHours'}.
          주면 화면에서 갱신 버튼 대신 미리 만든 지역 메뉴를 보여주고, config.json 은 건드리지 않는다.
    """
    out = pathlib.Path(out) if out else OUT
    cfg = load_json(CONFIG, {})
    text = normalize_keyword(keyword or cfg.get('keyword') or DEFAULT_KEYWORD)
    if not valid_keyword(text):
        raise SystemExit(f'지역 이름이 올바르지 않아요: {text!r} (한글/영문/숫자, 쉼표로 여러 곳)')
    try:
        window = int(window or cfg.get('days') or DEFAULT_WINDOW)
    except (TypeError, ValueError):
        window = DEFAULT_WINDOW
    if window not in WINDOW_CHOICES:
        window = DEFAULT_WINDOW
    template = (BASE / 'template.html').read_text(encoding='utf-8')
    today = now_kst().date()
    dates = [(today + datetime.timedelta(days=i)).isoformat() for i in range(window)]
    days = {iso: i for i, iso in enumerate(dates)}  # 마감일 -> 오늘부터 며칠 뒤(0=오늘)

    geo = Geocoder()
    log(f'0) "{text}" 지역 확인 중... (마감 기간: {window}일, {dates[0]} ~ {dates[-1]})')
    terms = build_terms(text, geo)
    for t in terms:
        log(f'   -> {t.desc} (검색어: {", ".join(t.searches)})')

    log('1) reviewtiful 검색 결과 가져오는 중...')
    targets, due_links, ok_searches, failed = {}, set(), 0, []
    jobs = [(ti, term, kw) for ti, term in enumerate(terms) for kw in term.searches]
    if len(jobs) > 3:
        log(f'   검색어 {len(jobs)}개 - 사이트 부담을 줄이려고 {SEARCH_GAP:g}초 간격으로 검색해요 (약 {len(jobs) * SEARCH_GAP / 60:.0f}분)')
    for j, (ti, term, kw) in enumerate(jobs):
        if j:
            time.sleep(SEARCH_GAP)
        try:
            found = get_list(kw)
            ok_searches += 1
        except Exception as e:  # noqa: BLE001 - 한 검색어 실패는 건너뛴다
            log(f'   "{kw}" 검색 실패: {e}')
            failed.append(kw)
            continue
        n = 0
        for x in found:
            if x.get('due') not in days:
                continue
            due_links.add(x['link'])
            if x['link'] in targets or not is_target(x.get('title', ''), kw):
                continue
            if term.area and kw == term.area.stem and kw in GENERIC_GU and \
                    not any(is_target(x['title'], p) for p in term.parent_kws):
                continue  # [대구 북구] 를 '포항 북구'로 착각하지 않게
            targets[x['link']] = dict(x, _term=ti, _kw=kw, _d=days[x['due']])
            n += 1
        log(f'   "{kw}": 전체 {len(found)}건 중 기간 내 마감 새 캠페인 {n}건')
    if not ok_searches:
        raise SystemExit('reviewtiful 검색이 모두 실패했어요. 인터넷 연결을 확인하고 몇 분 뒤 다시 갱신하세요.')
    targets = list(targets.values())

    key = os.environ.get('KAKAO_REST_KEY', '').strip()
    if not key and KAKAO_KEY_FILE.exists():
        key = KAKAO_KEY_FILE.read_text(encoding='utf-8-sig').strip()
    kakao = Kakao(key, geo) if key else None

    # 가게 이름으로 묶고, 마감이 빠른 가게부터 처리
    groups = collections.OrderedDict()
    for x in sorted(targets, key=lambda x: x['_d']):
        name = store_name(x)
        groups.setdefault(re.sub(r'\s+', '', name).lower(), {'name': name, 'items': []})['items'].append(x)
    for gi, g in enumerate(groups.values()):
        g['id'] = gi
        g['minD'] = min(x['_d'] for x in g['items'])

    page_state = {'blocked': set(), 'work_blocked': None}
    pages = {}
    stores, dropped = [], 0

    def process(group_list):
        nonlocal dropped
        for k, g in enumerate(group_list):
            first = g['items'][0]
            term = terms[first['_term']]
            geo.region = term.region
            ctx_kw = first['_kw'] if term.province else term.region.kw
            best = None  # (정확도, lat, lng, 표시 주소)
            admin_hint = sub_hint = None
            dist_texts = []  # 구·동을 알아낼 단서 (주소, 지도 검색 결과 이름)
            for x in g['items']:
                admin, sub = title_location(x['title'], term.region.kw)
                if admin is None and sub is None:
                    admin, sub = title_location(x['title'], x['_kw'])
                admin_hint = admin_hint or admin
                sub_hint = sub_hint or sub
                p = pages.get(x['link']) or {}
                a = pick_addr(p.get('addrs'), ctx_kw)
                shown = addr_text(a) if a else ''
                if a:
                    dist_texts.append(f"{shown} {a['paren']}".strip())
                cand = None
                coords = [c for c in p.get('coords') or [] if term.region.contains(*c)]
                if coords:
                    cand = (1, coords[0][0], coords[0][1], shown)
                elif a:
                    r = kakao.address(shown) if kakao and a['num'] else None
                    if r:
                        cand = (1, r[0], r[1], shown)
                    else:
                        r = geo.from_addr(a)
                        cand = (r[2], r[0], r[1], shown) if r else None
                        if r:
                            dist_texts.append(r[3])
                if cand and (best is None or cand[0] < best[0]):
                    best = cand
            matched = None
            if kakao and (best is None or best[0] >= 3):
                r = kakao.place(g['name'], admin_hint, ctx_kw)
                if r:
                    best, matched = (2, r[0], r[1], r[2]), r[3]
                    dist_texts.insert(0, r[4])
            if best is None or best[0] >= 6:
                r = geo.from_title(admin_hint, sub_hint, g['name'], ctx_kw)
                if r and (best is None or r[2] < best[0]):
                    best = (r[2], r[0], r[1], best[3] if best else '')
                if r:
                    dist_texts.append(r[3])
            campaigns = []
            for x in g['items']:
                apps, recruit = parse_applicants(x.get('applicant'))
                offer = re.sub(r'\s+', ' ', x.get('description') or '').strip()
                note = re.sub(r'\s+', ' ', x.get('note') or '').strip()
                won, pts = parse_amount(offer, note, x['title'])
                ch, ch_guess = parse_channels(note, x['title'], x['source'])
                link = x['link'] if x['link'].startswith(('http://', 'https://')) else ''
                campaigns.append({
                    'd': x['_d'], 'date': x['due'], 'source': x['source'], 'note': note, 'kw': x['_kw'],
                    'title': x['title'].strip(), 'offer': offer[:400],
                    'ch': ch, 'chGuess': ch_guess,
                    'won': won, 'pts': pts, 'apps': apps, 'recruit': recruit,
                    'link': link, 'host': urllib.parse.urlparse(link).netloc,
                })
            st = {'id': g['id'], 'name': g['name'], 'area': ' '.join(v for v in (admin_hint, sub_hint) if v),
                  'district': district_for(term.region, dist_texts, admin_hint), 'items': campaigns}
            if best:
                st.update(lat=round(best[1], 6), lng=round(best[2], 6), prec=best[0],
                          precLabel=PREC_LABEL[best[0]], addr=best[3])
                if matched:
                    st['matched'] = matched
            if keep_store(st, term):
                stores.append(st)
            else:
                dropped += 1
            if (k + 1) % 50 == 0:
                log(f'   가게 {k + 1}/{len(group_list)} 처리')
        geo.save()

    def write_html(partial):
        now = now_kst()
        first_region = terms[0].region
        data = {
            'keyword': text,
            'window': window,
            'desc': ' · '.join(t.desc for t in terms),
            'region': {'name': first_region.name, 'lat': first_region.lat, 'lng': first_region.lng},
            'focus': [[[t.area.s, t.area.w], [t.area.n, t.area.e]] for t in terms if t.area],
            'generated': now.strftime('%Y-%m-%d %H:%M'),
            'generatedIso': now.isoformat(timespec='seconds'),
            'generatedEpoch': int(time.time() * 1000),
            'static': site,
            'today': dates[0], 'dates': dates,
            'partial': partial,
            'blockedHosts': sorted(page_state['blocked']),
            'excluded': len(due_links) - len(targets),
            'outside': dropped,
            'failedSearches': failed,
            'stores': stores,
        }
        page = template
        for k, v in {
            '/*LEAFLET_CSS*/': vendor('leaflet.css'),
            '/*CLUSTER_CSS*/': vendor('MarkerCluster.css'),
            '/*LEAFLET_JS*/': vendor('leaflet.js'),
            '/*CLUSTER_JS*/': vendor('leaflet.markercluster.js'),
            '/*DATA*/': safe_json(data),
        }.items():
            page = page.replace(k, v, 1)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix('.tmp')
        tmp.write_text(page, encoding='utf-8')
        os.replace(tmp, out)  # 서버가 읽는 중에 반쯤 쓰인 파일이 보이지 않도록
        return data['generatedIso']

    all_groups = list(groups.values())
    phases = [all_groups]
    if window > 2:
        early = [g for g in all_groups if g['minD'] <= 1]
        late = [g for g in all_groups if g['minD'] > 1]
        if early and late:
            phases = [early, late]
    log('   카카오 가게명 검색: ' + ('사용' if kakao else '키 없음 -> OpenStreetMap 만 사용 (대략 위치 많음)'))
    for pi, group_list in enumerate(phases):
        label = '' if len(phases) == 1 else (' [1단계: 오늘·내일 마감 가게]' if pi == 0 else ' [2단계: 나머지 날짜]')
        items = [x for g in group_list for x in g['items']]
        log(f'2) 상세 페이지에서 주소 찾는 중...{label}')
        pages.update(collect_pages(items, page_state))
        log(f'3) 위치 좌표 찾는 중 (처음 보는 가게가 많으면 몇 분~수십 분 걸림)...{label}')
        process(group_list)
        if pi < len(phases) - 1:
            write_html(partial=True)
            log('   -> 오늘·내일 마감 가게로 지도를 먼저 저장했어요. 나머지 날짜를 계속 처리합니다.')
    log('4) HTML 만드는 중...')
    generated = write_html(partial=False)
    if site is None:  # 로컬 버전만 '마지막으로 본 지역'을 기억
        save_json(CONFIG, {'keyword': text, 'days': window})

    # ---- 요약
    blocked = page_state['blocked']
    prec_count = collections.Counter(s.get('prec') for s in stores)
    per_day = collections.Counter(c['d'] for s in stores for c in s['items'])
    log('')
    log(f'완료: {out}')
    if site is None:
        log('  지도 보기: 지도열기.bat 더블클릭 (또는 python open_map.py)')
    log(f'  "{text}" 체험단 {sum(per_day.values())}건 / 가게 {len(stores)}곳')
    log('  마감일별: ' + ', '.join(f'{dates[d][5:]} {per_day[d]}건' for d in range(window)))
    for p in sorted(k for k in prec_count if k):
        log(f'  - {PREC_LABEL[p]}: {prec_count[p]}곳')
    log(f'  - 위치 못 찾음(목록에만 표시): {prec_count.get(None, 0)}곳')
    log(f'  다른 지역 제외: {len(due_links) - len(targets)}건' + (f' / 지정 범위 밖 가게 제외: {dropped}곳' if dropped else ''))
    per_site = collections.defaultdict(lambda: [0, 0, 0])  # 전체, 페이지 확인, 주소/좌표 확보
    for x in targets:
        host = urllib.parse.urlparse(x['link']).netloc
        p = pages.get(x['link'])
        v = per_site[(x['source'], host)]
        v[0] += 1
        if p is not None:
            v[1] += 1
            if p.get('addrs') or p.get('coords'):
                v[2] += 1
    log('  사이트별 [주소 확보 / 상세 페이지 확인 / 전체]:')
    for (src, host), (tot, seen, got) in sorted(per_site.items(), key=lambda kv: -kv[1][0]):
        why = ' (회사망 차단)' if host in blocked else ' (주소 비공개 사이트)' if host in NO_ADDR_HOSTS else ''
        log(f'    {src}: {got} / {seen} / {tot}{why}')
    return generated


def _cli():
    args = sys.argv[1:]
    window = None
    if '--days' in args:
        i = args.index('--days')
        window = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    main(' '.join(args) or None, window)


if __name__ == '__main__':
    if not (BASE / 'template.html').exists():
        sys.exit('template.html 이 build.py 와 같은 폴더에 있어야 합니다.')
    _cli()
