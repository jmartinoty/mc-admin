// Comportements communs à toutes les pages authentifiées.
window.mc = window.mc || {};

(function () {
  // Les dialogues ouverts par un bouton rendent le focus à ce bouton à leur
  // fermeture, y compris pour les dialogues imbriqués.
  var openers = new WeakMap();

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("button, a");
    if (!trigger) return;
    requestAnimationFrame(function () {
      Array.prototype.forEach.call(document.querySelectorAll("dialog[open]"), function (dialog) {
        if (!openers.has(dialog) && !dialog.contains(trigger)) {
          openers.set(dialog, trigger);
        }
      });
    });
  }, true);

  Array.prototype.forEach.call(document.querySelectorAll("dialog"), function (dialog) {
    dialog.addEventListener("close", function () {
      var opener = openers.get(dialog);
      openers.delete(dialog);
      if (opener && opener.isConnected) opener.focus();
    });
  });
})();

(function () {
  // Dialogue « Détails » d'un toast d'erreur : texte technique complet,
  // copiable — le toast ne porte que le message court et le code.
  var dialog = document.getElementById("errorDetails");
  var open = document.getElementById("errorDetailsOpen");
  if (dialog && open) {
    open.addEventListener("click", function () { dialog.showModal(); });
    var closeBtn = document.getElementById("errorDetailsClose");
    if (closeBtn) closeBtn.addEventListener("click", function () { dialog.close(); });
    var copy = document.getElementById("errorDetailsCopy");
    var text = document.getElementById("errorDetailsText");
    if (copy && text) {
      copy.addEventListener("click", function () {
        var content = text.textContent || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(content).then(function () {
            copy.textContent = "Copié ✓";
            setTimeout(function () { copy.textContent = "Copier"; }, 2000);
          });
        }
      });
    }
  }
})();

(function () {
  var box = document.getElementById("toasts");
  if (!box) return;

  function dismiss(toast) {
    if (!toast || toast.classList.contains("out")) return;
    toast.classList.add("out");
    setTimeout(function () { toast.remove(); }, 350);
  }

  function arm(toast) {
    var timeout = Number(toast.getAttribute("data-timeout") || 8000);
    var timer = setTimeout(function () { dismiss(toast); }, timeout);
    var close = toast.querySelector(".toast-close");
    if (close) {
      close.addEventListener("click", function () {
        clearTimeout(timer);
        dismiss(toast);
      });
    }
  }

  Array.prototype.forEach.call(box.children, arm);
  window.mc.toast = function (message, kind, timeout) {
    var toast = document.createElement("div");
    var level = kind || "ok";
    toast.className = "toast " + level;
    toast.setAttribute("role", level === "error" ? "alert" : "status");
    toast.setAttribute("data-timeout", String(timeout || (level === "error" ? 12000 : 8000)));

    var text = document.createElement("span");
    text.textContent = message;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Fermer ce message");
    close.textContent = "×";
    toast.appendChild(text);
    toast.appendChild(close);
    box.appendChild(toast);
    arm(toast);
  };
})();

(function () {
  // État uniforme des formulaires : une soumission valide ne peut pas partir
  // deux fois. Les dimensions des boutons restent stables, seul leur état
  // disabled/aria-busy change.
  function markFormBusy(form) {
    if (!form || form.getAttribute("method") === "dialog" || form.dataset.busy === "true") return;
    form.dataset.busy = "true";
    form.setAttribute("aria-busy", "true");
    Array.prototype.forEach.call(
      form.querySelectorAll("button:not([type]), button[type='submit'], input[type='submit']"),
      function (control) {
        control.disabled = true;
        control.setAttribute("aria-disabled", "true");
      }
    );
  }

  window.mc.markFormBusy = markFormBusy;
  window.mc.submitForm = function (form) {
    markFormBusy(form);
    HTMLFormElement.prototype.submit.call(form);
  };

  document.addEventListener("submit", function (event) {
    var form = event.target;
    setTimeout(function () {
      if (!event.defaultPrevented) markFormBusy(form);
    }, 0);
  });
})();

(function () {
  // L'état réseau agrège les pollings. Un appel qui réussit ne masque pas la
  // panne d'une autre source encore en échec.
  var status = document.getElementById("networkStatus");
  var sources = {};
  var wasOffline = false;

  function sync() {
    var offline = Object.keys(sources).some(function (key) {
      return sources[key].failures >= 2;
    });
    if (status) status.hidden = !offline;
    if (wasOffline && !offline && window.mc.toast) {
      window.mc.toast("Connexion rétablie. Les données sont à nouveau actualisées.", "ok");
    }
    wasOffline = offline;
  }

  window.mc.reportNetwork = function (source, ok) {
    var current = sources[source] || { failures: 0 };
    current.failures = ok ? 0 : current.failures + 1;
    sources[source] = current;
    sync();
  };
})();

