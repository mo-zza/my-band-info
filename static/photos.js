// 사진 스트립 + 라이트박스. show(편집 가능)·gallery(읽기 전용) 공용.
// BandPhotos.mount(el, {date, band, worker, canEdit, files?, folder?, head?, onRender?})
//   files를 주면 fetch 없이 그대로 그림(갤러리). foot:false는 폴더 열기 줄 생략. head:false는 읽기 전용에서만(업로드 버튼이 h2 안에 있음).
// BandPhotos.uploading: 업로드 진행 중 여부(beforeunload용)
window.BandPhotos = (() => {
  const esc = x => String(x).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const $ = id => document.getElementById(id);
  const RM = matchMedia("(prefers-reduced-motion: reduce)");
  const thumbUrl = (id, sz) => `https://drive.google.com/thumbnail?id=${id}&sz=${sz}`;
  const thumbHTML = f => `<a href="https://drive.google.com/file/d/${esc(f.id)}/view" target="_blank" rel="noopener" data-id="${esc(f.id)}"><img src="https://drive.google.com/thumbnail?id=${esc(f.id)}&amp;sz=w400" loading="lazy" alt="${esc(f.name)}"></a>`;
  let busy = 0, cur = null, lbI = 0, lbT = 0, lbClosing = false; // cur: 라이트박스가 순회 중인 스트립(인스턴스)

  // ---- 라이트박스: 목록은 현재 스트립의 DOM에서 읽음 (업로드/삭제 후 항상 최신) ----
  const lbIds = () => [...cur.grid().children].map(a => a.dataset.id);
  function lbDisarm() { const b = $("lbd"); clearTimeout(b._t); b._t = 0; b.classList.remove("arm"); b.textContent = "삭제"; }
  // 삭제: 2단계(3초 내 재클릭) → Drive 휴지통 → 다음 사진(없으면 닫기)
  async function lbDel() {
    const b = $("lbd"); if (b.disabled) return;
    if (!b._t) { b.classList.add("arm"); b.textContent = "삭제?"; b._t = setTimeout(lbDisarm, 3000); return; }
    lbDisarm(); b.disabled = true;
    const id = lbIds()[lbI];
    try {
      const r = await fetch(cur.worker + "/photos?" + new URLSearchParams({ id }), { method: "DELETE" });
      if (!r.ok) throw 0;
    } catch (e) { b.disabled = false; b.textContent = "실패"; setTimeout(() => { if (!b._t) b.textContent = "삭제"; }, 2000); return; }
    b.disabled = false;
    cur.grid().querySelector(`a[data-id="${id}"]`).remove(); cur.fade();
    const n = lbIds().length;
    if (!n) { cur.render([]); return lbClose(); }
    const p = cur.q(".pst"); if (p) p.textContent = n + "장";
    if (lbI < n) lbShow(lbI); else { lbI = n - 1; lbClose(); }
  }
  function lbShow(i, first) {
    const ids = lbIds(); if (i < 0 || i >= ids.length) return;
    lbDisarm();
    const dir = first ? 0 : Math.sign(i - lbI);
    lbI = i; const id = ids[i], im = $("lbi");
    $("lbc").textContent = `${i + 1} / ${ids.length}`;
    $("lbo").href = `https://drive.google.com/file/d/${id}/view`;
    $("lbp").disabled = i === 0; $("lbn").disabled = i === ids.length - 1;
    if (first) { im.style.width = im.style.height = ""; im.src = thumbUrl(id, "w400"); } // 확대 중 빈 박스 방지용 임시 이미지
    const n = new Image(); // 새 이미지 load 전까지 이전 이미지 유지
    n.onload = () => {
      if (lbI !== i) return;
      im.src = n.src; im.style.width = im.style.height = "";
      if (dir && !RM.matches) { // 이전/다음 슬라이드 인
        const s = im.style; s.transition = "none"; s.opacity = 0; s.transform = `translateX(${dir * 24}px)`;
        im.offsetWidth; s.transition = ""; s.opacity = ""; s.transform = "";
      }
    };
    n.src = thumbUrl(id, "w1600");
  }
  // w를 썸네일 rect r의 위치·크기로 옮기는 transform 문자열 (w는 transform none·transition none 상태로 남음)
  function lbVec(w, r) {
    w.style.transition = "none"; w.style.transform = "none";
    const b = w.getBoundingClientRect();
    return `translate(${r.left - b.left}px,${r.top - b.top}px) scale(${r.width / b.width},${r.height / b.height})`;
  }
  function lbOpen(a) {
    ensureLb();
    const lb = $("lb"), w = $("lbw"), im = $("lbi"), r = a.querySelector("img").getBoundingClientRect();
    lbClosing = false; $("lbd").hidden = !cur.canEdit;
    lbShow(lbIds().indexOf(a.dataset.id), true);
    w.style.visibility = "hidden"; lb.showModal(); lb.focus();
    requestAnimationFrame(() => lb.classList.add("in"));
    im.decode().catch(() => {}).then(() => {
      if (/sz=w400/.test(im.src) && im.naturalWidth) { // 임시 이미지를 최종 크기(contain)로 맞춰 레이아웃 점프 방지
        const k = Math.min(innerWidth * .92 / im.naturalWidth, innerHeight * .84 / im.naturalHeight);
        im.style.width = im.naturalWidth * k + "px"; im.style.height = im.naturalHeight * k + "px";
      }
      if (RM.matches) { w.style.visibility = ""; return; }
      const v = lbVec(w, r); w.style.transform = v; w.style.visibility = "";
      w.offsetWidth; w.style.transition = ""; w.style.transform = "none";
    });
  }
  function lbClose() {
    const lb = $("lb"), w = $("lbw"); if (!lb.open || lbClosing) return;
    lbClosing = true; lb.classList.remove("in");
    const a = cur.grid().querySelector(`a[data-id="${lbIds()[lbI]}"]`);
    const fin = () => { lbDisarm(); clearTimeout(t); w.ontransitionend = null; lb.close(); w.style.transform = w.style.transition = ""; lbClosing = false; };
    let t = 0;
    if (RM.matches || !a) return fin();
    a.scrollIntoView({ inline: "nearest", block: "nearest" });
    const v = lbVec(w, a.querySelector("img").getBoundingClientRect());
    w.offsetWidth; w.style.transition = ""; w.style.transform = v;
    t = setTimeout(fin, 320);
    w.ontransitionend = e => { if (e.propertyName === "transform") fin(); };
  }
  function ensureLb() { // 페이지당 <dialog> 1개, 처음 열 때 생성
    if ($("lb")) return;
    document.body.insertAdjacentHTML("beforeend", `<dialog id="lb" aria-label="사진 보기" tabindex="-1">
  <div class="lbbar"><span id="lbc"></span><a id="lbo" target="_blank" rel="noopener">Drive에서 열기 ↗</a><button type="button" id="lbd">삭제</button><button type="button" id="lbx" aria-label="닫기">×</button></div>
  <button type="button" class="lbn" id="lbp" aria-label="이전">‹</button>
  <div id="lbw"><img id="lbi" alt=""></div>
  <button type="button" class="lbn" id="lbn" aria-label="다음">›</button>
</dialog>`);
    const lb = $("lb");
    $("lbx").onclick = lbClose;
    $("lbd").onclick = lbDel;
    $("lbp").onclick = () => lbShow(lbI - 1);
    $("lbn").onclick = () => lbShow(lbI + 1);
    lb.addEventListener("cancel", e => { e.preventDefault(); lbClose(); });
    lb.addEventListener("click", e => { if (e.target === lb) lbClose(); });
    lb.addEventListener("keydown", e => { if (e.key === "ArrowLeft") lbShow(lbI - 1); else if (e.key === "ArrowRight") lbShow(lbI + 1); });
    lb.addEventListener("touchstart", e => { lbT = e.changedTouches[0].clientX; }, { passive: true });
    lb.addEventListener("touchend", e => { const d = e.changedTouches[0].clientX - lbT; if (Math.abs(d) > 40) lbShow(lbI + (d < 0 ? 1 : -1)); });
  }

  // ---- 스트립 ----
  function mount(el, o) {
    let folder = o.folder || null, up = false;
    const q = s => el.querySelector(s), grid = () => q(".pgrid");
    const fade = () => { const g = grid(), w = g && g.parentElement; if (!w) return;
      w.classList.toggle("less", g.scrollLeft > 1); w.classList.toggle("mr", g.scrollLeft + g.clientWidth < g.scrollWidth - 1); };
    const inst = { canEdit: !!o.canEdit, worker: o.worker, q, grid, fade, render };
    // msg: 상태 문구(실패 등)
    function render(files, msg) {
      const n = files.length;
      el.innerHTML = (o.head === false ? "" : `<h2 class="ko">사진 <span><span class="pst">${esc(msg || (n ? n + "장" : ""))}</span>`
          + (o.canEdit ? `<button type="button" class="btn sm upBtn">${n ? "+ 올리기" : "+ 사진"}</button>` : "") + "</span></h2>")
        + (o.canEdit ? '<input type="file" class="upFile" accept="image/*" multiple hidden>' : "")
        + `<div class="pwrap"><div class="pgrid">${files.map(thumbHTML).join("")}</div></div>`
        + (n && folder && o.foot !== false ? `<div class="pfoot"><a href="${esc(folder)}" target="_blank" rel="noopener">폴더 열기 ↗</a></div>` : "")
        + (n ? "" : '<p class="empty pempty">아직 없음</p>')
        + (o.canEdit ? '<div class="err perr"></div>' : "");
      const g = grid(); g.onscroll = fade; new ResizeObserver(fade).observe(g); // 접힘/필터로 display 바뀔 때도 갱신
      const f = q(".upFile");
      if (f) { q(".upBtn").onclick = () => f.click();
        f.onchange = () => { const l = [...f.files]; f.value = ""; if (l.length && !up) upload(l); }; }
      if (o.onRender) o.onRender();
    }
    // quiet: 실패해도 현재 화면 유지 (업로드 후 동기화용)
    async function load(quiet) {
      try {
        const r = await fetch(o.worker + "/photos?" + new URLSearchParams({ date: o.date, band: o.band }));
        if (!r.ok) throw 0;
        const j = await r.json();
        if (up) return;
        folder = j.folder; render(j.files || []);
      } catch (e) { if (!quiet && !up) render([], "사진 목록 못 불러옴"); }
    }
    async function upload(list) {
      up = true; busy++;
      const btn = q(".upBtn"), pst = q(".pst"), g = grid(), perr = q(".perr"), added = [];
      const lbl = btn.textContent;
      btn.disabled = true; perr.innerHTML = "";
      for (const [i, f] of list.entries()) {
        btn.textContent = `${i + 1} / ${list.length}…`;
        try {
          const p = new URLSearchParams({ date: o.date, band: o.band, name: f.name, type: f.type });
          const r = await fetch(o.worker + "/photos?" + p, { method: "POST", headers: { "content-type": f.type }, body: f });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.error || "실패 " + r.status);
          added.push({ id: j.id, name: j.name });
          const em = q(".pempty"); if (em) em.remove();
          g.insertAdjacentHTML("beforeend", thumbHTML(added[added.length - 1]));
          const a = g.lastElementChild; a.classList.add("pop"); a.offsetWidth; a.classList.remove("pop"); fade();
        } catch (e) {
          const d = document.createElement("div"); d.textContent = `${f.name}: ${e.message || "네트워크 오류"}`; perr.append(d);
        }
      }
      pst.textContent = g.children.length ? g.children.length + "장" : "";
      btn.textContent = lbl; btn.disabled = false; up = false; busy--;
      if (added.length) load(true);
    }
    el.addEventListener("click", e => {
      const a = e.target.closest(".pgrid a"); if (!a) return;
      e.preventDefault(); cur = inst; lbOpen(a);
    });
    if (o.files) render(o.files); else load();
    return inst;
  }
  return { mount, get uploading() { return busy > 0; } };
})();
