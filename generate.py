#!/usr/bin/env python3
"""밴드 활동 대시보드 정적 페이지 생성기.

구글 캘린더 공개 ical(환경변수 ICAL_URL)을 내려받아 팀 이벤트를 분류하고,
setlists.json의 셋리스트를 붙여 template.html에 데이터를 주입한 뒤
dist/index.html 을 만든다. GitHub Actions에서 매일 실행됨.

이벤트 제목 규칙: "[팀명] 합주|공연|연습|모임|집회|예배|캠프|..."
[취소] 가 붙으면 무시. 팀 구분: 집회/예배/캠프/수련회 이벤트가 있으면 찬양팀.
"""
import hashlib
import html
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
CSS_VER = hashlib.md5((ROOT / "static" / "base.css").read_bytes()).hexdigest()[:8]  # 캐시 무효화용

REHEARSAL_KW = ("합주", "연습", "모임", "리허설", "OT")
GIG_KW = ("공연", "버스킹")
WORSHIP_KW = ("집회", "예배", "캠프", "수련회")

EXCLUDE = {"에덴워십", "j-pop"}          # 팀으로 집계하지 않는 이름 (소문자)
IGNORE_TITLE_KW = ("정기예배", "정기 예배")  # 이벤트 자체를 집계 제외

WD = "월화수목금토일"


CANON = {  # 표기 흔들림 보정 (소문자 키)
    "범의테두리": "법의테두리",
    "j-pop": "J-Pop",
    "201p": "201P",
    "차수": "차슈",
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
        for key in ("DTSTART", "DTEND", "SUMMARY", "LOCATION"):
            m = re.search(rf"^{key}[^:]*:(.*)$", block, re.M)
            if m:
                ev[key] = m.group(1).strip().replace("\\n", " ").replace("\\,", ",")
        if "DTSTART" in ev and "SUMMARY" in ev:
            events.append(ev)
    return events


def parse_date(dtstart: str):
    """YYYYMMDD / YYYYMMDDTHHMMSS(Z). UTC(Z)면 KST(+9)로 바꾼 뒤 날짜 취함."""
    m = re.match(r"(\d{8})(?:T(\d{6})(Z?))?", dtstart)
    if not m:
        return None
    dt = datetime.strptime(m.group(1) + (m.group(2) or "000000"), "%Y%m%d%H%M%S")
    if m.group(3):
        dt += timedelta(hours=9)
    return dt.date()


def parse_time(dtstart: str) -> str:
    """시각 있는 이벤트면 KST "HH:MM", 종일이면 ""."""
    m = re.match(r"\d{8}T(\d{6})(Z?)", dtstart)
    if not m:
        return ""
    dt = datetime.strptime(m.group(1), "%H%M%S")
    if m.group(2):
        dt += timedelta(hours=9)
    return dt.strftime("%H:%M")


def parse_end(ev):
    """다일 이벤트의 마지막 날. 하루짜리면 None. 종일 DTEND는 exclusive라 하루 뺌."""
    start, end = parse_date(ev["DTSTART"]), parse_date(ev.get("DTEND", ""))
    if end and "T" not in ev["DTSTART"]:
        end -= timedelta(days=1)
    return end if end and start and end > start else None


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
            "date": d, "t": parse_time(ev["DTSTART"]), "end": parse_end(ev), "bands": bands, "kind": kind,
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
                "date": d, "t": parse_time(ev["DTSTART"]), "end": parse_end(ev), "bands": [matched], "kind": "gig",
                "title": s.strip(), "loc": ev.get("LOCATION", "").split("|")[0].strip(),
            })
        else:
            watch.append({"date": d, "title": s.strip()})

    team_events.sort(key=lambda e: (e["date"], e["t"]))
    watch.sort(key=lambda e: e["date"])
    return team_events, watch


def yt_id(url: str):
    m = re.search(r"(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/))([\w-]{6,})", url)
    return m.group(1) if m else None


def video_embed(url: str):
    """영상 URL → {"src": iframe 주소, "url": 원본}. 모르는 호스트면 {"url"} (링크만)."""
    if (v := yt_id(url)):
        return {"src": f"https://www.youtube-nocookie.com/embed/{v}", "url": url}
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:.*&)?id=)([\w-]+)", url)
    if m:
        return {"src": f"https://drive.google.com/file/d/{m.group(1)}/preview", "url": url}
    return {"url": url}


def show_key(e):
    slug = re.sub(r"[^\w가-힣-]", "", "-".join(e["bands"]).lower().replace(" ", "-"))
    return f"{e['date']:%Y-%m-%d}-{slug}"


