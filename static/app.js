/* InkTime console 前端逻辑：画廊 + 墨水屏预览 */
"use strict";

const $ = (s) => document.querySelector(s);
const state = { sort: "memory", q: "", types: [], day: true };
let photos = [];
let current = null;
const captionOverrides = new Map();

/* ---------- 筛选：今日选片（与每日推送同一回退逻辑）+ 类型多选 ---------- */
let typeList = [];            // /api/types 返回的全部标签
let resolvedDay = "";         // 今日选片解析出的那天（YYYY-MM-DD）
let todayIsToday = false;     // 落点就是今天（未发生回退）
let todayCount = 0;           // 落点当天的候选场景数（叠+单张，与推送口径一致）

/* ---------- 瀑布流：JS 分列（从左到右逐行排序）+ 无限滚动 ---------- */
const PAGE_SIZE = 60;          // 每页张数，滚动临近底部时自动追加
const COL_GAP = 16;
let busy = false;              // 首屏或翻页请求在途
let exhausted = false;         // 当前筛选下已加载完全部
let reqSeq = 0;                // 筛选条件变更后丢弃在途的旧响应
let colEls = [];
let placedCount = 0;           // 已放入列的卡片数，用于轮询分列
const cardEls = new Map();     // path → 已渲染的卡片元素（重新分列时复用 DOM）

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (d) => (d || "").slice(0, 10);
const thumbURL = (p, size) =>
  `/api/thumb?path=${encodeURIComponent(p.path)}&v=${p.w}x${p.h}` + (size ? `&size=${size}` : "");

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
  $("#mToday").textContent = s.today_count;
  $("#mTodayDate").textContent = s.today;
  resolvedDay = s.today || "";
  todayIsToday = !!s.today_is_today;
  todayCount = s.today_count || 0;
  renderChips();
}

async function loadTypes() {
  typeList = await fetch("/api/types").then((r) => r.json());
  renderChips();
}

/* 「全部」= 清空所有筛选（点它回全库，此时才点亮）；「今日选片」开关默认开；
   类型多选 chips。今日选片紧随全部之后 */
function renderChips() {
  if (!typeList.length) return;
  const allBtn = `<button class="chip-btn${!state.day && !state.types.length ? " active" : ""}" data-type="全部" title="清空所有筛选，看全部照片">全部</button>`;
  const dayBtn = resolvedDay
    ? `<button class="chip-btn chip-day${state.day ? " active" : ""}" data-chip="day"
        title="${todayIsToday ? "只看历史上的今天" : "今天没达标场景，已按推送逻辑回退到这一天"}">今日选片${todayIsToday ? "" : " · " + resolvedDay.slice(5)} · ${todayCount} 场景</button>`
    : "";
  const typeBtns = typeList.filter((t) => t !== "全部").map((t) => {
    const active = state.types.includes(t);
    return `<button class="chip-btn${active ? " active" : ""}" data-type="${esc(t)}">${esc(t)}</button>`;
  });
  $("#typeChips").innerHTML = allBtn + dayBtn + typeBtns.join("");
}

function cardHTML(p) {
  return `
      <div class="slot">
        <div class="deck" aria-hidden="true"></div>
        <article class="card" data-path="${esc(p.path)}" tabindex="0">
          <div class="thumb"><img loading="lazy" src="${thumbURL(p)}"${p.w && p.h ? ` style="aspect-ratio:${p.w}/${p.h}"` : ""} alt=""><span class="stack-badge mono"></span></div>
          <div class="card-body">
            <p class="caption">${esc(p.caption)}</p>
            <div class="card-meta">
              <span class="meta mono">${fmtDate(p.date) || "无日期"}${p.city ? " · " + esc(p.city) : ""}</span>
              <span class="type-chip">${esc(p.type)}</span>
            </div>
          </div>
        </article>
      </div>`;
}

/* 列数与 CSS 时代一致：桌面按 200px 最小列宽自适应（最多 4 列），窄屏固定 2 列 */
function columnCount() {
  if (window.innerWidth <= 960) return 2;
  const fit = Math.floor(($("#grid").clientWidth + COL_GAP) / (200 + COL_GAP));
  return Math.max(1, Math.min(4, fit));
}

function layoutColumns() {
  const grid = $("#grid");
  grid.innerHTML = "";
  colEls = Array.from({ length: columnCount() }, () => {
    const c = document.createElement("div");
    c.className = "grid-col";
    grid.appendChild(c);
    return c;
  });
  placedCount = 0;
}

