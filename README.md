# my-band-info

밴드 활동 대시보드. 구글 캘린더를 원본으로 매일 08:00 KST에 GitHub Actions가
정적 페이지를 생성해 GitHub Pages로 배포한다.

## 동작

```
GitHub Actions (매일 08:00 KST, cron)
  → generate.py 실행
     ├─ 캘린더 ical 다운로드 (secrets.ICAL_URL)
     ├─ 이벤트 분류: [팀명] 합주/공연/집회/예배 …
     ├─ setlists.json 셋리스트 결합
     └─ template.html에 데이터 주입 → dist/index.html
  → GitHub Pages 배포 (생성물은 커밋되지 않음)
```

## 파일

| 파일 | 역할 |
|------|------|
| `generate.py` | ical 파싱 + 분류 + HTML 생성 |
| `template.html` | 대시보드 UI 템플릿 (`/*__DATA__*/` 마커에 데이터 주입) |
| `setlists.json` | 공연별 셋리스트 (캘린더에 없는 데이터, 수동 관리) |
| `.github/workflows/deploy.yml` | 매일 빌드 + Pages 배포 |

## 집계 규칙

- 캘린더 제목 `[팀명] 활동` 형식만 집계. `[취소]` 포함 시 제외
- 공연/버스킹 = 공연, 집회/예배/캠프/수련회 = 집회·예배 (단, "정기 예배"는 제외)
- 그 해 집회·예배 이벤트가 있는 팀은 찬양팀으로 분류
- 합동 공연(`[팀A, 팀B] 공연`)은 공연 1회, 팀별 상세에는 모두 표시
- 제외 팀 / 표기 보정은 `generate.py`의 `EXCLUDE` / `CANON`

## 셋리스트 추가

`setlists.json`에 항목 추가:

```json
{ "date": "2026-08-01", "band": "HALO", "songs": [{ "t": "곡명", "a": "원곡 아티스트" }] }
```

합동 공연은 `"sets": [{ "team": "팀명", "songs": [...] }]` 형식.

## 수동 실행

Actions 탭 → Build & Deploy Dashboard → Run workflow.
로컬 테스트: `ICAL_URL=<ical 주소> python3 generate.py && open dist/index.html`
