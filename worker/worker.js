const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type",
  "Access-Control-Allow-Methods": "GET, POST, DELETE",
};
const out = (status, obj) =>
  new Response(JSON.stringify(obj), { status, headers: { ...CORS, "Content-Type": "application/json" } });

const okUrl = (s) => /^https:\/\/\S+$/.test(s) && !s.includes(",");
const MAX_BYTES = 25 * 1024 * 1024;
const DRIVE = "https://www.googleapis.com/drive/v3/files";
let tokenCache = { token: "", exp: 0 }; // isolate 단위 캐시 (콜드스타트마다 재발급)

const dispatch = (env, payload) =>
  fetch("https://api.github.com/repos/mo-zza/my-band-info/dispatches", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "band-setlist",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: "setlist", client_payload: payload }),
  });

class GErr extends Error {}
const gjson = async (r, what) => {
  const t = await r.text();
  if (!r.ok) throw new GErr(`${what} ${r.status}: ${t.slice(0, 200)}`);
  return JSON.parse(t);
};

async function accessToken(env) {
  if (tokenCache.token && Date.now() < tokenCache.exp - 60000) return tokenCache.token;
  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: env.GOOGLE_CLIENT_ID, client_secret: env.GOOGLE_CLIENT_SECRET,
      refresh_token: env.GOOGLE_REFRESH_TOKEN, grant_type: "refresh_token",
    }),
  });
  const j = await gjson(r, "token");
  tokenCache = { token: j.access_token, exp: Date.now() + (j.expires_in || 3600) * 1000 };
  return tokenCache.token;
}

// 폴더 id 또는 null
async function findFolder(env, auth, name) {
  const q = `name='${name.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}' and '${env.DRIVE_ROOT}' in parents`
    + " and mimeType='application/vnd.google-apps.folder' and trashed=false";
  const l = await gjson(await fetch(`${DRIVE}?${new URLSearchParams({ q, fields: "files(id)" })}`, { headers: auth }), "folder list");
  return l.files && l.files.length ? l.files[0].id : null;
}

async function ensureFolder(env, auth, name) {
  const id = await findFolder(env, auth, name);
  if (id) return id;
  const c = await gjson(await fetch(`${DRIVE}?fields=id`, {
    method: "POST", headers: { ...auth, "Content-Type": "application/json" },
    body: JSON.stringify({ name, mimeType: "application/vnd.google-apps.folder", parents: [env.DRIVE_ROOT] }),
  }), "folder create");
  return c.id;
}

const folderUrl = (id) => `https://drive.google.com/drive/folders/${id}`;

async function listPhotos(env, url) {
  const p = url.searchParams;
  const [date, band] = ["date", "band"].map((k) => (p.get(k) || "").trim());
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return out(400, { error: "날짜 형식 오류 (YYYY-MM-DD)" });
  if (!band) return out(400, { error: "팀 이름이 비어 있음" });
  try {
    const auth = { Authorization: `Bearer ${await accessToken(env)}` };
    const id = await findFolder(env, auth, `${date}-${band}`);
    const ok = (o) => { const r = out(200, o); r.headers.set("Cache-Control", "no-store"); return r; };
    if (!id) return ok({ folder: null, files: [] });
    const q = `'${id}' in parents and mimeType contains 'image/' and trashed=false`;
    const l = await gjson(await fetch(`${DRIVE}?${new URLSearchParams({
      q, fields: "files(id,name,createdTime)", orderBy: "createdTime", pageSize: "200" })}`, { headers: auth }), "photo list");
    return ok({ folder: folderUrl(id), files: (l.files || []).map((f) => ({ id: f.id, name: f.name })) });
  } catch (e) {
    return out(e instanceof GErr ? 502 : 500, { error: e instanceof GErr ? e.message : "서버 오류" });
  }
}

// 루트 아래 모든 폴더의 사진 → { "<폴더이름>": {folder, files:[{id,name}]} } (갤러리용, 폴더 20개씩 묶어 조회)
async function listGallery(env) {
  try {
    const auth = { Authorization: `Bearer ${await accessToken(env)}` };
    // ponytail: 페이지네이션 없음(묶음당 1000장 초과분은 잘림) — 넘으면 nextPageToken 순회 또는 묶음 축소
    const get = async (q, fields, orderBy, what) => (await gjson(await fetch(`${DRIVE}?${new URLSearchParams({
      q, fields, orderBy, pageSize: "1000" })}`, { headers: auth }), what)).files || [];
    const folders = await get(`'${env.DRIVE_ROOT}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false`, "files(id,name)", "name", "folder list");
    const res = {}, byId = {};
    for (const f of folders) { res[f.name] = { folder: folderUrl(f.id), files: [] }; byId[f.id] = res[f.name]; }
    for (let i = 0; i < folders.length; i += 20) {
      const ps = folders.slice(i, i + 20).map((f) => `'${f.id}' in parents`).join(" or ");
      for (const f of await get(`(${ps}) and mimeType contains 'image/' and trashed=false`, "files(id,name,parents,createdTime)", "createdTime", "photo list")) {
        const d = byId[(f.parents || []).find((p) => byId[p])];
        if (d) d.files.push({ id: f.id, name: f.name });
      }
    }
    const r = out(200, res); r.headers.set("Cache-Control", "no-store"); return r;
  } catch (e) {
    return out(e instanceof GErr ? 502 : 500, { error: e instanceof GErr ? e.message : "서버 오류" });
  }
}

