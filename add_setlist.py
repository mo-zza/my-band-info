#!/usr/bin/env python3
"""사이트 폼 JSON(FORM_JSON: date, band, songs, video, memo)으로 setlists.json에 추가/교체한다."""
import json, os, sys
from datetime import datetime

from fill_yt import dumps

PATH = os.environ.get("SETLISTS_PATH", "setlists.json")


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
    for line in f.get("songs", "").splitlines():
        if line.strip():
            parts = line.strip().split(" - ")
            yt = parts.pop() if len(parts) > 1 and parts[-1].strip().startswith(("http://", "https://")) else ""
            t, _, a = " - ".join(parts).partition(" - ")
            songs.append({"t": t.strip(), "a": a.strip(), **({"yt": yt.strip()} if yt else {})})
    if not songs:
        fail("곡이 없습니다. 한 줄에 `곡명 - 아티스트` 형식으로 입력해주세요.")
    item = {"date": f["date"], "band": f["band"].strip()}
    vids = [v.strip() for v in f.get("video", "").split(",") if v.strip()]
    if vids:
        item["video"] = vids
    if f.get("memo"):
        item["memo"] = f["memo"]
    item["songs"] = songs

    with open(PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    for i, s in enumerate(data):
        if s["date"] == item["date"] and s.get("band") == item["band"]:
            old = {x["t"].strip().lower(): x["yt"] for x in s.get("songs", []) if x.get("yt")}
            for x in songs:
                if "yt" not in x and x["t"].strip().lower() in old:
                    x["yt"] = old[x["t"].strip().lower()]
            data[i] = item
            break
    else:
        data.append(item)
        data.sort(key=lambda s: s["date"])
    with open(PATH, "w", encoding="utf-8") as fh:
        fh.write(dumps(data) + "\n")
    print(f"ok: {item['date']} {item['band']} ({len(songs)}곡)")


if __name__ == "__main__":
    main()
