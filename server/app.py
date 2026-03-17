import sys
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify

app = Flask(__name__)

URL = "http://djlib-seat.sen.go.kr/domian5.php"
KST = timezone(timedelta(hours=9))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


@app.route("/status")
def status():
    return jsonify({"status": "ok"})


@app.route("/collect")
def collect():
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.content
    except Exception as e:
        return jsonify({"status": "error", "message": f"fetch failed: {e}"}), 200

    try:
        rows = parse(html)
    except Exception as e:
        return jsonify({"status": "error", "message": f"parse failed: {e}"}), 200

    if not rows:
        return jsonify({"status": "no_data"}), 200

    return jsonify({"status": "ok", "data": rows}), 200


def parse(html: bytes) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser", from_encoding="euc-kr")

    # 수집 시각: 페이지 상단 타임스탬프 우선, 없으면 현재 KST
    collected_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    ts_td = soup.find("td", attrs={"colspan": "6"})
    if ts_td:
        parts = ts_td.get_text(strip=True).split()
        if len(parts) >= 2:
            try:
                dt = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S")
                collected_at = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass

    # 전체 대기자수 파싱 (aggregate)
    waiting_total = 0
    for td in soup.find_all("td"):
        text = td.get_text(strip=True)
        if "전체 대기자수" in text:
            b = td.find("b")
            if b:
                try:
                    waiting_total = int(b.get_text(strip=True))
                except ValueError:
                    pass
            break

    # 좌석 현황 테이블 파싱
    rows = []
    for table in soup.find_all("table"):
        if "전체 좌석수" not in table.get_text():
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            texts = [td.get_text(strip=True) for td in tds]
            if not texts[0].isdigit():
                continue
            try:
                used_seats = int(texts[3])
            except ValueError:
                continue
            rows.append({
                "collected_at": collected_at,
                "room_name": texts[1],
                "used_seats": used_seats,
                "waiting": waiting_total,
            })
        break  # 첫 번째 좌석 테이블만

    return rows


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
