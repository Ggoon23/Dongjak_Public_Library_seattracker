import base64
import csv
import io
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from bs4 import BeautifulSoup
from flask import Flask, jsonify, send_file

app = Flask(__name__)

KST          = timezone(timedelta(hours=9))
LIB_URL      = "http://djlib-seat.sen.go.kr/domian5.php"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")   # "유저명/레포명"
CSV_PATH     = "data/seats.csv"
FIELDNAMES   = ["collected_at", "room_name", "total_seats", "used_seats", "waiting"]
COLLECT_OPEN_HOUR  = 7   # 수집 시작 시각 (KST)
COLLECT_CLOSE_HOUR = 22  # 수집 종료 시각 (KST, 미만)

# DoS 방어: /collect, /debug 수동 엔드포인트 쿨다운
_last_manual_trigger: float = 0
_MANUAL_COOLDOWN = 60  # 초
LOCAL_CSV    = Path(__file__).parent.parent / CSV_PATH
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

    # 대기자 리스트 ("호출 대기 리스트"와 구분)
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

    # 호출 대기 리스트 (호출됐지만 아직 착석 전 — 실질적 이용자로 간주)
    called_per_room: dict[str, int] = {}
    for table in soup.find_all("table"):
        if "호출 대기 리스트" not in table.get_text():
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            texts = [td.get_text(strip=True) for td in tds]
            if not texts[0].isdigit():
                continue
            room = texts[1]
            called_per_room[room] = called_per_room.get(room, 0) + 1
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
            room_name  = texts[1]
            called     = called_per_room.get(room_name, 0)
            used_seats = min(used_seats + called, total_seats)
            rows.append({
                "collected_at": collected_at,
                "room_name":    room_name,
                "total_seats":  total_seats,
                "used_seats":   used_seats,
                "waiting":      waiting_per_room.get(room_name, 0),
            })
        break

    return rows


# ── 로컬 CSV ──────────────────────────────────────────────────────

def init_local_csv():
    """서버 시작 시 GitHub에서 최신 CSV를 로컬로 pull"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        # 환경변수 없으면 로컬 파일만 사용
        if not LOCAL_CSV.exists():
            LOCAL_CSV.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_CSV.write_text(",".join(FIELDNAMES) + "\n", encoding="utf-8")
        return

    gh_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CSV_PATH}"
    try:
        resp = requests.get(url, headers=gh_headers, timeout=10)
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()["content"]).decode("utf-8")
            LOCAL_CSV.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_CSV.write_text(content, encoding="utf-8")
            print("[INIT] GitHub에서 CSV pull 완료")
        elif resp.status_code == 404:
            LOCAL_CSV.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_CSV.write_text(",".join(FIELDNAMES) + "\n", encoding="utf-8")
            print("[INIT] GitHub에 CSV 없음 — 빈 파일 생성")
        else:
            print(f"[WARN] GitHub pull 실패 ({resp.status_code})")
    except Exception as e:
        print(f"[WARN] GitHub pull 오류: {e}")


def append_to_local_csv(new_rows: list[dict]):
    """중복 없이 로컬 CSV에 append"""
    existing = set()
    if LOCAL_CSV.exists():
        with LOCAL_CSV.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing.add((row["collected_at"], row["room_name"]))

    to_add = [r for r in new_rows
              if (r["collected_at"], r["room_name"]) not in existing]
    if not to_add:
        print(f"[SKIP] 중복 데이터 ({new_rows[0]['collected_at']})")
        return

    with LOCAL_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerows(to_add)
    print(f"[LOCAL] {len(to_add)}행 로컬 저장 ({to_add[0]['collected_at']})")


# ── GitHub API 커밋 ───────────────────────────────────────────────

def commit_to_github():
    """로컬 CSV 전체를 GitHub에 커밋"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("[WARN] GITHUB_TOKEN 또는 GITHUB_REPO 환경변수 없음")
        return
    if not LOCAL_CSV.exists():
        print("[WARN] 로컬 CSV 없음 — 커밋 스킵")
        return

    gh_headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CSV_PATH}"

    sha = None
    resp = requests.get(url, headers=gh_headers, timeout=10)
    if resp.status_code == 200:
        sha = resp.json()["sha"]
    elif resp.status_code != 404:
        resp.raise_for_status()

    content = LOCAL_CSV.read_text(encoding="utf-8")
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    payload = {
        "message": f"data: hourly commit {ts}",
        "content": base64.b64encode(content.encode("utf-8")).decode(),
    }
    if sha:
        payload["sha"] = sha

    requests.put(url, headers=gh_headers, json=payload, timeout=10).raise_for_status()
    print(f"[OK] GitHub 커밋 완료 ({ts})")


