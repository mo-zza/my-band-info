#!/usr/bin/env python3
"""yt 없는 곡을 YouTube Data API로 검색해 setlists.json에 채운다. YT_API_KEY 없으면 건너뜀."""
import json, os, sys, urllib.error, urllib.parse, urllib.request

PATH = os.environ.get("SETLISTS_PATH", "setlists.json")


def dumps(o, ind=0):
    """json.dump(indent=2)와 같되, 값이 모두 스칼라인 객체(곡)는 한 줄로."""
    j = lambda x: json.dumps(x, ensure_ascii=False)
    pad, pad2 = "  " * ind, "  " * (ind + 1)
    if isinstance(o, dict):
        if all(not isinstance(v, (dict, list)) for v in o.values()):
            return "{ " + ", ".join(f"{j(k)}: {j(v)}" for k, v in o.items()) + " }"
        return "{\n" + ",\n".join(f"{pad2}{j(k)}: {dumps(v, ind + 1)}" for k, v in o.items()) + f"\n{pad}}}"
    if isinstance(o, list) and o:
        return "[\n" + ",\n".join(pad2 + dumps(v, ind + 1) for v in o) + f"\n{pad}]"
    return j(o)


def all_songs(data):
    for show in data:
        yield from show.get("songs", [])
        for st in show.get("sets", []):
            yield from st.get("songs", [])


class Quota(Exception):
    pass


def search(t, a, key):
    q = urllib.parse.urlencode({"part": "snippet", "type": "video", "videoCategoryId": "10",
                                "maxResults": 1, "q": f"{t} {a}", "key": key})
    try:
        with urllib.request.urlopen("https://www.googleapis.com/youtube/v3/search?" + q, timeout=20) as r:
            items = json.load(r).get("items", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        if e.code == 403:
            raise Quota(f"{e}\n{body}")
        print(f"  error {t} — {a}: {e}\n{body}")
        return None
    except urllib.error.URLError as e:
        print(f"  error {t} — {a}: {e}")
        return None
    if not items:
        print(f"  no result: {t} — {a}")
        return None
    print(f"  {t} — {a} → {items[0]['snippet']['title']}")
    return f"https://youtu.be/{items[0]['id']['videoId']}"


def main():
    with open(PATH, encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw)
    if "--check" in sys.argv:
        assert dumps(data) + "\n" == raw, "round-trip mismatch"
        print("round-trip ok")
        return
    key = os.environ.get("YT_API_KEY")
    if not key:
        print("skip: YT_API_KEY not set")
        return
    budget, cache, filled, skipped = 80, {}, 0, 0
    try:
        for s in all_songs(data):
            if s.get("yt"):
                continue
            k = (s["t"].strip().lower(), s["a"].strip().lower())
            if k not in cache:
                if budget <= 0:
                    skipped += 1
                    continue
                budget -= 1
                cache[k] = search(s["t"], s["a"], key)
            if cache[k]:
                s["yt"] = cache[k]
                filled += 1
            else:
                skipped += 1
    except Quota as e:
        print(f"quota/forbidden, stopping: {e}")
    if filled:
        with open(PATH, "w", encoding="utf-8") as f:
            f.write(dumps(data) + "\n")
    print(f"filled {filled}, skipped {skipped}")


if __name__ == "__main__":
    main()
