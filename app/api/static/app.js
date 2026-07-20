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
      // sinon on restaure sa position. En pause, on ne bouge JAMAIS le
      // scroll, même si l'utilisateur était en bas au moment de la pause.
      const logs = target.querySelector(".logs, .term-body");
      const paused = !!(window.mc && window.mc.termPaused);
      const wasAtBottom = !paused && (logs
        ? logs.scrollHeight - logs.scrollTop - logs.clientHeight < 8
        : true);
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


// Filtre des logs du terminal (niveau + texte, avec surlignage des
// correspondances et compteur). Les contrôles vivent dans la partie
// STATIQUE (term-foot) ; le fragment pollé est re-filtré après chaque swap
// via window.mc.applyLogFilter().
(function () {
  var text = document.getElementById("termFilterText");
  var lvls = document.getElementById("termFilterLvls");
  var count = document.getElementById("termSearchCount");
  if (!text && !lvls) return;
  var state = { lvl: "all", q: "" };

  function escapeHtml(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  // Surligne TOUTES les occurrences (scan par indexOf, jamais de regex —
  // la requête vient du navigateur, pas question d'y faire confiance).
  function highlightAll(raw, q) {
    if (!q) return escapeHtml(raw);
    var lower = raw.toLowerCase();
    var out = "";
    var i = 0;
    while (true) {
      var idx = lower.indexOf(q, i);
      if (idx === -1) { out += escapeHtml(raw.slice(i)); break; }
      out += escapeHtml(raw.slice(i, idx)) + "<mark>" + escapeHtml(raw.slice(idx, idx + q.length)) + "</mark>";
      i = idx + q.length;
    }
    return out;
  }

  function apply() {
    var lines = document.querySelectorAll(".term-body .ln");
    var matches = 0;
    Array.prototype.forEach.call(lines, function (ln) {
      var lvl = ln.getAttribute("data-lvl") || "info";
      // "info" regroupe tout ce qui n'est ni warn ni erreur (join/leave inclus).
      var lvlOk = state.lvl === "all"
        || (state.lvl === "info" && lvl !== "warn" && lvl !== "err")
        || lvl === state.lvl;
      var textEl = ln.querySelector(".ln-text");
      var raw = textEl ? textEl.textContent : ln.textContent;
      var qOk = !state.q || raw.toLowerCase().indexOf(state.q) !== -1;
      ln.style.display = lvlOk && qOk ? "" : "none";
      if (lvlOk && qOk && state.q) matches++;
      if (textEl) textEl.innerHTML = highlightAll(raw, state.q);
    });
    if (count) count.textContent = state.q ? (matches + (matches > 1 ? " lignes" : " ligne")) : "";
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

// Pause du défilement : gèle le scroll des logs pendant le polling (utile en
// pleine lecture d'un incident). État en mémoire (window.mc.termPaused), lu
// par la boucle de rafraîchissement au-dessus — pas de persistance, la
// pause ne doit pas survivre à un rechargement de page.
(function () {
  var toggle = document.getElementById("termPauseToggle");
  var rec = document.getElementById("termRec");
  if (!toggle) return;
  window.mc = window.mc || {};
  window.mc.termPaused = false;
  toggle.addEventListener("click", function () {
    window.mc.termPaused = !window.mc.termPaused;
    toggle.textContent = window.mc.termPaused ? "▶ reprendre" : "⏸ pause";
    toggle.setAttribute("aria-pressed", String(window.mc.termPaused));
    if (rec) rec.classList.toggle("paused", window.mc.termPaused);
  });
})();

// Copie d'une ligne de log. Délégué sur document (les lignes vivent dans le
// fragment pollé, remplacé toutes les 5 s — un listener posé directement
// dessus serait détruit au swap suivant).
(function () {
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".ln-copy");
    if (!btn) return;
    var textEl = btn.parentElement && btn.parentElement.querySelector(".ln-text");
    var raw = textEl ? textEl.textContent : "";
    if (!raw || !navigator.clipboard || !navigator.clipboard.writeText) return;
    navigator.clipboard.writeText(raw).then(function () {
      var original = btn.textContent;
      btn.textContent = "✓";
      setTimeout(function () { btn.textContent = original; }, 1200);
    });
  });
})();

// Commandes RCON favorites (owner) : indépendant de l'historique ↑↓
// (mc-cmd-history) — celles-ci sont choisies délibérément, jamais purgées
// automatiquement. Cliquer un favori REMPLIT le prompt sans l'envoyer :
// l'exécution reste un geste déclaré (Entrée / bouton envoyer), jamais un
// clic accidentel sur une commande RCON potentiellement destructive.
(function () {
  var KEY = "mc-cmd-favorites";
  var input = document.getElementById("termCmdInput");
  var starBtn = document.getElementById("termFavStar");
  var openBtn = document.getElementById("termFavOpen");
  var list = document.getElementById("termFavList");
  if (!input || !starBtn || !openBtn || !list) return;

  function load() {
    var values = [];
    try { values = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) {}
    return Array.isArray(values) ? values : [];
  }
  function save(favs) {
    try { localStorage.setItem(KEY, JSON.stringify(favs.slice(0, 20))); } catch (e) {}
  }
  function syncStar() {
    var v = input.value.trim();
    var fav = !!v && load().indexOf(v) !== -1;
    starBtn.textContent = fav ? "★" : "☆";
    starBtn.classList.toggle("on", fav);
  }
  function render() {
    var favs = load();
    list.innerHTML = "";
    if (!favs.length) {
      var empty = document.createElement("p");
      empty.className = "muted term-favs-empty";
      empty.textContent = "Aucun favori — ☆ pour en ajouter un.";
      list.appendChild(empty);
      return;
    }
    favs.forEach(function (cmd) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "term-fav-chip";
      chip.textContent = cmd;
      var rm = document.createElement("span");
      rm.className = "term-fav-rm";
      rm.textContent = "×";
      rm.setAttribute("aria-label", "Retirer des favoris");
      rm.addEventListener("click", function (e) {
        e.stopPropagation();
        save(load().filter(function (c) { return c !== cmd; }));
        render();
        syncStar();
      });
      chip.appendChild(rm);
      chip.addEventListener("click", function () {
        input.value = cmd;
        input.focus();
        list.hidden = true;
        syncStar();
      });
      list.appendChild(chip);
    });
  }

  starBtn.addEventListener("click", function () {
    var v = input.value.trim();
    if (!v) return;
    var favs = load();
    favs = favs.indexOf(v) !== -1 ? favs.filter(function (c) { return c !== v; }) : favs.concat([v]);
    save(favs);
    syncStar();
  });
  openBtn.addEventListener("click", function () {
    list.hidden = !list.hidden;
    if (!list.hidden) render();
  });
  input.addEventListener("input", syncStar);
  document.addEventListener("click", function (e) {
    if (!list.hidden && !list.contains(e.target) && e.target !== openBtn) list.hidden = true;
  });
})();
