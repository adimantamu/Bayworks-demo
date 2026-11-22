const $ = (id) => document.getElementById(id);
let selected = null;

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function setStatus(message, kind = "warn") {
  const el = $("ops-status");
  if (!message) {
    el.hidden = true;
    el.textContent = "";
    el.className = "banner";
    return;
  }
  el.hidden = false;
  el.textContent = message;
  el.className = kind === "ok" ? "banner ok" : kind === "err" ? "banner err" : "banner";
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body ? { "Content-Type": "application/json", ...(opts.headers || {}) } : opts.headers,
    ...opts,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function fmtLatency(ms) {
  if (ms == null) return "—";
  return `${(ms / 1000).toFixed(1)}s`;
}

function renderObjects(objects) {
  const root = $("objects");
  if (!root) return;
  if (!objects.length) {
    root.innerHTML = `<p class="hint">No objects yet. Dummy MP4/MPS seed after startup, or attach bay video from the service bay.</p>`;
    return;
  }
  root.innerHTML = objects
    .map((o) => {
      const playable = (o.content_type || "").startsWith("video/") && (o.content_type || "").includes("mp4");
      const media = playable
        ? `<video muted playsinline src="/api/uploads/${esc(o.id)}/file"></video>`
        : `<div class="hint" style="display:flex;align-items:center;justify-content:center;height:46px">${esc(o.media_kind || "file")}</div>`;
      const name = (o.metadata && o.metadata.filename) || o.s3_key;
      return `<div class="object-row">${media}<div class="kvs">
        <div><b>${esc(name)}</b> · ${esc(o.media_kind)} · ${esc(o.byte_size || 0)} B</div>
        <div class="hash">${esc(o.s3_uri)}</div>
        <div class="hash">sha256 ${esc(o.content_hash)}</div>
      </div></div>`;
    })
    .join("");
}

async function refresh() {
  const [metrics, list, objects] = await Promise.all([
    api("/api/ops/metrics"),
    api("/api/repairs"),
    api("/api/ops/objects").catch(() => ({ objects: [] })),
  ]);
  renderObjects(objects.objects || []);
  $("metrics").innerHTML = [
    ["Repairs", metrics.repairs],
    ["Filed", metrics.filed],
    ["Caught missing fields", metrics.caught_missing_fields],
    ["Dead-letter", metrics.dead_letter],
    ["Avg latency", metrics.avg_latency_s == null ? "—" : `${metrics.avg_latency_s.toFixed(1)}s`],
  ]
    .map(([l, n]) => `<div class="metric"><div class="n">${esc(n)}</div><div class="l">${esc(l)}</div></div>`)
    .join("");

  $("rows").innerHTML = list.repairs
    .map((r) => {
      const missing = (r.missing_required || []).length;
      const selectedCls = r.id === selected ? " selected" : "";
      return `<tr class="clickable${selectedCls}" data-id="${esc(r.id)}">
        <td class="mono">${esc(r.ro_number)}<div class="hint">${esc(r.repair_order?.labor_op_name || "")}</div></td>
        <td><span class="status-pill ${esc(r.status)}">${esc(r.status)}</span></td>
        <td>${missing ? `${missing} fields` : "—"}</td>
        <td class="mono">${esc(fmtLatency(r.latency_ms))}</td>
      </tr>`;
    })
    .join("");

  $("rows").querySelectorAll("tr").forEach((tr) => {
    tr.onclick = () => select(tr.dataset.id);
  });
  if (selected) await loadDetail(selected);
}

async function select(id) {
  selected = id;
  await loadDetail(id);
  $("rows").querySelectorAll("tr").forEach((tr) => {
    tr.classList.toggle("selected", tr.dataset.id === id);
  });
}

async function loadDetail(id) {
  const [repair, ev] = await Promise.all([
    api(`/api/repairs/${id}`),
    api(`/api/repairs/${id}/events`),
  ]);
  $("detail").innerHTML = `
    <div class="kvs">
      <div><b>${esc(repair.ro_number)}</b> · ${esc(repair.status)}</div>
      <div class="hash">${esc(repair.upload?.s3_uri || "")}</div>
      <div class="hash">sha256 ${esc(repair.upload?.content_hash || "")}</div>
      <div>prompt ${esc(repair.prompt_version)} · validator ${esc(repair.validator_version)} · story v${esc(repair.story_version || 0)}</div>
      <div>missing: ${esc((repair.missing_required || []).join(", ") || "none")}</div>
      ${repair.last_error ? `<div class="hash">last error: ${esc(repair.last_error)}</div>` : ""}
    </div>`;
  $("events").innerHTML = ev.events
    .map((e) => {
      const t = (e.ts || "").slice(11, 19);
      return `<div class="event ${esc(e.level)}"><div class="t">${esc(t)}<br>${esc(e.stage)}</div><div>${esc(e.message)}</div></div>`;
    })
    .join("");
}

async function injectFaults(count) {
  try {
    const r = await api("/api/ops/dms-fail-next", {
      method: "POST",
      body: JSON.stringify({ count }),
    });
    const label =
      count >= 5
        ? `Next ${r.fail_next} DMS writes will 503 — enough to dead-letter. Run a complete scenario.`
        : `Next DMS write will 503 (fail_next=${r.fail_next}). Run a complete scenario to see retry.`;
    setStatus(label, "warn");
  } catch (err) {
    setStatus(err.message, "err");
  }
}

$("fail-dms").addEventListener("click", () => injectFaults(1));
$("fail-dms-5").addEventListener("click", () => injectFaults(5));

$("reset-demo").addEventListener("click", async () => {
  try {
    await api("/api/ops/reset", { method: "POST" });
    selected = null;
    $("detail").textContent = "Select a repair.";
    $("events").innerHTML = "";
    setStatus("Demo reset. Fixtures and DMS fault injection cleared.", "ok");
    await refresh();
  } catch (err) {
    setStatus(err.message, "err");
  }
});

refresh().catch((err) => setStatus(err.message, "err"));
setInterval(() => {
  refresh().catch((err) => setStatus(err.message, "err"));
}, 1500);
