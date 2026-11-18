(() => {
  const CHEVRON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 15 5 5 5-5M7 9l5-5 5 5"/></svg>';
  const CHECK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';
  const enhanced = new WeakSet();
  let openClose = null;
  let nextId = 0;

  function enhance(select) {
    if (enhanced.has(select) || select.multiple || select.size > 1) return;
    enhanced.add(select);

    const id = `select-menu-${++nextId}`;
    const wrap = document.createElement("div");
    wrap.className = "select-menu";
    select.before(wrap);

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "select-menu-trigger";
    trigger.id = `${id}-trigger`;
    trigger.disabled = select.disabled;
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `${id}-list`);
    if (select.getAttribute("aria-invalid")) trigger.setAttribute("aria-invalid", "true");
    if (select.getAttribute("aria-describedby")) trigger.setAttribute("aria-describedby", select.getAttribute("aria-describedby"));
    const value = document.createElement("span");
    value.className = "select-menu-value";
    trigger.append(value);
    trigger.insertAdjacentHTML("beforeend", CHEVRON);

    const list = document.createElement("ul");
    list.className = "select-menu-list";
    list.id = `${id}-list`;
    list.setAttribute("role", "listbox");
    list.tabIndex = -1;
    list.hidden = true;

    select.hidden = true;
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");
    wrap.append(select, trigger, list);
    if (select.id) {
      for (const label of document.querySelectorAll(`label[for="${CSS.escape(select.id)}"]`)) label.htmlFor = trigger.id;
    }

    const renderValue = () => {
      const option = select.selectedOptions[0];
      value.textContent = option ? option.textContent.trim() : "";
    };

    const build = () => {
      list.replaceChildren(...[...select.options].map((option, index) => {
        const item = document.createElement("li");
        item.className = "select-menu-option";
        item.id = `${id}-option-${index}`;
        item.dataset.index = String(index);
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", String(option.selected));
        if (option.disabled) item.setAttribute("aria-disabled", "true");
        item.append(option.textContent.trim());
        item.insertAdjacentHTML("beforeend", CHECK);
        return item;
      }));
    };

    let active = 0;
    const setActive = (index) => {
      const items = list.children;
      if (!items.length) return;
      active = Math.max(0, Math.min(index, items.length - 1));
      [...items].forEach((item, position) => item.classList.toggle("is-active", position === active));
      items[active].scrollIntoView({ block: "nearest" });
      list.setAttribute("aria-activedescendant", items[active].id);
    };

    const close = (returnFocus = false) => {
      if (list.hidden) return;
      list.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      list.removeAttribute("aria-activedescendant");
      if (openClose === close) openClose = null;
      if (returnFocus) trigger.focus({ preventScroll: true });
    };

    const show = () => {
      if (openClose) openClose(false);
      build();
      list.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      setActive(select.selectedIndex >= 0 ? select.selectedIndex : 0);
      list.focus({ preventScroll: true });
      openClose = close;
    };

    const choose = (index) => {
      const option = select.options[index];
      if (!option || option.disabled) return;
      if (select.selectedIndex !== index) {
        select.selectedIndex = index;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      renderValue();
      close(true);
    };

    trigger.addEventListener("click", () => (list.hidden ? show() : close(true)));
    trigger.addEventListener("keydown", (event) => {
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && list.hidden) {
        event.preventDefault();
        show();
      }
    });
    list.addEventListener("keydown", (event) => {
      switch (event.key) {
        case "ArrowDown": event.preventDefault(); setActive(active + 1); break;
        case "ArrowUp": event.preventDefault(); setActive(active - 1); break;
        case "Home": event.preventDefault(); setActive(0); break;
        case "End": event.preventDefault(); setActive(list.children.length - 1); break;
        case "Enter":
        case " ": event.preventDefault(); choose(active); break;
        case "Escape": event.preventDefault(); close(true); break;
        case "Tab": close(false); break;
      }
    });
    list.addEventListener("click", (event) => {
      const item = event.target.closest("[data-index]");
      if (item) choose(Number(item.dataset.index));
    });
    list.addEventListener("pointermove", (event) => {
      const item = event.target.closest("[data-index]");
      if (item && Number(item.dataset.index) !== active) setActive(Number(item.dataset.index));
    });
    select.addEventListener("change", renderValue);
    renderValue();
  }

  document.addEventListener("pointerdown", (event) => {
    if (openClose && !event.target.closest(".select-menu")) openClose(false);
  });

  const scan = (node) => {
    if (node.matches?.("select")) enhance(node);
    node.querySelectorAll?.("select").forEach(enhance);
  };
  scan(document);
  new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) if (node.nodeType === 1) scan(node);
    }
  }).observe(document.body, { childList: true, subtree: true });
})();
