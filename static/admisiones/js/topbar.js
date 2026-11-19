(() => {
  // The bar wraps to two rows on small screens; keep the sidebar offset and anchor padding in step with its real height.
  const bar = document.querySelector(".topbar");
  if (!bar || !window.ResizeObserver) return;
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--topbar-height", `${Math.ceil(bar.getBoundingClientRect().height)}px`);
  }).observe(bar);
})();