/* 轮询分列：第 i 张进第 i%n 列。名次严格按行递增（1,2,3 / 4,5,6 / …从左到右），
   横竖构图是打分后自然混排，长期来看各列高度接近 */
function placeCard(card) {
  colEls[placedCount % colEls.length].appendChild(card);
  placedCount++;
}

/* 相似分叠：stack_id 来自分析阶段的 dHash 指纹 + 时间聚类（analyze_photos.rebuild_stacks） */
let stacks = new Map();       // stack_id → { members, badge, card, slot, coverPath, repPhoto }
let flowItems = [];           // 实际占了版面的照片（各叠的代表张），重排用

/* ---------- 卡牌堆查看器：点卡片 → 屏幕中间弹出一叠牌（Swiper Cards 式），
   拖动/←→ 翻牌，单击/回车把当前牌映射到右侧墨水屏预览 ---------- */
const VIEWER_BEHIND = 2;      // 顶牌后面垫几张
const VIEWER_FLY_PX = 120;    // 拖动超过这个距离松手 = 飞出翻牌
let viewerEl = null;          // 弹层 DOM（复用）
let viewerItems = null;       // 当前这叠牌的全部照片
let viewerIdx = 0;            // 顶牌序号
let viewerStack = null;       // 弹的是哪一叠（单张为 null）
let viewerDrag = null;        // 进行中的拖动
let viewerFlying = false;     // 飞出动画中（忽略输入）
let viewerGestureAt = 0;      // 上次手势（拖动/翻页）时刻：其衍生的 click 不当作“点牌外”退出

function viewerCardEl(m, idx) {
  const el = document.createElement("button");
  el.className = "cardv";
  el.dataset.idx = String(idx);   // 位置由 viewerSync 统一摆（追加同帧即定位，不会闪）
  el.innerHTML = `
    <span class="cardv-inner">
      <img loading="lazy" draggable="false" src="${thumbURL(m, 1000)}"${m.w && m.h ? ` style="aspect-ratio:${m.w}/${m.h}"` : ""} alt="">
      <span class="cardv-cap">${esc(m.caption || fmtDate(m.date))}</span>
      ${viewerStack && m.path === viewerStack.coverPath ? '<span class="cardv-cover mono">封面</span>' : ""}
    </span>`;
  return el;
}

// 垫牌的位置：Swiper cards 式——每层上移 9px、转 2.2°、微缩放
function viewerStackTransform(o) {
  return `translateX(-50%) translateY(${-o * 9}px) rotate(${(o * 2.2).toFixed(2)}deg) scale(${(1 - o * 0.035).toFixed(3)})`;
}

function viewerSync() {
  const stage = viewerEl.querySelector(".cardv-stage");
  const len = viewerItems.length;
  const maxO = Math.min(3, len - 1);   // 可见 0..2 层，第 3 层藏住做缓冲
  const want = [];
  for (let o = 0; o <= maxO; o++) want.push((viewerIdx + o) % len);
  for (const el of [...stage.children]) {
    const o = (Number(el.dataset.idx) - viewerIdx + len) % len;
    if (o > maxO && !el.classList.contains("fly")) el.remove();
  }
  for (const idx of want) {
    if (!stage.querySelector(`.cardv[data-idx="${idx}"]`)) {
      stage.appendChild(viewerCardEl(viewerItems[idx], idx));
    }
  }
  for (const el of stage.children) {
    if (el.classList.contains("fly")) continue;
    const o = (Number(el.dataset.idx) - viewerIdx + len) % len;
    el.style.zIndex = String(10 - o);
    el.style.opacity = o > VIEWER_BEHIND ? "0" : "1";
    el.style.transform = viewerStackTransform(o);
  }
  const m = viewerItems[viewerIdx];
  viewerEl.querySelector(".cardv-meta").textContent =
    `${m.date || "无日期"}${m.city ? " · " + m.city : ""} · 回忆度 ${m.memory} ｜ ${viewerIdx + 1} / ${len}`;
}

