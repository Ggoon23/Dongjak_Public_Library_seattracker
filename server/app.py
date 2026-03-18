import base64
import csv
import io
import os
from datetime import datetime, timezone, timedelta

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from bs4 import BeautifulSoup
from flask import Flask, jsonify

app = Flask(__name__)

KST         = timezone(timedelta(hours=9))
LIB_URL     = "http://djlib-seat.sen.go.kr/domian5.php"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")   # "유저명/레포명"
CSV_PATH     = "data/seats.csv"
FIELDNAMES   = ["collected_at", "room_name", "total_seats", "used_seats", "waiting"]
LIB_HEADERS  = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ── 스크래핑 ──────────────────────────────────────────────────────

def scrape() -> list[dict]:
    resp = requests.get(LIB_URL, headers=LIB_HEADERS, timeout=20)
    resp.raise_for_status()
    return parse(resp.content)


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

    # 열람실별 대기자수 카운트 ("대기자 리스트" 테이블 — "호출 대기 리스트"와 구분)
    waiting_per_room: dict[str, int] = {}
    for table in soup.find_all("table"):
        if "대기자 리스트" not in table.get_text() or "호출" in table.get_text():
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            texts = [td.get_text(strip=True) for td in tds]
            if not texts[0].isdigit():
                continue
            room = texts[1]
            waiting_per_room[room] = waiting_per_room.get(room, 0) + 1
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
                total_seats = int(texts[2])
                used_seats  = int(texts[3])
            except ValueError:
                continue
            room_name = texts[1]
            rows.append({
                "collected_at": collected_at,
                "room_name":    room_name,
                "total_seats":  total_seats,
                "used_seats":   used_seats,
                "waiting":      waiting_per_room.get(room_name, 0),
            })
        break

    return rows


# ── GitHub API 커밋 ───────────────────────────────────────────────

def commit_to_github(new_rows: list[dict]):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("[WARN] GITHUB_TOKEN 또는 GITHUB_REPO 환경변수 없음")
        return

    gh_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CSV_PATH}"

    resp = requests.get(url, headers=gh_headers, timeout=10)

    if resp.status_code == 200:
        file_data = resp.json()
        sha = file_data["sha"]
        current = base64.b64decode(file_data["content"]).decode("utf-8")
    elif resp.status_code == 404:
        sha = None
        current = ",".join(FIELDNAMES) + "\n"
    else:
        resp.raise_for_status()

    # 중복 제거
    existing = set()
    for row in csv.DictReader(io.StringIO(current)):
        existing.add((row["collected_at"], row["room_name"]))

    to_add = [r for r in new_rows
              if (r["collected_at"], r["room_name"]) not in existing]

    if not to_add:
        print(f"[SKIP] 중복 데이터 ({new_rows[0]['collected_at']})")
        return

    # CSV append
    if not current.endswith("\n"):
        current += "\n"
    for r in to_add:
        current += f"{r['collected_at']},{r['room_name']},{r['total_seats']},{r['used_seats']},{r['waiting']}\n"

    ts = to_add[0]["collected_at"]
    payload = {
        "message": f"data: collect {ts} ({len(to_add)} rows)",
        "content": base64.b64encode(current.encode("utf-8")).decode(),
    }
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers, json=payload, timeout=10).raise_for_status()
    print(f"[OK] {len(to_add)}행 커밋 완료 ({ts})")


# ── 스케줄 작업 ───────────────────────────────────────────────────

def collect_job():
    try:
        rows = scrape()
        if rows:
            commit_to_github(rows)
    except Exception as e:
        print(f"[ERROR] collect_job: {e}")


scheduler = BackgroundScheduler(timezone=KST)
# 08:00~17:50 KST — 매 10분
scheduler.add_job(collect_job, CronTrigger(hour="8-17", minute="*/10", timezone=KST))
# 18:00 KST
scheduler.add_job(collect_job, CronTrigger(hour="18", minute="0", timezone=KST))
scheduler.start()


# ── 엔드포인트 ────────────────────────────────────────────────────

@app.route("/status")
def status():
    return jsonify({"status": "ok"})


@app.route("/collect")
def collect():
    """수동 즉시 수집 (테스트용)"""
    try:
        rows = scrape()
        if not rows:
            return jsonify({"status": "no_data"}), 200
        commit_to_github(rows)
        return jsonify({"status": "ok", "rows": len(rows)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200


@app.route("/debug")
def debug():
    """파싱 결과 확인용 (커밋 없음)"""
    try:
        rows = scrape()
        return jsonify({"status": "ok", "data": rows}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
