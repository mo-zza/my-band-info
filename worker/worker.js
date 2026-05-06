const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type",
  "Access-Control-Allow-Methods": "POST",
};
const out = (status, obj) =>
  new Response(JSON.stringify(obj), { status, headers: { ...CORS, "Content-Type": "application/json" } });

export default {
  async fetch(req, env) {
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
    if (req.method !== "POST") return out(405, { error: "POST only" });
    let b;
    try { b = await req.json(); } catch { return out(400, { error: "JSON 아님" }); }
    const s = (k) => (typeof b[k] === "string" ? b[k].trim() : "");
    const [date, band, songs, video, memo] = ["date", "band", "songs", "video", "memo"].map(s);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return out(400, { error: "날짜 형식 오류 (YYYY-MM-DD)" });
    if (!band) return out(400, { error: "팀 이름이 비어 있음" });
    if (!songs) return out(400, { error: "곡 목록이 비어 있음" });
    const r = await fetch("https://api.github.com/repos/mo-zza/my-band-info/dispatches", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "band-setlist",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "setlist", client_payload: { date, band, songs, video, memo } }),
    });
    return r.status === 204 ? out(200, { ok: true }) : out(502, { error: "github " + r.status });
  },
};
