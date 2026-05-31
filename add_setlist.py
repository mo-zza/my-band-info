#!/usr/bin/env python3
"""사이트 폼 JSON(FORM_JSON: date, band, songs, video, memo)으로 setlists.json에 부분 갱신한다 (payload에 키가 있는 필드만)."""
import json, os, re, sys
from datetime import datetime

from fill_yt import dumps

PATH = os.environ.get("SETLISTS_PATH", "setlists.json")


def ok_url(s):
    return bool(re.fullmatch(r"https://\S+", s)) and "," not in s


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def main():
    f = json.loads(os.environ["FORM_JSON"])
    try:
        datetime.strptime(f.get("date", ""), "%Y-%m-%d")
    except ValueError:
        fail(f"날짜 형식 오류: YYYY-MM-DD 로 입력해주세요 (입력값: {f.get('date', '')!r})")
    if not f.get("band"):
        fail("팀 이름이 비어 있습니다.")
    songs = []
    for n, line in enumerate((l for l in f.get("songs", "").splitlines() if l.strip()), 1):
        parts = line.strip().split(" - ")
        yt = parts.pop() if len(parts) > 1 and parts[-1].strip().startswith(("http://", "https://")) else ""
        if yt and not ok_url(yt.strip()):
            fail(f"{n}번째 곡 링크: https://로 시작하는 링크 하나만")
        t, _, a = " - ".join(parts).partition(" - ")
        songs.append({"t": t.strip(), "a": a.strip(), **({"yt": yt.strip()} if yt else {})})
    vid = f.get("video", "")
    if not isinstance(vid, str) or (vid.strip() and not ok_url(vid.strip())):
        fail("공연 영상: https://로 시작하는 링크 하나만")
    vid = vid.strip()
    with open(PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    key = (f["date"], f["band"].strip())
    hit = next((s for s in data if (s["date"], s.get("band")) == key), None)
    item = hit if hit is not None else {"date": key[0], "band": key[1]}
    if "songs" in f:
        old = {x["t"].strip().lower(): x["yt"] for x in item.get("songs", []) if x.get("yt")}
        for x in songs:
            if "yt" not in x and x["t"].strip().lower() in old:
                x["yt"] = old[x["t"].strip().lower()]
        item["songs"] = songs
    for k, v in (("video", vid), ("memo", f.get("memo") or "")):
        if k in f:  # 키가 있는 필드만 갱신, 빈 값이면 제거
            if v:
                item[k] = v
            else:
                item.pop(k, None)
    if hit is None:
        data.append(item)
        data.sort(key=lambda s: s["date"])
    if not (item.get("songs") or item.get("video") or item.get("memo")):  # 결과가 전부 비면 삭제
        data = [s for s in data if s is not item]
        with open(PATH, "w", encoding="utf-8") as fh:
            fh.write(dumps(data) + "\n")
        print(f"ok: {item['date']} {item['band']} (삭제)")
        return
    with open(PATH, "w", encoding="utf-8") as fh:
        fh.write(dumps(data) + "\n")
    print(f"ok: {item['date']} {item['band']} ({len(item.get('songs', []))}곡)")


if __name__ == "__main__":
    main()