function viewerFly(dir) {
  // 循环牌堆：无论往左还是往右翻，顶牌都绕到整叠最后面、下一张顶上来，首尾循环。
  // dir 只决定顶牌飞出动画顺着哪个方向滑（跟着拖动方向 / 按键走）
  const len = viewerItems.length;
  if (viewerFlying || len < 2) return false;
  viewerFlying = true;
  viewerGestureAt = Date.now();
  const stage = viewerEl.querySelector(".cardv-stage");
  const top = stage.querySelector(`.cardv[data-idx="${viewerIdx}"]`);
  viewerIdx = (viewerIdx + 1) % len;
  const newO = (Number(top.dataset.idx) - viewerIdx + len) % len;   // 原顶牌的新深度（= 最后面）
  if (newO <= VIEWER_BEHIND) {
    viewerFlying = false;
    viewerSync();                                // 小叠：顶牌直接滑进牌堆最后面
  } else {
    // 大叠：顶牌顺着拖动方向滑出、z 压到牌堆后面塞进去，淡出落进隐藏缓冲层
    const side = dir === 1 ? "-" : "";
    top.classList.add("fly");
    top.style.zIndex = "1";
    top.style.transform = `translateX(calc(-50% + ${side}230px)) translateY(-27px) rotate(${side}9deg) scale(0.895)`;
    top.style.opacity = "0";
    setTimeout(() => {
      const o = (Number(top.dataset.idx) - viewerIdx + len) % len;
      if (o <= 3) {
        top.classList.remove("fly");
        top.style.zIndex = String(10 - o);
        top.style.transform = viewerStackTransform(o);
        top.style.opacity = "0";
      } else {
        top.remove();
      }
      viewerFlying = false;
    }, 300);
    viewerSync();                                // 其余牌前进；这张会从牌堆底部绕回来
  }
  return true;
}

