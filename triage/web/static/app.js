"use strict";
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem("pubtriage." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem("pubtriage." + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
    del(k) { try { localStorage.removeItem("pubtriage." + k); } catch { /* ignore */ } },
  };
  async function api(method, url, body) {
    const res = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }

  const SOURCE_LABEL = { iris: "IRIS", arxiv: "arXiv", openalex: "OpenAlex", unpaywall: "Unpaywall" };
  const STATUSES = ["present", "missing", "uncertain", "error", "not_checked"];
  const state = { data: null, sort: store.get("sort", { col: "year", desc: true }), hidden: store.get("hidden", null), filters: null };

  // ---------- tabs ----------
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  function showTab(name) {
    document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    $("#tab-papers").hidden = name !== "papers";
    $("#tab-config").hidden = name !== "config";
    if (name === "config") loadConfig();
    if (name === "config") history.replaceState(null, "", "#config");
    else if (location.hash === "#config") history.replaceState(null, "", location.pathname);
  }

  // ---------- cell helpers ----------
  const label = (col) => SOURCE_LABEL[col] || (col.startsWith("ack_") ? col.slice(4) : { acks: "Acknowledged", pdf_source: "PDF from", ack_status: "Ack scan", rank: "Rank" }[col] || col.replace("_", " "));
  const srcStatus = (r, s) => (r.sources && r.sources[s] ? r.sources[s].status : "not_checked");
  function projValue(r, p) { // Y | body | N | "" (no readable pdf)
    if (!r.ack || r.ack.status !== "ok") return "";
    const m = r.ack.matches[p];
    return m === "ack" ? "Y" : m === "body" ? "body" : "N";
  }
  const badge = (status) => `<span class="badge s-${esc(status)}">${esc(status.replace("_", " "))}</span>`;
  function ackChips(r) {
    if (!r.ack) return `<span class="chip none">no PDF</span>`;
    if (r.ack.status !== "ok") return `<span class="chip none">${esc(r.ack.status)}</span>`;
    const projects = state.data.projects;
    const parts = projects.filter((p) => r.ack.matches[p]).map((p) => {
      const kind = r.ack.matches[p];
      return `<span class="chip ${kind}" title="${kind === "ack" ? "in acknowledgements" : "mentioned only in body"}">${esc(p)}</span>`;
    });
    return parts.join("") || `<span class="muted">none</span>`;
  }
  function cmpMark(r) {
    const c = r.iris_vs_arxiv;
    if (!c) return "";
    if (c.verdict === "same") return ` <span class="cmp same" title="title and authors match the arXiv entry">= arXiv</span>`;
    if (c.accepted) return ` <span class="cmp checked" title="${esc(c.verdict)}, checked by hand">≠ arXiv ✓</span>`;
    return ` <span class="cmp diff" title="${esc(c.verdict)} (vs arXiv)">≠ arXiv</span>`;
  }
  const cmpValue = (r) => (!r.iris_vs_arxiv ? "na" : r.iris_vs_arxiv.verdict === "same" ? "same" : r.iris_vs_arxiv.accepted ? "accepted" : "differs");
  const rankValue = (r) => { const k = r.ranking; return !k || !k.rank || k.status === "uncertain" ? "unknown" : k.top ? "top" : "nottop"; };
  function rankBadge(r) {
    const k = r.ranking;
    if (!k || k.status === "not_applicable") return `<span class="muted">—</span>`;
    if (!k.rank) return `<span class="badge s-na" title="${esc(k.evidence)}">${k.status === "no_data" ? "no list" : "?"}</span>`;
    const cls = k.top ? "s-present" : "s-na";
    const mark = k.status === "uncertain" ? "?" : k.status === "validated" ? " ✓" : k.status === "manual" ? " •" : "";
    return `<span class="badge ${k.status === "uncertain" ? "s-uncertain" : cls}" title="${esc([k.source, k.evidence].filter(Boolean).join(": "))}">${esc(k.rank)}${mark}</span>`;
  }
  function sortKey(r, col) {
    if (col === "title") return r.title.toLowerCase();
    if (col === "year") return r.year || "";
    if (col === "authors") return (r.authors || []).join("; ").toLowerCase();
    if (col === "acks") return r.ack ? (r.ack.acknowledged || []).length + (r.ack.status === "ok" ? 0.5 : 0) : -1;
    if (col === "ack_status") return r.ack ? r.ack.status : "no_pdf";
    if (col === "rank") { const o = ["Q1", "A*", "A", "Q2", "B", "Q3", "C", "Q4", "Unranked"]; const k = r.ranking; return k && k.rank ? o.indexOf(k.rank) : 99; }
    if (col.startsWith("ack_")) return projValue(r, col.slice(4));
    if (SOURCE_LABEL[col]) return STATUSES.indexOf(srcStatus(r, col));
    return String(r[col] ?? "").toLowerCase();
  }
  function cell(r, col) {
    if (col === "title") return `<td class="title">${esc(r.title)}${r.venue ? `<div class="venue">${esc(r.venue)}</div>` : ""}</td>`;
    if (col === "authors") { const a = r.authors || []; return `<td>${esc(a.slice(0, 3).join("; "))}${a.length > 3 ? " et al." : ""}</td>`; }
    if (col === "doi") return `<td>${r.doi ? `<a href="https://doi.org/${esc(r.doi)}" target="_blank" rel="noopener">${esc(r.doi)}</a>` : ""}</td>`;
    if (col === "acks") return `<td>${ackChips(r)}</td>`;
    if (col === "ack_status") return `<td>${badge(r.ack ? r.ack.status : "no_pdf")}</td>`;
    if (col === "rank") return `<td>${rankBadge(r)}</td>`;
    if (col.startsWith("ack_")) {
      const v = projValue(r, col.slice(4));
      const cls = { Y: "s-present", body: "s-uncertain", N: "s-missing", "": "s-na" }[v];
      return `<td><span class="badge ${cls}">${v || "—"}</span></td>`;
    }
    if (SOURCE_LABEL[col]) {
      const s = r.sources && r.sources[col];
      const st = srcStatus(r, col);
      const b = s && s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${badge(st)}</a>` : badge(st);
      return `<td>${b}${col === "iris" ? cmpMark(r) : ""}</td>`;
    }
    return `<td>${esc(r[col] ?? "")}</td>`;
  }

  // ---------- filters ----------
  function defaultFilters() {
    const f = { q: "", sources: {}, project: "", projMode: "Y", pdf: "", cmp: "", rank: "" };
    for (const [k, v] of Object.entries(state.data.default_filters || {})) {
      if (SOURCE_LABEL[k]) f.sources[k] = v;
      else if (k.startsWith("ack_")) { f.project = k.slice(4); f.projMode = v; }
      else if (k === "pdf") f.pdf = v;
      else if (k === "iris_vs_arxiv") f.cmp = v;
      else if (k === "rank") f.rank = v;
    }
    return f;
  }
  function buildFilterControls() {
    const f = state.filters;
    $("#source-filters").innerHTML = state.data.configured_sources.concat(state.data.sources.filter((s) => !state.data.configured_sources.includes(s)))
      .map((s) => `<label>${esc(label(s))} <select data-src="${esc(s)}"><option value="">any</option>${STATUSES.map((st) => `<option ${f.sources[s] === st ? "selected" : ""}>${st}</option>`).join("")}</select></label>`).join("");
    $("#source-filters").querySelectorAll("select").forEach((sel) => sel.addEventListener("change", () => {
      if (sel.value) f.sources[sel.dataset.src] = sel.value; else delete f.sources[sel.dataset.src];
      saveFilters(); renderTable();
    }));
    $("#proj-filter").innerHTML = `<option value="">any</option>` + state.data.projects.map((p) => `<option ${f.project === p ? "selected" : ""}>${esc(p)}</option>`).join("");
    $("#proj-mode").value = f.projMode;
    $("#proj-mode").disabled = !f.project;
    $("#pdf-filter").value = f.pdf;
    $("#cmp-filter").value = f.cmp || "";
    $("#rank-filter").value = f.rank || "";
    $("#search").value = f.q;
  }
  function saveFilters() { store.set("filters", state.filters); }
  $("#search").addEventListener("input", (e) => { state.filters.q = e.target.value; saveFilters(); renderTable(); });
  $("#proj-filter").addEventListener("change", (e) => { state.filters.project = e.target.value; $("#proj-mode").disabled = !e.target.value; saveFilters(); renderTable(); });
  $("#proj-mode").addEventListener("change", (e) => { state.filters.projMode = e.target.value; saveFilters(); renderTable(); });
  $("#pdf-filter").addEventListener("change", (e) => { state.filters.pdf = e.target.value; saveFilters(); renderTable(); });
  $("#cmp-filter").addEventListener("change", (e) => { state.filters.cmp = e.target.value; saveFilters(); renderTable(); });
  $("#rank-filter").addEventListener("change", (e) => { state.filters.rank = e.target.value; saveFilters(); renderTable(); });
  $("#filters-reset").addEventListener("click", () => { state.filters = defaultFilters(); saveFilters(); buildFilterControls(); renderTable(); });

  function matches(r) {
    const f = state.filters;
    if (f.q) {
      const hay = [r.title, r.doi, r.venue, r.year, ...(r.authors || [])].join(" ").toLowerCase();
      if (!f.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
    }
    for (const [s, st] of Object.entries(f.sources)) if (srcStatus(r, s) !== st) return false;
    if (f.project && projValue(r, f.project) !== f.projMode) return false;
    if (f.pdf === "yes" && !r.pdf_path) return false;
    if (f.pdf === "no" && r.pdf_path) return false;
    if (f.cmp && cmpValue(r) !== f.cmp) return false;
    if (f.rank && rankValue(r) !== f.rank) return false;
    return true;
  }

  // ---------- columns ----------
  const visibleColumns = () => state.data.columns.concat(state.extraCols || []).filter((c) => !(state.hidden || []).includes(c));
  function buildColumnMenu() {
    const all = state.data.valid_columns;
    const shown = new Set(visibleColumns());
    $("#col-list").innerHTML = all.map((c) => `<label><input type="checkbox" value="${esc(c)}" ${shown.has(c) ? "checked" : ""}> ${esc(label(c))}</label>`).join("");
    $("#col-list").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => {
      const c = cb.value;
      state.hidden = (state.hidden || []).filter((x) => x !== c);
      state.extraCols = (state.extraCols || []).filter((x) => x !== c);
      if (!cb.checked) state.hidden.push(c);
      else if (!state.data.columns.includes(c)) state.extraCols.push(c);
      store.set("hidden", state.hidden); store.set("extraCols", state.extraCols);
      renderTable();
    }));
  }
  $("#col-reset").addEventListener("click", () => {
    state.hidden = []; state.extraCols = []; store.del("hidden"); store.del("extraCols"); buildColumnMenu(); renderTable();
  });

  // ---------- table ----------
  function renderSummary() {
    const recs = state.data.records;
    const stats = [["papers", recs.length]];
    for (const s of state.data.sources) {
      stats.push([`on ${label(s)}`, recs.filter((r) => srcStatus(r, s) === "present").length]);
      stats.push([`missing from ${label(s)}`, recs.filter((r) => srcStatus(r, s) === "missing").length]);
    }
    if (recs.some((r) => r.iris_vs_arxiv)) stats.push(["IRIS ≠ arXiv to check", recs.filter((r) => cmpValue(r) === "differs").length]);
    if (recs.some((r) => r.ranking && r.ranking.rank)) stats.push([`top venue (${[...(state.data.top_labels || ["Q1", "A*"])].join(" / ")})`, recs.filter((r) => rankValue(r) === "top").length]);
    stats.push(["PDFs retrieved", recs.filter((r) => r.pdf_path).length]);
    stats.push(["with acknowledged project", recs.filter((r) => r.ack && r.ack.acknowledged.length).length]);
    if ((state.data.excluded_hidden || []).length) stats.push(["excluded (Settings → Paper list)", state.data.excluded_hidden.length]);
    $("#summary").innerHTML = recs.length ? stats.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${esc(k)}</span></div>`).join("") : "";
    $("#generated").textContent = state.data.generated_at ? `Last run: ${new Date(state.data.generated_at).toLocaleString()}` : "No run yet";
  }
  function renderTable() {
    const cols = visibleColumns();
    const { col, desc } = state.sort;
    const thead = $("#papers thead tr");
    thead.innerHTML = cols.map((c) => `<th data-col="${esc(c)}" class="${c === col ? "sorted" + (desc ? " desc" : "") : ""}">${esc(label(c))}</th>`).join("");
    thead.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => {
      const c = th.dataset.col;
      state.sort = { col: c, desc: state.sort.col === c ? !state.sort.desc : false };
      store.set("sort", state.sort); renderTable();
    }));
    let rows = state.data.records.filter(matches);
    if (col) rows = rows.slice().sort((a, b) => { const x = sortKey(a, col), y = sortKey(b, col); return (x < y ? -1 : x > y ? 1 : 0) * (desc ? -1 : 1); });
    state.shown = rows.map((r) => r.id);
    $("#papers tbody").innerHTML = rows.map((r) => `<tr tabindex="0" data-id="${esc(r.id)}">${cols.map((c) => cell(r, c)).join("")}</tr>`).join("");
    $("#count").textContent = `${rows.length} of ${state.data.records.length} papers`;
    $("#empty").hidden = state.data.records.length > 0;
  }
  $("#papers tbody").addEventListener("click", (e) => { const tr = e.target.closest("tr"); if (tr && !e.target.closest("a")) openDetail(tr.dataset.id); });
  $("#papers tbody").addEventListener("keydown", (e) => { if (e.key === "Enter") { const tr = e.target.closest("tr"); if (tr) openDetail(tr.dataset.id); } });

  async function loadPapers() {
    state.data = await api("GET", "/api/papers");
    state.extraCols = store.get("extraCols", []).filter((c) => state.data.valid_columns.includes(c));
    if (!state.filters) {
      const saved = store.get("filters", null);
      state.filters = saved && typeof saved === "object" ? { ...defaultFilters(), ...saved } : defaultFilters();
    }
    buildRunSources(); buildFilterControls(); buildColumnMenu(); renderSummary(); renderTable(); buildExport(); renderIrisIndex(); renderRankInfo();
    const m = location.hash.match(/^#paper=(.+)$/);
    if (m) openDetail(decodeURIComponent(m[1]));
  }

  // ---------- export ----------
  function formatOptions(sel) {
    return Object.entries(state.data.citation_formats || {}).map(([k, v]) => `<option value="${esc(k)}" ${k === sel ? "selected" : ""}>${esc(v)}</option>`).join("");
  }
  function buildExport() { $("#export-format").innerHTML = formatOptions(store.get("exportFormat", "bibtex")); }
  $("#export-format").addEventListener("change", (e) => store.set("exportFormat", e.target.value));
  async function exportCitations(ids, format) {
    const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids, format }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `HTTP ${res.status}`); }
    const name = (res.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: name ? name[1] : "citations.txt" });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $("#export-btn").addEventListener("click", () => {
    if (!state.shown || !state.shown.length) { alert("No papers shown."); return; }
    exportCitations(state.shown, $("#export-format").value).catch((err) => alert(err.message));
  });
  function renderRankInfo() {
    const el = $("#rank-info"), ri = state.data.rankings;
    if (!ri) { el.hidden = true; return; }
    const parts = [];
    $("#rank-dl").hidden = false;
    if (state.data.ranking_pages) $("#sjr-page").href = state.data.ranking_pages.sjr;
    if (!ri.sjr_csv && !ri.core_csv) parts.push("Rankings: no CORE / SCImago list yet: get them below. Manual venue ranks and per-paper ranks still work.");
    else parts.push(`Rankings: ${ri.journals.toLocaleString()} journals (${ri.sjr_label || "SJR"}), ${ri.conferences.toLocaleString()} conferences (CORE).`);
    if (ri.errors && ri.errors.length) parts.push("Problems: " + ri.errors.join("; "));
    el.textContent = parts.join(" ");
    el.hidden = false;
  }
  const listName = (w) => (w === "core" ? "CORE" : "SCImago");
  document.querySelectorAll("#rank-dl [data-up]").forEach((inp) => inp.addEventListener("change", async () => {
    const which = inp.dataset.up, msg = $("#rank-dl-msg"), file = inp.files[0];
    if (!file) return;
    msg.textContent = `Checking ${listName(which)} file…`;
    const fd = new FormData();
    fd.append("which", which);
    fd.append("file", file);
    try {
      const res = await fetch("/api/rankings/upload", { method: "POST", body: fd });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
      msg.textContent = `${listName(which)}: ${d.rows.toLocaleString()} rows imported.`;
      await loadPapers();
    } catch (err) { msg.textContent = `${listName(which)}: ${err.message}`; }
    inp.value = "";
  }));
  document.querySelectorAll("#rank-dl [data-dl]").forEach((b) => b.addEventListener("click", async () => {
    const which = b.dataset.dl, msg = $("#rank-dl-msg");
    document.querySelectorAll("#rank-dl button").forEach((x) => { x.disabled = true; });
    msg.textContent = `Downloading ${which === "core" ? "CORE" : "SCImago"} list…`;
    try {
      const r = await api("POST", "/api/rankings/download", { which });
      msg.textContent = `${which === "core" ? "CORE" : "SCImago"}: ${r.rows.toLocaleString()} rows saved to ${r.path}.`;
      await loadPapers();
    } catch (err) { msg.textContent = `${which === "core" ? "CORE" : "SCImago"}: ${err.message}`; }
    document.querySelectorAll("#rank-dl button").forEach((x) => { x.disabled = false; });
  }));
  function renderIrisIndex() {
    const el = $("#iris-index"), iris = state.data.iris;
    el.hidden = !iris;
    if (!iris) return;
    if (iris.mode === "orcid") { el.textContent = `IRIS: checking the records linked from ORCID ${iris.orcid} (one request per record, cached).`; return; }
    if (iris.mode === "export") { el.textContent = "IRIS: using the manual export file."; return; }
    const ix = iris.index;
    el.textContent = !ix || !ix.exists
      ? "IRIS index: not harvested yet. The first run with IRIS harvests the whole repository (~155k records, about 2 h at 5 s/request); you can cancel and it resumes next time. Later runs only fetch changes."
      : ix.incomplete
        ? `IRIS index: harvest in progress / interrupted (${ix.records.toLocaleString()} records so far); the next run resumes it.`
        : `IRIS index: ${ix.records.toLocaleString()} records, harvested ${new Date(ix.harvested_until).toLocaleString()}; the next run only fetches changes.`;
  }

  // ---------- detail panel ----------
  const fileUrl = (kind, id) => `/${kind}/${id.split("/").map(encodeURIComponent).join("/")}`;
  function openDetail(id) {
    const r = state.data.records.find((x) => x.id === id);
    if (!r) return;
    history.replaceState(null, "", "#paper=" + encodeURIComponent(id));
    const a = r.authors || [];
    const sources = Object.entries(r.sources || {}).map(([name, s]) => `<tr><td>${esc(label(name))}</td><td>${badge(s.status)}</td>
      <td>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a>` : ""}</td>
      <td class="muted">${esc([s.evidence, s.score != null ? `score ${Number(s.score).toFixed(2)}` : "", s.fulltext ? "full text" : "", s.open_access ? "open access" : ""].filter(Boolean).join(" · "))}</td></tr>`).join("");
    let ackHtml;
    if (!r.ack) {
      const tried = Object.entries(r.sources || {}).filter(([, s]) => s.status === "present").map(([n]) => label(n));
      ackHtml = `<p class="muted">No open-access PDF found${tried.length ? ` (tried: ${esc(tried.join(", "))})` : ""}, so acknowledgements could not be checked.</p>`;
    } else if (r.ack.status !== "ok") {
      ackHtml = `<p class="muted">The PDF has no extractable text (scanned?), so acknowledgements could not be checked.</p>`;
    } else {
      const rows = state.data.projects.map((p) => {
        const v = projValue(r, p);
        const where = { Y: `in acknowledgements${r.ack.pages[p] ? ` (page ${r.ack.pages[p]})` : ""}`, body: "mentioned only in the body, check by hand", N: "not found" }[v];
        return `<tr><td>${esc(p)}</td><td><span class="badge ${{ Y: "s-present", body: "s-uncertain", N: "s-missing" }[v]}">${v}</span></td><td class="muted">${esc(where)}</td></tr>`;
      }).join("");
      const snippets = (r.ack.regions || []).slice(0, 3).map((g) => `<div class="snippet"><span class="muted small">${esc(g.kind)}${g.page ? `, page ${g.page}` : ""}</span><br>${esc(g.snippet)}</div>`).join("");
      ackHtml = `<table class="mini"><tbody>${rows}</tbody></table>${snippets || `<p class="muted">No acknowledgements / funding section detected.</p>`}`;
    }
    const page = r.ack && r.ack.page;
    const c = r.iris_vs_arxiv;
    let cmpHtml = "";
    if (c) {
      const tdiff = c.title_diff.map((d) => d.op === "equal" ? esc(d.arxiv)
        : `${d.arxiv ? `<del title="only in arXiv">${esc(d.arxiv)}</del>` : ""}${d.iris ? `<ins title="only in IRIS">${esc(d.iris)}</ins>` : ""}`).join(" ");
      const statusTxt = { same: "", moved: "different position", missing_in_iris: "missing in IRIS", extra_in_iris: "only in IRIS" };
      const rows = c.author_rows.map((a) => `<tr class="ar-${a.status}"><td>${a.arxiv_pos ?? ""}</td><td>${esc(a.arxiv ?? "—")}</td>
        <td>${a.iris_pos ?? ""}</td><td>${esc(a.iris ?? "—")}</td><td>${esc(statusTxt[a.status])}</td></tr>`).join("");
      const v = r.validation || {};
      cmpHtml = `<h3>IRIS vs arXiv</h3>
        <p><span class="cmp ${c.verdict === "same" ? "same" : c.accepted ? "checked" : "diff"}">${esc(c.verdict)}${c.accepted ? " · checked" : ""}</span>
          <span class="muted small">case and punctuation are ignored</span></p>
        <div class="muted small">Title ${c.title_same ? "✓ same words" : `✗ ${c.title_changes} change(s): <del>only in arXiv</del> <ins>only in IRIS</ins>`}</div>
        <p class="tdiff">${tdiff}</p>
        ${c.title_same ? "" : `<p class="muted small">IRIS: ${esc(c.iris_title)}</p>`}
        <div class="muted small">Authors ${c.authors_same ? (c.order_same ? "✓ same" : "✓ same people, different order") : "✗ different"}</div>
        <table class="mini authors"><thead><tr><th>#</th><th>arXiv</th><th>#</th><th>IRIS</th><th></th></tr></thead><tbody>${rows}</tbody></table>
        ${c.verdict !== "same" ? `<div class="row validate-row">
          <input id="cmp-note" placeholder="Note (optional), e.g. IRIS is correct" value="${esc(v.note || "")}">
          ${c.accepted ? `<button type="button" id="cmp-undo">Undo check</button>` : `<button type="button" id="cmp-accept" class="primary">Mark difference as checked</button>`}
        </div>` : ""}`;
    } else if (r.sources && r.sources.iris && r.sources.arxiv) {
      cmpHtml = `<h3>IRIS vs arXiv</h3><p class="muted">Not comparable: the paper needs an entry on both IRIS and arXiv.</p>`;
    }
    const k = r.ranking || {};
    const v = r.validation || {};
    const statusLabel = { matched: "found in the list", uncertain: "uncertain match, please check", not_found: "not found in the list",
      no_data: "no list configured", manual: "from the manual venue list", validated: "set by hand", not_applicable: "not ranked" }[k.status] || k.status;
    const rankHtml = `<h3>Venue ranking</h3>
      <dl class="meta"><dt>Kind</dt><dd>${esc(k.kind || "—")}</dd>
        <dt>Rank</dt><dd>${k.rank ? `<b>${esc(k.rank)}</b>${k.top ? ' <span class="badge s-present">top</span>' : ""}` : "—"} <span class="muted">(${esc(statusLabel || "")})</span></dd>
        ${k.matched ? `<dt>Matched</dt><dd>${esc(k.matched)}</dd>` : ""}
        <dt>Evidence</dt><dd>${esc([k.source, k.evidence].filter(Boolean).join(": ") || "—")}</dd></dl>
      <div class="row validate-row">
        <select id="rank-set" aria-label="Rank"><option value="">— not set —</option>${(state.data.valid_ranks || []).map((x) => `<option ${v.rank === x ? "selected" : ""}>${esc(x)}</option>`).join("")}</select>
        <button type="button" id="rank-save">${v.rank ? "Update rank" : "Set rank by hand"}</button>
        ${v.rank ? `<button type="button" id="rank-clear" class="link">Remove manual rank</button>` : ""}
      </div>`;
    $("#detail-body").innerHTML = `
      <h2>${esc(r.title)}</h2>
      <div class="muted">${esc(a.join("; "))}</div>
      <dl class="meta">
        <dt>Venue</dt><dd>${esc(r.venue || "—")}</dd>
        <dt>Year</dt><dd>${esc(r.year || "—")}</dd>
        <dt>DOI</dt><dd>${r.doi ? `<a href="https://doi.org/${esc(r.doi)}" target="_blank" rel="noopener">${esc(r.doi)}</a>` : "—"}</dd>
        ${r.arxiv_id ? `<dt>arXiv id</dt><dd>${esc(r.arxiv_id)}</dd>` : ""}
        <dt>PDF</dt><dd>${r.pdf_path ? `from ${esc(r.pdf_source || "?")} · <a href="${fileUrl("pdf", r.id)}" target="_blank">open in new tab</a>` : "not available"}</dd>
      </dl>
      <h3>Sources</h3>
      <table class="mini"><thead><tr><th>Source</th><th>Status</th><th>Link</th><th>Evidence</th></tr></thead><tbody>${sources}</tbody></table>
      ${cmpHtml}
      ${rankHtml}
      ${r.notes && r.notes.length ? `<h3>Notes</h3><ul class="notes">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      <h3>Acknowledgements</h3>
      ${ackHtml}
      ${r.ack_page_pdf ? `<details class="pdfbox" data-src="${fileUrl("ackpage", r.id)}#view=FitH" data-cls="ack">
        <summary>Acknowledgements page (page ${page})</summary></details>` : ""}
      ${r.pdf_path ? `<details class="pdfbox" data-src="${fileUrl("pdf", r.id)}${page ? `#page=${page}` : ""}" data-cls="full">
        <summary>Full PDF${r.pdf_cached ? ' <span class="muted small">(cached)</span>' : ""}</summary></details>` : ""}
      <h3>IRIS cover page</h3>
      <p class="muted small">Front page to put before the accepted manuscript when depositing it on IRIS
        (reference, DOI, license), filled from ${r.sources && r.sources.iris && ["present", "uncertain"].includes(r.sources.iris.status) ? "the IRIS record" : "the ORCID/Crossref metadata"}.</p>
      <a class="button" href="${fileUrl("cover", r.id)}" download>Download IRIS cover page</a>
      <h3>Exclude</h3>
      <p class="muted small">Not your paper, or not something to track (e.g. a workshop proceedings or a project page)?</p>
      <button type="button" id="exclude-btn" class="danger">Exclude this paper</button>
      <h3>Cite</h3>
      <div class="row cite-row"><select id="cite-format" aria-label="Citation format">${formatOptions(store.get("citeFormat", "bibtex"))}</select>
        <button type="button" id="cite-copy">Copy</button><button type="button" id="cite-dl">Download</button></div>
      <pre id="cite-text" class="cite"></pre>`;
    // PDFs load only when their section is expanded
    $("#detail-body").querySelectorAll("details.pdfbox").forEach((d) => d.addEventListener("toggle", () => {
      if (d.open && !d.querySelector("iframe")) {
        const f = Object.assign(document.createElement("iframe"), { src: d.dataset.src, title: d.querySelector("summary").textContent });
        f.className = "pdf " + d.dataset.cls;
        d.appendChild(f);
      }
    }));
    const showCite = async () => {
      const fmt = $("#cite-format").value;
      store.set("citeFormat", fmt);
      try {
        const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [r.id], format: fmt, download: false }) });
        $("#cite-text").textContent = res.ok ? await res.text() : "Could not format citation.";
      } catch { $("#cite-text").textContent = "Could not format citation."; }
    };
    $("#cite-format").addEventListener("change", showCite);
    $("#cite-copy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText($("#cite-text").textContent); $("#cite-copy").textContent = "Copied"; setTimeout(() => { $("#cite-copy").textContent = "Copy"; }, 1200); }
      catch { const sel = window.getSelection(), rg = document.createRange(); rg.selectNodeContents($("#cite-text")); sel.removeAllRanges(); sel.addRange(rg); }
    });
    $("#cite-dl").addEventListener("click", () => exportCitations([r.id], $("#cite-format").value).catch((err) => alert(err.message)));
    showCite();
    $("#exclude-btn").addEventListener("click", async () => {
      if (!confirm(`Exclude "${r.title}"?\n\nIt disappears from the table and is skipped in future runs. To bring it back, delete it from Settings → Paper list → Excluded papers.`)) return;
      try { await api("POST", "/api/exclude", { title: r.title }); closeDetail(); await loadPapers(); }
      catch (err) { alert(err.message); }
    });
    const validate = async (body) => {
      try {
        await api("POST", "/api/validate", { id: r.id, ...body });
        await loadPapers();
        openDetail(r.id);
      } catch (err) { alert(err.message); }
    };
    const note = () => ($("#cmp-note") ? $("#cmp-note").value.trim() || null : undefined);
    if ($("#cmp-accept")) $("#cmp-accept").addEventListener("click", () => validate({ iris_vs_arxiv: "accepted", note: note() }));
    if ($("#cmp-undo")) $("#cmp-undo").addEventListener("click", () => validate({ iris_vs_arxiv: null }));
    $("#rank-save").addEventListener("click", () => validate({ rank: $("#rank-set").value || null }));
    if ($("#rank-clear")) $("#rank-clear").addEventListener("click", () => validate({ rank: null }));
    $("#detail").hidden = false; $("#overlay").hidden = false;
    $("#detail").scrollTop = 0;
    $("#detail-close").focus();
  }
  function closeDetail() {
    $("#detail").hidden = true; $("#overlay").hidden = true; $("#detail-body").innerHTML = "";
    history.replaceState(null, "", location.pathname);
  }
  $("#detail-close").addEventListener("click", closeDetail);
  $("#overlay").addEventListener("click", closeDetail);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#detail").hidden) closeDetail(); });

  // ---------- run control ----------
  function buildRunSources() {
    const box = $("#run-sources");
    if (box.dataset.built === state.data.configured_sources.join(",")) return;
    box.dataset.built = state.data.configured_sources.join(",");
    box.innerHTML = state.data.configured_sources.map((s) => `<label><input type="checkbox" value="${esc(s)}" checked> ${esc(label(s))}</label>`).join(" ");
  }
  let polling = null, lastState = null;
  $("#run-btn").addEventListener("click", async () => {
    const sources = [...$("#run-sources").querySelectorAll("input:checked")].map((i) => i.value);
    if (!sources.length) { alert("Select at least one source."); return; }
    try {
      await api("POST", "/api/run", { limit: $("#run-limit").value || null, sources, reload_pdfs: $("#run-reload").checked, no_download: $("#run-nodownload").checked });
      pollStatus();
    } catch (err) { alert(err.message); }
  });
  $("#cancel-btn").addEventListener("click", async () => { try { await api("POST", "/api/run/cancel"); } catch (err) { alert(err.message); } });
  async function pollStatus() {
    clearTimeout(polling);
    let st;
    try { st = await api("GET", "/api/status"); } catch { polling = setTimeout(pollStatus, 3000); return; }
    const running = st.state === "running";
    $("#run-btn").disabled = running;
    $("#cancel-btn").hidden = !running;
    $("#run-status").hidden = st.state === "idle";
    const harvest = !st.n && /IRIS: (\d+) records indexed \(list size (\d+)\)/.exec(st.current || "");
    const pct = st.n ? Math.round((100 * st.i) / st.n) : harvest ? Math.round((100 * harvest[1]) / harvest[2]) : 0;
    $("#progress-bar").style.width = (running ? pct : st.state === "done" ? 100 : pct) + "%";
    const text = {
      running: st.n ? `Running… ${st.i}/${st.n} · ${st.current}` : `Running… ${st.current || "starting"}`,
      done: st.summary ? `Done: ${st.summary.papers} papers, ${st.summary.pdfs} PDFs (${st.summary.pdfs_cached ?? 0} from cache), ${st.summary.acknowledged} with acknowledged projects, ${st.summary.ack_pages} ack pages.` : "Done.",
      cancelled: `Cancelled after ${st.i} papers. Partial results were saved.`,
      failed: `Failed: ${st.error}`,
    }[st.state] || "";
    $("#run-text").textContent = text;
    $("#run-log").textContent = (st.log || []).join("\n");
    if (lastState === "running" && !running) loadPapers();
    if (running && state.data && state.data.iris && lastState !== "running") renderIrisIndex();
    lastState = st.state;
    if (running) polling = setTimeout(pollStatus, 1500);
  }

  // ---------- settings tab ----------
  const form = $("#config-form");
  let cfgMeta = null;
  const lines = (obj) => Object.entries(obj || {}).map(([k, v]) => `${k}: ${v}`).join("\n");
  const parseLines = (text, num) => {
    const out = {};
    for (const raw of text.split("\n")) {
      const line = raw.trim();
      if (!line) continue;
      const i = line.lastIndexOf(":");
      if (i < 1) throw new Error(`Cannot parse line "${line}" (expected key: value)`);
      const k = line.slice(0, i).trim(), v = line.slice(i + 1).trim();
      if (num && Number.isNaN(Number(v))) throw new Error(`"${v}" is not a number (line "${line}")`);
      out[k] = num ? Number(v) : v;
    }
    return out;
  };
  const csv = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const textLines = (s) => s.split("\n").map((x) => x.trim()).filter(Boolean);
  const SOURCE_HELP = {
    iris: "your institution's repository: is the paper deposited? do title/authors match arXiv? (needs the IRIS address)",
    arxiv: "is there an arXiv version? (official arXiv API)",
    openalex: "open-access copies listed by OpenAlex (optional)",
    unpaywall: "open-access copies listed by Unpaywall (optional)",
  };
  function projectRow(p = { name: "", aliases: [] }) {
    const div = document.createElement("div");
    div.className = "proj-row";
    div.innerHTML = `<input placeholder="Project name (e.g. MYPROJECT)" class="p-name" value="${esc(p.name)}">
      <input placeholder="Aliases, comma-separated (optional)" class="p-aliases" value="${esc((p.aliases || []).join(", "))}">
      <button type="button" class="link p-del" aria-label="Remove project">Remove</button>`;
    div.querySelector(".p-del").addEventListener("click", () => div.remove());
    return div;
  }
  $("#add-project").addEventListener("click", () => $("#cfg-projects").appendChild(projectRow()));
  const setVal = (name, v) => { if (form.elements[name]) form.elements[name].value = v ?? ""; };
  async function loadConfig() {
    setMsg("");
    try { cfgMeta = await api("GET", "/api/config"); } catch (err) { setMsg(err.message, true); return; }
    const c = cfgMeta.config;
    $("#cfg-path").textContent = cfgMeta.path;
    setVal("user.name", c.user.name); setVal("user.orcid", c.user.orcid); setVal("user.email", c.user.email);
    const emailWarn = /@example\.(org|com)$/i.test(c.user.email || "");
    form.elements["user.email"].classList.toggle("invalid", emailWarn);
    $("#email-warn").hidden = !emailWarn;
    setVal("iris.url", c.iris.url);
    const pl = c.paper_list || {};
    setVal("paper_list.arxiv_author_id", pl.arxiv_author_id);
    setVal("paper_list.exclude_titles", (pl.exclude_titles || []).join("\n"));
    setVal("paper_list.source", pl.source || "orcid"); setVal("paper_list.file", pl.file);
    $("#cfg-projects").innerHTML = "";
    c.projects.forEach((p) => $("#cfg-projects").appendChild(projectRow(p)));
    $("#cfg-sources").innerHTML = cfgMeta.valid_sources.map((s) => `<label><input type="checkbox" name="src" value="${esc(s)}" ${c.sources.includes(s) ? "checked" : ""}>
      <b>${esc(label(s))}</b> <span class="muted">— ${esc(SOURCE_HELP[s] || "")}</span></label>`).join("");
    const rk = c.rankings || {};
    setVal("rankings.top_quartiles", (rk.top_quartiles || []).join(", ")); setVal("rankings.top_core", (rk.top_core || []).join(", "));
    setVal("rankings.venues", lines(rk.venues)); setVal("rankings.core_source", rk.core_source);
    setVal("rankings.sjr_area", rk.sjr_area); setVal("rankings.sjr_year", rk.sjr_year);
    setVal("rankings.sjr_categories", (rk.sjr_categories || []).join("\n"));
    setVal("rankings.core_csv", rk.core_csv); setVal("rankings.sjr_csv", rk.sjr_csv);
    const nval = Object.keys(c.validations || {}).length;
    $("#cfg-validations").textContent = `Per-paper checks: ${nval} (set them in a paper's details: "Mark difference as checked", "Set rank by hand").`;
    for (const k of ["version_text", "license_text", "repository_name", "assets_dir"]) setVal("cover." + k, (c.cover || {})[k]);
    setVal("dashboard.columns", c.dashboard.columns.join(", "));
    setVal("dashboard.default_filters", lines(c.dashboard.default_filters));
    $("#cfg-valid-cols").textContent = cfgMeta.valid_columns.join(", ");
    for (const k of ["oai_url", "mode", "metadata_prefix", "set", "export_path"]) setVal("iris." + k, c.iris[k]);
    $("#cfg-oai").placeholder = cfgMeta.iris_endpoint ? `${cfgMeta.iris_endpoint} (from the IRIS address)` : "set the IRIS address first";
    setVal("pdf_priority", c.pdf_priority.join(", "));
    $("#cfg-delays").innerHTML = cfgMeta.rate_limits.map((r) => `<tr><td><code>${esc(r.host)}</code></td><td class="muted">${esc(r.why)}</td>
      <td>${r.default == null ? "—" : esc(r.default) + " s"}</td>
      <td><input type="number" min="0" step="0.1" data-host="${esc(r.host)}" value="${r.override ?? ""}" placeholder="${r.default ?? ""}"></td></tr>`).join("");
    setVal("cache_dir", c.cache_dir); setVal("output_dir", c.output_dir);
    setVal("dashboard.host", c.dashboard.host); setVal("dashboard.port", c.dashboard.port);
  }
  function setMsg(text, err) { const m = $("#cfg-msg"); m.textContent = text; m.className = err ? "err" : "ok"; }
  $("#cfg-reload").addEventListener("click", loadConfig);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const el = form.elements, nul = (v) => (v.trim() === "" ? null : v.trim());
    let data;
    try {
      const rate = {};
      form.querySelectorAll("#cfg-delays input[data-host]").forEach((i) => { if (i.value.trim() !== "") rate[i.dataset.host] = Number(i.value); });
      data = {
        ...cfgMeta.config,
        user: { name: nul(el["user.name"].value), orcid: nul(el["user.orcid"].value), email: nul(el["user.email"].value) },
        paper_list: { source: el["paper_list.source"].value, file: nul(el["paper_list.file"].value),
          arxiv_author_id: nul(el["paper_list.arxiv_author_id"].value), exclude_titles: textLines(el["paper_list.exclude_titles"].value) },
        sources: [...form.querySelectorAll("input[name=src]:checked")].map((i) => i.value),
        iris: { url: nul(el["iris.url"].value), oai_url: nul(el["iris.oai_url"].value), mode: el["iris.mode"].value,
          metadata_prefix: el["iris.metadata_prefix"].value.trim() || "oai_dc", set: nul(el["iris.set"].value),
          export_path: nul(el["iris.export_path"].value) },
        projects: [...$("#cfg-projects").querySelectorAll(".proj-row")].map((row) => {
          const name = row.querySelector(".p-name").value.trim(), aliases = csv(row.querySelector(".p-aliases").value);
          return aliases.length ? { name, aliases } : { name };
        }).filter((p) => p.name),
        rankings: { ...(cfgMeta.config.rankings || {}),
          top_quartiles: csv(el["rankings.top_quartiles"].value), top_core: csv(el["rankings.top_core"].value),
          venues: parseLines(el["rankings.venues"].value, false), core_source: el["rankings.core_source"].value.trim() || "ICORE2026",
          sjr_area: el["rankings.sjr_area"].value.trim() || "1700", sjr_year: nul(el["rankings.sjr_year"].value),
          sjr_categories: textLines(el["rankings.sjr_categories"].value),
          core_csv: nul(el["rankings.core_csv"].value), sjr_csv: nul(el["rankings.sjr_csv"].value) },
        dashboard: { host: el["dashboard.host"].value.trim() || "127.0.0.1", port: Number(el["dashboard.port"].value) || 8000,
          columns: csv(el["dashboard.columns"].value), default_filters: parseLines(el["dashboard.default_filters"].value, false) },
        cover: { version_text: el["cover.version_text"].value.trim(), license_text: el["cover.license_text"].value.trim(),
          repository_name: el["cover.repository_name"].value.trim(), assets_dir: el["cover.assets_dir"].value.trim() },
        pdf_priority: csv(el.pdf_priority.value),
        rate_limits: rate,
        cache_dir: el.cache_dir.value.trim() || "cache", output_dir: el.output_dir.value.trim() || "output",
      };
    } catch (err) { setMsg(err.message, true); return; }
    try {
      cfgMeta = await api("PUT", "/api/config", data);
      setMsg("Saved. Applies to the next run.");
      state.filters = null; store.del("filters");
      await loadConfig(); loadPapers();
    } catch (err) { setMsg(err.message, true); }
  });
  // sidebar: highlight the section in view
  const navLinks = [...document.querySelectorAll(".settings-nav a")];
  if ("IntersectionObserver" in window) {
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + en.target.id));
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    document.querySelectorAll("#config-form fieldset[id]").forEach((f) => obs.observe(f));
  }
  navLinks.forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    document.querySelector(a.getAttribute("href")).scrollIntoView({ behavior: "smooth", block: "start" });
  }));

  // ---------- first-start setup ----------
  let suName = null;
  function suProjectRow(p = { name: "", aliases: [] }) {
    const row = projectRow(p);
    return row;
  }
  $("#su-add").addEventListener("click", () => $("#su-projects").appendChild(suProjectRow()));
  $("#su-check").addEventListener("click", async () => {
    const msg = $("#su-orcid-msg");
    msg.className = "hint"; msg.textContent = "Checking on ORCID…";
    try {
      const r = await api("POST", "/api/setup/orcid", { orcid: $("#su-orcid").value });
      suName = r.name;
      $("#su-orcid").value = r.orcid;
      msg.textContent = `Found: ${r.name || "(name not public)"}${r.works != null ? `, ${r.works} works on ORCID` : ""}.`;
    } catch (err) { msg.className = "hint err"; msg.textContent = err.message; }
  });
  $("#setup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const projects = [...$("#su-projects").querySelectorAll(".proj-row")].map((row) => ({
      name: row.querySelector(".p-name").value.trim(),
      aliases: row.querySelector(".p-aliases").value.split(",").map((x) => x.trim()).filter(Boolean),
    })).filter((p) => p.name);
    try {
      await api("POST", "/api/setup", { orcid: $("#su-orcid").value.trim(), name: suName, projects,
        iris_url: $("#su-iris").value.trim(), email: $("#su-email").value.trim() });
      location.reload();
    } catch (err) { $("#su-msg").textContent = err.message; }
  });
  async function needsSetup() {
    let st;
    try { st = await api("GET", "/api/setup"); } catch { return false; }
    if (!st.needed) return false;
    document.querySelector(".tabs").hidden = true;
    $("#tab-papers").hidden = true; $("#tab-config").hidden = true;
    $("#setup").hidden = false;
    $("#su-iris").value = st.default_iris_url || "";
    if (!$("#su-projects").children.length) $("#su-projects").appendChild(suProjectRow());
    return true;
  }

  // ---------- boot ----------
  needsSetup().then((setup) => {
    if (setup) return;
    if (location.hash === "#config") showTab("config");
    loadPapers().catch((err) => { $("#empty").hidden = false; $("#empty").textContent = "Could not load data: " + err.message; });
    pollStatus();
  });
})();
