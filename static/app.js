/* InkTime console 前端逻辑：画廊 + 墨水屏预览 */
"use strict";

const $ = (s) => document.querySelector(s);
const state = { type: "全部", sort: "memory", q: "" };
let photos = [];
let current = null;
const captionOverrides = new Map();

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (d) => (d || "").slice(0, 10);
const thumbURL = (p) =>
  `/api/thumb?path=${encodeURIComponent(p.path)}&v=${p.w}x${p.h}`;

function renderURL(p, { caption, place, ditherType, ditherKernel, border }) {
  const q = new URLSearchParams({
    path: p.path,
    caption: caption ?? "",
    place: place ?? "",
    date: p.date || "",
    ditherType: ditherType || "DIFFUSION",
    ditherKernel: ditherKernel || "FLOYD_STEINBERG",
    border: border ?? 0,
  });
  return `/api/render?${q}`;
}

/* ---------- 数据加载 ---------- */

async function loadStats() {
  const s = await fetch("/api/stats").then((r) => r.json());
  $("#mTotal").textContent = s.total;
  $("#mAvg").textContent = s.avg_memory;
  $("#mHigh").textContent = s.high_count;
  $("#mToday").textContent = s.picks.length;
  $("#mTodayDate").textContent = s.today;

  const section = $("#picksSection");
  if (s.picks.length) {
    section.hidden = false;
    $("#picksRow").innerHTML = s.picks
      .map(
        (p, i) => `
      <button class="pick" data-path="${esc(p.path)}">
        <span class="thumb"><img loading="lazy" src="${thumbURL(p)}" alt=""></span>
        <span class="pick-body">
          <span class="pick-top"><span class="rank mono">#${i + 1}</span></span>
          <span class="caption">${esc(p.caption)}</span>
          <span class="meta mono">${fmtDate(p.date)}${p.city ? " · " + esc(p.city) : ""}</span>
        </span>
      </button>`
      )
      .join("");
  }
}

async function loadTypes() {
  const types = await fetch("/api/types").then((r) => r.json());
  $("#typeChips").innerHTML = types
    .map(
      (t) =>
        `<button class="chip-btn${t === state.type ? " active" : ""}" data-type="${esc(t)}">${esc(t)}</button>`
    )
    .join("");
}

async function loadPhotos() {
  const q = new URLSearchParams(state);
  $("#grid").innerHTML = `<div class="empty-note">加载中…</div>`;
  photos = await fetch(`/api/photos?${q}`).then((r) => r.json());
  if (!photos.length) {
    $("#grid").innerHTML = `<div class="empty-note">没有匹配的照片，换个筛选条件试试。</div>`;
    return;
  }
  $("#grid").innerHTML = photos
    .map((p) => `
      <article class="card" data-path="${esc(p.path)}" tabindex="0">
        <div class="thumb"><img loading="lazy" src="${thumbURL(p)}" alt=""></div>
        <div class="card-body">
          <p class="caption">${esc(p.caption)}</p>
          <div class="card-meta">
            <span class="meta mono">${fmtDate(p.date)}${p.city ? " · " + esc(p.city) : ""}</span>
            <span class="type-chip">${esc(p.type)}</span>
          </div>
        </div>
      </article>`)
    .join("");
}

/* ---------- 墨水屏预览 ---------- */

let renderTimer = null;
function refreshPreview({ flash = false } = {}) {
  if (!current) return;
  const caption = captionOverrides.get(current.path) ?? current.caption;
  const opts = {
    caption,
    place: current.city,
    ditherType: $("#ditherTypeSel").value,
    ditherKernel: $("#ditherKernelSel").value,
    border: $("#borderSel").value,
  };
  const img = $("#previewImg");
  const idle = $("#screenIdle");
  const url = renderURL(current, opts);
  const flashEl = $("#screenFlash");
  img.onload = () => {
    idle.hidden = true;
    img.hidden = false;
    if (flash) {
      flashEl.classList.remove("active");
      void flashEl.offsetWidth; // 重新触发动画
      flashEl.classList.add("active");
    }
  };
  img.onerror = () => {   // 图片缺失（如已从磁盘移除）时回落小眼睛占位
    img.hidden = true;
    idle.hidden = false;
  };
  img.src = url;
}

