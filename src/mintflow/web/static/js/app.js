// MintFlow Web: the only first-party script (web design W3). No inline scripts are allowed.
"use strict";

(function () {
  function cookie(name) {
    const prefix = name + "=";
    for (const part of document.cookie.split(";")) {
      const trimmed = part.trim();
      if (trimmed.startsWith(prefix)) {
        return decodeURIComponent(trimmed.slice(prefix.length));
      }
    }
    return null;
  }

  // Send the session-bound CSRF token with every htmx request, as the server requires.
  document.addEventListener("htmx:configRequest", function (event) {
    const body = document.body;
    const token = cookie(body.dataset.csrfCookie);
    if (token !== null) {
      event.detail.headers[body.dataset.csrfHeader] = token;
    }
  });

  // Elements with data-redirect-after navigate there once their request succeeded.
  document.addEventListener("htmx:afterRequest", function (event) {
    const target = event.detail.elt.dataset.redirectAfter;
    if (target && event.detail.successful) {
      window.location.assign(target);
    }
  });
})();
