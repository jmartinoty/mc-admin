// Défilement et zoom du graphe MSPT.
//
// AMÉLIORATION PROGRESSIVE : le gabarit rend déjà un graphe complet et les
// liens 24 h / 3 j / 7 j fonctionnent seuls. Ce script ne fait qu'AJOUTER le
// glisser et le zoom ; s'il ne s'exécute pas, la page reste celle d'avant.
//
// La géométrie reste calculée par le SERVEUR (/fragments/mspt.json renvoie des
// coordonnées SVG toutes faites) : l'échelle qui inclut toujours 0 et le seuil,
// la coupure des trous de mesure et le placement des repères sont déjà écrits
// et testés côté Python — les réimplémenter ici serait deux vérités pour une
// donnée. Le navigateur n'envoie que des BORNES, jamais une requête.
(function () {
  var wrap = document.querySelector("[data-perf-chart]");
  if (!wrap || !window.fetch || !window.AbortController) return;

  var NS = "http://www.w3.org/2000/svg";
  var svg = wrap.querySelector("[data-perf-svg]");
  var mobile = wrap.querySelector("[data-perf-mobile]");
  var seuil = wrap.querySelector("[data-perf-seuil]");
  var axe = wrap.querySelector("[data-perf-axe]");
  var pic = document.querySelector("[data-perf-pic]");
  var etiqFenetre = document.querySelector("[data-perf-fenetre]");
  var etiqTrous = document.querySelector("[data-perf-trous]");
  var badge = document.querySelector("[data-perf-badge]");
  var legendeJoueurs = document.querySelector("[data-perf-legende-joueurs]");
  var etiqJmax = document.querySelector("[data-perf-jmax]");
  var url = wrap.getAttribute("data-url");
  if (!svg || !mobile || !url) return;

  // Doivent rester alignées sur MonitoringMixin (PERFORMANCE_SPAN_MIN/MAX,
  // PERFORMANCE_RETENTION) : le serveur reborne de toute façon et renvoie la
  // fenêtre réellement servie, ces valeurs n'évitent qu'un aller-retour inutile.
  var MIN_SPAN = 900, MAX_SPAN = 30 * 86400, RETENTION = 30 * 86400;
  var VUE = 600;                       // largeur du viewBox rendu par le serveur

  var debut = parseFloat(wrap.getAttribute("data-debut"));
  var fin = parseFloat(wrap.getAttribute("data-fin"));
  if (!isFinite(debut) || !isFinite(fin) || fin <= debut) return;

  function maintenant() { return Date.now() / 1000; }
  function largeurPx() { return svg.getBoundingClientRect().width || 1; }

  function borner() {
    var now = maintenant(), plancher = now - RETENTION;
    var largeur = Math.min(MAX_SPAN, Math.max(MIN_SPAN, fin - debut));
    fin = Math.min(now, Math.max(plancher + largeur, fin));
    debut = fin - largeur;
  }

  // ---- rendu ----

  function vider(noeud) { while (noeud.firstChild) noeud.removeChild(noeud.firstChild); }

  function repere(m) {
    var g = document.createElementNS(NS, "g");
    g.setAttribute("class", "perf-marker perf-marker-" + m.kind);
    var titre = document.createElementNS(NS, "title");
    titre.textContent = m.label + " · " + m.at;   // textContent : jamais d'innerHTML
    var trait = document.createElementNS(NS, "line");
    trait.setAttribute("x1", m.x); trait.setAttribute("y1", "4");
    trait.setAttribute("x2", m.x); trait.setAttribute("y2", "136");
    trait.setAttribute("vector-effect", "non-scaling-stroke");
    var point = document.createElementNS(NS, "circle");
    point.setAttribute("cx", m.x); point.setAttribute("cy", "6"); point.setAttribute("r", "3");
    g.appendChild(titre); g.appendChild(trait); g.appendChild(point);
    return g;
  }

  function majBadge(data) {
    // Le badge est LE verdict de la page : il décrit la fenêtre AFFICHÉE.
    // Sans verdict (fenêtre vide, Prometheus injoignable), il DISPARAÎT :
    // « absence de mesure » n'est ni bonne ni mauvaise, et `over` absent de
    // ces réponses donnait `undefined` -> badge basculé à tort en « sous le
    // seuil » (revue du 25/08).
    if (!badge) return;
    if (typeof data.over === "undefined") { badge.hidden = true; return; }
    badge.hidden = false;
    badge.textContent = data.over ? "seuil dépassé" : "sous le seuil";
    badge.classList.toggle("ko", !!data.over);
    badge.classList.toggle("ok", !data.over);
  }

  function dessiner(data) {
    mobile.removeAttribute("transform");
    majBadge(data);
    if (data.indisponible) { wrap.classList.add("perf-muet"); return; }
    wrap.classList.remove("perf-muet");
    vider(mobile);
    (data.joueurs || []).forEach(function (points) {
      var ligne = document.createElementNS(NS, "polyline");
      ligne.setAttribute("class", "perf-players");
      ligne.setAttribute("vector-effect", "non-scaling-stroke");
      ligne.setAttribute("points", points);
      mobile.appendChild(ligne);
    });
    (data.segments || []).forEach(function (points) {
      var ligne = document.createElementNS(NS, "polyline");
      ligne.setAttribute("class", "perf-line");
      ligne.setAttribute("vector-effect", "non-scaling-stroke");
      ligne.setAttribute("points", points);
      mobile.appendChild(ligne);
    });
    if (legendeJoueurs) {
      // La légende suit la COURBE, pas la fenêtre d'origine : défiler d'une
      // fenêtre habitée vers une fenêtre sans mesure joueurs (ou l'inverse)
      // doit la faire apparaître/disparaître avec les polylines.
      var aDesJoueurs = (data.joueurs || []).length > 0;
      legendeJoueurs.hidden = !aDesJoueurs;
      if (etiqJmax && aDesJoueurs && data.joueurs_max) {
        etiqJmax.textContent = data.joueurs_max;
      }
    }
    (data.markers || []).forEach(function (m) { mobile.appendChild(repere(m)); });
    if (seuil && data.threshold_y != null) {
      seuil.setAttribute("y1", data.threshold_y);
      seuil.setAttribute("y2", data.threshold_y);
    }
    if (axe && data.ticks) {
      vider(axe);
      data.ticks.forEach(function (t) {
        var span = document.createElement("span");
        span.textContent = t;
        axe.appendChild(span);
      });
    }
    if (pic && data.max != null) pic.textContent = data.max;
    if (etiqFenetre && data.window) etiqFenetre.textContent = data.window;
    if (etiqTrous) {
      etiqTrous.textContent = data.trous
        ? " La courbe est interrompue à " + data.trous + " endroit" +
          (data.trous > 1 ? "s" : "") + " : aucune mesure n'y a été enregistrée " +
          "(serveur ou supervision à l'arrêt)."
        : "";
    }
    if (data.vide) {
      vider(mobile);
      annoncer("Aucune mesure sur cette période.");
    }
  }

  // ---- chargement (différé, annulable) ----

  var enCours = null, minuteur = null;

  function demander() {
    majControles();
    if (minuteur) clearTimeout(minuteur);
    // Un glissement continu ne déclenche qu'UN appel : on attend que le geste
    // se pose, et toute requête encore en vol est abandonnée.
    minuteur = setTimeout(function () {
      if (enCours) enCours.abort();
      enCours = new AbortController();
      wrap.classList.add("perf-chargement");
      fetch(url + "?start=" + Math.round(debut) + "&end=" + Math.round(fin),
            { signal: enCours.signal, headers: { Accept: "application/json" },
              credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (data) {
          // Le serveur a pu RAMENER la fenêtre (rétention, futur) : on repart
          // de ce qu'il a réellement servi, jamais de ce qu'on avait demandé.
          if (isFinite(data.debut) && isFinite(data.fin)) {
            debut = data.debut; fin = data.fin;
          }
          dessiner(data);
          majControles();
        })
        .catch(function () {
          // Réseau ou requête annulée : on GARDE l'image précédente plutôt
          // que d'afficher un graphe vide qui passerait pour une mesure.
        })
        .then(function () { wrap.classList.remove("perf-chargement"); });
    }, 180);
  }

  // ---- gestes ----

  function apercu(dxPx, echelle, ancrePx) {
    // Retour visuel IMMÉDIAT pendant le geste : on déplace le groupe au lieu
    // d'attendre le serveur. Le rechargement remplacera ce décalage.
    var dx = dxPx * (VUE / largeurPx());
    if (echelle && echelle !== 1) {
      var a = (ancrePx || largeurPx() / 2) * (VUE / largeurPx());
      mobile.setAttribute("transform",
        "translate(" + (a - a * echelle + dx) + ",0) scale(" + echelle + ",1)");
    } else {
      mobile.setAttribute("transform", "translate(" + dx + ",0)");
    }
  }

  function defiler(fraction) {
    var largeur = fin - debut;
    debut += largeur * fraction; fin += largeur * fraction;
    borner(); demander();
  }

  function zoomer(facteur, ancreFraction) {
    var largeur = fin - debut;
    var nouvelle = Math.min(MAX_SPAN, Math.max(MIN_SPAN, largeur * facteur));
    var pivot = debut + largeur * ancreFraction;
    debut = pivot - nouvelle * ancreFraction;
    fin = debut + nouvelle;
    borner(); demander();
  }

  var glisse = null;

  svg.addEventListener("pointerdown", function (event) {
    if (event.button !== 0 && event.pointerType === "mouse") return;
    glisse = { x: event.clientX, depuis: debut, jusqu: fin };
    svg.setPointerCapture(event.pointerId);
    wrap.classList.add("perf-glisse");
  });

  svg.addEventListener("pointermove", function (event) {
    if (!glisse) return;
    var dx = event.clientX - glisse.x;
    apercu(dx, 1);
    var largeur = glisse.jusqu - glisse.depuis;
    // Tirer vers la DROITE remonte le temps : le contenu suit le doigt.
    var decalage = -(dx / largeurPx()) * largeur;
    debut = glisse.depuis + decalage; fin = glisse.jusqu + decalage;
    borner();
  });

  function finGlisse(event) {
    if (!glisse) return;
    glisse = null;
    wrap.classList.remove("perf-glisse");
    if (event && event.pointerId != null && svg.hasPointerCapture(event.pointerId)) {
      svg.releasePointerCapture(event.pointerId);
    }
    demander();
  }
  svg.addEventListener("pointerup", finGlisse);
  svg.addEventListener("pointercancel", finGlisse);

  svg.addEventListener("wheel", function (event) {
    if (event.ctrlKey) return;                 // laisse le zoom natif du navigateur
    event.preventDefault();
    var rect = svg.getBoundingClientRect();
    var ancre = Math.min(1, Math.max(0, (event.clientX - rect.left) / (rect.width || 1)));
    apercu(0, event.deltaY > 0 ? 1 / 1.2 : 1.2, event.clientX - rect.left);
    zoomer(event.deltaY > 0 ? 1.2 : 1 / 1.2, ancre);
  }, { passive: false });

  svg.setAttribute("tabindex", "0");
  svg.addEventListener("keydown", function (event) {
    var touches = {
      ArrowLeft: function () { defiler(-0.25); },
      ArrowRight: function () { defiler(0.25); },
      "+": function () { zoomer(1 / 1.5, 0.5); },
      "=": function () { zoomer(1 / 1.5, 0.5); },
      "-": function () { zoomer(1.5, 0.5); },
      Home: revenirAuPresent,
    };
    var action = touches[event.key];
    if (!action) return;
    event.preventDefault();
    action();
  });

  // ---- commandes et annonce ----

  function revenirAuPresent() {
    var largeur = fin - debut;
    fin = maintenant(); debut = fin - largeur;
    borner(); demander();
  }

  function bouton(texte, aria, action) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "txtbtn small";
    b.textContent = texte;
    if (aria) b.setAttribute("aria-label", aria);
    b.addEventListener("click", action);
    return b;
  }

  var barre = document.createElement("div");
  barre.className = "perf-nav";
  var precedent = bouton("◀", "Période précédente", function () { defiler(-0.5); });
  var suivant = bouton("▶", "Période suivante", function () { defiler(0.5); });
  var present = bouton("Revenir à maintenant", null, revenirAuPresent);
  present.classList.add("perf-present");
  var annonce = document.createElement("span");
  annonce.className = "perf-periode";
  annonce.setAttribute("role", "status");
  annonce.setAttribute("aria-live", "polite");
  barre.appendChild(precedent);
  barre.appendChild(annonce);
  barre.appendChild(suivant);
  barre.appendChild(present);
  wrap.parentNode.insertBefore(barre, wrap);

  function horodate(secondes) {
    return new Date(secondes * 1000).toLocaleString("fr-FR", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
    });
  }

  function annoncer(texte) {
    annonce.textContent = texte || ("du " + horodate(debut) + " au " + horodate(fin));
  }

  function majControles() {
    annoncer();
    var auPresent = fin >= maintenant() - 90;
    present.hidden = auPresent;
    suivant.disabled = auPresent;
    precedent.disabled = debut <= maintenant() - RETENTION + 1;
    var joueursDesc = (legendeJoueurs && !legendeJoueurs.hidden && etiqJmax)
      ? " Les joueurs en ligne sont tracés en arrière-plan, de 0 à " + etiqJmax.textContent + "."
      : "";
    svg.setAttribute("aria-label",
      "Graphe MSPT, période affichée du " + horodate(debut) + " au " + horodate(fin) + "." +
      joueursDesc +
      " Flèches gauche et droite pour se déplacer, plus et moins pour zoomer, " +
      "touche Origine pour revenir au présent.");
  }

  Array.prototype.forEach.call(document.querySelectorAll("[data-perf-ancre]"), function (lien) {
    lien.addEventListener("click", function (event) {
      var d = parseFloat(lien.getAttribute("data-debut"));
      var f = parseFloat(lien.getAttribute("data-fin"));
      if (!isFinite(d) || !isFinite(f) || f <= d) return;   // lien cassé : navigation normale
      event.preventDefault();
      debut = d; fin = f;
      borner(); demander();
      wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  });

  wrap.classList.add("perf-interactif");
  majControles();
})();
