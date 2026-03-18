# 동작도서관 열람실 좌석 현황 자동 수집기

동작구립도서관 열람실 좌석 현황을 10분마다 자동 수집해 CSV로 누적 저장합니다.

- **Cloudtype**: 한국 서버에서 직접 스크래핑 + GitHub API로 커밋 (해외 IP 차단 우회)
- **GitHub Actions**: 1시간마다 서버 생존 확인 → 다운 시 자동 재시작

## 구조

```
Cloudtype Flask 서버 (한국 서버)
    └─ APScheduler: 10분마다 (KST 08:00~18:00)
        ├─ 도서관 페이지 스크래핑
        └─ GitHub API로 data/seats.csv에 직접 커밋

GitHub Actions
    └─ 1시간마다 GET /status 헬스체크
        └─ 다운 시 ctype CLI로 재시작
```

## 파일 구성

```
├── .github/workflows/collect.yml   # 헬스체크 + 재시작 워크플로우
├── server/
│   ├── app.py                      # Flask 서버 (Cloudtype 배포)
│   └── requirements.txt
├── data/
│   └── seats.csv                   # 누적 수집 데이터
├── logs/
│   └── error.log
├── requirements.txt                # Cloudtype 패키지 설치용 (루트)
└── cloudtype.yaml                  # Cloudtype 배포 설정
```

## CSV 형식

| 컬럼 | 설명 |
|---|---|
| `collected_at` | 수집 시각 (KST, `YYYY-MM-DD HH:MM`) |
| `room_name` | 열람실명 |
| `total_seats` | 전체 좌석수 |
| `used_seats` | 사용 좌석수 |
| `waiting` | 대기자수 |

- 동일 `(collected_at, room_name)` 조합 중복 skip
- 항상 append, 덮어쓰기 없음

## API 엔드포인트

| 엔드포인트 | 설명 |
|---|---|
| `GET /status` | 서버 생존 확인 → `{"status": "ok"}` |
| `GET /collect` | 즉시 수집 + 커밋 (수동 트리거용) |
| `GET /debug` | 즉시 수집 후 결과 반환 (커밋 없음, 테스트용) |

## 초기 설정

### 1. Cloudtype 서버 배포

1. [cloudtype.io](https://cloudtype.io) → 새 서비스 → 이 레포 연결
2. 설정:
   - **언어:** Python / Flask
   - **서비스 이름:** `djlib-seat-server`
   - **포트:** `3000`
   - **Start command:**
     ```
     pip install -r server/requirements.txt && gunicorn --chdir server app:app --bind 0.0.0.0:3000 --workers 1 --timeout 30
     ```
3. **환경변수** 추가:
   | 이름 | 값 |
   |---|---|
   | `GITHUB_TOKEN` | repo write 권한 PAT |
   | `GITHUB_REPO` | `유저명/레포명` |

### 2. GitHub Secrets 설정

레포 → Settings → Secrets and variables → Actions

| Secret | 값 |
|---|---|
| `CLOUDTYPE_URL` | Cloudtype 서비스 URL |
| `CLOUDTYPE_TOKEN` | Cloudtype API 토큰 |

### 3. 동작 확인

배포 후 브라우저에서 확인:
- `/status` → `{"status": "ok"}`
- `/debug` → 파싱 결과 JSON (열람실별 전체/사용/대기 수)

---

## 개발 과정 주요 시행착오

### 1. 도서관 서버의 해외 IP 차단

GitHub Actions 러너는 미국 서버 → 도서관 서버(`djlib-seat.sen.go.kr`)가 해외 IP 접속을 차단해 타임아웃 발생. `User-Agent` 없이 요청하면 응답 자체가 없고, 한국 IP에서만 정상 응답함.

**해결:** GitHub Actions에서 직접 스크래핑하는 구조를 포기하고, 한국 서버인 Cloudtype에 Flask 서버를 올려 스크래핑을 위임.

---

### 2. Cloudtype 게이트웨이 타임아웃

초기 설계는 "GitHub Actions → Cloudtype `/collect` 호출 → 결과 CSV 저장" 구조였음. 브라우저에서 `/collect`는 정상 동작했지만, GitHub Actions(미국)에서 호출하면 Cloudtype 게이트웨이가 응답 대기 중 먼저 끊고 HTML 에러 페이지를 반환. JSON 파싱 실패로 매번 `error.log`에 기록됨.

원인: GitHub Actions → Cloudtype 네트워크 지연 + 도서관 페이지 응답 시간(~20초)이 합산되어 Cloudtype 무료 플랜 게이트웨이 타임아웃 초과.

**해결:** 구조를 전면 변경. Cloudtype 서버가 APScheduler로 직접 10분마다 수집하고 GitHub API로 커밋. GitHub Actions는 헬스체크 역할만 수행.

---

### 3. GitHub Actions 스케줄 불안정

`*/10 * * * *`으로 설정했으나 실제로는 10분 간격이 보장되지 않음. 30분 이상 지연되거나 통째로 스킵되는 경우가 빈번했음. 특히 레포 활동이 적으면 더 심해짐.

**해결:** 스케줄링 자체를 Cloudtype의 APScheduler로 이전. GitHub Actions 스케줄은 1시간마다 헬스체크 용도로만 남겨 신뢰성 문제 회피.

---

### 4. 대기자수 항상 0 버그

페이지에 "호출 대기 리스트"와 "대기자 리스트" 두 테이블이 존재. 둘 다 `열람실명`, `좌석번호` 컬럼을 가지고 있어 동일한 조건에 매칭됨. 코드가 첫 번째 매칭 테이블에서 `break`하여 "호출 대기 리스트"만 처리하고 실제 대기자 데이터가 있는 "대기자 리스트"는 무시.

**해결:** 필터 조건을 `"대기자 리스트" in text and "호출" not in text`로 변경해 두 테이블을 정확히 구분.