def build_data(team_events, watch, setlists, today):
    cur = today.year
    iso = lambda d: d.strftime("%Y-%m-%d")

    def find_setlist(date_s, bands):
        for sl in setlists:
            if sl["date"] == date_s and (sl.get("band") in bands or not sl.get("band")):
                return sl
        return None

    # SHOWS: 모든 연도의 비합주 이벤트 1건씩 + 이벤트→key 맵 (대시보드 링크용)
    shows, seen = [], {}
    for e in team_events:
        if e["kind"] == "rehearsal":
            continue
        k = base = show_key(e)
        seen[base] = seen.get(base, 0) + 1
        if seen[base] > 1:
            k = f"{base}-{seen[base]}"
        e["key"] = k
        sl = find_setlist(iso(e["date"]), e["bands"]) or {}
        v = sl.get("video") or []
        show = {"key": k, "d": iso(e["date"]), "band": " · ".join(e["bands"]),
                "bands": e["bands"], "title": e["title"], "loc": e["loc"],
                "w": e["kind"] == "worship",
                "videos": [video_embed(u) for u in ([v] if isinstance(v, str) else v)],
                "memo": sl.get("memo", "")}
        if e["end"]:
            show["e"] = iso(e["end"])
        if "sets" in sl:
            show["sets"] = sl["sets"]
        elif "songs" in sl:
            show["sets"] = [{"team": sl.get("band", ""), "songs": sl["songs"]}]
        shows.append(show)

    # STAGE: 올해 공연 + 집회·예배
    stage = [x for x in shows if x["d"].startswith(str(cur))]

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
            "d": iso(e["date"]), "t": e["t"], "band": " · ".join(e["bands"]),
            "kind": e["title"], "loc": e["loc"],
            **({"e": iso(e["end"])} if e["end"] else {}),
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
                    gig = {"d": iso(e["date"]), "loc": " · ".join(label_parts), "u": e["key"]}
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
        "sched": sched, "bands": bands_by_year, "yearly": yearly, "shows": shows,
    }


def render(data) -> str:
    j = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
    block = f"""  const DATA_ASOF = {j(data['asof'])};
  const WD = {j(WD)};
  const STAGE = {j(data['stage'])};
  const WATCHING = {j(data['watching'])};
  const SCHED = {j(data['sched'])};
  const BANDS = {j(data['bands'])};
  const YEARLY = {j(data['yearly'])};
"""
    template = (ROOT / "template.html").read_text(encoding="utf-8").replace("base.css\"", f"base.css?v={CSS_VER}\"")
    assert "/*__DATA__*/" in template, "template marker missing"
    return template.replace("/*__DATA__*/", block, 1)


