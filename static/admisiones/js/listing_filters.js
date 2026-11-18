const filterPanel = document.querySelector(".filter-disclosure");
if (filterPanel) {
  const compactFilters = matchMedia("(max-width: 760px)");
  const sync = () => {
    filterPanel.open = !compactFilters.matches || filterPanel.hasAttribute("data-has-selection")
      || filterPanel.contains(document.activeElement);
  };
  sync();
  compactFilters.addEventListener("change", sync);
}

const filterForm = document.querySelector(".filter-form");
if (filterForm) {
  let searchTimer;
  const applyFilters = (field) => {
    // Keep the focus where the person was typing or choosing, so it is still there after the page reloads.
    try { sessionStorage.setItem("filterFocus", field.name); } catch {}
    filterForm.requestSubmit();
  };
  filterForm.addEventListener("change", (event) => {
    if (event.target.name !== "q") applyFilters(event.target);
  });
  filterForm.addEventListener("input", (event) => {
    if (event.target.name !== "q") return;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => applyFilters(event.target), 450);
  });
  try {
    const focusName = sessionStorage.getItem("filterFocus");
    if (focusName) {
      sessionStorage.removeItem("filterFocus");
      const field = document.querySelector(`.filter-form [name="${CSS.escape(focusName)}"]`);
      (field?.closest(".select-menu")?.querySelector(".select-menu-trigger") ?? field)?.focus({ preventScroll: true });
    }
  } catch {}
}
