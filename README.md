# my-band-info

밴드 활동 대시보드. 구글 캘린더를 원본으로 매일 08:00 KST에 GitHub Actions가
정적 페이지를 생성해 GitHub Pages로 배포한다.

## 동작

```
GitHub Actions (매일 08:00 KST, cron)
  → fill_yt.py 실행 (YT_API_KEY 있으면 yt 없는 곡 검색 → setlists.json 커밋)
  → generate.py 실행
     ├─ 캘린더 ical 다운로드 (secrets.ICAL_URL)
     ├─ 이벤트 분류: [팀명] 합주/공연/집회/예배 …
     ├─ setlists.json 셋리스트 결합
     ├─ template.html에 데이터 주입 → dist/index.html
     ├─ 공연별 상세 페이지 → dist/shows/<key>/index.html (show.html)
     └─ static/ 복사 → dist/
  → GitHub Pages 배포 (생성물은 커밋되지 않음)

사이트 폼(upload.html) → Cloudflare Worker → repository_dispatch → setlist 워크플로
  → add_setlist.py → setlists.json 커밋 → deploy.yml 실행
```

## 파일

| 파일 | 역할 |
|------|------|
| `generate.py` | ical 파싱 + 분류 + HTML 생성 |
| `template.html` | 대시보드 UI 템플릿 (`/*__DATA__*/` 마커에 데이터 주입) |
| `show.html` | 공연 상세 페이지 템플릿 (Python에서 직접 치환) |
| `static/base.css` | 대시보드·상세 공용 CSS (dist/로 복사) |
| `fill_yt.py` | `yt` 없는 곡을 YouTube Data API로 검색해 setlists.json에 채움 (빌드 시) |
| `add_setlist.py` | 폼 JSON(`FORM_JSON`)으로 setlists.json에 추가/교체 |
| `static/upload.html` | 셋리스트 입력 폼 (Worker로 POST) |
| `worker/` | Cloudflare Worker: `repository_dispatch` 전송 |
| `setlists.json` | 공연별 셋리스트 (캘린더에 없는 데이터, 수동 관리) |
| `.github/workflows/deploy.yml` | 매일 빌드 + Pages 배포 |
| `.github/workflows/setlist.yml` | `repository_dispatch(setlist)` → setlists.json 커밋 → 배포 실행 |

## 집계 규칙

- 캘린더 제목 `[팀명] 활동` 형식만 집계. `[취소]` 포함 시 제외
- 공연/버스킹 = 공연, 집회/예배/캠프/수련회 = 집회·예배 (단, "정기 예배"는 제외)
- 그 해 집회·예배 이벤트가 있는 팀은 찬양팀으로 분류
- 합동 공연(`[팀A, 팀B] 공연`)은 공연 1회, 팀별 상세에는 모두 표시
- 제외 팀 / 표기 보정은 `generate.py`의 `EXCLUDE` / `CANON`

## 셋리스트 추가

**사이트 폼으로 올리기:** `upload.html` (대시보드 footer·상세 페이지의 `셋리스트 올리기` 링크, 날짜·팀 프리필).
날짜는 `YYYY-MM-DD`, 곡은 한 줄에 한 곡: `곡명 - 아티스트` 또는 `곡명 - 아티스트 - 유튜브링크` (링크 비우면 자동 검색, 교체 시 기존 링크는 같은 곡명이면 유지), 영상은 쉼표 구분. 폼에서 공연 선택 → 기존 내용 채워짐 → 수정 후 올리기 (합동 공연은 미지원, 상세 페이지의 `수정`은 해당 공연이 선택된 채 열림). 제출하면 Worker가 Action을 호출해 `setlists.json`에 커밋하고 배포한다 (1~2분).
같은 날짜+팀이 이미 있으면 교체된다 (수정용). 형식이 틀리면 Action이 실패한다 (Actions 탭 확인).

직접 편집하려면 `setlists.json`에 항목 추가:

```json
{ "date": "2026-08-01", "band": "HALO", "songs": [{ "t": "곡명", "a": "원곡 아티스트" }] }
```

선택 필드:

- `video`: 우리 공연 실황 URL (배열). YouTube / Google Drive는 상세 페이지에 임베드, 그 외는 링크만.
  구글 드라이브 영상은 "링크가 있는 모든 사용자" 공유가 필요하다.
- `memo`: 자유 텍스트 (줄바꿈 허용)
- 곡의 `yt`: 원곡/참고 영상 URL → 곡명 옆 `원곡 ↗` 링크. 비워두면 다음 빌드에서 자동 검색해 채운다. 오매칭이면 `yt` 값을 직접 고치면 유지된다. 검색 결과가 없으면 유튜브 검색 링크(`원곡 🔍`)가 표시된다.

```json
{ "date": "2026-10-24", "band": "아크크루", "video": ["https://youtu.be/xxxx"], "memo": "첫 합동 무대",
  "songs": [{ "t": "곡명", "a": "아티스트", "yt": "https://youtu.be/yyyy" }] }
```

합동 공연은 `"sets": [{ "team": "팀명", "songs": [...] }]` 형식.

## YouTube 링크 자동 채우기 셋업

1. Google Cloud 콘솔에서 YouTube Data API v3 활성화 → API 키 발급
2. 레포 Settings → Secrets → `YT_API_KEY` 등록 (없으면 조용히 건너뜀)
3. 쿼터는 하루 100검색 (새 곡만 검색하므로 충분). 1회 최대 80건.

## Worker 배포

1. Cloudflare 가입
2. `cd worker && npx wrangler login`
3. GitHub fine-grained 토큰 발급 (이 레포만, Contents: Read and write)
4. `npx wrangler secret put GITHUB_TOKEN`
5. `npx wrangler deploy` → 출력된 URL을 `static/upload.html`의 `WORKER_URL`에 넣는다

## 수동 실행

Actions 탭 → Build & Deploy Dashboard → Run workflow.
로컬 테스트: `ICAL_URL=<ical 주소> python3 generate.py && open dist/index.html`
