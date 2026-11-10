/* FusionMassAnalyzer palette: sunburst + sorted table, talks to Python via adsk.fusionSendData. */
(function () {
  "use strict";

  const FLAG_LABELS = {
    no_material: "sans matériau",
    zero_mass: "masse nulle",
    not_solid: "corps surfacique",
    error: "erreur de calcul",
  };

  const state = {
    payload: null,
    focusId: null,
    activeId: null,
    sortKey: "total",
    sortAsc: false,
    demo: false,
  };

  const $ = (sel) => document.querySelector(sel);
  const el = {
    total: $("#total"), meta: $("#meta"), status: $("#status"), legend: $("#legend"),
    chart: $("#chart"), crumbs: $("#crumbs"), tbody: $("#table tbody"), filter: $("#filter"),
    rowCount: $("#row-count"), warnings: $("#warnings"),
    optHidden: $("#opt-hidden"), optBodies: $("#opt-bodies"), optLog: $("#opt-log"), optCsv: $("#opt-csv"),
    btnAnalyze: $("#btn-analyze"), btnHeat: $("#btn-heat"), btnRestore: $("#btn-restore"), btnCsv: $("#btn-csv"),
  };

  // ------------------------------------------------------------------ utils
  function formatMass(kg) {
    if (kg == null || isNaN(kg)) return "—";
    if (Math.abs(kg) >= 1) return kg.toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " kg";
    const g = kg * 1000;
    const digits = Math.abs(g) >= 100 ? 1 : Math.abs(g) >= 10 ? 2 : 3;
    return g.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits }) + " g";
  }
  const formatPct = (p) => p.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " %";
  const flagText = (flags) => flags.map((f) => FLAG_LABELS[f] || f).join(", ");

  function setStatus(msg, isError) {
    el.status.textContent = msg || "";
    el.status.classList.toggle("error", !!isError);
  }

  function busy(on) {
    [el.btnAnalyze, el.btnHeat, el.btnRestore, el.btnCsv].forEach((b) => (b.disabled = on));
    if (!on) updateButtons();
  }

  function updateButtons() {
    const has = !!state.payload;
    el.btnHeat.disabled = !has;
    el.btnCsv.disabled = !has;
    el.btnRestore.disabled = state.demo;
  }

  // ------------------------------------------------------------------ bridge
  function call(action, data) {
    if (state.demo) return Promise.resolve(window.FMA_DEMO.handle(action, data || {}));
    return Promise.resolve(adsk.fusionSendData(action, JSON.stringify(data || {}))).then((raw) => {
      const reply = typeof raw === "string" && raw ? JSON.parse(raw) : { ok: false, error: "Réponse vide de Fusion" };
      if (!reply.ok) throw new Error(reply.error);
      return reply.data;
    });
  }

  window.fusionJavaScriptHandler = {
    handle: function (action) {
      try {
        if (action === "refresh") analyze();
      } catch (e) {
        console.error(e);
      }
      return "OK";
    },
  };

  // ------------------------------------------------------------------ actions
  function analyze(first) {
    busy(true);
    setStatus("Analyse en cours…");
    return call(first ? "ready" : "analyze", { ignoreHidden: el.optHidden.checked })
      .then((payload) => {
        const keepFocus = state.focusId;
        state.payload = payload;
        state.focusId = keepFocus;
        renderAll();
        const skipped = payload.skippedHidden ? ` · ${payload.skippedHidden} élément(s) masqué(s) ignoré(s)` : "";
        setStatus((state.demo ? "Mode démo (hors Fusion)" : "Analyse terminée") + skipped);
        el.legend.hidden = !payload.heatmapActive;
      })
      .catch((e) => setStatus(e.message, true))
      .finally(() => busy(false));
  }

  function selectInFusion(id) {
    state.activeId = id;
    el.tbody.querySelectorAll("tr").forEach((tr) => tr.classList.toggle("active", tr.dataset.id === id));
    call("select", { id }).catch((e) => setStatus(e.message, true));
  }

  function applyHeatmap() {
    busy(true);
    setStatus("Application des couleurs…");
    call("heatmap", { logScale: el.optLog.checked })
      .then((r) => {
        renderLegend(r.legend);
        setStatus(`${r.colored} pièce(s) colorée(s) — « Restaurer » remet les apparences d'origine.`);
      })
      .catch((e) => setStatus(e.message, true))
      .finally(() => busy(false));
  }

  function restore() {
    busy(true);
    call("restore", {})
      .then((r) => {
        el.legend.hidden = true;
        setStatus(r.leftovers
          ? `${r.restored} surcharge(s) MassHeat_ résiduelle(s) supprimée(s).`
          : `${r.restored} apparence(s) restaurée(s).`);
      })
      .catch((e) => setStatus(e.message, true))
      .finally(() => busy(false));
  }

  function exportCsv() {
    call("exportCsv", { format: el.optCsv.value })
      .then((r) => setStatus(r.cancelled ? "Export annulé." : "CSV exporté : " + r.path))
      .catch((e) => setStatus(e.message, true));
  }

  // ------------------------------------------------------------------ data
  function hierarchy() {
    const showBodies = el.optBodies.checked;
    const total = state.payload.total;
    const strip = (n) => {
      const children = (n.children || []).filter((c) => showBodies || c.kind !== "body").map(strip);
      // bodies hidden: a node owning bodies AND sub-occurrences gets a synthetic
      // "(corps propres)" slice so its own mass does not show up as a gap
      if (!showBodies && children.length && n.own > 0) {
        children.push({ id: n.id, name: "(corps propres)", kind: "own", path: n.path,
          own: n.own, total: n.own, pct: total > 0 ? (100 * n.own) / total : 0, flags: [], children: [] });
      }
      return { ...n, children };
    };
    const tree = strip(state.payload.tree);
    return d3.hierarchy(tree, (d) => (d.children && d.children.length ? d.children : null))
      .sum((d) => (d.kind === "body" || d.kind === "own" ? d.total : d.children.length ? 0 : d.own))
      .sort((a, b) => b.value - a.value);
  }

  // ------------------------------------------------------------------ render
  function renderAll() {
    const p = state.payload;
    el.total.textContent = formatMass(p.total);
    const occ = countKinds(p.tree);
    el.meta.textContent = `${p.document || p.tree.name} · ${occ.occurrence} occurrence(s) · ${occ.body} corps`;
    renderWarnings(p.warnings || []);
    renderChart();
  }

  function countKinds(node, acc) {
    acc = acc || { occurrence: 0, body: 0 };
    if (node.kind in acc) acc[node.kind]++;
    (node.children || []).forEach((c) => countKinds(c, acc));
    return acc;
  }

  function renderWarnings(list) {
    el.warnings.hidden = !list.length;
    if (!list.length) return;
    el.warnings.querySelector("summary").textContent = `⚠ ${list.length} élément(s) à vérifier`;
    const ul = el.warnings.querySelector("ul");
    ul.replaceChildren(...list.map((w) => {
      const li = document.createElement("li");
      li.textContent = `${w.path} — ${flagText(w.flags)}`;
      li.addEventListener("click", () => selectInFusion(w.id));
      return li;
    }));
  }

  function renderLegend(legend) {
    if (!legend) return;
    el.legend.replaceChildren();
    const lo = document.createElement("span");
    lo.textContent = "léger";
    const bar = document.createElement("div");
    bar.className = "bar";
    legend.forEach((b) => {
      const s = document.createElement("span");
      s.style.background = `rgb(${b.rgb.join(",")})`;
      s.title = b.name;
      bar.appendChild(s);
    });
    const hi = document.createElement("span");
    hi.textContent = "lourd" + (el.optLog.checked ? " (échelle log)" : "");
    el.legend.append(lo, bar, hi);
    el.legend.hidden = false;
  }

  function renderChart() {
    el.chart.replaceChildren();
    const root = hierarchy();
    if (!root.value) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Aucune masse à afficher (design vide ou corps sans matériau).";
      el.chart.appendChild(p);
      renderTable(root);
      renderCrumbs(root);
      return;
    }

    d3.partition().size([2 * Math.PI, root.height + 1])(root);
    root.each((d) => (d.current = d));

    const palette = d3.schemeTableau10;
    const topIndex = new Map((root.children || []).map((c, i) => [c, i]));
    const color = (d) => {
      let top = d;
      while (top.depth > 1) top = top.parent;
      const base = d3.color(palette[(topIndex.get(top) || 0) % palette.length]);
      return base.brighter(0.22 * (d.depth - 1)).formatHex();
    };

    const width = 600;
    const radius = width / 6;
    const arc = d3.arc()
      .startAngle((d) => d.x0).endAngle((d) => d.x1)
      .padAngle((d) => Math.min((d.x1 - d.x0) / 2, 0.004)).padRadius(radius * 1.5)
      .innerRadius((d) => d.y0 * radius)
      .outerRadius((d) => Math.max(d.y0 * radius, d.y1 * radius - 1));
    const arcVisible = (d) => d.y1 <= 3 && d.y0 >= 1 && d.x1 > d.x0;
    const labelVisible = (d) => d.y1 <= 3 && d.y0 >= 1 && (d.y1 - d.y0) * (d.x1 - d.x0) > 0.06;
    const labelTransform = (d) => {
      const x = ((d.x0 + d.x1) / 2) * 180 / Math.PI;
      const y = ((d.y0 + d.y1) / 2) * radius;
      return `rotate(${x - 90}) translate(${y},0) rotate(${x < 180 ? 0 : 180})`;
    };

    const svg = d3.select(el.chart).append("svg").attr("viewBox", [-width / 2, -width / 2, width, width]);
    const g = svg.append("g");

    const path = g.append("g").selectAll("path")
      .data(root.descendants().slice(1))
      .join("path")
      .attr("fill", color)
      .attr("fill-opacity", (d) => (arcVisible(d.current) ? (d.children ? 0.9 : 0.7) : 0))
      .attr("pointer-events", (d) => (arcVisible(d.current) ? "auto" : "none"))
      .attr("d", (d) => arc(d.current))
      .on("click", (event, d) => {
        selectInFusion(d.data.id);
        if (d.children) zoom(d);
        else highlightRow(d.data.id);
      });
    path.append("title").text((d) =>
      `${d.ancestors().map((a) => a.data.name).reverse().join(" / ")}\n${formatMass(d.value)} · ${formatPct(d.data.pct)}` +
      (d.data.flags.length ? `\n⚠ ${flagText(d.data.flags)}` : ""));

    const label = g.append("g").attr("text-anchor", "middle").selectAll("text")
      .data(root.descendants().slice(1))
      .join("text")
      .attr("class", "arc-label").attr("dy", "0.35em")
      .attr("opacity", (d) => +labelVisible(d.current))
      .attr("transform", (d) => labelTransform(d.current))
      .text((d) => (d.data.name.length > 14 ? d.data.name.slice(0, 13) + "…" : d.data.name));

    const center = g.append("circle").attr("r", radius).attr("fill", "transparent")
      .attr("pointer-events", "all").style("cursor", "pointer")
      .on("click", () => focus.parent && zoom(focus.parent));
    center.append("title").text("Remonter d'un niveau");
    const centerText = g.append("text").attr("class", "center-label").attr("text-anchor", "middle");
    const cName = centerText.append("tspan").attr("x", 0).attr("dy", "-0.4em").attr("font-weight", 600);
    const cMass = centerText.append("tspan").attr("x", 0).attr("dy", "1.3em");
    const cPct = centerText.append("tspan").attr("x", 0).attr("dy", "1.3em").attr("class", "sub");

    let focus = root;
    function updateCenter() {
      const n = focus.data.name;
      cName.text(n.length > 22 ? n.slice(0, 21) + "…" : n);
      cMass.text(formatMass(focus.value));
      cPct.text(focus === root ? "100 %" : formatPct(focus.data.pct) + " du total");
    }

    function zoom(p, instant) {
      focus = p;
      state.focusId = p.data.id;
      root.each((d) => (d.target = {
        x0: Math.max(0, Math.min(1, (d.x0 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        x1: Math.max(0, Math.min(1, (d.x1 - p.x0) / (p.x1 - p.x0))) * 2 * Math.PI,
        y0: Math.max(0, d.y0 - p.depth),
        y1: Math.max(0, d.y1 - p.depth),
      }));
      const t = svg.transition().duration(instant ? 0 : 650);
      path.transition(t)
        .tween("data", (d) => {
          const i = d3.interpolate(d.current, d.target);
          return (tt) => (d.current = i(tt));
        })
        .filter(function (d) { return +this.getAttribute("fill-opacity") || arcVisible(d.target); })
        .attr("fill-opacity", (d) => (arcVisible(d.target) ? (d.children ? 0.9 : 0.7) : 0))
        .attr("pointer-events", (d) => (arcVisible(d.target) ? "auto" : "none"))
        .attrTween("d", (d) => () => arc(d.current));
      label.filter(function (d) { return +this.getAttribute("opacity") || labelVisible(d.target); })
        .transition(t)
        .attr("opacity", (d) => +labelVisible(d.target))
        .attrTween("transform", (d) => () => labelTransform(d.current));
      updateCenter();
      renderCrumbs(p, zoom);
      renderTable(p);
    }

    const initial = state.focusId && root.descendants().find((d) => d.data.id === state.focusId && d.children);
    zoom(initial || root, true);
  }

  function renderCrumbs(node, zoom) {
    el.crumbs.replaceChildren();
    node.ancestors().reverse().forEach((a, i, arr) => {
      if (i) {
        const s = document.createElement("span");
        s.className = "sep";
        s.textContent = "›";
        el.crumbs.appendChild(s);
      }
      const link = document.createElement(i === arr.length - 1 || !zoom ? "span" : "a");
      link.textContent = a.data.name;
      if (link.tagName === "A") link.addEventListener("click", () => zoom(a));
      el.crumbs.appendChild(link);
    });
  }

  let tableNode = null;
  function renderTable(node) {
    if (node) tableNode = node;
    if (!tableNode) return;
    const q = el.filter.value.trim().toLowerCase();
    let rows = tableNode.descendants().slice(1).filter((d) => d.data.kind !== "own").map((d) => ({
      id: d.data.id, name: d.data.name, path: d.data.path, kind: d.data.kind,
      total: d.data.total, pct: d.data.pct, flags: d.data.flags,
    }));
    if (q) rows = rows.filter((r) => r.name.toLowerCase().includes(q) || r.path.toLowerCase().includes(q));
    const k = state.sortKey;
    const dir = state.sortAsc ? 1 : -1;
    rows.sort((a, b) => (typeof a[k] === "string" ? a[k].localeCompare(b[k]) : a[k] - b[k]) * dir);

    const max = rows.reduce((m, r) => Math.max(m, r.total), 0) || 1;
    const LIMIT = 1500;
    const frag = document.createDocumentFragment();
    rows.slice(0, LIMIT).forEach((r) => {
      const tr = document.createElement("tr");
      tr.dataset.id = r.id;
      tr.className = "kind-" + r.kind + (r.id === state.activeId ? " active" : "");
      const name = document.createElement("td");
      name.textContent = r.name;
      if (r.flags.length) {
        const f = document.createElement("span");
        f.className = "flag";
        f.textContent = "⚠";
        f.title = flagText(r.flags);
        name.appendChild(f);
      }
      const path = document.createElement("td");
      path.className = "path";
      path.textContent = r.path;
      path.title = r.path;
      const mass = document.createElement("td");
      mass.className = "num bar-cell";
      const bar = document.createElement("i");
      bar.style.width = (100 * r.total / max).toFixed(1) + "%";
      mass.append(bar, document.createTextNode(formatMass(r.total)));
      const pct = document.createElement("td");
      pct.className = "num";
      pct.textContent = formatPct(r.pct);
      tr.append(name, path, mass, pct);
      tr.addEventListener("click", () => selectInFusion(r.id));
      frag.appendChild(tr);
    });
    el.tbody.replaceChildren(frag);
    el.rowCount.textContent = rows.length > LIMIT ? `${LIMIT} / ${rows.length} lignes` : `${rows.length} ligne(s)`;
    document.querySelectorAll("th[data-sort]").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === k);
      th.classList.toggle("asc", th.dataset.sort === k && state.sortAsc);
    });
  }

  function highlightRow(id) {
    const tr = el.tbody.querySelector(`tr[data-id="${id}"]`);
    if (tr) tr.scrollIntoView({ block: "nearest" });
  }

  // ------------------------------------------------------------------ wiring
  el.btnAnalyze.addEventListener("click", () => analyze());
  el.optHidden.addEventListener("change", () => analyze());
  el.optBodies.addEventListener("change", () => state.payload && renderChart());
  el.btnHeat.addEventListener("click", applyHeatmap);
  el.btnRestore.addEventListener("click", restore);
  el.btnCsv.addEventListener("click", exportCsv);
  el.filter.addEventListener("input", () => renderTable());
  document.querySelectorAll("th[data-sort]").forEach((th) =>
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      state.sortAsc = state.sortKey === key ? !state.sortAsc : key === "name" || key === "path";
      state.sortKey = key;
      renderTable();
    }));

  // Fusion injects `adsk` after the page loads; outside Fusion, fall back to demo data.
  updateButtons();
  let tries = 0;
  (function waitForFusion() {
    if (window.adsk && typeof adsk.fusionSendData === "function") return analyze(true);
    if (++tries < 30) return setTimeout(waitForFusion, 100);
    const s = document.createElement("script");
    s.src = "demo.js";
    s.onload = () => { state.demo = true; analyze(true); };
    s.onerror = () => setStatus("Fusion introuvable : ouvrez cette palette depuis le complément.", true);
    document.body.appendChild(s);
  })();
})();
