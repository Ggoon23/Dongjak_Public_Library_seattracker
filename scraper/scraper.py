import csv
import os
import sys
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

URL = "http://djlib-seat.sen.go.kr/domian5.php"
CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "seats.csv")
FIELDNAMES = ["collected_at", "room_name", "total_seats", "used_seats", "remaining_seats"]
KST = timezone(timedelta(hours=9))


def fetch_page() -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    }
    resp = requests.get(URL, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.content


def parse_rows(html: bytes) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser", from_encoding="euc-kr")

    # 수집 시각: 페이지 상단 타임스탬프 우선, 없으면 현재 시각
    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    timestamp_td = soup.find("td", attrs={"colspan": "6"})
    if timestamp_td:
        raw = timestamp_td.get_text(strip=True)
        # "2026-03-17 22:29:31 현재" 형태
        parts = raw.split()
        if len(parts) >= 2:
            try:
                dt = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S")
                collected_at = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass

    rows = []
    # 좌석 현황 테이블: 헤더에 '전체 좌석수' 포함된 테이블
    tables = soup.find_all("table")
    seat_table = None
    for table in tables:
        headers_text = table.get_text()
        if "전체 좌석수" in headers_text or "전체" in headers_text and "잔여" in headers_text:
            seat_table = table
            break

    if seat_table is None:
        raise ValueError("좌석 현황 테이블을 찾을 수 없습니다.")

    for tr in seat_table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        texts = [td.get_text(strip=True) for td in tds]
        # 첫 번째 열이 숫자(행번호)인 행만 처리
        if not texts[0].isdigit():
            continue
        room_name = texts[1]
        try:
            total_seats = int(texts[2])
            used_seats = int(texts[3])
            remaining_seats = int(texts[4])
        except ValueError:
            continue

        rows.append({
            "collected_at": collected_at,
            "room_name": room_name,
            "total_seats": total_seats,
            "used_seats": used_seats,
            "remaining_seats": remaining_seats,
        })

    return rows


def load_existing_timestamps(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    timestamps = set()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            timestamps.add(row.get("collected_at", ""))
    return timestamps


def append_rows(path: str, rows: list[dict]) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    file_exists = os.path.exists(path)
    written = 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)
            written += 1
    return written


def main():
    try:
        html = fetch_page()
    except Exception as e:
        print(f"[ERROR] 페이지 수집 실패: {e}", file=sys.stderr)
        sys.exit(0)  # 워크플로우는 성공으로 종료

    try:
        rows = parse_rows(html)
    except Exception as e:
        print(f"[ERROR] 파싱 실패: {e}", file=sys.stderr)
        sys.exit(0)

    if not rows:
        print("[WARN] 파싱된 행이 없습니다.", file=sys.stderr)
        sys.exit(0)

    existing_timestamps = load_existing_timestamps(CSV_PATH)
    new_rows = [r for r in rows if r["collected_at"] not in existing_timestamps]

    if not new_rows:
        print(f"[SKIP] 동일 타임스탬프({rows[0]['collected_at']}) 이미 존재, 건너뜁니다.")
        # 커밋 불필요 신호
        print("NEW_DATA=false")
        sys.exit(0)

    written = append_rows(CSV_PATH, new_rows)
    print(f"[OK] {written}행 저장 완료 (타임스탬프: {new_rows[0]['collected_at']})")
    print("NEW_DATA=true")


if __name__ == "__main__":
    main()
