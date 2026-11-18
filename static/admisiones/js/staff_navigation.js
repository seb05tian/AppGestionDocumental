const sidebarSlot = document.querySelector(".sidebar-slot");
const sidebar = sidebarSlot.querySelector(".reviewer-sidebar");
const sidebarDrawer = sidebarSlot.querySelector(".sidebar-drawer");
const sidebarTrigger = sidebarSlot.querySelector(".sidebar-trigger");
const sidebarClose = sidebarSlot.querySelector(".sidebar-close");
const sidebarFold = sidebarSlot.querySelector(".sidebar-fold");
const sidebarSearchToggle = sidebarSlot.querySelector(".sidebar-search-toggle");
const sidebarSearch = sidebarSlot.querySelector(".sidebar-search");
const sidebarQuery = sidebarSearch.querySelector("input");
const sidebarGroups = sidebar.querySelectorAll(".sidebar-nav section");
const sidebarNoResults = sidebar.querySelector(".sidebar-no-results");
const sidebarNarrow = matchMedia("(max-width: 760px)");
const sidebarPreference = "documental-menu-contraido";

let sidebarCollapsed = false;
try {
  sidebarCollapsed = sessionStorage.getItem(sidebarPreference) === "true";
} catch {
  // The menu still works when the browser disallows storage.
}

function filterSidebar() {
  const normalize = (text) => text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("es");
  const query = normalize(sidebarQuery.value.trim());
  let hasResults = false;
  for (const group of sidebarGroups) {
    const groupMatches = normalize(group.querySelector("h2").textContent).includes(query);
    let hasMatchingLink = false;
    for (const link of group.querySelectorAll(".sidebar-link")) {
      link.hidden = !groupMatches && !normalize(link.textContent).includes(query);
      hasMatchingLink ||= !link.hidden;
    }
    group.hidden = !hasMatchingLink;
    hasResults ||= hasMatchingLink;
  }
  sidebarNoResults.hidden = hasResults;
}

function showSidebarSearch(open) {
  sidebarSearch.hidden = !open;
  sidebarSearchToggle.setAttribute("aria-expanded", String(open));
  const label = open ? "Cerrar búsqueda del menú" : "Buscar en el menú";
  sidebarSearchToggle.setAttribute("aria-label", label);
  sidebarSearchToggle.title = label;
  sidebarSearchToggle.querySelector("use").setAttribute("href", open ? "#nav-close" : "#nav-search");
  if (open) {
    sidebarQuery.focus();
  } else {
    sidebarQuery.value = "";
    filterSidebar();
  }
}

function syncSidebar() {
  const narrow = sidebarNarrow.matches;
  const wasOpen = sidebarDrawer.open;
  sidebarTrigger.hidden = !narrow;
  sidebarClose.hidden = !narrow;
  sidebarFold.hidden = narrow;
  sidebarSearchToggle.hidden = !narrow && sidebarCollapsed;
  sidebarSlot.classList.toggle("is-collapsed", !narrow && sidebarCollapsed);

  const label = sidebarCollapsed ? "Expandir menú" : "Contraer menú";
  sidebarFold.setAttribute("aria-label", label);
  sidebarFold.setAttribute("aria-expanded", String(!sidebarCollapsed));
  sidebarFold.title = label;

  if (narrow) {
    sidebarDrawer.append(sidebar);
  } else if (sidebar.parentElement === sidebarDrawer) {
    sidebarDrawer.close();
    sidebarSlot.insertBefore(sidebar, sidebarDrawer);
    if (wasOpen) sidebarFold.focus();
  }
}

sidebarFold.addEventListener("click", () => {
  sidebarCollapsed = !sidebarCollapsed;
  try {
    sessionStorage.setItem(sidebarPreference, String(sidebarCollapsed));
  } catch {
    // The menu still works when the browser disallows storage.
  }
  showSidebarSearch(false);
  syncSidebar();
});
sidebarSearchToggle.addEventListener("click", () => showSidebarSearch(sidebarSearch.hidden));
sidebarQuery.addEventListener("input", filterSidebar);
sidebarQuery.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    showSidebarSearch(false);
    sidebarSearchToggle.focus();
  }
});

sidebarTrigger.addEventListener("click", () => {
  sidebarDrawer.showModal();
  sidebarTrigger.setAttribute("aria-expanded", "true");
  sidebarClose.focus();
});
sidebarClose.addEventListener("click", () => sidebarDrawer.close());
sidebarDrawer.addEventListener("click", (event) => {
  if (event.target === sidebarDrawer) sidebarDrawer.close();
});
sidebarDrawer.addEventListener("close", () => {
  sidebarTrigger.setAttribute("aria-expanded", "false");
  showSidebarSearch(false);
  if (sidebarNarrow.matches) sidebarTrigger.focus();
});

sidebarNarrow.addEventListener("change", () => {
  showSidebarSearch(false);
  syncSidebar();
});
syncSidebar();
