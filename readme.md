# 동작도서관 열람실 좌석 현황 자동 수집기

GitHub Actions + Cloudtype(PaaS) 조합으로 동작구립도서관 열람실 좌석 현황을 10분마다 자동 수집합니다.
Cloudtype 서버가 한국 IP를 사용하므로 도서관 서버 접근 차단 문제가 없습니다.

## 구조

```
GitHub Actions (스케줄러 + 저장)
    └─ 10분마다 실행 (KST 09:00~18:00)
        ├─ Cloudtype Flask 서버에 /collect 요청
        └─ 응답 데이터를 data/seats.csv에 append 후 커밋

Cloudtype Flask 서버 (스크래퍼)
    └─ 도서관 페이지 scraping → JSON 반환
```

## 파일 구성

```
├── .github/workflows/collect.yml   # 스케줄러 + 저장 워크플로우
├── server/
│   ├── app.py                      # Flask API 서버 (Cloudtype 배포)
│   └── requirements.txt
├── data/
│   └── seats.csv                   # 누적 수집 데이터
├── logs/
│   └── error.log                   # 에러 기록
├── requirements.txt                # Cloudtype 패키지 설치용 (루트)
└── cloudtype.yaml                  # Cloudtype 배포 설정
```

## CSV 형식

| 컬럼 | 설명 |
|---|---|
| `collected_at` | 수집 시각 (KST, `YYYY-MM-DD HH:MM`) |
| `room_name` | 열람실명 |
| `used_seats` | 사용 좌석수 |
| `waiting` | 전체 대기자수 |

- 동일 `(collected_at, room_name)` 조합은 중복 skip
- 항상 append, 덮어쓰기 없음

## API 엔드포인트

| 엔드포인트 | 응답 |
|---|---|
| `GET /status` | `{"status": "ok"}` |
| `GET /collect` | `{"status": "ok", "data": [...]}` / `{"status": "no_data"}` / `{"status": "error", "message": "..."}` |

## GitHub Secrets 설정

레포 → Settings → Secrets and variables → Actions → New repository secret

| Secret 이름 | 값 |
|---|---|
| `CLOUDTYPE_URL` | Cloudtype 서비스 URL (예: `https://xxxx.run.goorm.io`) |
| `CLOUDTYPE_TOKEN` | Cloudtype API 토큰 |

## 배포 방법

### 1. Cloudtype 서버 배포

1. [cloudtype.io](https://cloudtype.io) 로그인 → 새 서비스 생성 → 이 레포 연결
2. 설정값 입력:
   - **언어:** Python / Flask
   - **서비스 이름:** `djlib-seat-server`
   - **포트:** `3000`
   - **Start command:** `pip install -r server/requirements.txt && gunicorn --chdir server app:app --bind 0.0.0.0:3000 --workers 1 --timeout 30`
3. 배포 완료 후 서비스 URL 복사 → `CLOUDTYPE_URL` Secret에 저장

### 2. Cloudtype API 토큰 발급

Cloudtype 대시보드 → 우측 상단 프로필 → 설정 → API 토큰 발급 → `CLOUDTYPE_TOKEN` Secret에 저장

### 3. GitHub Actions 활성화

push 후 Actions 탭 → **Run workflow** 로 수동 첫 실행 테스트

## 워크플로우 동작 순서

1. `GET /status` 로 서버 생존 확인
2. 서버 다운 시 → ctype CLI로 재시작 → 30초 대기
3. `GET /collect` 로 수집 요청
4. 응답 처리:
   - `ok` → `data/seats.csv` append 후 커밋
   - `no_data` → 로그 출력만, 커밋 없음
   - `error` → `logs/error.log` 기록 후 커밋
