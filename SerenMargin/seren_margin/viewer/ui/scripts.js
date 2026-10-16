// ── SerenMargin viewer - leaf logic ──────────────────────────────────────────
// Snaps onto the SerenMeninges shell, which provides api() (same-origin, the
// saved bearer attached), escapeHtml(), showTab() and the 🔑 token modal.
//
// THE RULE OF THIS PAGE: it is the engine check, and nothing else. It calls
// exactly three routes - "/", "/notes/stats" and "/stores" - and every one of
// them is content-blind by construction. No note, no topic, no letter, no
// dedication is fetched here, and tests/test_viewer.py reads this file and
// fails the build if a fourth route appears. The diary is the writer's.

// ---- errors -----------------------------------------------------------------
function showErr(msg) {
    document.getElementById("globalErr").innerHTML = `<div class="err">${escapeHtml(msg)}</div>`;
}
function clearErr() { document.getElementById("globalErr").innerHTML = ""; }

// ---- small helpers ----------------------------------------------------------
function relAge(ts) {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return Math.round(s) + "s ago";
    if (s < 5400) return Math.round(s / 60) + "m ago";
    if (s < 129600) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
}
function human(n) {
    n = Number(n || 0);
    for (const u of ["B", "KB", "MB", "GB"]) { if (n < 1024 || u === "GB") return (u === "B" ? n : n.toFixed(1)) + " " + u; n /= 1024; }
}
function stat(n, lbl, accent) {
    return `<div class="stat${accent ? " accent" : ""}"><div class="big">${n}</div><div class="lbl">${lbl}</div></div>`;
}
function setFinder(finder) {
    const b = document.getElementById("finderBadge");
    if (!b) return;
    b.textContent = `finder: ${finder || "…"}`;
    b.className = "head-pill" + (finder === "fts" ? " hot" : "");
}

// ---- status -----------------------------------------------------------------
// The kind vocabulary /notes/stats buckets into; the order the bars show in.
const KINDS = ["note", "observation", "idea", "question", "feeling", "reminder", "letter", "other", "_unkinded"];
const KIND_LABEL = { _unkinded: "no kind given" };

async function loadStatus() {
    clearErr();
    try {
        const [root, stats] = await Promise.all([api("/"), api("/notes/stats")]);
        setFinder(root.finder);
        const ks = stats.keepsakes || { count: 0, bytes: 0 };
        document.getElementById("stats").innerHTML =
            stat(stats.total ?? 0, "notes on the board", true) +
            stat(stats.kinds?.letter ?? 0, "letters", false) +
            stat(`${ks.count}<span class="sub">${human(ks.bytes)}</span>`, "keepsakes", false);
        const kinds = stats.kinds || {};
        const order = KINDS.filter(k => kinds[k]).concat(Object.keys(kinds).filter(k => !KINDS.includes(k)));
        const max = Math.max(1, ...order.map(k => kinds[k]));
        document.getElementById("kindBars").innerHTML = order.map(k => `
          <div class="bar-row">
            <span class="pn">${escapeHtml(KIND_LABEL[k] || k)}</span>
            <span class="bar"><i style="width:${Math.round(kinds[k] / max * 100)}%"></i></span>
            <span class="bn">${kinds[k]}</span>
          </div>`).join("") || `<div class="empty">nothing on the board yet</div>`;
        const up = root.updates || {};
        const updates = up.status === "update_available" ? `${up.latest} available (installed ${up.installed})`
                      : up.status ? up.status.replace(/_/g, " ") : "unknown";
        document.getElementById("serviceMeta").innerHTML = [
            ["version", root.version || "?"],
            ["finder", root.finder === "fts" ? "fts (full-text search)" : root.finder === "like" ? "like (a sqlite without FTS5)" : root.finder || "?"],
            ["updates", updates],
            ["ethos", root.ethos || ""],
        ].map(([k, v]) => `<div class="row"><span class="k">${escapeHtml(k)}</span><span class="v">${escapeHtml(String(v))}</span></div>`).join("");
    } catch (e) { showErr(e.message); }
}

// ---- backups: the same panel on every Seren viewer --------------------------
// Reads /stores (seren_sinew.stores): what is kept, the snapshots, when a
// Lodestar last pulled, how the last rehearsal went. Counts and times only.
// Kept parallel with Memory's and Loci's copy on purpose: fix one, fix all.
function renderBackups(el, d) {
    if (!d || !d.ok) { el.innerHTML = `<div class="empty">this service keeps no snapshots (backup.enabled is off)</div>`; return; }
    const s = d.snapshots || {};
    const latest = s.latest ? `${escapeHtml(s.latest.id)} · ${relAge(s.latest.created_at)} · ${escapeHtml(s.latest.reason || "")}` : "none yet";
    const pull = s.last_pull ? `${relAge(s.last_pull.created_at)} (${escapeHtml(s.last_pull.id)})` : "never from this box";
    const r = d.last_rehearsal;
    const reh = r ? `<span class="${r.ok ? "ok" : "bad"}">${r.ok ? "ok" : "FAILED"}</span> · ${relAge(r.at)} · ${escapeHtml(r.snapshot)} (${escapeHtml(r.source)}${r.problems ? `, ${r.problems} problem${r.problems === 1 ? "" : "s"}` : ""})`
                  : "none since this process started";
    const stores = (d.stores || []).map(st =>
        `<div class="row"><span class="k">${escapeHtml(st.name)}</span><span class="v">${escapeHtml(st.kind)} · ${human(st.bytes)}${st.backed_up ? "" : " · not copied"}${st.exists ? "" : " · nothing on disk yet"}</span></div>`).join("");
    el.innerHTML = `
      <div class="row"><span class="k">snapshots</span><span class="v">${s.count ?? 0} kept · newest ${s.keep_daily} daily, then one a week for ${s.keep_weekly} weeks</span></div>
      <div class="row"><span class="k">newest</span><span class="v">${latest}</span></div>
      <div class="row"><span class="k">last pull</span><span class="v">${pull}</span></div>
      <div class="row"><span class="k">last rehearsal</span><span class="v">${reh}</span></div>
      ${stores}`;
}
async function loadBackups() {
    clearErr();
    const el = document.getElementById("backupPanel");
    try { renderBackups(el, await api("/stores")); }
    catch (e) { el.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
}

// ---- tabs, refresh, boot ----------------------------------------------------
function switchTab(tab) {
    showTab(tab);
    if (tab === "status") loadStatus();
    if (tab === "backups") loadBackups();
}
function reload() {
    const active = document.querySelector(".tabbar .tab.active");
    switchTab(active ? active.dataset.tab : "status");
}
loadStatus();
