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
CSS_VER = hashlib.md5(b"".join((ROOT / "static" / f).read_bytes() for f in ("base.css", "photos.js"))).hexdigest()[:8]  # 캐시 무효화용 (css+js 하나로)

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


def band_slug(name: str) -> str:
    return re.sub(r"[^\w가-힣-]", "", name.lower().replace(" ", "-"))


def show_key(e):
    return f"{e['date']:%Y-%m-%d}-{band_slug('-'.join(e['bands']))}"


def worship_label(title: str) -> str:
    return "집회" if "집회" in title else ("캠프" if "캠프" in title else "예배")


def tpl(name: str) -> str:
    """템플릿 읽고 정적 자산 URL에 캐시 버스터 부여."""
    return ((ROOT / name).read_text(encoding="utf-8")
            .replace('base.css"', f'base.css?v={CSS_VER}"').replace('photos.js"', f'photos.js?v={CSS_VER}"'))


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
        v = sl.get("video") or ""
        if isinstance(v, list):  # 레거시: 첫 항목만
            if len(v) > 1:
                print(f"경고: {iso(e['date'])} {' · '.join(e['bands'])} video 배열 {len(v)}개 → 첫 항목만 사용", file=sys.stderr)
            v = v[0] if v else ""
        show = {"key": k, "d": iso(e["date"]), "band": " · ".join(e["bands"]),
                "bands": e["bands"], "slugs": [band_slug(b) for b in e["bands"]],
                "title": e["title"], "loc": e["loc"], "t": e["t"],
                "w": e["kind"] == "worship",
                "video": video_embed(v) if v else None,
                "memo": sl.get("memo", "")}
        if e["end"]:
            show["e"] = iso(e["end"])
        if "sets" in sl:
            show["sets"] = sl["sets"]
        elif "songs" in sl:
            show["sets"] = [{"team": sl.get("band", ""), "songs": sl["songs"]}]
        shows.append(show)

    # 이전/다음 공연 (날짜순; 같은 날은 입력 순서 유지)
    ordered = sorted(shows, key=lambda x: x["d"])
    ref = lambda x: {"key": x["key"], "d": x["d"], "band": x["band"]} if x else None
    for i, x in enumerate(ordered):
        x["prev"] = ref(ordered[i - 1] if i else None)
        x["next"] = ref(ordered[i + 1] if i + 1 < len(ordered) else None)

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
                        gig["k"] = worship_label(e["title"])
                    t["gigs"].append(gig)
        rows = []
        for name, t in per.items():
            row = {"name": name, "slug": band_slug(name), "r": t["r"], "gigs": t["gigs"]}
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

    # BANDS_PAGES: 팀별 상세 페이지 데이터 (합동 공연은 양쪽 팀에 포함)
    show_by_key = {x["key"]: x for x in shows}
    pages = []
    for name in sorted({b for e in team_events for b in e["bands"]}):
        mine = [e for e in team_events if name in e["bands"]]
        evs, rep_, vc, gc = [], {}, {}, {}
        st = {"reh_year": 0, "reh_all": 0, "gig_year": 0, "gig_all": 0, "gig_planned": 0}
        next_gig = next_reh = last_reh = None
        for e in mine:  # team_events는 날짜 오름차순
            n_songs = 0
            dd = {"d": iso(e["date"]), "days": (e["date"] - today).days}
            if e["kind"] == "rehearsal":
                if e["date"] <= today:
                    last_reh = {**dd, "days": (today - e["date"]).days}
                    v = vc.setdefault(e["loc"], {"loc": e["loc"] or "장소 미정", "raw": e["loc"], "n": 0, "cur": 0})
                    v["n"] += 1
                    v["cur"] += e["date"].year == cur
                    v["last"] = dd["d"]
                    st["reh_all"] += 1
                    st["reh_year"] += e["date"].year == cur
                elif next_reh is None:
                    next_reh = dd
            else:
                if e["date"] <= today:
                    st["gig_all"] += 1
                    st["gig_year"] += e["date"].year == cur
                    g = gc.setdefault(e["loc"], {"loc": e["loc"] or "장소 미정", "n": 0, "cur": 0})
                    g["n"] += 1
                    g["cur"] += e["date"].year == cur
                    g["last"], g["key"] = dd["d"], e["key"]
                else:
                    st["gig_planned"] += 1
                if next_gig is None and e["date"] >= today:
                    next_gig = {**dd, "key": e["key"], "title": e["title"], "w": e["kind"] == "worship"}
                sets = show_by_key[e["key"]].get("sets", [])
                for s in sets:
                    if len(sets) > 1 and s["team"].strip().lower() != name.lower():
                        continue
                    for song in s["songs"]:
                        n_songs += 1
                        r = rep_.setdefault(song["t"].strip().lower(), {"t": song["t"], "a": song["a"], "yt": "", "n": 0})
                        r["n"] += 1
                        r["yt"] = song.get("yt") or r["yt"]
                        r["last"], r["last_key"] = iso(e["date"]), e["key"]
            ev = {"d": iso(e["date"]), "t": e["t"], "kind": e["kind"], "title": e["title"], "loc": e["loc"], "songs": n_songs}
            if e["kind"] != "rehearsal":
                ev["key"] = e["key"]
            evs.append(ev)
        srt = lambda d: sorted(d.values(), key=lambda v: (v["n"], v["last"]), reverse=True)
        venues = srt(vc)
        st["venues_reh"] = [{k: v[k] for k in ("loc", "n", "last", "cur")} for v in venues]
        st["venues_gig"] = [{k: v[k] for k in ("loc", "n", "last", "cur", "key")} for v in srt(gc)]
        pages.append({
            "name": name, "slug": band_slug(name), "w": any(e["kind"] == "worship" for e in mine),
            "events": sorted(evs, key=lambda x: (x["d"], x["t"]), reverse=True),
            "repertoire": sorted(rep_.values(), key=lambda r: (r["n"], r["last"]), reverse=True),
            "next_gig": next_gig, "next_reh": next_reh, "last_reh": last_reh,
            "reh_loc": venues[0]["raw"] if venues else "",
            "stats": st,
        })

    return {
        "bands_pages": pages, "asof": iso(today), "stage": stage, "watching": watching,
        "sched": sched, "bands": bands_by_year, "yearly": yearly, "shows": shows,
    }