def render_show(show, asof) -> str:
    e = html.escape
    d = datetime.strptime(show["d"], "%Y-%m-%d")
    wd = lambda x: WD[x.weekday()]
    mo = f"{d:%Y.%m} · {wd(d)}요일"
    if show.get("e"):
        d2 = datetime.strptime(show["e"], "%Y-%m-%d")
        mo = f"{d:%Y.%m} · {wd(d)} – {d2:%d}" if d2.month == d.month else f"{d:%Y.%m} · {wd(d)} – {d2:%m.%d}"
        mo += f" {wd(d2)}"
    sub = " · ".join(x for x in (show["title"], show["loc"]) if x)
    left = (d.date() - datetime.strptime(asof, "%Y-%m-%d").date()).days
    dd = f'\n    <div class="dd">{"D-Day" if left == 0 else f"D-{left}"}</div>' if left >= 0 else ""

    ids = []  # 연속 재생 플레이리스트 (곡 렌더 순서)

    def song(s, i):
        pid = yt_id(s.get("yt", ""))
        if pid:
            ids.append(pid)
        yt = (f'<button type="button" class="yt play" data-i="{len(ids) - 1}" '
              f'aria-label="{e(s["t"])} 재생">재생 ▶</button>' if pid else
              f'<a href="{e(s["yt"])}" target="_blank" rel="noopener" class="yt" '
              f'aria-label="{e(s["t"])} 원곡 영상 (새 창)">원곡 ↗</a>' if s.get("yt") else
              f'<a href="https://www.youtube.com/results?search_query={quote(s["t"] + " " + s["a"])}" target="_blank" rel="noopener" class="yt srch" '
              f'aria-label="{e(s["t"])} 유튜브 검색 (새 창)">원곡 🔍</a>')
        return f'<li><span class="no">{i + 1}</span><span>{e(s["t"])}<span class="ar">{e(s["a"])}</span></span>{yt}</li>'

    ul = lambda songs: "<ul>" + "".join(song(s, i) for i, s in enumerate(songs)) + "</ul>"
    sets = show.get("sets", [])
    n = sum(len(t["songs"]) for t in sets)
    sl = "".join((f'<div class="tm">{e(t["team"])} · {len(t["songs"])}곡</div>' if len(sets) > 1 else "")
                 + ul(t["songs"]) for t in sets)

    body = f"""<div class="next{'' if show['w'] else ' gig'}">
    <div class="big">{d.day}</div>
    <div>
      <div class="mo">{mo}</div>
      <h1 class="band">{e(show['band'])}</h1>
      <div class="what">{e(sub)}</div>
    </div>{dd}
  </div>"""
    joint = len(show["bands"]) > 1
    flat = [x for t in sets for x in t["songs"]]
    btn = '' if joint else '<button class="btn sm" id="editBtn">편집</button>'
    vid = '\n  <div id="video">'
    if show["videos"] or not joint:
        vid += "\n  <h2>공연 영상</h2>"
        if not show["videos"]:
            vid += ('\n  <div class="cta sm"><p>공연 영상 없음</p>'
                     '<button class="btn" id="addVideo">영상 추가</button></div>')
        for v in show["videos"]:
            if "src" in v:
                vid += (f'\n  <iframe class="vid" src="{e(v["src"])}" loading="lazy" allowfullscreen '
                         'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"></iframe>')
            else:
                host = urlparse(v["url"]).netloc
                vid += (f'\n  <p><a class="out" href="{e(v["url"])}" target="_blank" rel="noopener">'
                         f'영상 열기 ↗ · {e(host)}</a></p>')
    body += vid + '</div>'
    ph = '\n  <div id="photos">'
    if not joint:  # 목록은 로드 시 Worker에서 가져옴 (JS가 채움)
        ph += '<h2>사진 <span id="pst">불러오는 중…</span></h2>'
    body += ph + '</div>'
    body += f"\n  <h2>Setlist<span id=\"hr\">{f'{n}곡 {btn}' if n else '' if joint else '0곡'}</span></h2>\n  <div id=\"view\">"
    if n:
        body += f'<div class="setl">{sl}</div>'
    elif joint:
        body += '<div class="empty">셋리스트 미등록 (합동 공연은 setlists.json에서 편집)</div>'
    else:
        body += ('<div class="cta"><p>셋리스트가 아직 없음</p>'
                 '<button class="btn primary" id="editBtn">셋리스트 만들기</button></div>')
    body += "</div>"
    body += '\n  <div id="memo">'
    if show["memo"]:
        body += f'<h2>Memo</h2>\n  <p style="white-space:pre-line">{e(show["memo"])}</p>'
    body += '</div>'
    body += (f'\n  <footer class="foot"><span>Generated {e(asof)}</span>'
             '<a href="../../#shows">← Band Calendar</a></footer>')
    page = (ROOT / "show.html").read_text(encoding="utf-8").replace("base.css\"", f"base.css?v={CSS_VER}\"")
    title = e(f"{show['band']} · {show['d']}")
    ogt = e(f"{show['band']} · {d:%Y.%m.%d}")
    ogd = e(" · ".join(x for x in (show["title"], show["loc"], f"{n}곡" if n else "") if x))
    return (page.replace("<!--__TITLE__-->", title).replace("<!--__OGT__-->", ogt)
            .replace("<!--__OGD__-->", ogd).replace("<!--__BODY__-->", body)
            .replace("/*__IDS__*/[]", json.dumps(ids))
            .replace("/*__SHOW__*/{}", json.dumps({
                "d": show["d"], "band": show["band"], "joint": joint, "w": show["w"], "songs": flat,
                "video": [v["url"] for v in show["videos"]], "memo": show["memo"]},
                ensure_ascii=False).replace("</", "<\\/")))


def main():
    url = os.environ.get("ICAL_URL")
    if not url:
        print("FAIL: ICAL_URL env var not set", file=sys.stderr)
        sys.exit(1)
    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    today = kst_now.date()

    events = fetch_events(url)
    setlists = json.loads((ROOT / os.environ.get("SETLISTS_PATH", "setlists.json")).read_text(encoding="utf-8"))
    team_events, watch = classify(events)
    data = build_data(team_events, watch, setlists, today)

    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    (out / "index.html").write_text(render(data), encoding="utf-8")
    shutil.copytree(ROOT / "static", out, dirs_exist_ok=True)
    for sh in data["shows"]:
        p = out / "shows" / sh["key"]
        p.mkdir(parents=True, exist_ok=True)
        (p / "index.html").write_text(render_show(sh, data["asof"]), encoding="utf-8")
    print(f"OK {today}: stage {len(data['stage'])}, shows {len(data['shows'])}, sched {len(data['sched'])}, "
          f"years {[r['y'] for r in data['yearly']]} → dist/index.html")


if __name__ == "__main__":
    main()
