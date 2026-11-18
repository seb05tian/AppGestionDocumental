(() => {
  const dialog = document.querySelector(".management-dialog");
  if (!dialog?.showModal) return;

  const title = dialog.querySelector("h2");
  const loading = dialog.querySelector(".management-dialog-loading");
  const error = dialog.querySelector(".management-dialog-error");
  const formSlot = dialog.querySelector(".management-dialog-form");
  const headers = { "X-Management-Dialog": "1" };
  let opener;
  let loadRequest;
  let saving = false;
  let backdropGesture = false;

  async function readForm(response) {
    const html = new DOMParser().parseFromString(await response.text(), "text/html");
    const form = html.querySelector("[data-management-form]");
    if (!form) throw new Error("Missing form");
    return form;
  }

  function showForm(form) {
    formSlot.replaceChildren(form);
    title.textContent = form.dataset.title;
    const target = form.querySelector("[aria-invalid='true']:not([type='hidden'])")
      || form.querySelector(".notice a")
      || form.querySelector("input:not([type='hidden']), select, textarea");
    target?.focus();
  }

  document.addEventListener("click", async (event) => {
    const trigger = event.target.closest("a[data-management-dialog]");
    if (!trigger || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    if (dialog.open || trigger.origin !== location.origin) return;

    event.preventDefault();
    const navigationDrawer = trigger.closest(".sidebar-drawer[open]");
    opener = navigationDrawer ? document.querySelector(".sidebar-trigger") : trigger;
    navigationDrawer?.close();
    title.textContent = trigger.dataset.managementDialog;
    formSlot.replaceChildren();
    error.hidden = true;
    loading.hidden = false;
    dialog.showModal();

    const request = new AbortController();
    loadRequest = request;
    try {
      const response = await fetch(trigger.href, { headers, signal: request.signal, redirect: "error" });
      if (!response.ok) throw new Error("Unavailable form");
      const form = await readForm(response);
      if (loadRequest === request && dialog.open) showForm(form);
    } catch {
      if (loadRequest !== request || !dialog.open) return;
      const fallback = document.createElement("a");
      fallback.href = trigger.href;
      fallback.textContent = "Abrir formulario en una página";
      error.replaceChildren("No pudimos cargar el formulario. ", fallback);
      error.hidden = false;
    } finally {
      if (loadRequest === request) loading.hidden = true;
    }
  });

  dialog.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.matches("[data-management-form]")) return;
    event.preventDefault();
    const submit = form.querySelector("button[type='submit']");
    if (saving || submit.disabled) return;

    const body = new FormData(form);
    const fields = form.querySelector(".management-form-body");
    const submitLabel = submit.textContent;
    const closeControls = dialog.querySelectorAll("[data-management-close]");
    saving = true;
    error.hidden = true;
    fields.inert = true;
    submit.disabled = true;
    submit.textContent = "Guardando…";
    for (const control of closeControls) control.disabled = true;
    let unconfirmed = false;
    const request = new AbortController();
    const timeout = setTimeout(() => request.abort(), 20000);

    try {
      const response = await fetch(form.action, {
        method: "POST", body, headers, redirect: "error", signal: request.signal,
      });
      if (response.status === 422 || response.headers.get("Content-Type")?.includes("text/html")) {
        showForm(await readForm(response));
      } else {
        if (!response.ok) throw new Error("Save not confirmed");
        const result = await response.json();
        if (typeof result.location !== "string") throw new Error("Missing destination");
        const destination = new URL(result.location, location.href);
        if (destination.origin !== location.origin) throw new Error("Invalid destination");
        dialog.close();
        if (destination.pathname === location.pathname && !destination.search) location.reload();
        else location.assign(destination);
      }
    } catch {
      unconfirmed = true;
      error.textContent = "No pudimos confirmar el guardado. Cierra el formulario y revisa el listado o el expediente antes de intentarlo de nuevo.";
      error.hidden = false;
    } finally {
      clearTimeout(timeout);
      saving = false;
      fields.inert = false;
      submit.disabled = unconfirmed;
      submit.textContent = submitLabel;
      for (const control of closeControls) control.disabled = false;
    }
  });

  dialog.addEventListener("cancel", (event) => {
    if (saving) event.preventDefault();
  });
  dialog.addEventListener("pointerdown", (event) => {
    backdropGesture = event.isPrimary && event.button === 0 && event.target === dialog;
  });
  dialog.addEventListener("pointerup", (event) => { backdropGesture &&= event.target === dialog; });
  dialog.addEventListener("pointercancel", () => { backdropGesture = false; });
  dialog.addEventListener("click", (event) => {
    if (!saving && (event.target.closest("[data-management-close]") || (backdropGesture && event.target === dialog))) dialog.close();
    backdropGesture = false;
  });
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    loadRequest?.abort();
    loadRequest = null;
    formSlot.replaceChildren();
    opener?.focus({ preventScroll: true });
  });
})();