function selectPhoto(p, { scroll = false, flash = true } = {}) {
  current = p;
  document.querySelectorAll(".card, .pick").forEach((el) => {
    el.classList.toggle("selected", el.dataset.path === p.path);
  });
  $("#captionInput").value = captionOverrides.get(p.path) ?? p.caption;
  $("#panelInfo").innerHTML = `回忆度 ${p.memory} · 美观度 ${p.beauty}<span class="push-history mono" id="pushHistory"></span>`;
  loadPushHistory(p.path);
  $("#pushBtn").disabled = false;
  refreshPreview({ flash });
  if (scroll && window.innerWidth <= 960) {
    $(".preview-col").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function loadPushHistory(path) {
  const el = $("#pushHistory");
  if (!el) return;
  el.textContent = "查询推送记录…";
  try {
    const info = await fetch(`/api/pushinfo?path=${encodeURIComponent(path)}`).then((r) => r.json());
    if (!current || current.path !== path) return;   // 期间已切换照片
    el.textContent = info.count
      ? `已推送 ${info.count} 次 · 最近 ${info.last}`
      : "未推送过";
  } catch {
    el.textContent = "";
  }
}

/* ---------- 事件 ---------- */

function bindEvents() {
  $("#typeChips").addEventListener("click", (e) => {
    const btn = e.target.closest(".chip-btn");
    if (!btn) return;
    state.type = btn.dataset.type;
    loadTypes();
    loadPhotos();
  });

  $("#sortSel").addEventListener("change", (e) => {
    state.sort = e.target.value;
    loadPhotos();
  });

  let searchTimer = null;
  $("#searchInput").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.q = e.target.value.trim();
      loadPhotos();
    }, 300);
  });

  $("#grid").addEventListener("click", (e) => {
    const card = e.target.closest(".card");
    if (!card) return;
    const p = photos.find((x) => x.path === card.dataset.path);
    if (p) selectPhoto(p, { scroll: true });
  });
  $("#grid").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest(".card");
    if (!card) return;
    e.preventDefault();
    const p = photos.find((x) => x.path === card.dataset.path);
    if (p) selectPhoto(p, { scroll: true });
  });

  $("#picksRow").addEventListener("click", (e) => {
    const btn = e.target.closest(".pick");
    if (!btn) return;
    const p = photos.find((x) => x.path === btn.dataset.path);
    if (p) selectPhoto(p, { scroll: true });
  });

  let captionTimer = null;
  $("#captionInput").addEventListener("input", (e) => {
    if (!current) return;
    captionOverrides.set(current.path, e.target.value);
    clearTimeout(captionTimer);
    captionTimer = setTimeout(() => refreshPreview(), 350);
  });

  $("#borderSel").addEventListener("change", () => refreshPreview({ flash: true }));
  // 误差扩散 → 算法下拉；有序/关闭 → 框内显示固定文案
  function syncDitherUI() {
    const type = $("#ditherTypeSel").value;
    const isDiffusion = type === "DIFFUSION";
    $("#ditherKernelSel").hidden = isDiffusion === false;
    const staticBox = $("#kernelStatic");
    staticBox.hidden = isDiffusion;
    staticBox.textContent = type === "ORDERED" ? "固定使用 Bayer 8×8" : "无算法";
  }
  $("#ditherTypeSel").addEventListener("change", (e) => {
    syncDitherUI();
    refreshPreview({ flash: true });
  });
  $("#ditherKernelSel").addEventListener("change", () => refreshPreview({ flash: true }));
  syncDitherUI();
  $("#screenFlash").addEventListener("animationend", () =>
    $("#screenFlash").classList.remove("active")
  );

  // ---------- 推送到设备 ----------
  let pushing = false;
  $("#pushBtn").addEventListener("click", async () => {
    if (!current || pushing) return;
    const btn = $("#pushBtn");
    const msg = $("#pushMsg");
    pushing = true;
    btn.disabled = true;
    btn.textContent = "推送中…";
    msg.hidden = true;
    try {
      const r = await fetch("/api/push", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: current.path,
          caption: $("#captionInput").value,
          place: current.city,
          date: current.date || "",
          ditherType: $("#ditherTypeSel").value,
          ditherKernel: $("#ditherKernelSel").value,
          border: $("#borderSel").value,
          refreshNow: true,
        }),
      });
      const d = await r.json();
      msg.hidden = false;
      msg.classList.toggle("error", !d.ok);
      if (d.ok && d.page) {
        msg.innerHTML = `${esc(d.message)} · <a href="${esc(d.page)}" target="_blank" rel="noopener">碰一碰页面 ↗</a>`;
        if (current) loadPushHistory(current.path);   // 推送记录即时刷新
      } else {
        msg.textContent = d.message || (d.ok ? "已推送" : "推送失败");
      }
    } catch {
      msg.hidden = false;
      msg.classList.add("error");
      msg.textContent = "请求失败：本地服务无响应";
    } finally {
      pushing = false;
      btn.disabled = false;
      btn.textContent = "推送到设备";
    }
  });
}

/* ---------- 启动 ---------- */

(async function init() {
  bindEvents();
  await Promise.all([loadStats(), loadTypes()]);
  await loadPhotos();
  if (photos.length) {
    selectPhoto(photos[0], { flash: false });
  }
})();

/* ---------- 深浅色切换 ---------- */
/* 已抽到 static/theme.js，主页与碰一碰页共用 */