# ── 스케줄 작업 ───────────────────────────────────────────────────

def collect_job():
    try:
        now_hour = datetime.now(KST).hour
        if not (COLLECT_OPEN_HOUR <= now_hour < COLLECT_CLOSE_HOUR):
            return
        rows = scrape()
        if rows:
            append_to_local_csv(rows)
    except Exception as e:
        print(f"[ERROR] collect_job: {e}")


def hourly_commit_job():
    try:
        commit_to_github()
    except Exception as e:
        print(f"[ERROR] hourly_commit_job: {e}")


init_local_csv()

scheduler = BackgroundScheduler(timezone=KST)
# 07:00~21:50 KST — 매 10분 수집
scheduler.add_job(collect_job, CronTrigger(hour="7-21", minute="*/10", timezone=KST))
# 매 정시 GitHub 커밋 (07~22시)
scheduler.add_job(hourly_commit_job, CronTrigger(hour="7-22", minute="0", timezone=KST))
scheduler.start()


# ── 엔드포인트 ────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    """좌석 현황 대시보드"""
    return send_file(Path(__file__).parent.parent / "data" / "seats_dashboard.html")


@app.route("/api/seats")
def seats():
    """GitHub(최신) + 로컬(미커밋 신규분) 병합 후 JSON 반환"""
    rows_by_key = {}

    # 1) GitHub에서 커밋된 데이터 로드
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            gh_headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            }
            url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{CSV_PATH}"
            resp = requests.get(url, headers=gh_headers, timeout=10)
            if resp.status_code == 200:
                content = base64.b64decode(resp.json()["content"]).decode("utf-8")
                for row in csv.DictReader(io.StringIO(content)):
                    key = (row["collected_at"], row["room_name"])
                    rows_by_key[key] = {
                        "collected_at": row["collected_at"],
                        "room_name":    row["room_name"],
                        "total_seats":  int(row["total_seats"]),
                        "used_seats":   int(row["used_seats"]),
                        "waiting":      int(row["waiting"]),
                    }
        except Exception as e:
            print(f"[WARN] /api/seats GitHub 로드 실패: {e}")

    # 2) 로컬 파일에서 미커밋 신규 데이터 추가
    if LOCAL_CSV.exists():
        with LOCAL_CSV.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row["collected_at"], row["room_name"])
                if key not in rows_by_key:
                    rows_by_key[key] = {
                        "collected_at": row["collected_at"],
                        "room_name":    row["room_name"],
                        "total_seats":  int(row["total_seats"]),
                        "used_seats":   int(row["used_seats"]),
                        "waiting":      int(row["waiting"]),
                    }

    if not rows_by_key:
        return jsonify({"status": "error", "message": "데이터 없음"}), 404

    result = sorted(rows_by_key.values(), key=lambda r: r["collected_at"])
    return jsonify(result)


@app.route("/status")
def status():
    return jsonify({"status": "ok"})


@app.route("/collect")
def collect():
    """수동 즉시 수집 (테스트용)"""
    global _last_manual_trigger
    now = time.time()
    if now - _last_manual_trigger < _MANUAL_COOLDOWN:
        retry = int(_MANUAL_COOLDOWN - (now - _last_manual_trigger))
        return jsonify({"status": "rate_limited", "retry_after": retry}), 429
    _last_manual_trigger = now
    try:
        rows = scrape()
        if not rows:
            return jsonify({"status": "no_data"}), 200
        append_to_local_csv(rows)
        return jsonify({"status": "ok", "rows": len(rows)}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200


@app.route("/debug")
def debug():
    """파싱 결과 확인용 (저장 없음)"""
    global _last_manual_trigger
    now = time.time()
    if now - _last_manual_trigger < _MANUAL_COOLDOWN:
        retry = int(_MANUAL_COOLDOWN - (now - _last_manual_trigger))
        return jsonify({"status": "rate_limited", "retry_after": retry}), 429
    _last_manual_trigger = now
    try:
        rows = scrape()
        return jsonify({"status": "ok", "data": rows}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)