(function () {
  var dialog = document.getElementById("appConfirm");
  if (!dialog) return;

  var text = document.getElementById("appConfirmText");
  var checkRow = document.getElementById("appConfirmCheckRow");
  var check = document.getElementById("appConfirmCheck");
  var checkLabel = document.getElementById("appConfirmCheckLabel");
  var pending = null;
  var opener = null;

  function openConfirm(form) {
    if (!form) return;
    pending = form;
    opener = document.activeElement;
    text.textContent = form.getAttribute("data-confirm") || "Confirmer ?";
    // Case optionnelle (data-confirm-check) : décochée par défaut à CHAQUE
    // ouverture — un choix « forcer » ne doit jamais survivre d'un dialogue
    // au suivant.
    var checkText = form.getAttribute("data-confirm-check") || "";
    if (checkRow) {
      checkRow.hidden = !checkText;
      if (checkLabel) checkLabel.textContent = checkText;
      if (check) check.checked = false;
    }
    if (dialog.showModal) dialog.showModal();
    else window.mc.submitForm(form);
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".js-confirm-open");
    if (!button) return;
    event.preventDefault();
    openConfirm(button.closest("form"));
  });

  document.addEventListener("submit", function (event) {
    var form = event.target.closest ? event.target.closest("form.js-confirm-form") : null;
    if (form && pending !== form) {
      event.preventDefault();
      openConfirm(form);
    }
  }, true);

  document.getElementById("appConfirmCancel").addEventListener("click", function () {
    pending = null;
    dialog.close();
  });

  document.getElementById("appConfirmOk").addEventListener("click", function () {
    if (!pending) return;
    var form = pending;
    pending = null;
    dialog.close();
    // La case est reportée comme un vrai champ de formulaire : même
    // sémantique qu'une checkbox native, absente si décochée.
    var name = form.getAttribute("data-confirm-check-name");
    if (name) {
      var previous = form.querySelector("input[data-confirm-injected]");
      if (previous) previous.remove();
      if (check && check.checked && checkRow && !checkRow.hidden) {
        var extra = document.createElement("input");
        extra.type = "hidden";
        extra.name = name;
        extra.value = "yes";
        extra.setAttribute("data-confirm-injected", "");
        form.appendChild(extra);
      }
    }
    window.mc.submitForm(form);
  });

  dialog.addEventListener("close", function () {
    pending = null;
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  });
})();