def render(data) -> str:
    j = lambda v: json.dumps(v, ensure_ascii=False, indent=2)
    block = f"""  const DATA_ASOF = {j(data['asof'])};
  const WD = {j(WD)};
  const STAGE = {j([{k: v for k, v in x.items() if k not in ("prev", "next")} for x in data['stage']])};
  const WATCHING = {j(data['watching'])};
  const SCHED = {j(data['sched'])};
  const BANDS = {j(data['bands'])};
  const YEARLY = {j(data['yearly'])};
"""
    template = tpl("template.html")
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
    if show.get("t"):
        mo += f" · {show['t']}"
    loc = (f'<a href="https://map.naver.com/p/search/{quote(show["loc"], safe="")}" target="_blank" rel="noopener">'
           f'{e(show["loc"])} ↗</a>' if show["loc"] else "")
    sub = " · ".join(x for x in (e(show["title"]), loc) if x)
    left = (d.date() - datetime.strptime(asof, "%Y-%m-%d").date()).days
    upcoming = left >= 0
    dd = f'\n    <div class="dd">{"D-Day" if left == 0 else f"D-{left}"}</div>' if upcoming else ""

    ids = []  # 연속 재생 플레이리스트 (곡 렌더 순서)

    def song(s, i):
        pid = yt_id(s.get("yt", ""))
        if pid:
            ids.append(pid)
        yt = (f'<button type="button" class="yt play" data-i="{len(ids) - 1}" '
              f'aria-label="{e(s["t"])} 재생">재생 ▶</button>' if pid else
              f'<a href="{e(s["yt"])}" target="_blank" rel="noopener" class="yt" '
              f'aria-label="{e(s["t"])} 원곡 영상 (새 창)">링크 ↗</a>' if s.get("yt") else
              f'<a href="https://www.youtube.com/results?search_query={quote(s["t"] + " " + s["a"])}" target="_blank" rel="noopener" class="yt srch" '
              f'aria-label="{e(s["t"])} 유튜브 검색 (새 창)">검색 ↗</a>')
        return f'<li><span class="no">{i + 1}</span><span>{e(s["t"])}<span class="ar">{e(s["a"])}</span></span>{yt}</li>'

    ul = lambda songs: "<ul>" + "".join(song(s, i) for i, s in enumerate(songs)) + "</ul>"
    sets = show.get("sets", [])
    n = sum(len(t["songs"]) for t in sets)
    sl = "".join((f'<div class="tm">{e(t["team"])} · {len(t["songs"])}곡</div>' if len(sets) > 1 else "")
                 + ul(t["songs"]) for t in sets)

    hero = f"""<div class="next{'' if show['w'] else ' gig'}">
    <div class="big">{d.day}</div>
    <div>
      <div class="mo">{mo}</div>
      <h1 class="band">{e(show['band']) if len(show["bands"]) > 1 else f'<a href="../../bands/{band_slug(show["band"])}/">{e(show["band"])}</a>'}</h1>
      <div class="what">{sub}</div>
    </div>{dd}
  </div>"""
    joint = len(show["bands"]) > 1
    btn = '' if joint else '<button class="btn sm" id="editBtn">편집</button>'
    H2 = '<h2 class="ko">'  # 첫 h2는 아래서 .first 부여

    # 공연 영상: 1개 (YouTube는 파사드)
    vid = '\n  <div id="video">'
    v = show["video"]
    if v:
        vid += f"\n  {H2}공연 영상</h2>"
        u = e(v["url"])
        y = yt_id(v["url"])
        if y:
            vid += (f'\n  <button type="button" class="vid yt-fac" data-id="{y}" aria-label="영상 재생" '
                    f'style="background-image:url(https://i.ytimg.com/vi/{y}/hqdefault.jpg)"></button>')
        elif "src" in v:
            vid += (f'\n  <iframe class="vid" src="{e(v["src"])}" loading="lazy" allowfullscreen '
                    'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"></iframe>')
        else:
            vid += f'\n  <p><a class="out" href="{u}" target="_blank" rel="noopener">영상 열기 ↗ · {e(urlparse(v["url"]).netloc)}</a></p>'
    elif not joint:
        vid += (f'\n  {H2}공연 영상 <span><button class="btn sm" id="addVideo">+ 영상</button></span></h2>'
                '\n  <p class="empty">아직 없음</p>')
    vid += '</div>'
    ph = '\n  <div id="photos">'
    if not joint:  # 목록은 로드 시 Worker에서 가져옴 (JS가 채움)
        ph += f'{H2}사진 <span><span id="pst">불러오는 중…</span></span></h2>'
    ph += '</div>'
    playall = '<button class="btn sm" id="playAll">전체 재생 ▶</button>' if ids else ''
    hr = f'{playall}{n}곡 {btn}' if n else '' if joint else '0곡'
    setl = f"\n  {H2}셋리스트<span id=\"hr\">{hr}</span></h2>\n  <div id=\"view\">"
    if n:
        setl += f'<div class="setl">{sl}</div>'
    elif joint:
        setl += '<div class="empty">셋리스트 미등록 (합동 공연은 setlists.json에서 편집)</div>'
    else:
        setl += ('<div class="cta"><p>셋리스트가 아직 없음</p>'
                 '<button class="btn primary" id="editBtn">셋리스트 만들기</button></div>')
    setl += "</div>"
    memo = '\n  <div id="memo">'
    if show["memo"]:
        memo += f'{H2}메모</h2>\n  <p style="white-space:pre-line">{e(show["memo"])}</p>'
    elif upcoming and not joint:
        memo += (f'{H2}메모 <span><button class="btn sm" id="addMemo">+ 메모</button></span></h2>'
                 '\n  <p class="empty">집합 시간, 준비물 등</p>')
    memo += '</div>'
    # B1: 공연 후는 기록(영상·사진) 먼저, 공연 전은 준비(메모·셋리스트) 먼저
    secs = memo + setl + vid + ph if upcoming else vid + ph + setl + memo
    body = hero + secs.replace(H2, '<h2 class="ko first">', 1)

    def pn(x, cls, txt):
        return f'<a class="{cls}" href="../{x["key"]}/">{e(txt)}</a>' if x else ""
    pv, nx = show.get("prev"), show.get("next")
    nav = (pn(pv, "pv", pv and f"← {pv['d'][5:].replace('-', '.')} {pv['band']}")
           + pn(nx, "nx", nx and f"{nx['d'][5:].replace('-', '.')} {nx['band']} →"))
    if nav:
        body += f'\n  <nav class="pn">{nav}</nav>'
    body += (f'\n  <footer class="foot"><span>Generated {e(asof)}</span>'
             '<a href="../../#shows">← Band Calendar</a></footer>')
    flat = [x for t in sets for x in t["songs"]]
    page = tpl("show.html")
    title = e(f"{show['band']} · {show['d']}")
    ogt = e(f"{show['band']} · {d:%Y.%m.%d}")
    ogd = e(" · ".join(x for x in (show["title"], show["loc"], f"{n}곡" if n else "") if x))
    return (page.replace("<!--__TITLE__-->", title).replace("<!--__OGT__-->", ogt)
            .replace("<!--__OGD__-->", ogd).replace("<!--__BODY__-->", body)
            .replace("/*__IDS__*/[]", json.dumps(ids))
            .replace("/*__SHOW__*/{}", json.dumps({
                "d": show["d"], "t": show.get("t", ""), "band": show["band"], "joint": joint, "w": show["w"],
                "up": upcoming, "songs": flat,
                "video": show["video"]["url"] if show["video"] else "", "memo": show["memo"]},
                ensure_ascii=False).replace("</", "<\\/")))


