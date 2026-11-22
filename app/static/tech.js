const STEPS = [
  "queued",
  "transcribing",
  "extracting",
  "validating",
  "needs_followup",
  "filing",
  "filed",
  "dead_letter",
];
const $ = (id) => document.getElementById(id);

const state = {
  repairId: null,
  recording: false,
  recorder: null,
  chunks: [],
  poll: null,
  orders: [],
  sawDuplicate: false,
  questionKey: null,
};

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function flash(message, kind = "err") {
  const el = $("flash");
  if (!message) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = message;
  el.className = kind === "ok" ? "banner ok" : kind === "warn" ? "banner" : "banner err";
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

async function loadOrders() {
  const rooftop = $("rooftop").value.trim() || "keegan-honda";
  const data = await api(`/api/repair-orders?rooftop_id=${encodeURIComponent(rooftop)}`);
  state.orders = data.orders;
  $("ro").innerHTML = data.orders
    .map(
      (o) =>
        `<option value="${esc(o.ro_number)}">${esc(o.ro_number)} · ${esc(o.labor_op_name)}</option>`
    )
    .join("");
  applyRo();
}

function currentOrder() {
  return state.orders.find((o) => o.ro_number === $("ro").value);
}

function applyRo() {
  const o = currentOrder();
  if (!o) return;
  $("vin").value = o.vin;
  $("mileage").value = o.mileage ?? "";
  $("ro-meta").textContent = `${o.vehicle} · ${o.technician} · “${o.customer_concern}”`;
}

$("ro").addEventListener("change", applyRo);
$("rooftop").addEventListener("change", () => {
  loadOrders().catch((err) => {
    $("ro-meta").textContent = err.message;
    flash(err.message);
  });
});

function sha256Hex(buffer) {
  return crypto.subtle.digest("SHA-256", buffer).then((digest) =>
    [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("")
  );
}

function mime() {
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) return "audio/webm;codecs=opus";
  if (MediaRecorder.isTypeSupported("audio/webm")) return "audio/webm";
  if (MediaRecorder.isTypeSupported("audio/mp4")) return "audio/mp4";
  return "";
}

function setEvidenceEnabled(on) {
  const input = $("evidence-file");
  const wrap = $("evidence-wrap");
  input.disabled = !on;
  wrap.style.opacity = on ? "1" : "0.55";
}

$("rec").addEventListener("click", async () => {
  if (state.recording) {
    state.recorder.stop();
    return;
  }
  flash("");
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    const denied = err && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError");
    flash(denied ? "Microphone permission denied. Use a fixture or upload an audio file." : err.message);
    return;
  }
  const type = mime();
  state.recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
  state.chunks = [];
  state.recorder.ondataavailable = (e) => {
    if (e.data.size) state.chunks.push(e.data);
  };
  state.recorder.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    state.recording = false;
    $("rec").classList.remove("hot");
    $("rec").textContent = "Tap to record cause + correction";
    const blob = new Blob(state.chunks, { type: state.recorder.mimeType || "audio/webm" });
    try {
      await uploadBlob(blob);
    } catch (err) {
      flash(err.message);
    }
  };
  state.recorder.start();
  state.recording = true;
  $("rec").classList.add("hot");
  $("rec").textContent = "Recording… tap to stop";
});

$("audio-file").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";
  if (!file) return;
  flash("");
  try {
    await uploadBlob(file);
  } catch (err) {
    flash(err.message);
  }
});

$("evidence-file").addEventListener("change", async (e) => {
  const file = e.target.files && e.target.files[0];
  e.target.value = "";
  if (!file) return;
  if (!state.repairId) {
    flash("Record a note or run a scenario first, then attach bay video or photo.");
    return;
  }
  flash("");
  try {
    await uploadEvidence(file);
  } catch (err) {
    flash(err.message);
  }
});

