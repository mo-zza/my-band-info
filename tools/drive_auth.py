#!/usr/bin/env python3
"""Drive 업로드용 OAuth refresh token 발급 + 루트 폴더(밴드 사진) 생성. 값은 화면에만 출력한다.
사용: GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python3 tools/drive_auth.py"""
import json, os, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

REDIRECT = "http://localhost:8787/cb"


def call(url, data=None, token=None, as_json=False):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
    if as_json:
        body, h["Content-Type"] = json.dumps(data).encode(), "application/json"
    else:
        body = urlencode(data).encode() if data else None
    with urlopen(Request(url, body, h)) as r:
        return json.load(r)


def main():
    cid, secret = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
    if not (cid and secret):
        print("GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET 환경변수가 필요합니다.\n"
              "예: GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python3 tools/drive_auth.py", file=sys.stderr)
        sys.exit(1)
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path != "/cb":
                self.send_response(404); self.end_headers(); return
            got["code"], got["error"] = q.get("code", [""])[0], q.get("error", [""])[0]
            self.send_response(200); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers()
            self.wfile.write("완료. 터미널로 돌아가세요.".encode())
            threading.Thread(target=self.server.shutdown).start()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("localhost", 8787), H)
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive.file", "access_type": "offline", "prompt": "consent"})
    print("브라우저에서 승인하세요:\n" + url)
    webbrowser.open(url)
    srv.serve_forever()
    if not got.get("code"):
        print("승인 실패: " + (got.get("error") or "code 없음"), file=sys.stderr)
        sys.exit(1)
    tok = call("https://oauth2.googleapis.com/token", {
        "code": got["code"], "client_id": cid, "client_secret": secret,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    if not tok.get("refresh_token"):
        print("refresh_token 없음. https://myaccount.google.com/permissions 에서 앱 권한을 삭제 후 재시도.", file=sys.stderr)
        sys.exit(1)
    root = call("https://www.googleapis.com/drive/v3/files",
                {"name": "밴드 사진", "mimeType": "application/vnd.google-apps.folder"}, tok["access_token"], True)
    call(f"https://www.googleapis.com/drive/v3/files/{root['id']}/permissions",
         {"role": "reader", "type": "anyone"}, tok["access_token"], True)
    print(f"\nwrangler secret put 에 넣을 값:\nGOOGLE_CLIENT_ID={cid}\nGOOGLE_CLIENT_SECRET={secret}\n"
          f"GOOGLE_REFRESH_TOKEN={tok['refresh_token']}\nDRIVE_ROOT={root['id']}")


if __name__ == "__main__":
    main()
