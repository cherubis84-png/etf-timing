# ETF 국면 전망

미국 ETF(지수·섹터·채권·금/은·달러·코인)의 상승·하락 가능성을 거시 지표 흐름의 조합(국면)으로 판단하는 아이폰·아이패드용 웹앱입니다.
GitHub Actions가 매일 아침 데이터를 받아 국면과 통계를 다시 계산하고, 앱(GitHub Pages)은 그 결과(`data.json`)만 읽습니다.
API 키가 앱에 노출되지 않고, 휴대폰에서 계산할 필요도 없습니다.

## 설치 (한 번만, 약 10분)

1. GitHub에서 새 저장소 생성 (예: `etf-timing`, Public)
2. 이 폴더의 파일을 모두 업로드 — 숨김 폴더 `.github/workflows/update.yml` 도 반드시 포함
   - 웹에서 올릴 때 `.github` 폴더가 빠지면: 저장소에서 **Add file → Create new file** 로
     파일 이름에 `.github/workflows/update.yml` 을 입력하고 내용을 붙여 넣으세요.
3. (권장) FRED API 키 등록 — 없어도 동작하지만 있으면 더 안정적
   - https://fred.stlouisfed.org/docs/api/api_key.html 에서 무료 발급
   - 저장소 **Settings → Secrets and variables → Actions → New repository secret**
     이름 `FRED_API_KEY`, 값 = 발급받은 키
4. **Settings → Actions → General → Workflow permissions** 에서 **Read and write permissions** 선택 후 저장
5. **Actions** 탭 → `데이터 갱신` → **Run workflow** (첫 실행 5~10분)
6. **Settings → Pages** → Branch `main` / `/ (root)` 저장
7. 아이폰 Safari로 `https://<아이디>.github.io/etf-timing/` 접속 → 공유 → **홈 화면에 추가**

첫 실행 전까지는 화면에 "데모 데이터" 표시가 뜹니다(합성 데이터, 실제 시장과 무관).

## 자동 갱신
- 한국시간 화~토 오전 7시 17분 (미국 장 마감 이후)
- 수동: Actions → 데이터 갱신 → Run workflow
- 실패한 데이터는 `data/cache` 의 직전 값으로 대체하고 로그에 남깁니다(`data.json` 의 `log`).

## 무엇을 보여주나
- **현재 국면**: 성장·물가·국채금리·연준·달러·위험심리·기업이익·지정학 8가지 흐름의 현재 상태와, 이를 묶은 해설 문장
- **자산 전망 (이번 주 / 이번 달 / 이번 분기)**: 현재와 같은 흐름 조합이었던 과거 시기에 각 자산이 실제로 낸 수익률의 중앙값(기대수익)·범위·상승확률, 유망/주의 자산과 대표 ETF
- **판단 근거**: 어떤 흐름 조합을 근거로 했는지, 그 조합이 나타난 과거 시기 목록과 시기별 수익
- **전환 경고**: 기준선에 가까운 흐름이 13주 안에 바뀔 확률과, 바뀌면 자산 전망이 어떻게 달라지는지
- **지표**: 점도표, GDP·GDPNow, CPI·근원 CPI·근원 PCE, 국채금리, 달러 인덱스, VIX, 공포·탐욕, S&P 500 EPS 성장 예상, GPR 등
- **국면·검증**: 2000년 이후 국면 이력, 표본 외 전략 검증

## 데이터 출처
| 구분 | 지표 | 출처 |
|---|---|---|
| 성장 | CFNAI 3개월 평균, 실질 GDP, GDPNow, 실업률, 실업수당 청구, 경기침체 확률 | FRED |
| 물가 | CPI, 근원 CPI, 근원 PCE, 기대인플레이션 | FRED |
| 금리·연준 | 3개월·2년·10년·30년 국채, 실효 기준금리, 목표 범위, **점도표 중간값(FEDTARMD)** | FRED |
| 달러 | 달러 인덱스 DXY | Yahoo Finance |
| 위험심리 | VIX, 하이일드 스프레드, NFCI / 공포·탐욕 지수 | FRED / CNN(비공식) |
| 기업이익 | 미국 기업이익(CP) / **S&P 500 분기·연간 EPS 성장 예상, 선행 PER** | FRED / FactSet Earnings Insight |
| 지정학 | Geopolitical Risk Index(일간) | Caldara & Iacoviello |
| 가격 | ETF·지수·선물 (상장 전 국채는 금리로 근사) | Yahoo Finance (실패 시 Stooq) |

점도표·애널리스트 예상은 과거 시점 값이 공개되지 않아 해설과 지표 화면에만 쓰고, 통계 계산에는 과거 값을 그대로 재현할 수 있는 시장금리·실제 기업이익을 씁니다.

## 판단 방식
1. 8가지 흐름을 매주 두 상태로 판정 (발표 지연 반영, 기준선 근처 잔떨림 방지)
2. 자산마다 어떤 흐름이 이후 수익률을 실제로 갈랐는지 t값으로 측정 → 영향 큰 흐름만 강한 순으로 사용
3. 현재와 같은 조합의 과거 주(최소 52주, 서로 다른 3개 시기 이상)에서 이후 수익률 통계. 부족하면 약한 흐름부터 빼서 넓힘
4. 흐름별 13주 내 전환 확률(기준선과의 거리별 과거 사례) → 전환 시 전망 재계산
5. 2000년부터 그 시점 데이터만으로 반복 계산해 신뢰도 산출

## 바꾸고 싶을 때
`engine/config.py`에서 자산·ETF(`ASSETS`), 흐름 판정 기준(`DIMS`), 통계 기준을 바꿀 수 있습니다.

로컬 확인: `pip install -r requirements.txt` 후 `python engine/update.py --demo` → `python -m http.server` → http://localhost:8000

## 주의
과거 검증 결과는 미래 수익을 보장하지 않습니다. 투자 판단의 참고 자료로만 사용하세요.