def render_band(b, asof) -> str:
    e = html.escape
    fmt = lambda d: d[5:].replace("-", ".")
    wd = lambda s: WD[datetime.strptime(s, "%Y-%m-%d").weekday()]
    g_lbl = "집회" if b["w"] else "공연"
    cur = int(asof[:4])
    evs = b["events"]  # 날짜 내림차순
    years = sorted({int(ev["d"][:4]) for ev in evs}, reverse=True)
    y = cur if cur in years else (years[0] if years else cur)
    ye = [ev for ev in evs if int(ev["d"][:4]) == y]
    n_r = sum(ev["kind"] == "rehearsal" and ev["d"] <= asof for ev in ye)
    n_g = sum(ev["kind"] != "rehearsal" and ev["d"] <= asof for ev in ye)
    n_u = sum(ev["kind"] != "rehearsal" and ev["d"] > asof for ev in ye)
    mo = f'{"찬양팀" if b["w"] else "밴드"} · {y}'
    ng, nr, lr = b["next_gig"], b["next_reh"], b["last_reh"]
    rt = ""
    if ng:
        lab = worship_label(ng["title"]) if ng["w"] else "공연"
        rt += (f'<a class="nx{" w" if ng["w"] else ""}" href="../../shows/{ng["key"]}/">'
               f'<b>{"D-Day" if ng["days"] == 0 else f"D-{ng['days']}"}</b><span>{fmt(ng["d"])} {lab} →</span></a>')
    aux = ([f'다음 합주 {fmt(nr["d"])} · {nr["days"]}일 뒤'] if nr else []) + \
          ([f'마지막 합주 {fmt(lr["d"])} · {"오늘" if lr["days"] == 0 else f"{lr['days']}일 전"}'] if lr else [])
    if aux:
        rt += '<div class="aux">' + "<br>".join(aux) + "</div>"
    hero = (f'<div class="next{"" if b["w"] else " gig"}"><div>\n      <div class="mo">{mo}</div>\n'
            f'      <h1 class="band">{e(b["name"])}</h1>\n    </div>' + (f'<div class="rt">{rt}</div>' if rt else "") + "</div>")

    # 레퍼토리 (show.html 셋리스트 행과 같은 마크업 + 횟수·마지막 연주)
    ids = []

    def song(s):
        pid = yt_id(s["yt"]) if s["yt"] else None
        if pid:
            ids.append(pid)
        yt = (f'<button type="button" class="yt play" data-i="{len(ids) - 1}" aria-label="{e(s["t"])} 재생">▶</button>' if pid else
              f'<a href="{e(s["yt"])}" target="_blank" rel="noopener" class="yt" aria-label="{e(s["t"])} 원곡 영상 (새 창)">링크 ↗</a>' if s["yt"] else
              f'<a href="https://www.youtube.com/results?search_query={quote(s["t"] + " " + s["a"])}" target="_blank" rel="noopener" class="yt srch" '
              f'aria-label="{e(s["t"])} 유튜브 검색 (새 창)">검색 ↗</a>')
        ld = s["last"][5:] if s["last"][:4] == asof[:4] else s["last"][2:]
        ld = f'<a href="../../shows/{s["last_key"]}/">{ld.replace("-", ".")}</a>'
        return (f'<li><span class="no{" c1" if s["n"] == 1 else ""}">{s["n"]}×</span>'
                f'<span>{e(s["t"])}<span class="ar">{e(s["a"])}<span class="rpm">{" · " if s["a"] else ""}{ld}</span></span></span>'
                f'<span class="rp">{ld}</span>{yt}</li>')

    rp = b["repertoire"]
    lis = "".join(song(s) for s in rp)
    playall = '<button class="btn sm" id="playAll">전체 재생 ▶</button>' if ids else ""
    photos = f'<a class="btn sm" href="../../gallery/?team={quote(b["name"], safe="")}">사진 →</a>'
    rep_sec = (f'<h2 class="ko">레퍼토리<span id="hr">{photos}{playall}{len(rp)}곡</span></h2>\n  <div id="view">'
               + (f'<div class="setl"><ul>{lis}</ul></div>' if rp else '<div class="empty">셋리스트 기록 없음</div>') + "</div>")

    # 타임라인: 공연만 행, 공연 사이 합주는 한 줄로 묶음
    def gig_row(ev):
        k = ev["kind"]
        lbl = worship_label(ev["title"]) if k == "worship" else "공연"
        d = f'{fmt(ev["d"])} ({wd(ev["d"])})'
        tt = ev["title"] if ev["title"] not in ("합주", "공연", "집회", "예배", "캠프") else ""
        href = f'../../shows/{ev["key"]}/'
        kc = f'k{" w" if k == "worship" else ""}'
        title = f'<a class="lnk" href="{href}">{e(tt)}</a>' if tt else ""
        lbl_html = f'<span class="{kc}">{lbl}</span>' if tt else f'<a class="lnk {kc}" href="{href}">{lbl}</a>'
        loc = f'<span class="t">{"· " if tt else ""}{e(ev["loc"])}</span>' if ev["loc"] else ""
        n = f'{ev["songs"]}곡' if ev["songs"] else ""
        up = " upnext" + (" w" if k == "worship" else "") if ng and ev["key"] == ng["key"] else ""
        return (f'<div class="row{up}"><span class="d">{d}</span>'
                f'<span class="nm">{lbl_html} {title}{loc}</span><span class="loc">{n}</span></div>')

    def bundle(rs):  # rs: 내림차순
        chips = "".join(
            f'<span class="g{" open" if r["d"] > asof else ""}"'
            + (f' data-tip="{e(r["loc"])}"' if r["loc"] and r["loc"] != b["reh_loc"] else "")
            + f'>{fmt(r["d"])}</span>' for r in reversed(rs))
        return f'<div class="row reh"><span class="d">합주 ×{len(rs)}</span><span class="nm">{chips}</span></div>'

    def items(year_evs):  # [(미래 여부, html)]
        out, buf = [], []

        def flush():
            if buf:
                out.append((buf[0]["d"] > asof, bundle(buf)))
                buf.clear()
        for ev in year_evs:
            if ev["kind"] == "rehearsal":
                buf.append(ev)
            else:
                flush()
                out.append((ev["d"] >= asof, gig_row(ev)))
        flush()
        return out

    tl = ""
    any_future = any(ev["d"] > asof for ev in evs)
    for yy in years:
        ye_ = [ev for ev in evs if int(ev["d"][:4]) == yy]
        if yy < cur:
            nr_ = sum(ev["kind"] == "rehearsal" for ev in ye_)
            tl += (f'<details class="fold"><summary>{yy} · 합주 {nr_} · {g_lbl} {len(ye_) - nr_}</summary>'
                   f'{"".join(h for _, h in items(ye_))}</details>')
            continue
        its = [h for _, h in items(ye_)]
        if yy == cur and any_future:  # 오늘 구분선: 첫 비미래 행 앞(없으면 끝)
            fl = [f for f, _ in items(ye_)]
            i = next((i for i, f in enumerate(fl) if not f), len(its))
            its.insert(i, f'<div class="sub today">오늘 · {fmt(asof)}</div>')
        tl += f'<div class="sub">{yy}</div>{"".join(its)}'
    tl_sec = f'<h2 class="ko">타임라인<span>{len(evs)}건</span></h2>\n  <div class="tour bands">{tl}</div>'

    st = b["stats"]
    vr, vg = st["venues_reh"], st["venues_gig"]

    def card(v, unit, lbl, note):
        return f'<div class="s"><div class="v">{v}<small>{unit}</small></div><div class="l">{lbl}</div><div class="n">{note}</div></div>'
    def top(vs, k):
        named = [v for v in vs if v["loc"] != "장소 미정"]  # 카드는 이름 있는 장소 우선
        if not named:
            return f'<div class="s"><div class="v">–</div><div class="l">{k}</div><div class="n">장소 기록 없음</div></div>'
        v = named[0]
        return card(v["n"], "회", e(v["loc"]), f'{k} {len(named)}곳 · 올해 {v["cur"]}회')
    lastd = max((v["last"] for v in vr), default="")
    stat_sec = (f'<h2 class="ko first">{y} <span>올해 요약 · 전체 기간</span></h2>\n  <div class="stat">'
                + card(st["reh_year"], "회", "합주", f'전체 {st["reh_all"]}회' + (f' · 마지막 {fmt(lastd)}' if lastd else ""))
                + card(st["gig_year"], "회", g_lbl, f'전체 {st["gig_all"]}회' + (f' · +{st["gig_planned"]} 예정' if st["gig_planned"] else ""))
                + top(vr, "합주실") + top(vg, "공연장") + "</div>")

    def vrows(vs, link):
        rows = "".join(
            f'<div class="row"><span class="nm">{e(v["loc"])}</span><span class="n">×{v["n"]}</span>'
            + (f'<a class="lst" href="../../shows/{v["key"]}/">마지막 {fmt(v["last"])}</a>' if link else f'<span class="lst">마지막 {fmt(v["last"])}</span>')
            + "</div>" for v in vs[:6])
        return rows or '<div class="empty">기록 없음</div>'
    loc_sec = (f'<h2 class="ko">장소<span>합주실 · 공연장</span></h2>\n  <div class="venues tour bands">'
               f'<div><div class="sub">합주실</div>{vrows(vr, False)}</div>'
               f'<div><div class="sub">공연장</div>{vrows(vg, True)}</div></div>')

    body = (hero + "\n  " + stat_sec + "\n  " + loc_sec + "\n  " + rep_sec + "\n  " + tl_sec
            + f'\n  <footer class="foot"><span>Generated {e(asof)}</span><a href="../../">← Band Calendar</a></footer>')
    return (tpl("band.html").replace("<!--__TITLE__-->", e(b["name"])).replace("<!--__BODY__-->", body)
            .replace("<!--__TEAM__-->", "w" if b["w"] else "b")
            .replace("/*__IDS__*/[]", json.dumps(ids)))


