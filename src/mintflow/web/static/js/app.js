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

  // A failed request leaves the page as it was and says so in the status region, in the page's
  // language (the server puts the translated text on the region).
  document.addEventListener("htmx:responseError", function () {
    const status = document.getElementById("status");
    if (status) {
      status.textContent = status.dataset.errorMessage || "Something went wrong. Please try again.";
    }
  });

  // A page can load under a pointer resting on the sidebar (after a click on a section). The
  // browser applies that hover on the first paint, where the expansion delay does not act, so the
  // server renders the sidebar "resting" and it wakes when the pointer first moves over it; from
  // then on hovering expands it after the pause, and heading for the content never finds it open.
  const sidebar = document.querySelector(".sidebar");
  if (sidebar) {
    // Still over the sidebar a moment after moving: the user wants it, so wake it. Leaving it
    // (heading for the content) ends the resting state at once, so later hovers are ordinary.
    let wakeTimer = null;
    const wake = function () {
      clearTimeout(wakeTimer);
      sidebar.classList.remove("is-resting");
    };
    sidebar.addEventListener("pointermove", function () {
      if (sidebar.classList.contains("is-resting") && wakeTimer === null) {
        wakeTimer = setTimeout(wake, 150);
      }
    });
    sidebar.addEventListener("pointerleave", wake);
  }

  // The theme switch (design W13): the choice is kept in a cookie the server reads, so the next
  // page renders in it at once; without a choice the system setting applies.
  const themeCookie = "mintflow_theme";
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function effectiveTheme() {
    const chosen = document.documentElement.dataset.theme;
    return chosen || (darkQuery.matches ? "dark" : "light");
  }

  function syncThemeSwitches() {
    const dark = effectiveTheme() === "dark";
    for (const control of document.querySelectorAll("[data-theme-switch]")) {
      control.setAttribute("aria-checked", dark ? "true" : "false");
    }
  }

  document.addEventListener("click", function (event) {
    const control = event.target.closest && event.target.closest("[data-theme-switch]");
    if (!control) {
      return;
    }
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    const secure = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = themeCookie + "=" + next + "; Path=/; Max-Age=31536000; SameSite=Lax" + secure;
    syncThemeSwitches();
  });

  darkQuery.addEventListener("change", syncThemeSwitches);
  document.addEventListener("DOMContentLoaded", syncThemeSwitches);

  // Elements with data-redirect-after navigate there once their request succeeded.
  document.addEventListener("htmx:afterRequest", function (event) {
    const target = event.detail.elt.dataset.redirectAfter;
    if (target && event.detail.successful) {
      window.location.assign(target);
    }
  });
})();
