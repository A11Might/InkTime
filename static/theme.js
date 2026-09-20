/* 深浅色主题：首帧前由页面 head 内联脚本定值，这里负责按钮交互与跟随系统 */
(function () {
  const KEY = "inktime-theme";
  const root = document.documentElement;
  const btn = document.getElementById("themeToggle");
  const system = matchMedia("(prefers-color-scheme: dark)");

  const resolved = () => {
    const stored = localStorage.getItem(KEY);
    if (stored === "light" || stored === "dark") return stored;
    return system.matches ? "dark" : "light";   // 未手动选过 → 跟随系统
  };

  function paint() {
    const t = resolved();
    root.dataset.theme = t;
    if (!btn) return;
    btn.dataset.resolved = t;
    const label = t === "dark" ? "切换到浅色模式" : "切换到深色模式";
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }

  if (btn) {
    btn.addEventListener("click", () => {
      localStorage.setItem(KEY, resolved() === "dark" ? "light" : "dark");
      paint();
    });
  }

  // 未手动选择时跟随系统实时变化
  system.addEventListener("change", paint);

  paint();
})();
