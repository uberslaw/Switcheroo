/* Pause HTMX swaps that would remount a focused select/input/textarea.
   Timer polls are not user-trusted; a real click (new port) still goes through. */
(function () {
  function focusedField() {
    const active = document.activeElement;
    if (!active) return null;
    if (/^(SELECT|INPUT|TEXTAREA)$/.test(active.tagName)) return active;
    return null;
  }

  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    const field = focusedField();
    if (!field) return;
    const triggering = evt.detail.requestConfig && evt.detail.requestConfig.triggeringEvent;
    const userClick = triggering && triggering.isTrusted;
    if (userClick) return;
    const target = evt.detail.target;
    if (target && typeof target.contains === "function" && target.contains(field)) {
      evt.preventDefault();
    }
  });

  document.body.addEventListener("htmx:afterRequest", function (evt) {
    const form = evt.detail.elt;
    if (!form || !form.matches) return;
    if (form.matches("form[hx-post*='request/vlan']") && evt.detail.successful) {
      form.reset();
    }
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    if (!evt.detail.target || evt.detail.target.id !== "toast-slot") return;
    const toast = evt.detail.target.querySelector(".toast");
    if (!toast) return;
    window.setTimeout(function () {
      toast.classList.add("toast-out");
      window.setTimeout(function () {
        if (toast.parentNode) toast.remove();
      }, 250);
    }, 4000);
  });
})();
