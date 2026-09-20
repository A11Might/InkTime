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
    ditherType: ditherType || "ORDERED",
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
  const url = renderURL(current, opts);
  const flashEl = $("#screenFlash");
  img.onload = () => {
    if (flash) {
      flashEl.classList.remove("active");
      void flashEl.offsetWidth; // 重新触发动画
      flashEl.classList.add("active");
    }
  };
  img.src = url;
}

function selectPhoto(p, { scroll = false, flash = true } = {}) {
  current = p;
  document.querySelectorAll(".card, .pick").forEach((el) => {
    el.classList.toggle("selected", el.dataset.path === p.path);
  });
  $("#captionInput").value = captionOverrides.get(p.path) ?? p.caption;
  $("#panelInfo").textContent = `回忆度 ${p.memory} · 美观度 ${p.beauty}`;
  $("#pushBtn").disabled = false;
  refreshPreview({ flash });
  if (scroll && window.innerWidth <= 960) {
    $(".preview-col").scrollIntoView({ behavior: "smooth", block: "start" });
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
  $("#ditherTypeSel").addEventListener("change", (e) => {
    // 抖动算法只在「误差扩散」下生效
    $("#ditherKernelSel").disabled = e.target.value !== "DIFFUSION";
    refreshPreview({ flash: true });
  });
  $("#ditherKernelSel").addEventListener("change", () => refreshPreview({ flash: true }));
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
(function () {
  const KEY = "inktime-theme";
  const root = document.documentElement;
  const btn = document.getElementById("themeToggle");
  if (!btn) return;
  const system = matchMedia("(prefers-color-scheme: dark)");

  const resolved = () => {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") return stored;
    return system.matches ? "dark" : "light";   // 未手动选过 → 跟随系统
  };

  function paint() {
    const t = resolved();
    root.dataset.theme = t;
    btn.dataset.resolved = t;
    const label = t === "dark" ? "切换到浅色模式" : "切换到深色模式";
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }

  btn.addEventListener("click", () => {
    const next = resolved() === "dark" ? "light" : "dark";
    localStorage.setItem(KEY, next);
    paint();
  });

  // 未手动选择时跟随系统实时变化
  system.addEventListener("change", paint);

  paint();
})();