async function uploadBlob(blob, parentRepairId = null) {
  const buf = await blob.arrayBuffer();
  const hash = await sha256Hex(buf);
  const contentType = blob.type || "audio/webm";
  const body = {
    rooftop_id: $("rooftop").value,
    ro_number: $("ro").value,
    content_hash: hash,
    content_type: contentType,
    byte_size: blob.size,
    media_kind: "audio",
  };
  const signed = await api("/api/uploads/presign", { method: "POST", body: JSON.stringify(body) });
  if (signed.idempotent && signed.repair_id) {
    watch(signed.repair_id, { duplicate: true });
    return;
  }
  try {
    const put = await fetch(signed.upload_url, {
      method: "PUT",
      headers: { "Content-Type": contentType },
      body: blob,
    });
    if (!put.ok) throw new Error("presigned PUT failed");
    const done = await api("/api/uploads/complete", {
      method: "POST",
      body: JSON.stringify({
        ...body,
        vin: $("vin").value,
        s3_key: signed.s3_key,
        parent_repair_id: parentRepairId,
      }),
    });
    watch(done.id, done);
  } catch (_err) {
    const form = new FormData();
    form.append("file", blob, blob.name || "note.webm");
    form.append("rooftop_id", $("rooftop").value);
    form.append("ro_number", $("ro").value);
    form.append("vin", $("vin").value);
    form.append("content_hash", hash);
    form.append("parent_repair_id", parentRepairId || "");
    const res = await fetch("/api/uploads/proxy", { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    const done = await res.json();
    watch(done.id, done);
  }
}

function drawToJpeg(source, w, h) {
  if (!w || !h) return null;
  const canvas = document.createElement("canvas");
  const max = 320;
  const scale = max / Math.max(w, h);
  canvas.width = Math.max(1, Math.round(w * scale));
  canvas.height = Math.max(1, Math.round(h * scale));
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  try {
    return canvas.toDataURL("image/jpeg", 0.6);
  } catch (_err) {
    return null;
  }
}

function captureKeyframe(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const finish = (data) => {
      URL.revokeObjectURL(url);
      resolve(data);
    };
    if (file.type.startsWith("image/")) {
      const img = new Image();
      img.onload = () => finish(drawToJpeg(img, img.naturalWidth, img.naturalHeight));
      img.onerror = () => finish(null);
      img.src = url;
      return;
    }
    if (!file.type.startsWith("video/")) {
      finish(null);
      return;
    }
    const video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    const timer = setTimeout(() => finish(null), 4000);
    const stop = (data) => {
      clearTimeout(timer);
      finish(data);
    };
    video.onseeked = () => stop(drawToJpeg(video, video.videoWidth, video.videoHeight));
    video.onloadeddata = () => {
      try {
        video.currentTime = Math.min(0.4, (video.duration || 1) * 0.1);
      } catch (_err) {
        stop(null);
      }
    };
    video.onerror = () => stop(null);
    video.src = url;
  });
}

async function uploadEvidence(file) {
  const buf = await file.arrayBuffer();
  const hash = await sha256Hex(buf);
  const contentType = file.type || "application/octet-stream";
  const mediaKind = contentType.startsWith("image/") ? "photo" : "video";
  const keyframe = await captureKeyframe(file);
  const metadata = {
    filename: file.name,
    keyframe,
    captured_at: new Date().toISOString(),
  };
  const body = {
    rooftop_id: $("rooftop").value,
    ro_number: $("ro").value,
    content_hash: hash,
    content_type: contentType,
    byte_size: file.size,
    media_kind: mediaKind,
  };
  const signed = await api("/api/uploads/presign", { method: "POST", body: JSON.stringify(body) });
  if (!signed.idempotent) {
    try {
      const put = await fetch(signed.upload_url, {
        method: "PUT",
        headers: { "Content-Type": contentType },
        body: file,
      });
      if (!put.ok) throw new Error("presigned PUT failed");
    } catch (_err) {
      const form = new FormData();
      form.append("file", file, file.name || "evidence.bin");
      form.append("rooftop_id", $("rooftop").value);
      form.append("ro_number", $("ro").value);
      form.append("vin", $("vin").value);
      form.append("content_hash", hash);
      form.append("parent_repair_id", "");
      form.append("attach_repair_id", state.repairId);
      form.append("media_kind", mediaKind);
      form.append("metadata_json", JSON.stringify(metadata));
      const res = await fetch("/api/uploads/proxy", { method: "POST", body: form });
      if (!res.ok) throw new Error(await res.text());
      const done = await res.json();
      watch(done.id, done);
      return;
    }
  }
  const done = await api(`/api/repairs/${state.repairId}/evidence`, {
    method: "POST",
    body: JSON.stringify({
      content_hash: hash,
      s3_key: signed.s3_key,
      content_type: contentType,
      byte_size: file.size,
      media_kind: mediaKind,
      metadata,
    }),
  });
  watch(done.id, done);
}