(function () {
  var banner = document.querySelector("[data-activity-banner]");
  if (!banner) return;

  var title = document.getElementById("activityTitle");
  var message = document.getElementById("activityText");
  var meter = document.getElementById("activityMeter");
  var bar = document.getElementById("activityProgressBar");
  var percent = document.getElementById("activityProgressPct");
  var hint = document.getElementById("activityHintText");
  var details = document.getElementById("activityDetails");
  var action = document.getElementById("activityAction");
  var actionButton = document.getElementById("activityActionButton");
  var close = document.getElementById("activityClose");
  var terminal = { success: true, failed: true, idle: true };
  var statusUrl = banner.getAttribute("data-status-url") || "/activity-status";
  var lastPhase = banner.getAttribute("data-phase") || "idle";
  var lastKind = banner.getAttribute("data-kind") || "idle";
  var previousOperationActive = !terminal[lastPhase] && (
    lastKind === "backup" || lastKind === "restore"
  );

  function setHint(data, isResult) {
    if (isResult && data.archive) {
      hint.textContent = data.archive;
    } else if (data.active && data.kind === "restore") {
      hint.textContent = (data.filename || "") + " · sauvegardes et restaurations en pause.";
    } else {
      hint.textContent = data.archive || "";
    }
    if (data.detail_url) {
      details.href = data.detail_url;
      details.textContent = data.detail_label || "Voir le détail";
      details.hidden = false;
    } else {
      details.hidden = true;
      details.removeAttribute("href");
      details.textContent = "";
    }
    if (data.action_url) {
      action.action = data.action_url;
      actionButton.textContent = data.action_label || "Agir";
      action.hidden = false;
    } else {
      action.hidden = true;
      action.removeAttribute("action");
      actionButton.textContent = "";
    }
  }

  function render(data) {
    var phase = data.phase || "idle";
    var kind = data.kind || "idle";
    var isResult = phase === "success" || phase === "failed";
    var visible = !!data.active || isResult;
    var operationActive = !!data.active && (kind === "backup" || kind === "restore");
    var operationFinished = previousOperationActive && !operationActive;

    banner.classList.toggle("is-hidden", !visible);
    banner.classList.remove(
      "activity-backup", "activity-restore", "activity-waiting",
      "activity-success", "activity-error"
    );
    if (isResult) banner.classList.add(phase === "success" ? "activity-success" : "activity-error");
    else if (kind === "restore") banner.classList.add("activity-restore");
    else if (kind === "restart") banner.classList.add("activity-waiting");
    else if (kind === "backup") banner.classList.add("activity-backup");

    banner.setAttribute("data-phase", phase);
    banner.setAttribute("data-kind", kind);
    banner.setAttribute("role", phase === "failed" ? "alert" : "status");
    banner.setAttribute("aria-live", phase === "failed" ? "assertive" : "polite");
    title.textContent = data.title || "";
    message.textContent = data.message || "";
    setHint(data, isResult);

    close.hidden = !isResult;
    meter.hidden = !data.active || isResult;
    if (data.active && (data.progress_percent === null || data.progress_percent === undefined)) {
      meter.removeAttribute("aria-valuenow");
      bar.classList.add("indeterminate");
      bar.style.width = "";
      percent.textContent = "";
    } else if (data.active) {
      var value = Math.max(0, Math.min(100, Number(data.progress_percent)));
      meter.setAttribute("aria-valuenow", String(value));
      bar.classList.remove("indeterminate");
      bar.style.width = value + "%";
      percent.textContent = value + "%";
    }

    lastPhase = phase;
    lastKind = kind;
    previousOperationActive = operationActive;
    if (operationFinished) {
      window.dispatchEvent(new CustomEvent("mc:activity-finished", { detail: data }));
    }
  }

  function poll() {
    fetch(statusUrl, { headers: { "X-Requested-With": "fetch" } })
      .then(function (response) {
        if (!response.ok) throw new Error("activity status unavailable");
        return response.json();
      })
      .then(function (data) {
        window.mc.reportNetwork("activity", true);
        if (data && data.authenticated !== false) render(data);
      })
      .catch(function () {
        window.mc.reportNetwork("activity", false);
      })
      .finally(function () {
        var active = !terminal[lastPhase];
        setTimeout(poll, active ? 2000 : 5000);
      });
  }

  setTimeout(poll, 500);
})();

(function () {
  var button = document.getElementById("userMenuBtn");
  var menu = document.getElementById("userMenu");
  if (button && menu) {
    function closeMenu(returnFocus) {
      menu.setAttribute("hidden", "");
      button.setAttribute("aria-expanded", "false");
      if (returnFocus) button.focus();
    }
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      if (menu.hasAttribute("hidden")) {
        menu.removeAttribute("hidden");
        button.setAttribute("aria-expanded", "true");
      } else {
        closeMenu(false);
      }
    });
    menu.addEventListener("click", function (event) { event.stopPropagation(); });
    document.addEventListener("click", function () { closeMenu(false); });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !menu.hasAttribute("hidden")) closeMenu(true);
    });
  }

  var order = ["auto", "dark", "light"];
  var label = document.getElementById("themeLabel");
  var current = "auto";
  try { current = localStorage.getItem("mc-theme") || "auto"; } catch (error) {}

  function themeName(value) {
    if (value === "dark") return "sombre";
    if (value === "light") return "clair";
    return "auto";
  }

  if (label) label.textContent = themeName(current);
  var toggle = document.getElementById("themeToggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      current = order[(order.indexOf(current) + 1) % order.length];
      try { localStorage.setItem("mc-theme", current); } catch (error) {}
      if (current === "auto") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.setAttribute("data-theme", current);
      if (label) label.textContent = themeName(current);
    });
  }
})();


(function () {
  // Menu mobile : replié par défaut, ☰ le déplie ; suivre un lien le referme.
  var burger = document.getElementById("navBurger");
  if (!burger) return;
  var sidebar = burger.closest(".sidebar");
  function setOpen(open) {
    sidebar.classList.toggle("open", open);
    document.documentElement.classList.toggle("nav-open", open);
    burger.setAttribute("aria-expanded", open ? "true" : "false");
    burger.setAttribute("aria-label", open ? "Fermer le menu" : "Ouvrir le menu");
  }
  burger.addEventListener("click", function () {
    setOpen(!sidebar.classList.contains("open"));
  });
  sidebar.addEventListener("click", function (event) {
    if (event.target.closest(".navitem")) setOpen(false);
  });
  document.addEventListener("click", function (event) {
    if (!sidebar.classList.contains("open")) return;
    if (!sidebar.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && sidebar.classList.contains("open")) {
      setOpen(false);
      burger.focus();
    }
  });
})();
