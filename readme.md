# 동작도서관 열람실 좌석 현황 자동 수집기

서울특별시 동작구립도서관 열람실 실시간 좌석 현황을 GitHub Actions로 자동 수집해 CSV에 누적 저장합니다.

## 수집 대상

- **URL:** http://djlib-seat.sen.go.kr/domian5.php
- **열람실:** 1층 자료실(열람하부) / 1층 디지털학습실 / 3층 학습실

## 실행 주기

매 10분마다 자동 실행 (GitHub Actions schedule)

```
9:00 → 9:10 → 9:20 → 9:30 → ...
```

> GitHub Actions의 schedule은 UTC 기준으로 동작합니다. 실제 실행 시각은 명시된 시각에서 수 분 지연될 수 있습니다.

## 저장 구조

```
data/seats.csv
```

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `collected_at` | `YYYY-MM-DD HH:MM` | 페이지 표시 시각 (KST) |
| `room_name` | string | 열람실명 |
| `total_seats` | int | 전체 좌석수 |
| `used_seats` | int | 사용 좌석수 |
| `remaining_seats` | int | 잔여 좌석수 |

- 새 데이터가 있을 때만 커밋 (동일 타임스탬프 중복 skip)
- 기존 데이터는 덮어쓰지 않고 append

## 레포 구조

```
djlib-seat-tracker/
├── .github/
│   └── workflows/
│       └── collect.yml       # GitHub Actions 워크플로우
├── scraper/
│   ├── scraper.py            # 수집 스크립트
│   └── requirements.txt
└── data/
    └── seats.csv             # 누적 데이터
```

## 로컬 실행

```bash
pip install -r scraper/requirements.txt
python scraper/scraper.py
```

## 오류 처리

- 네트워크 오류 또는 파싱 실패 시 워크플로우는 **성공**으로 종료 (데이터 미수집만 발생)
- 새 데이터가 없으면 커밋하지 않음
