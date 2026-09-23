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

  // After a partial update, move focus to the new content so keyboard and screen-reader users
  // land on it (web design W6): the last element matching data-focus-target, which for
  // appended pages is the first row of the newest page.
  // data-focus-first focuses the first match instead, for example the first invalid field.
  document.addEventListener("htmx:afterSettle", function (event) {
    const dataset = event.detail.elt.dataset || {};
    if (dataset.focusFirst) {
      const first = document.querySelector(dataset.focusFirst);
      if (first) {
        first.focus();
        return;
      }
    }
    const matches = dataset.focusTarget ? document.querySelectorAll(dataset.focusTarget) : [];
    if (matches.length > 0) {
      matches[matches.length - 1].focus();
    }
  });

  // Forms with data-warn-unsaved ask before the page is left with unsaved changes.
  let unsaved = false;
  document.addEventListener("input", function (event) {
    if (event.target.closest && event.target.closest("form[data-warn-unsaved]")) {
      unsaved = true;
    }
  });
  document.addEventListener("htmx:beforeRequest", function (event) {
    if (event.detail.elt.matches && event.detail.elt.matches("form[data-warn-unsaved]")) {
      unsaved = false;
    }
  });
  window.addEventListener("beforeunload", function (event) {
    if (unsaved) {
      event.preventDefault();
    }
  });

  // A failed request leaves the page as it was and says so in the status region.
  document.addEventListener("htmx:responseError", function () {
    const status = document.getElementById("status");
    if (status) {
      status.textContent = "Something went wrong. Please try again.";
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
