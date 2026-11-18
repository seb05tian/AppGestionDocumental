(function () {
  "use strict";

  function dismiss(toast) {
    if (toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    setTimeout(function () { toast.remove(); }, 300);
  }

  document.querySelectorAll("[data-toast]").forEach(function (toast) {
    var remaining = Number(toast.dataset.toastDuration) || 5000;
    var startedAt = Date.now();
    var timer = setTimeout(function () { dismiss(toast); }, remaining);

    // Pause while the pointer is over the message so it can be read.
    toast.addEventListener("mouseenter", function () {
      clearTimeout(timer);
      remaining -= Date.now() - startedAt;
    });
    toast.addEventListener("mouseleave", function () {
      startedAt = Date.now();
      timer = setTimeout(function () { dismiss(toast); }, Math.max(remaining, 1000));
    });
    toast.querySelector(".toast-close").addEventListener("click", function () { dismiss(toast); });
  });
})();