def render_gallery(shows, asof) -> str:
    items = sorted(({"key": s["key"], "d": s["d"], "band": s["band"], "bands": s["bands"], "title": s["title"],
                     "loc": s["loc"], "w": s["w"], "folderName": f"{s['d']}-{s['band']}",
                     "slug": band_slug(s["band"]) if len(s["bands"]) == 1 else "",
                     "k": worship_label(s["title"]) if s["w"] else "공연"} for s in shows),
                   key=lambda x: x["d"], reverse=True)
    return (tpl("gallery.html")
            .replace("<!--__ASOF__-->", html.escape(asof))
            .replace("/*__WD__*/[]", json.dumps(list(WD), ensure_ascii=False))
            .replace("/*__SHOWS__*/[]", json.dumps(items, ensure_ascii=False).replace("</", "<\\/")))


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
    for b in data["bands_pages"]:
        p = out / "bands" / b["slug"]
        p.mkdir(parents=True, exist_ok=True)
        (p / "index.html").write_text(render_band(b, data["asof"]), encoding="utf-8")
    (out / "gallery").mkdir(exist_ok=True)
    (out / "gallery" / "index.html").write_text(render_gallery(data["shows"], data["asof"]), encoding="utf-8")
    print(f"OK {today}: stage {len(data['stage'])}, shows {len(data['shows'])}, sched {len(data['sched'])}, "
          f"years {[r['y'] for r in data['yearly']]} → dist/index.html")


if __name__ == "__main__":
    main()
