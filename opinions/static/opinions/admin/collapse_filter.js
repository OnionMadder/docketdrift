// Collapsible changelist filter sidebar for the Django admin.
//
// The Judge changelist (and other wide list views) has a right-hand FILTER
// sidebar that eats horizontal space and squeezes the inline-editable columns
// (courtlistener_id, cl_absent). This adds a "Hide filters" toggle to the
// top-right object-tools row that collapses the sidebar and lets the results
// take the full width. The choice is remembered in localStorage so it stays
// collapsed as you page through and edit.
//
// Vanilla JS, no deps -- matches the project's minimal-JS admin convention.
(function () {
  "use strict";
  var KEY = "dd-admin-filters-collapsed";

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }

  ready(function () {
    // Only act on changelists that actually have a filter sidebar.
    if (!document.getElementById("changelist-filter")) { return; }

    var link;
    function apply(collapsed) {
      document.body.classList.toggle("dd-filters-collapsed", collapsed);
      if (link) {
        link.textContent = collapsed ? "Show filters" : "Hide filters";
        link.setAttribute("aria-expanded", String(!collapsed));
      }
    }

    // Prefer an object-tools pill (top-right, inherits admin styling); fall
    // back to a floating button styled by the stylesheet.
    var tools = document.querySelector(".object-tools");
    if (tools) {
      var li = document.createElement("li");
      link = document.createElement("a");
      link.href = "#";
      link.id = "dd-filter-toggle";
      li.appendChild(link);
      tools.insertBefore(li, tools.firstChild);
    } else {
      link = document.createElement("button");
      link.type = "button";
      link.id = "dd-filter-toggle";
      link.className = "button";
      document.body.appendChild(link);
    }

    apply(localStorage.getItem(KEY) === "1");

    link.addEventListener("click", function (e) {
      e.preventDefault();
      var collapsed = document.body.classList.contains("dd-filters-collapsed");
      collapsed = !collapsed;
      try { localStorage.setItem(KEY, collapsed ? "1" : "0"); } catch (err) {}
      apply(collapsed);
    });
  });
})();
