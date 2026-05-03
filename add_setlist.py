#!/usr/bin/env python3
"""GitHub Issue Form 본문(ISSUE_BODY)을 파싱해 setlists.json에 추가/교체한다."""
import json, os, re, sys
from datetime import datetime

from fill_yt import dumps

PATH = os.environ.get("SETLISTS_PATH", "setlists.json")
# .github/ISSUE_TEMPLATE/setlist.yml 의 label 과 일치해야 함
LABELS = {"날짜": "date", "팀": "band", "곡 목록": "songs", "공연 영상": "video", "메모": "memo"}


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def parse(body):
    f = {}
    for part in re.split(r"^### ", body.replace("\r\n", "\n"), flags=re.M)[1:]:
        label, _, val = part.partition("\n")
        label, val = label.strip(), val.strip()
        if label in LABELS and val != "_No response_":
            f[LABELS[label]] = val
    return f


def main():
    f = parse(os.environ.get("ISSUE_BODY", ""))
    try:
        datetime.strptime(f.get("date", ""), "%Y-%m-%d")
    except ValueError:
        fail(f"날짜 형식 오류: YYYY-MM-DD 로 입력해주세요 (입력값: {f.get('date', '')!r})")
    if not f.get("band"):
        fail("팀 이름이 비어 있습니다.")
    songs = []
    for line in f.get("songs", "").splitlines():
        if line.strip():
            t, _, a = line.partition(" - ")
            songs.append({"t": t.strip(), "a": a.strip()})
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