document.querySelectorAll("[data-scenario]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    flash("");
    try {
      const done = await api("/api/scenarios", {
        method: "POST",
        body: JSON.stringify({ scenario: btn.dataset.scenario, rooftop_id: $("rooftop").value }),
      });
      const order = state.orders.find((o) => o.ro_number === done.ro_number);
      if (order) {
        $("ro").value = order.ro_number;
        applyRo();
      }
      watch(done.id, done);
    } catch (err) {
      flash(err.message);
    }
  });
});

function watch(id, snapshot) {
  if (snapshot && snapshot.duplicate) {
    state.sawDuplicate = true;
  } else if (state.repairId !== id) {
    state.sawDuplicate = false;
  }
  state.repairId = id;
  setEvidenceEnabled(true);
  $("empty").hidden = true;
  $("live").hidden = false;
  if (snapshot && snapshot.status) render(snapshot);
  else if (state.sawDuplicate) $("dup").hidden = false;
  if (state.poll) clearInterval(state.poll);
  refresh();
  state.poll = setInterval(refresh, 700);
}

async function refresh() {
  if (!state.repairId) return;
  try {
    const repair = await api(`/api/repairs/${state.repairId}`);
    render(repair);
    if (["filed", "needs_followup", "dead_letter"].includes(repair.status)) {
      clearInterval(state.poll);
    }
  } catch (err) {
    flash(err.message);
  }
}

function render(repair) {
  if (repair.duplicate) state.sawDuplicate = true;
  $("dup").hidden = !state.sawDuplicate;
  const errEl = $("job-err");
  if (repair.last_error) {
    errEl.hidden = false;
    errEl.textContent = repair.last_error;
  } else {
    errEl.hidden = true;
    errEl.textContent = "";
  }
  $("steps").innerHTML = STEPS.map((s) => {
    let cls = "";
    if (repair.status === s) cls = s === "needs_followup" || s === "dead_letter" ? "bad" : "on";
    else if (STEPS.indexOf(repair.status) > STEPS.indexOf(s) && s !== "needs_followup") cls = "ok";
    if (repair.status === "filed" && (s === "needs_followup" || s === "dead_letter")) cls = "";
    if (repair.status === "dead_letter" && s === "needs_followup") cls = "";
    return `<span class="${cls}">${s.replaceAll("_", " ")}</span>`;
  }).join("");
  $("status-line").innerHTML = `<span class="status-pill ${esc(repair.status)}">${esc(repair.status)}</span>`;
  const hash = repair.upload?.content_hash || "";
  $("lineage").innerHTML = `
    <div>RO <b>${esc(repair.ro_number)}</b> · VIN <b>${esc(repair.vin)}</b></div>
    <div class="hash">sha256 <b>${esc(hash)}</b></div>
    <div>${esc(repair.upload?.s3_uri || "")}</div>
    <div>schema ${esc(repair.schema_version || "—")} · prompt ${esc(repair.prompt_version || "—")} · validator ${esc(repair.validator_version || "—")} · v${esc(repair.story_version || 0)}</div>
  `;
  const story = repair.story || {};
  const block = [
    story.concern && `CONCERN\n${story.concern.toUpperCase()}`,
    story.cause && `CAUSE\n${story.cause.toUpperCase()}`,
    story.correction && `CORRECTION\n${story.correction.toUpperCase()}`,
  ]
    .filter(Boolean)
    .join("\n\n");
  $("story").textContent = block || "Waiting on extraction…";
  $("transcript").textContent = repair.transcript || "—";
  renderFacts(story);
  renderEvidence(repair);
  renderQuestions(repair);
}

function prettyName(name) {
  return String(name || "").replaceAll("_", " ");
}