async function uploadPhoto(req, env, url) {
  const p = url.searchParams;
  const [date, band, name, type] = ["date", "band", "name", "type"].map((k) => (p.get(k) || "").trim());
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return out(400, { error: "날짜 형식 오류 (YYYY-MM-DD)" });
  if (!band) return out(400, { error: "팀 이름이 비어 있음" });
  if (!name) return out(400, { error: "파일 이름이 비어 있음" });
  if (!type.startsWith("image/")) return out(400, { error: "이미지 파일만 올릴 수 있음" });
  if (Number(req.headers.get("content-length") || 0) > MAX_BYTES) return out(413, { error: "파일이 25MB 초과" });
  const data = await req.arrayBuffer();
  if (!data.byteLength) return out(400, { error: "빈 파일" });
  if (data.byteLength > MAX_BYTES) return out(413, { error: "파일이 25MB 초과" });
  try {
    const auth = { Authorization: `Bearer ${await accessToken(env)}` };
    const folderId = await ensureFolder(env, auth, `${date}-${band}`);
    const bd = "bnd" + crypto.randomUUID().replace(/-/g, "");
    const enc = new TextEncoder();
    const body = new Blob([
      enc.encode(`--${bd}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify({ name, parents: [folderId] })}\r\n--${bd}\r\nContent-Type: ${type}\r\n\r\n`),
      data,
      enc.encode(`\r\n--${bd}--`),
    ]);
    const f = await gjson(await fetch("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name", {
      method: "POST", headers: { ...auth, "Content-Type": `multipart/related; boundary=${bd}` }, body,
    }), "upload");
    return out(200, { ok: true, id: f.id, name: f.name, folder: folderUrl(folderId) });
  } catch (e) {
    return out(e instanceof GErr ? 502 : 500, { error: e instanceof GErr ? e.message : "서버 오류" });
  }
}

async function deletePhoto(env, url) {
  const id = url.searchParams.get("id") || "";
  if (!/^[\w-]{10,}$/.test(id)) return out(400, { error: "파일 id 형식 오류" });
  try {
    const auth = { Authorization: `Bearer ${await accessToken(env)}` };
    // 휴지통으로 이동 (Drive에서 30일간 복구 가능)
    await gjson(await fetch(`${DRIVE}/${id}?fields=id`, {
      method: "PATCH", headers: { ...auth, "Content-Type": "application/json" }, body: JSON.stringify({ trashed: true }),
    }), "delete");
    return out(200, { ok: true });
  } catch (e) {
    return out(e instanceof GErr ? 502 : 500, { error: e instanceof GErr ? e.message : "서버 오류" });
  }
}

export default {
  async fetch(req, env) {
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    const url = new URL(req.url);
    if (req.method === "GET" && url.pathname === "/photos") return listPhotos(env, url);
    if (req.method === "GET" && url.pathname === "/gallery") return listGallery(env);
    if (req.method === "DELETE" && url.pathname === "/photos") return deletePhoto(env, url);
    if (req.method !== "POST") return out(405, { error: "GET, POST, DELETE only" });
    if (url.pathname === "/photos") return uploadPhoto(req, env, url);
    let b;
    try { b = await req.json(); } catch { return out(400, { error: "JSON 아님" }); }
    if (b.video !== undefined && typeof b.video !== "string") return out(400, { error: "공연 영상: https://로 시작하는 링크 하나만" });
    const s = (k) => (typeof b[k] === "string" ? b[k].trim() : "");
    const [date, band, songs, video, memo] = ["date", "band", "songs", "video", "memo"].map(s);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return out(400, { error: "날짜 형식 오류 (YYYY-MM-DD)" });
    if (!band) return out(400, { error: "팀 이름이 비어 있음" });
    if (video && !okUrl(video)) return out(400, { error: "공연 영상: https://로 시작하는 링크 하나만" });
    const lines = songs.split("\n").filter((l) => l.trim());
    for (const [i, l] of lines.entries()) {
      const p = l.trim().split(" - ");
      const lk = p.length > 1 ? p[p.length - 1].trim() : "";
      if (/^https?:\/\//.test(lk) && !okUrl(lk)) return out(400, { error: `${i + 1}번째 곡 링크: https://로 시작하는 링크 하나만` });
    }
    const r = await dispatch(env, { date, band, songs, video, memo });
    return r.status === 204 ? out(200, { ok: true }) : out(502, { error: "github " + r.status });
  },
};
