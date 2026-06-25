// gradphone — operator's & tenant's dashboard.
// Polls /ui/calls/live and /ui/history (tenant-scoped on the server side),
// posts to /ui/dial, opens result modal on completion.

const LIVE_POLL_MS = 3000;
const HISTORY_POLL_MS = 10000;
const TENANTS_POLL_MS = 15000;
const RESULT_POLL_MS = 5000;
const RESULT_DEADLINE_MS = 10 * 60 * 1000;

const $ = (id) => document.getElementById(id);
const IS_OPERATOR = document.body.dataset.role === "operator";

// ─── Toast helper ────────────────────────────────────────
let toastTimer = null;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("visible"), 4000);
}

// ─── Modal ───────────────────────────────────────────────
function openResultModal(room, result) {
  $("modalSubtitle").textContent = room;
  const body = $("modalBody");
  const br = (result && result.business_result) || {};
  const rows = [
    ["status", br.status || "—"],
    ["answer", br.answer || "—"],
    ["confidence", br.confidence || "—"],
    ["duration", `${(result.duration_seconds || 0).toFixed(1)}s`],
    ["framework", result.framework || "—"],
  ];
  if (result.answered_by) rows.push(["answered by", result.answered_by]);
  if (result.twilio_call_status) rows.push(["twilio status", result.twilio_call_status]);
  body.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(String(v))}</dd>`).join("");
  $("modalbackdrop").classList.add("visible");
}
function closeModal() { $("modalbackdrop").classList.remove("visible"); }
window.closeModal = closeModal;

// ─── HTML escaping ───────────────────────────────────────
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ─── Fetch helpers ───────────────────────────────────────
async function getJson(path) {
  const r = await fetch(path, { credentials: "same-origin" });
  if (r.status === 401 || r.status === 307) { window.location.href = "/ui/login"; return null; }
  return r.json();
}
async function postJson(path, body) {
  const r = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.status === 401 || r.status === 307) { window.location.href = "/ui/login"; return null; }
  return r.json();
}

// ─── Live calls ──────────────────────────────────────────
function renderLive(calls) {
  $("livecount").textContent = String(calls.length).padStart(2, "0");
  $("livedot").classList.toggle("live", calls.length > 0);

  const container = $("livecalls");
  if (!calls.length) {
    container.innerHTML = `<div class="empty">no calls in flight</div>`;
    return;
  }
  const rows = calls.map((c) => `
    <tr>
      <td><span class="dest">${escapeHtml(c.destination || "?")}</span>
          <span class="lang-tag">${escapeHtml(c.language || "en")}</span>
          <span class="task">${escapeHtml(c.business_name || c.room || "")}</span></td>
      <td><span class="phase">${escapeHtml(c.phase || "—")}</span></td>
      <td><span class="ts">${(c.age_seconds || 0).toFixed(0)}s</span></td>
    </tr>
  `).join("");
  container.innerHTML = `
    <table>
      <thead><tr><th>Destination</th><th>Phase</th><th>Age</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function refreshLive() {
  const data = await getJson("/ui/calls/live");
  if (!data) return;
  renderLive(data.calls || []);
}

