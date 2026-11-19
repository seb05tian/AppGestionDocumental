(() => {
  let tip = null;
  let current = null;

  const hide = () => {
    current = null;
    if (tip) tip.hidden = true;
  };

  const show = (target) => {
    current = target;
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "icon-tooltip";
      tip.setAttribute("aria-hidden", "true");
      document.body.append(tip);
    }
    tip.textContent = target.dataset.tooltip;
    tip.hidden = false;
    const box = target.getBoundingClientRect();
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    let top = box.top - height - 8;
    const below = top < 4;
    if (below) top = box.bottom + 8;
    const left = Math.min(Math.max(4, box.left + box.width / 2 - width / 2), innerWidth - width - 4);
    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;
    tip.style.setProperty("--arrow-x", `${box.left + box.width / 2 - left}px`);
    tip.classList.toggle("is-below", below);
  };

  document.addEventListener("pointerover", (event) => {
    const target = event.target.closest?.("[data-tooltip]");
    if (target && target !== current) show(target);
    else if (!target && current) hide();
  });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest?.("[data-tooltip]");
    if (target) show(target);
  });
  document.addEventListener("focusout", hide);
  document.addEventListener("click", hide);
  document.addEventListener("scroll", hide, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hide();
  });
})();
