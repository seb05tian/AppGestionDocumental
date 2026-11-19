(() => {
  const endpoint = new URL(location.href);
  endpoint.searchParams.set("analisis", "1");

  function schedule() {
    if (document.querySelector("[data-ai-pending]")) setTimeout(refresh, 5000);
  }

  async function refresh() {
    if (document.hidden) {
      schedule();
      return;
    }
    try {
      const response = await fetch(endpoint, { cache: "no-store" });
      if (response.redirected || [401, 403, 404].includes(response.status)) return;
      if (response.ok) {
        const template = document.createElement("template");
        template.innerHTML = await response.text();
        const results = template.content.querySelector("[data-ai-results]");
        if (!results) return;
        for (const section of document.querySelectorAll("[data-ai-pending]")) {
          const result = results.querySelector(`[data-ai-version="${section.dataset.aiVersion}"]`);
          if (!result) {
            section.querySelector(".automatic-status").textContent = "El documento cambió. Recarga el expediente.";
            section.querySelector(".subtle-text")?.remove();
            section.removeAttribute("data-ai-pending");
          } else {
            if (section.innerHTML !== result.innerHTML) section.replaceChildren(...result.childNodes);
            section.toggleAttribute("data-ai-pending", result.hasAttribute("data-ai-pending"));
          }
        }
      }
    } catch {
      // The next check can recover a temporary connection failure.
    }
    schedule();
  }

  schedule();
})();
