#!/usr/bin/env python3
"""밴드 활동 대시보드 정적 페이지 생성기.

구글 캘린더 공개 ical(환경변수 ICAL_URL)을 내려받아 팀 이벤트를 분류하고,
setlists.json의 셋리스트를 붙여 template.html에 데이터를 주입한 뒤
dist/index.html 을 만든다. GitHub Actions에서 매일 실행됨.

이벤트 제목 규칙: "[팀명] 합주|공연|연습|모임|집회|예배|캠프|..."
[취소] 가 붙으면 무시. 팀 구분: 집회/예배/캠프/수련회 이벤트가 있으면 찬양팀.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent

REHEARSAL_KW = ("합주", "연습", "모임", "리허설", "OT")
GIG_KW = ("공연", "버스킹")
WORSHIP_KW = ("집회", "예배", "캠프", "수련회")

EXCLUDE = {"에덴워십", "j-pop"}          # 팀으로 집계하지 않는 이름 (소문자)
IGNORE_TITLE_KW = ("정기예배", "정기 예배")  # 이벤트 자체를 집계 제외

CANON = {  # 표기 흔들림 보정 (소문자 키)
    "범의테두리": "법의테두리",
    "j-pop": "J-Pop",
    "201p": "201P",
}


def canon(name: str) -> str:
    name = name.strip()
    return CANON.get(name.lower(), name)


def fetch_events(url: str):
    raw = urllib.request.urlopen(url, timeout=60).read().decode("utf-8")
    raw = raw.replace("\r\n ", "").replace("\r\n\t", "")
    events = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S):
        ev = {}
        for key in ("DTSTART", "SUMMARY", "LOCATION"):
            m = re.search(rf"^{key}[^:]*:(.*)$", block, re.M)
            if m:
                ev[key] = m.group(1).strip().replace("\\n", " ").replace("\\,", ",")
        if "DTSTART" in ev and "SUMMARY" in ev:
            events.append(ev)
    return events


def parse_date(dtstart: str):
    m = re.match(r"(\d{8})", dtstart)
    return datetime.strptime(m.group(1), "%Y%m%d").date() if m else None


def event_kind(title: str):
    if any(k in title for k in GIG_KW):
        return "gig"
    if any(k in title for k in WORSHIP_KW):
        return "worship"
    if any(k in title for k in REHEARSAL_KW):
        return "rehearsal"
    return None


def classify(events):
    team_events, known = [], set()
    for ev in events:
        s = ev["SUMMARY"]
        d = parse_date(ev["DTSTART"])
        if d is None or "[취소]" in s or ":취소" in s:
            continue
        m = re.match(r"\[([^\]]+)\]\s*(.+)", s)
        if not m:
            continue
        bands = [canon(b) for b in m.group(1).split(",")
                 if canon(b).lower() not in EXCLUDE]
        if not bands or any(k in m.group(2) for k in IGNORE_TITLE_KW):
            continue
        kind = event_kind(m.group(2))
        if kind is None:
            continue
        team_events.append({
            "date": d, "bands": bands, "kind": kind,
            "title": m.group(2).strip(),
            "loc": ev.get("LOCATION", "").split("|")[0].strip(),
        })
        known.update(bands)

    watch = []
    for ev in events:
        s = ev["SUMMARY"]
        d = parse_date(ev["DTSTART"])
        if d is None or s.startswith("[") or "공연" not in s:
            continue
        prefix = s.split("공연")[0].strip()
        matched = next((b for b in known if b and b in prefix), None)
        if matched:
            team_events.append({
                "date": d, "bands": [matched], "kind": "gig",
                "title": s.strip(), "loc": ev.get("LOCATION", "").split("|")[0].strip(),
            })
        else:
            watch.append({"date": d, "title": s.strip()})

    team_events.sort(key=lambda e: e["date"])
    watch.sort(key=lambda e: e["date"])
    return team_events, watch


def build_data(team_events, watch, setlists, today):
    cur = today.year
    iso = lambda d: d.strftime("%Y-%m-%d")

    def find_setlist(date_s, bands):
        for sl in setlists:
            if sl["date"] == date_s and (sl.get("band") in bands or not sl.get("band")):
                return sl
        return None

    # STAGE: 올해 공연 + 집회·예배
    stage = []
    for e in team_events:
        if e["date"].year != cur or e["kind"] == "rehearsal":
            continue
        row = {
            "d": iso(e["date"]),
            "band": " · ".join(e["bands"]),
            "title": e["title"],
            "loc": e["loc"],
        }
        if e["kind"] == "worship":
            row["w"] = True
        sl = find_setlist(row["d"], e["bands"])
        if sl:
            if "sets" in sl:
                row["sets"] = sl["sets"]
            else:
                row["setlist"] = sl["songs"]
        stage.append(row)

    # WATCHING: 다가오는 관람 공연
    watching = [{"d": iso(w["date"]), "title": w["title"] + " (관람)"}
                for w in watch if w["date"] >= today]

    # SCHED: 2주 이내 전체 일정
    horizon = today + timedelta(days=14)
    sched = []
    for e in team_events:
        if not (today <= e["date"] <= horizon):
            continue
        sched.append({
            "d": iso(e["date"]), "band": " · ".join(e["bands"]),
            "kind": e["title"], "loc": e["loc"],
            **({"w": True} if e["kind"] == "worship" else {}),
            **({"g": True} if e["kind"] == "gig" else {}),
        })

    # BANDS / YEARLY
    years = sorted({e["date"].year for e in team_events}, reverse=True)
    bands_by_year, yearly = {}, []
    for y in years:
        evs = [e for e in team_events if e["date"].year == y]
        wt = {b for e in evs if e["kind"] == "worship" for b in e["bands"]}
        per = {}
        for e in evs:
            for b in e["bands"]:
                t = per.setdefault(b, {"r": 0, "gigs": []})
                if e["kind"] == "rehearsal":
                    t["r"] += 1
                else:
                    label_parts = [e["loc"]] if e["loc"] else []
                    if e["title"] not in ("공연", "집회"):
                        label_parts.append(e["title"])
                    gig = {"d": iso(e["date"]), "loc": " · ".join(label_parts)}
                    if e["kind"] == "worship":
                        gig["k"] = "집회" if "집회" in e["title"] else ("캠프" if "캠프" in e["title"] else "예배")
                    t["gigs"].append(gig)
        rows = []
        for name, t in per.items():
            row = {"name": name, "r": t["r"], "gigs": t["gigs"]}
            if name in wt:
                row["w"] = True
            rows.append(row)
        rows.sort(key=lambda b: (bool(b.get("w")), -(b["r"] + len(b["gigs"]))))
        bands_by_year[y] = rows
        yearly.append({
            "y": y,
            "bands": len({b for e in evs for b in e["bands"]} - wt),
            "gigs": len([e for e in evs if e["kind"] == "gig"]),
            "rehearsals": len([e for e in evs if e["kind"] == "rehearsal"]),
        })
    yearly.sort(key=lambda r: r["y"])

    return {
        "asof": iso(today), "stage": stage, "watching": watching,
        "sched": sched, "bands": bands_by_year, "yearly": yearly,
    }


def render(data) -> str:
    j = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
    block = f"""  const DATA_ASOF = {j(data['asof'])};
  const WD = "월화수목금토일";
  const STAGE = {j(data['stage'])};
  const WATCHING = {j(data['watching'])};
  const SCHED = {j(data['sched'])};
  const BANDS = {j(data['bands'])};
  const YEARLY = {j(data['yearly'])};
"""
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    assert "/*__DATA__*/" in template, "template marker missing"
    return template.replace("/*__DATA__*/", block, 1)


def main():
    url = os.environ.get("ICAL_URL")
    if not url:
        print("FAIL: ICAL_URL env var not set", file=sys.stderr)
        sys.exit(1)
    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    today = kst_now.date()

    events = fetch_events(url)
    setlists = json.loads((ROOT / "setlists.json").read_text(encoding="utf-8"))
    team_events, watch = classify(events)
    data = build_data(team_events, watch, setlists, today)

    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(render(data), encoding="utf-8")
    print(f"OK {today}: stage {len(data['stage'])}, sched {len(data['sched'])}, "
          f"years {[r['y'] for r in data['yearly']]} → dist/index.html")


if __name__ == "__main__":
    main()