function fmtValue(value) {
  if (value == null || value === "") return "";
  const n = Number(value);
  if (Number.isFinite(n)) return Number.isInteger(n) ? String(n) : String(n);
  return String(value);
}

function renderFacts(story) {
  const chips = [];
  for (const m of story.measurements || []) {
    if (m.value == null) continue;
    const src = m.source === "prompted" ? "prompted" : "spoken";
    const unit = m.unit ? ` ${m.unit}` : "";
    chips.push(
      `<span class="chip ${src}">${esc(prettyName(m.name))} ${esc(fmtValue(m.value))}${esc(unit)}<em>${src}</em></span>`
    );
  }
  for (const dtc of story.dtcs || []) {
    chips.push(`<span class="chip">${esc(dtc)}<em>dtc</em></span>`);
  }
  if (story.cylinder != null) {
    chips.push(`<span class="chip">cylinder ${esc(story.cylinder)}</span>`);
  }
  if (story.codes_cleared != null) {
    chips.push(
      `<span class="chip">codes ${story.codes_cleared ? "cleared" : "not cleared"}</span>`
    );
  }
  for (const part of story.parts || []) {
    const pn = part.part_number ? ` ${esc(part.part_number)}` : "";
    chips.push(
      `<span class="chip">${esc(part.action || "replaced")} ${esc(part.name)}${pn}</span>`
    );
  }
  $("facts").innerHTML = chips.join("") || `<span class="hint">No structured measurements yet.</span>`;
}

function renderEvidence(repair) {
  const items = repair.evidence || [];
  $("evidence-section").hidden = items.length === 0;
  const root = $("evidence");
  root.replaceChildren();
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "evidence-item";
    const thumb = item.metadata && (item.metadata.keyframe || item.metadata.thumbnail);
    if (thumb) {
      const img = document.createElement("img");
      img.alt = "";
      img.src = thumb;
      row.appendChild(img);
    }
    const meta = document.createElement("div");
    const kind = document.createElement("div");
    kind.textContent = item.media_kind || "file";
    const uri = document.createElement("div");
    uri.className = "hash";
    uri.textContent = item.s3_uri || "";
    const extra = document.createElement("div");
    extra.className = "hint";
    const name = item.metadata && item.metadata.filename;
    extra.textContent = [name, item.byte_size ? `${item.byte_size} B` : ""].filter(Boolean).join(" · ");
    meta.append(kind, uri, extra);
    row.appendChild(meta);
    root.appendChild(row);
  }
}

function renderQuestions(repair) {
  const root = $("questions");
  const qs = repair.followup_questions || [];
  if (repair.status !== "needs_followup" || !qs.length) {
    state.questionKey = null;
    root.innerHTML = "";
    return;
  }
  const key = `${repair.id}:${repair.story_version}:${qs.map((q) => q.field).join(",")}`;
  if (state.questionKey === key) return;
  state.questionKey = key;
  root.innerHTML = `<div class="banner">Required warranty fields missing. Do not file. Ask the technician before the claim leaves the bay.</div>`;
  qs.forEach((q) => {
    const wrap = document.createElement("div");
    wrap.className = "q";
    const label = document.createElement("label");
    label.textContent = q.question;
    const input = document.createElement("input");
    input.dataset.field = q.field;
    input.placeholder = q.unit || "";
    input.type = q.input_type === "number" ? "text" : "text";
    wrap.append(label, input);
    root.appendChild(wrap);
  });
  const submit = document.createElement("button");
  submit.className = "go";
  submit.type = "button";
  submit.textContent = "Submit measurements";
  submit.style.marginTop = "12px";
  submit.onclick = async () => {
    const answers = {};
    root.querySelectorAll("input[data-field]").forEach((el) => {
      answers[el.dataset.field] = el.value;
    });
    try {
      const done = await api(`/api/repairs/${repair.id}/answers`, {
        method: "POST",
        body: JSON.stringify({ answers }),
      });
      state.questionKey = null;
      watch(done.id, done);
    } catch (err) {
      flash(err.message);
    }
  };
  root.appendChild(submit);
}

setEvidenceEnabled(false);
loadOrders().catch((err) => {
  $("ro-meta").textContent = err.message;
  flash(err.message);
});
