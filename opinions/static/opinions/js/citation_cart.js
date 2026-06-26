/* DocketDrift -- "Cite this case" + client-side citation cart (NH only).
 *
 * Privacy posture ("Data is sacred", see CLAUDE.md): this module is 100%
 * client-side. The cart lives in localStorage on the visitor's browser and
 * NEVER touches the server -- no fetch, no POST, no beacon. The set of cites
 * a researcher is assembling is work product; we cannot be subpoenaed for
 * what we never stored. Do not add a network call to this file.
 *
 * Loaded only on NH opinion_detail pages (template-side state.code gate).
 * Vanilla JS, no dependencies. Hydrates two things:
 *   1. the inline "Cite this case" tool (.cite-tool) on the page, and
 *   2. a floating cart FAB (built here, appended to <body>).
 */
(function () {
  "use strict";

  var STORAGE_KEY = "docketdrift.nh.citation_cart";

  /* ---------------------------------------------------------------- store */

  function readCart() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function writeCart(items) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
    } catch (e) {
      /* storage full or blocked (private mode): cart degrades to in-page only */
    }
  }

  /* ------------------------------------------------------------- clipboard */

  function legacyCopy(text, onOk) {
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      if (onOk) onOk();
    } catch (e) {
      /* clipboard blocked; nothing else we can do */
    }
  }

  function copyText(text, onOk) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { if (onOk) onOk(); },
        function () { legacyCopy(text, onOk); }
      );
    } else {
      legacyCopy(text, onOk);
    }
  }

  /* ------------------------------------------------------ cart operations */

  function inCart(opinionId) {
    var items = readCart();
    for (var i = 0; i < items.length; i++) {
      if (String(items[i].opinion_id) === String(opinionId)) return true;
    }
    return false;
  }

  function addToCart(item) {
    if (inCart(item.opinion_id)) return false;
    var items = readCart();
    items.push(item);
    writeCart(items);
    renderCart();
    return true;
  }

  function removeFromCart(opinionId) {
    var items = readCart().filter(function (it) {
      return String(it.opinion_id) !== String(opinionId);
    });
    writeCart(items);
    renderCart();
  }

  function clearCart() {
    writeCart([]);
    renderCart();
  }

  function exportText() {
    var items = readCart();
    var lines = [];
    for (var i = 0; i < items.length; i++) {
      lines.push((i + 1) + ". " + items[i].citation_text);
    }
    return lines.join("\n");
  }

  /* ----------------------------------------------------------- cite tool */

  function hydrateCiteTool() {
    var tool = document.querySelector(".cite-tool");
    if (!tool) return;

    var btn = tool.querySelector(".cite-tool__btn");
    var display = tool.querySelector(".citation-display");
    var textEl = tool.querySelector(".citation-display__text");
    var copyBtn = tool.querySelector(".citation-copy");
    var addBtn = tool.querySelector(".citation-add");
    var fmtBtn = tool.querySelector(".citation-format-toggle");
    if (!btn || !display || !textEl) return;

    var bluebook = tool.getAttribute("data-bluebook") || "";
    var plain = tool.getAttribute("data-plain") || "";
    var opinionId = tool.getAttribute("data-opinion-id") || "";
    var caseName = tool.getAttribute("data-case-name") || "";

    /* Toggle the inline panel (not a modal -- expands in place). */
    btn.addEventListener("click", function () {
      var open = display.hidden;
      display.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
    });

    /* Bluebook <-> plain quick-reference toggle (display only; the cart
       always stores the full Bluebook form). */
    if (fmtBtn) {
      fmtBtn.addEventListener("click", function () {
        var showingPlain = textEl.getAttribute("data-format") === "plain";
        if (showingPlain) {
          textEl.textContent = bluebook;
          textEl.setAttribute("data-format", "bluebook");
          fmtBtn.textContent = "(plain)";
          fmtBtn.setAttribute("aria-pressed", "false");
        } else {
          textEl.textContent = plain;
          textEl.setAttribute("data-format", "plain");
          fmtBtn.textContent = "(Bluebook)";
          fmtBtn.setAttribute("aria-pressed", "true");
        }
      });
    }

    function flashBtn(el, label) {
      var prev = el.textContent;
      el.textContent = label;
      el.classList.add("is-flashed");
      window.setTimeout(function () {
        el.textContent = prev;
        el.classList.remove("is-flashed");
      }, 1400);
    }

    if (copyBtn) {
      copyBtn.addEventListener("click", function () {
        copyText(textEl.textContent, function () {
          flashBtn(copyBtn, "Copied");
        });
      });
    }

    if (addBtn) {
      addBtn.addEventListener("click", function () {
        var added = addToCart({
          opinion_id: opinionId,
          citation_text: bluebook,
          case_name: caseName,
          added_at: Date.now()
        });
        flashBtn(addBtn, added ? "Added ✓" : "In cart");
      });
    }
  }

  /* ----------------------------------------------------------- cart FAB */

  var fab = null;     // floating "[N cites] View" button + panel container
  var fabCount = null;
  var fabPanel = null;
  var fabList = null;

  function buildFab() {
    fab = document.createElement("div");
    fab.className = "citation-cart-fab";
    fab.setAttribute("aria-live", "polite");

    var pill = document.createElement("button");
    pill.type = "button";
    pill.className = "citation-cart-pill";
    pill.setAttribute("aria-expanded", "false");

    var icon = document.createElement("span");
    icon.className = "citation-cart-pill__icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "§";   // section sign -- legal-cite glyph

    fabCount = document.createElement("span");
    fabCount.className = "citation-cart-pill__count";

    var label = document.createElement("span");
    label.className = "citation-cart-pill__label";
    label.textContent = "View";

    pill.appendChild(icon);
    pill.appendChild(fabCount);
    pill.appendChild(label);

    fabPanel = document.createElement("div");
    fabPanel.className = "citation-cart-panel";
    fabPanel.hidden = true;

    var head = document.createElement("div");
    head.className = "citation-cart-panel__head";
    var title = document.createElement("strong");
    title.textContent = "Citation cart";
    head.appendChild(title);
    var closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "citation-cart-close";
    closeBtn.setAttribute("aria-label", "Close citation cart");
    closeBtn.textContent = "×";
    head.appendChild(closeBtn);

    fabList = document.createElement("ol");
    fabList.className = "citation-cart-list";

    var actions = document.createElement("div");
    actions.className = "citation-cart-panel__actions";
    var copyAll = document.createElement("button");
    copyAll.type = "button";
    copyAll.className = "citation-cart-copyall";
    copyAll.textContent = "Copy all";
    var clear = document.createElement("button");
    clear.type = "button";
    clear.className = "citation-cart-clear";
    clear.textContent = "Clear cart";
    actions.appendChild(copyAll);
    actions.appendChild(clear);

    fabPanel.appendChild(head);
    fabPanel.appendChild(fabList);
    fabPanel.appendChild(actions);

    fab.appendChild(fabPanel);
    fab.appendChild(pill);
    document.body.appendChild(fab);

    pill.addEventListener("click", function () {
      var open = fabPanel.hidden;
      fabPanel.hidden = !open;
      pill.setAttribute("aria-expanded", String(open));
    });
    closeBtn.addEventListener("click", function () {
      fabPanel.hidden = true;
      pill.setAttribute("aria-expanded", "false");
    });

    copyAll.addEventListener("click", function () {
      copyText(exportText(), function () {
        var prev = copyAll.textContent;
        copyAll.textContent = "Copied ✓";
        copyAll.classList.add("is-flashed");
        window.setTimeout(function () {
          copyAll.textContent = prev;
          copyAll.classList.remove("is-flashed");
        }, 1400);
      });
    });

    clear.addEventListener("click", function () {
      if (window.confirm("Clear all cites from the cart?")) {
        clearCart();
      }
    });

    /* Remove a single item (event-delegated on the list). */
    fabList.addEventListener("click", function (e) {
      var rm = e.target.closest ? e.target.closest(".citation-cart-item__remove") : null;
      if (!rm) return;
      removeFromCart(rm.getAttribute("data-opinion-id"));
    });
  }

  function renderCart() {
    var items = readCart();

    if (!items.length) {
      if (fab) fab.hidden = true;
      return;
    }
    if (!fab) buildFab();
    fab.hidden = false;
    fabCount.textContent = items.length + (items.length === 1 ? " cite" : " cites");

    /* Rebuild the list (oldest first -- cart insertion order). */
    while (fabList.firstChild) fabList.removeChild(fabList.firstChild);
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "citation-cart-item";

      var text = document.createElement("span");
      text.className = "citation-cart-item__text";
      text.textContent = it.citation_text;

      var rm = document.createElement("button");
      rm.type = "button";
      rm.className = "citation-cart-item__remove";
      rm.setAttribute("data-opinion-id", String(it.opinion_id));
      rm.setAttribute("aria-label", "Remove from cart");
      rm.textContent = "×";

      li.appendChild(text);
      li.appendChild(rm);
      fabList.appendChild(li);
    });
  }

  /* ------------------------------------------------------------- bootstrap */

  function init() {
    hydrateCiteTool();
    renderCart();

    /* Keep the FAB in sync if another tab on the same browser edits the
       cart (localStorage 'storage' event fires in OTHER tabs only). */
    window.addEventListener("storage", function (e) {
      if (e.key === STORAGE_KEY) renderCart();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
