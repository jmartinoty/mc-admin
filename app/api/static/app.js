// Rafraîchit le fragment de status par polling (pas de SPA, pas de dépendance).
// Volontairement en vanilla JS : asset local unique, embarquable en iframe sans
// CDN externe. Le swap remplace uniquement #status-body.
(function () {
  const INTERVAL_MS = 5000;
  const target = document.getElementById("status-body");
  if (!target) return;

  // Au chargement : montrer la fin des logs (les lignes les plus récentes).
  const initialLogs = target.querySelector(".logs, .term-body");
  if (initialLogs) initialLogs.scrollTop = initialLogs.scrollHeight;

  async function refresh() {
    try {
      const res = await fetch("/fragment/status", { headers: { "X-Requested-With": "fetch" } });
      if (!res.ok) {
        if (window.mc && window.mc.reportNetwork) window.mc.reportNetwork("status", false);
        return;
      }
      const html = await res.text();
      if (window.mc && window.mc.reportNetwork) window.mc.reportNetwork("status", true);

      // Le swap innerHTML réinitialise le scroll des logs : on préserve la
      // lecture. Si l'utilisateur était en bas (défaut), on y reste collé ;
      // sinon on restaure sa position.
      const logs = target.querySelector(".logs, .term-body");
      const wasAtBottom = logs
        ? logs.scrollHeight - logs.scrollTop - logs.clientHeight < 8
        : true;
      const prevScroll = logs ? logs.scrollTop : 0;

      target.innerHTML = html;

      if (window.mc && window.mc.applyLogFilter) window.mc.applyLogFilter();

      const newLogs = target.querySelector(".logs, .term-body");
      if (newLogs) newLogs.scrollTop = wasAtBottom ? newLogs.scrollHeight : prevScroll;
    } catch (_) {
      if (window.mc && window.mc.reportNetwork) window.mc.reportNetwork("status", false);
    }
  }

  setInterval(refresh, INTERVAL_MS);
})();

// Terminal : "/" pour focus le prompt, Échap pour en sortir, historique des
// commandes (flèches haut/bas), persisté en localStorage. Le prompt vit hors
// de la zone rafraîchie, il n'est donc pas remplacé par le polling.
(function () {
  var input = document.querySelector(".term-prompt input[name='command']");
  if (!input) return;
  var form = input.form;

  document.addEventListener("keydown", function (e) {
    var el = document.activeElement;
    var typing = el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
    if (e.key === "/" && !typing) {
      e.preventDefault();
      e.stopPropagation();
      input.focus();
    }
  }, true);

  var KEY = "mc-cmd-history";
  function loadHistory() {
    var values = [];
    try { values = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) {}
    return Array.isArray(values) ? values : [];
  }
  var hist = loadHistory();
  var idx = hist.length;

  function moveCursorEnd() { var v = input.value; input.value = ""; input.value = v; }
  function rememberCommand() {
    var v = input.value.trim();
    if (!v) return;
    hist = loadHistory().filter(function (c) { return c !== v; });
    hist.push(v);
    if (hist.length > 50) hist = hist.slice(-50);
    try { localStorage.setItem(KEY, JSON.stringify(hist)); } catch (e) {}
    idx = hist.length;
  }

  if (form) {
    form.addEventListener("submit", rememberCommand, true);
  }

  input.addEventListener("focus", function () { hist = loadHistory(); idx = hist.length; });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      input.blur();
    } else if (e.key === "ArrowUp") {
      hist = loadHistory();
      if (idx > 0) { idx--; input.value = hist[idx]; e.preventDefault(); e.stopPropagation(); moveCursorEnd(); }
    } else if (e.key === "ArrowDown") {
      hist = loadHistory();
      if (idx < hist.length - 1) { idx++; input.value = hist[idx]; }
      else { idx = hist.length; input.value = ""; }
      e.preventDefault(); e.stopPropagation(); moveCursorEnd();
    }
  }, true);
})();


// Filtre des logs du terminal (niveau + texte). Les contrôles vivent dans la
// partie STATIQUE (term-foot) ; le fragment pollé est re-filtré après chaque
// swap via window.mc.applyLogFilter().
(function () {
  var text = document.getElementById("termFilterText");
  var lvls = document.getElementById("termFilterLvls");
  if (!text && !lvls) return;
  var state = { lvl: "all", q: "" };

  function apply() {
    var lines = document.querySelectorAll(".term-body .ln");
    Array.prototype.forEach.call(lines, function (ln) {
      var lvl = ln.getAttribute("data-lvl") || "info";
      // "info" regroupe tout ce qui n'est ni warn ni erreur (join/leave inclus).
      var lvlOk = state.lvl === "all"
        || (state.lvl === "info" && lvl !== "warn" && lvl !== "err")
        || lvl === state.lvl;
      var qOk = !state.q || ln.textContent.toLowerCase().indexOf(state.q) !== -1;
      ln.style.display = lvlOk && qOk ? "" : "none";
    });
  }
  window.mc = window.mc || {};
  window.mc.applyLogFilter = apply;

  if (text) text.addEventListener("input", function () {
    state.q = text.value.trim().toLowerCase();
    apply();
  });
  if (lvls) lvls.addEventListener("click", function (e) {
    var btn = e.target.closest("button[data-lvl]");
    if (!btn) return;
    state.lvl = btn.getAttribute("data-lvl");
    Array.prototype.forEach.call(lvls.querySelectorAll("button"), function (b) {
      b.classList.toggle("on", b === btn);
    });
    apply();
  });
})();