// ─── History ─────────────────────────────────────────────
function renderHistory(calls) {
  $("histcount").textContent = String(calls.length).padStart(2, "0");

  const container = $("history");
  if (!calls.length) {
    container.innerHTML = `<div class="empty">no calls yet — try one</div>`;
    return;
  }
  const rows = calls.map((c) => {
    const status = c.status || "pending";
    const ts = (c.started_at || "").slice(0, 16).replace("T", " ");
    const dur = c.duration_seconds ? `${c.duration_seconds.toFixed(0)}s` : "—";
    const answer = c.answer ? `<span class="answer">${escapeHtml(c.answer)}</span>` : "";
    const audioBtn = c.room
      ? `<a href="#" onclick="event.preventDefault(); playAudio('${escapeHtml(c.room)}')">▶ audio</a>`
      : "";
    return `
      <tr>
        <td><span class="dest">${escapeHtml(c.destination || "?")}</span>
            <span class="lang-tag">${escapeHtml(c.language || "en")}</span>
            <span class="task">${escapeHtml(c.task || "")}</span>
            ${answer}</td>
        <td><span class="status ${status}">${escapeHtml(status)}</span></td>
        <td><span class="ts">${ts}</span><br><span class="ts">${dur}</span></td>
        <td>${audioBtn}</td>
      </tr>
    `;
  }).join("");
  container.innerHTML = `
    <table>
      <thead><tr><th>Destination · Task</th><th>Status</th><th>When</th><th>Audio</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

async function refreshHistory() {
  const data = await getJson("/ui/history");
  if (!data) return;
  renderHistory(data.calls || []);
}

// ─── Tenants (operator only) ─────────────────────────────
function renderTenants(rows) {
  const container = $("tenants");
  if (!container) return;
  $("tenantcount").textContent = String(rows.length).padStart(2, "0");
  if (!rows.length) {
    container.innerHTML = `<div class="empty">no registered tenants yet</div>`;
    return;
  }
  const trs = rows.map((t) => {
    const active = !!t.is_active;
    const usage = `${t.calls_today || 0} / ${t.effective_quota || 0}`;
    const inFlight = t.in_flight || 0;
    const toggleLabel = active ? "Deactivate" : "Activate";
    return `
      <tr data-tid="${t.id}">
        <td><span class="dest">${escapeHtml(t.name || "—")}</span>
            <span class="task">telegram_id=${escapeHtml(String(t.telegram_id || "—"))}</span></td>
        <td><span class="ts">${escapeHtml(usage)} today</span><br>
            <span class="ts">${inFlight} in flight</span></td>
        <td>
          <span class="status ${active ? 'answered' : 'failed'}">${active ? 'active' : 'disabled'}</span>
        </td>
        <td>
          <a href="#" onclick="event.preventDefault(); toggleTenant(${t.id}, ${active ? 0 : 1})">${toggleLabel}</a><br>
          <a href="#" onclick="event.preventDefault(); editQuota(${t.id}, ${t.custom_calls_per_day === null ? 'null' : t.custom_calls_per_day})">Quota</a>
        </td>
      </tr>
    `;
  }).join("");
  container.innerHTML = `
    <table>
      <thead><tr><th>Tenant</th><th>Usage</th><th>State</th><th>Actions</th></tr></thead>
      <tbody>${trs}</tbody>
    </table>
  `;
}

async function refreshTenants() {
  if (!IS_OPERATOR) return;
  const data = await getJson("/ui/tenants");
  if (!data) return;
  renderTenants(data.tenants || []);
}

window.toggleTenant = async function (tid, newActive) {
  const r = await postJson(`/ui/tenants/${tid}/update`, { is_active: !!newActive });
  if (r && r.ok) {
    toast(`Tenant ${tid} ${newActive ? "activated" : "deactivated"}.`);
    refreshTenants();
  } else {
    toast(`Update failed: ${r && r.error}`);
  }
};

window.editQuota = async function (tid, current) {
  const ans = prompt(
    `Custom daily quota for tenant ${tid} (blank = use default, 0 = clear override):`,
    current === null ? "" : String(current),
  );
  if (ans === null) return;
  const trimmed = ans.trim();
  const r = await postJson(`/ui/tenants/${tid}/update`, {
    custom_calls_per_day: trimmed === "" ? null : Number(trimmed),
  });
  if (r && r.ok) {
    toast(`Quota updated.`);
    refreshTenants();
  } else {
    toast(`Update failed: ${r && r.error}`);
  }
};

// ─── Audio playback ──────────────────────────────────────
let activeAudio = null;
window.playAudio = function (room) {
  if (activeAudio) { activeAudio.pause(); activeAudio = null; }
  const audio = new Audio(`/ui/audio/${encodeURIComponent(room)}`);
  audio.controls = true;
  audio.play().catch((e) => toast(`Audio: ${e.message}`));
  activeAudio = audio;
};

// ─── Dial form ───────────────────────────────────────────
$("dialform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const btn = f.querySelector("button");
  const notice = $("dialnotice");
  notice.className = "dial-notice active";
  notice.textContent = "Dispatching…";
  btn.disabled = true;

  const payload = {
    to: f.to.value,
    reason: f.reason.value,
    language: f.language.value,
    business_name: f.business_name.value,
  };
  const out = await postJson("/ui/dial", payload);
  if (!out) { btn.disabled = false; return; }
  if (!out.ok) {
    notice.className = "dial-notice active error";
    notice.textContent = out.error || "Dispatch failed";
    btn.disabled = false;
    return;
  }
  const room = out.room;
  notice.textContent = `Ringing… room ${room}`;
  toast(`Calling ${payload.to}`);
  await refreshLive();
  btn.disabled = false;
  f.reason.value = "";

  pollResultThenModal(room);
});

async function pollResultThenModal(room) {
  const deadline = Date.now() + RESULT_DEADLINE_MS;
  while (Date.now() < deadline) {
    const data = await getJson(`/ui/result/${encodeURIComponent(room)}`);
    if (!data) return;
    if (data.status === "complete") {
      openResultModal(room, data.result);
      await refreshHistory();
      await refreshLive();
      return;
    }
    if (data.status === "missing" || data.status === "error") {
      toast(`Result error: ${data.error || data.status}`);
      return;
    }
    await new Promise((r) => setTimeout(r, RESULT_POLL_MS));
  }
  toast("Result poll timed out");
}

// ─── Voice clone (tenant only) ───────────────────────────
async function refreshVoice() {
  const el = $("voicestatus");
  if (!el) return;
  const data = await getJson("/ui/voice");
  if (!data || !data.ok) return;
  if (data.voice_id) {
    el.innerHTML = `<div class="voice-pill on">CLONED</div>
      <div class="voice-uid">${escapeHtml(data.voice_name || "—")}</div>
      <div class="voice-uid faint">${escapeHtml(data.voice_id)}</div>`;
  } else {
    el.innerHTML = `<div class="voice-pill off">DEFAULT</div>
      <div class="voice-uid faint">No clone — uses the language default</div>`;
  }
}

const voiceForm = $("voiceform");
if (voiceForm) {
  voiceForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fileInput = $("voicefile");
    const notice = $("voicenotice");
    const btn = voiceForm.querySelector("button.voice-btn");
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      notice.className = "voice-notice active error";
      notice.textContent = "Pick an audio file first.";
      return;
    }
    notice.className = "voice-notice active";
    notice.textContent = `Uploading ${file.name} (${(file.size/1024).toFixed(0)} KB) and cloning…`;
    btn.disabled = true;

    const fd = new FormData();
    fd.append("audio", file);
    const r = await fetch("/ui/voice", {
      method: "POST",
      credentials: "same-origin",
      body: fd,
    });
    const data = await r.json().catch(() => ({}));
    btn.disabled = false;
    if (!data.ok) {
      notice.className = "voice-notice active error";
      notice.textContent = `Failed: ${data.error || r.statusText}`;
      return;
    }
    notice.className = "voice-notice active";
    notice.textContent = `Cloned. Future calls will use ${data.voice_name}.`;
    fileInput.value = "";
    refreshVoice();
  });
}

window.clearVoice = async function () {
  if (!confirm("Clear your custom voice and revert to the language default?")) return;
  const r = await postJson("/ui/voice/clear", {});
  if (!r) return;
  if (r.ok) {
    toast("Voice cleared.");
    refreshVoice();
  } else {
    toast(`Clear failed: ${r.error || ""}`);
  }
};

// ─── Boot ────────────────────────────────────────────────
refreshLive();
refreshHistory();
refreshTenants();
refreshVoice();
setInterval(refreshLive, LIVE_POLL_MS);
setInterval(refreshHistory, HISTORY_POLL_MS);
if (IS_OPERATOR) setInterval(refreshTenants, TENANTS_POLL_MS);
