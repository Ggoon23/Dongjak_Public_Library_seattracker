import sys
import threading
import time
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
SCRAPE_INTERVAL = 300  # 5분마다 백그라운드 수집

_cache: dict = {"rows": None, "error": None}
_lock = threading.Lock()


# ── 백그라운드 스크래퍼 ────────────────────────────────────────────

def scrape_once():
    try:
        resp = requests.get(URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        rows = parse(resp.content)
        with _lock:
            _cache["rows"] = rows
            _cache["error"] = None
    except Exception as e:
        with _lock:
            _cache["error"] = str(e)


def background_loop():
    while True:
        scrape_once()
        time.sleep(SCRAPE_INTERVAL)


threading.Thread(target=background_loop, daemon=True).start()


# ── 엔드포인트 ────────────────────────────────────────────────────

@app.route("/status")
def status():
    return jsonify({"status": "ok"})


@app.route("/collect")
def collect():
    with _lock:
        rows = _cache["rows"]
        error = _cache["error"]

    # 캐시 비어있으면 (콜드스타트 직후) 즉시 동기 스크래핑
    if rows is None and error is None:
        scrape_once()
        with _lock:
            rows = _cache["rows"]
            error = _cache["error"]

    if error:
        return jsonify({"status": "error", "message": error}), 200
    if not rows:
        return jsonify({"status": "no_data"}), 200
    return jsonify({"status": "ok", "data": rows}), 200


# ── 파서 ──────────────────────────────────────────────────────────

def parse(html: bytes) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser", from_encoding="euc-kr")

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
        break

    return rows


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