function openViewer(items, stack = null) {
  if (!viewerEl) {
    viewerEl = document.createElement("div");
    viewerEl.className = "cardv-modal";
    viewerEl.innerHTML = `
      <div class="cardv-stage"></div>
      <div class="cardv-info">
        <span class="cardv-meta mono"></span>
        <span class="cardv-hint mono">拖动 / ← → 翻牌 · 单击选中 · Esc 关闭</span>
      </div>`;
    const stage = viewerEl.querySelector(".cardv-stage");

    stage.addEventListener("pointerdown", (e) => {
      if (viewerFlying) return;
      const top = stage.querySelector(`.cardv[data-idx="${viewerIdx}"]`);
      if (!top || e.target.closest(".cardv") !== top) return;   // 只有顶牌可拖
      viewerDrag = { sx: e.clientX, sy: e.clientY, dx: 0, dy: 0, el: top };
      top.style.transition = "none";   // 拖动跟手，不带过渡
      stage.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    stage.addEventListener("pointermove", (e) => {
      if (!viewerDrag) return;
      const d = viewerDrag;
      d.dx = e.clientX - d.sx;
      d.dy = e.clientY - d.sy;
      d.el.style.transform = `translateX(calc(-50% + ${d.dx}px)) translateY(${(d.dy * 0.35).toFixed(1)}px) rotate(${(d.dx * 0.055).toFixed(2)}deg)`;
      // 拖动时下一张垫牌随进度顶上来（两个方向翻都是前进，进度都按横向位移算）
      const p = Math.min(Math.abs(d.dx) / VIEWER_FLY_PX, 1);
      for (const el of stage.children) {
        const o = Number(el.dataset.idx) - viewerIdx;
        if (o <= 0 || el.classList.contains("fly")) continue;
        el.style.transform = viewerStackTransform(Math.max(o - p, 0));
      }
    });
    const endDrag = (e) => {
      if (!viewerDrag) return;
      const { dx, dy, el } = viewerDrag;
      viewerGestureAt = Date.now();   // 拖动衍生的 click（被捕获到 stage）不当作“点牌外”
      viewerDrag = null;
      el.style.transition = "";
      if (Math.abs(dx) < 6 && Math.abs(dy) < 24) {
        confirmViewer();                                    // 原地松手 = 单击选中（竖拖不算）
      } else if (Math.abs(dx) < VIEWER_FLY_PX || !viewerFly(dx < 0 ? 1 : -1)) {
        viewerSync();                                       // 不够阈值或到头了，弹回
      }
    };
    stage.addEventListener("pointerup", endDrag);
    stage.addEventListener("pointercancel", endDrag);
    viewerEl.addEventListener("click", (e) => {
      // 点照片以外的任何地方（遮罩/牌面上下左右空白/信息栏）都退出预览；
      // 拖动/翻页会衍生落在牌外的 click（指针捕获到 stage），350ms 内不当作退出
      if (!e.target.closest(".cardv") && Date.now() - viewerGestureAt > 350) closeViewer();
    });
    document.addEventListener("keydown", (e) => {
      if (!viewerEl || viewerEl.hidden) return;
      if (e.key === "ArrowRight") { viewerFly(1); e.preventDefault(); }
      else if (e.key === "ArrowLeft") { viewerFly(-1); e.preventDefault(); }
      else if (e.key === "Enter") { e.preventDefault(); confirmViewer(); }
      else if (e.key === "Escape") closeViewer();
    });
    document.body.appendChild(viewerEl);
  }
  viewerStack = stack;
  viewerItems = items;
  // 有封面的叠从封面那张开起
  let start = 0;
  if (stack) {
    const ci = items.findIndex((x) => x.path === stack.coverPath);
    if (ci > 0) start = ci;
  }
  viewerIdx = start;
  viewerFlying = false;
  viewerEl.querySelector(".cardv-stage").innerHTML = "";
  viewerSync();
  viewerEl.hidden = false;
  document.body.style.overflow = "hidden";   // 弹层开着时锁住后面页面的滚动
}

function confirmViewer() {
  const m = viewerItems?.[viewerIdx];
  if (m && viewerStack && viewerStack.members.length > 1) {
    setCover(viewerStack, m);   // 挑中谁，谁当这叠的封面
    // 封面选择写库持久化：清同叠旧封面 + 标记新封面；失败不打断（下次选时再写）
    fetch("/api/cover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: m.path }),
    }).catch(() => {});
  }
  closeViewer();
  if (m) selectPhoto(m);
}

/* 选中的成员成为这叠的封面：画廊卡片立即换成它，推送它 */
function setCover(stack, m) {
  const old = stack.repPhoto;
  if (old && old.path === m.path) return;
  stack.coverPath = m.path;
  stack.repPhoto = m;
  const card = stack.card;
  const img = card.querySelector(".thumb img");
  img.src = thumbURL(m);
  if (m.w && m.h) img.style.aspectRatio = `${m.w}/${m.h}`;
  card.querySelector(".caption").textContent = m.caption;
  card.querySelector(".card-meta .meta").textContent =
    `${fmtDate(m.date) || "无日期"}${m.city ? " · " + m.city : ""}`;
  card.querySelector(".type-chip").textContent = m.type;
  card.dataset.path = m.path;  const fi = flowItems.indexOf(old);
  if (fi !== -1) flowItems[fi] = m;
  if (old && cardEls.get(old.path) === stack.slot) {
    cardEls.delete(old.path);
    cardEls.set(m.path, stack.slot);
  }
}

function closeViewer() {
  if (viewerEl) viewerEl.hidden = true;
  document.body.style.overflow = "";   // 解锁页面滚动
  viewerItems = null;
  viewerStack = null;
  viewerDrag = null;
}

function renderCards(items) {
  for (const p of items) {
    const key = p.stack ?? "";   // stack_id 直接做 key（从 1 起）；无叠用 "" 占位
    const hit = key && stacks.get(key);
    if (hit) {
      hit.members.push(p);
      hit.badge.textContent = `×${hit.members.length}`;
      hit.card.classList.add("stacked");
      hit.slot.classList.add("has-stack");
      if (p.cover && hit.coverPath !== p.path) setCover(hit, p);   // 封面成员到了，卡片换成它
      continue;
    }
    const t = document.createElement("template");
    t.innerHTML = cardHTML(p);
    const slot = t.content.firstElementChild;
    const card = slot.querySelector(".card");
    cardEls.set(p.path, slot);
    if (key) {
      stacks.set(key, {
        members: [p], badge: slot.querySelector(".stack-badge"),
        card, slot, coverPath: p.path, repPhoto: p,
      });
    }
    flowItems.push(p);
    placeCard(slot);
  }
}

/* 窗口宽度跨过列数临界点时，按原顺序重新分列（复用已渲染的 DOM） */
function relayout() {
  if (!flowItems.length || columnCount() === colEls.length) return;
  layoutColumns();
  for (const p of flowItems) {
    const el = cardEls.get(p.path);
    if (el) placeCard(el);
  }
}

function updateFoot(loaded, total, mode) {
  const foot = $("#gridFoot");
  if (mode === "loading") {
    foot.hidden = false;
    foot.textContent = total ? `加载中… ${loaded} / ${total}` : "加载中…";
  } else if (loaded === 0) {
    foot.hidden = true;
  } else if (mode === "done") {
    foot.hidden = false;
    foot.textContent = total ? `共 ${total} 张 · 已全部加载` : `共 ${loaded} 张`;
  } else {
    foot.hidden = false;
    foot.textContent = total ? `已加载 ${loaded} / ${total} 张` : `已加载 ${loaded} 张`;
  }
}

function nearBottom() {
  const r = $("#gridFoot").getBoundingClientRect();
  return r.top < window.innerHeight + 1000;
}

async function fetchPage(offset) {
  const params = { sort: state.sort };
  if (state.q) params.q = state.q;
  if (state.types.length) params.types = state.types.join(",");
  if (state.day && resolvedDay) {
    params.md = resolvedDay.slice(5);   // 跨年匹配月-日（历史上的今天）
  }
  const q = new URLSearchParams({ ...params, limit: PAGE_SIZE, offset });
  const r = await fetch(`/api/photos?${q}`);
  return {
    items: await r.json(),
    total: Number(r.headers.get("X-Total-Count")) || 0,
  };
}

async function loadPhotos() {
  const seq = ++reqSeq;
  busy = true;
  exhausted = false;
  photos = [];
  cardEls.clear();
  stacks = new Map();
  flowItems = [];
  layoutColumns();
  updateFoot(0, 0, "loading");
  try {
    const { items, total } = await fetchPage(0);
    if (seq !== reqSeq) return;
    photos = items;
    exhausted = items.length < PAGE_SIZE;
    if (!photos.length) {
      $("#grid").innerHTML = "";   // 撤掉空列，让提示独占整行
      const empty = document.createElement("div");
      empty.className = "empty-note";
      empty.textContent = "没有匹配的照片，换个筛选条件试试。";
      $("#grid").appendChild(empty);
      return;
    }
    layoutColumns();
    renderCards(photos);
    updateFoot(photos.length, total, exhausted ? "done" : "idle");
    if (!current) selectPhoto(photos[0], { flash: false });
  } finally {
    if (seq === reqSeq) {
      busy = false;
      // 哨兵仍在视口附近就继续补页：IO 只在状态变化时触发，
      // 快速滚到底/重置后停在短页面时，靠这里把后续页接上
      if (!exhausted && nearBottom()) loadMore();
    }
  }
}

async function loadMore() {
  if (busy || exhausted) return;
  const seq = reqSeq;
  busy = true;
  updateFoot(photos.length, 0, "loading");
  try {
    const { items, total } = await fetchPage(photos.length);
    if (seq !== reqSeq) return;
    photos = photos.concat(items);
    renderCards(items);
    exhausted = items.length < PAGE_SIZE;
    updateFoot(photos.length, total, exhausted ? "done" : "idle");
  } catch {
    /* 拉取失败不打断浏览：继续滚动会再次尝试 */
  } finally {
    if (seq === reqSeq) {
      busy = false;
      if (!exhausted && nearBottom()) loadMore();
    }
  }
}

/* ---------- 墨水屏预览 ---------- */

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
    if (btn.dataset.chip === "day") {
      state.day = !state.day;   // 开关：点一次筛当天，再点一次回到全部
    } else if (btn.dataset.type === "全部") {
      state.types = [];         // 全部 = 清空所有筛选，回全库
      state.day = false;
    } else {
      const t = btn.dataset.type;
      state.types = state.types.includes(t)
        ? state.types.filter((x) => x !== t)
        : state.types.concat(t);
    }
    renderChips();
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

  // 点卡片：叠 → 弹出这叠的卡牌堆；单张 → 同样弹出（只有一张，单击即选中）
  const activateCard = (card) => {
    const p = photos.find((x) => x.path === card.dataset.path);
    if (!p) return;
    const stack = p.stack != null ? stacks.get(p.stack) : null;
    if (stack && stack.members.length > 1) openViewer(stack.members, stack);
    else openViewer([p]);
  };
  $("#grid").addEventListener("click", (e) => {
    const card = e.target.closest(".card");
    if (card) activateCard(card);
  });
  $("#grid").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest(".card");
    if (!card) return;
    e.preventDefault();
    activateCard(card);
  });

  // 无限滚动：IO 为主（页面可见时零开销），500ms 轮询兜底——
  // webview 被遮挡/节流时 IO 不派发回调，轮询保证滚动到附近总能续页
  const maybeLoadMore = () => { if (!busy && !exhausted && nearBottom()) loadMore(); };
  const io = new IntersectionObserver(
    (entries) => { if (entries.some((e) => e.isIntersecting)) maybeLoadMore(); },
    { rootMargin: "1000px 0px" },
  );
  io.observe($("#gridFoot"));
  setInterval(maybeLoadMore, 500);

  // 窗口宽度跨过列数临界点时重排
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(relayout, 150);
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
  await loadPhotos();   // 首页加载完自动选中第一名（loadPhotos 内处理）
})();

/* ---------- 深浅色切换 ---------- */
/* 已抽到 static/theme.js，主页与碰一碰页共用 */
