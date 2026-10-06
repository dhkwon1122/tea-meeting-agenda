// doc2report 웹 화면 — 외부 라이브러리 없이 (사내망에서 CDN이 막혀 있어도 동작하도록).
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const STORE_KEY = "doc2report.options.v1";
const API_VERSION = 7;  // 서버(web/server.py)의 API_VERSION과 같아야 한다
const RESTART_HELP = "서버 창(검은 창)을 모두 닫고 start_webapp.bat을 다시 실행한 뒤, 이 화면에서 Ctrl+F5로 새로 고침하세요.";

// 화면 위에 계속 떠 있는 안내(몇 초 뒤 사라지는 알림으로는 원인을 읽기 어렵다).
function banner(title, detail, warn) {
  const el = $("#banner");
  el.className = "banner" + (warn ? " warn" : "");
  el.innerHTML = `<b>${esc(title)}</b>${esc(detail || "")}`;
}

const state = {
  inputs: [],      // {type, url|upload_id|text, name, title, detail, error}
  profiles: {},    // name -> profile_info
  schema: null,
  status: null,
  polling: null,
};

// ── 서버 호출 ────────────────────────────────────────────────────────────

async function api(path, body, extraHeaders) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: Object.assign({ "X-Doc2Report": "1" },
      body instanceof Blob ? {} : { "Content-Type": "application/json" }, extraHeaders || {}),
    body: body instanceof Blob ? body : JSON.stringify(body),
  };
  const res = await fetch(path, init);
  let data;
  try { data = await res.json(); } catch (e) { data = { ok: false, error: `응답 오류 (${res.status})` }; }
  if (data.locked && res.status === 401) throw new Locked();
  if (!res.ok || data.ok === false) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(message, bad) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), bad ? 6000 : 2500);
}

// ── 상태 표시 ────────────────────────────────────────────────────────────

async function loadStatus() {
  const st = await api("/api/status");
  state.status = st;
  if (st.locked) throw new Locked();
  if (st.version !== API_VERSION) {
    // 예전 서버가 새 화면 파일을 내보내는 중 — 이대로면 서식·옵션이 비어 보인다(2026-09-29 사용자 PC)
    throw new StaleServer("서버 프로그램이 이 화면보다 예전 버전입니다.");
  }
  if (st.stale) {
    banner("서버를 켠 뒤 프로그램이 업데이트되었습니다(git pull).", " 새 기능을 쓰려면 " + RESTART_HELP, true);
  }
  if (st.profile_errors && st.profile_errors.length) {
    banner("읽지 못한 서식 파일이 있습니다 — 그 서식만 빼고 보여 줍니다.", " " + st.profile_errors.join(" / "), true);
  }
  renderAccount(st);
  const pdf = $("#st-pdf");
  pdf.textContent = st.pdf.available ? `PDF: ${st.pdf.engine}` : "PDF 변환기 없음";
  pdf.className = "chip " + (st.pdf.available ? "ok" : "bad");
  pdf.title = st.pdf.available ? "" : "MS Word 또는 LibreOffice가 있어야 PDF를 만들 수 있습니다";
  $("#out-pdf").disabled = !st.pdf.available;
  $("#out-dir").textContent = st.output_dir;
  if (!st.llm.configured) {
    $("#use-llm").closest("label").classList.add("disabled");
  }
}

// ── 사용자 (2026-09-30: 팀 공유 — 누구의 토큰·어떤 API를 쓰는지 늘 보이게) ─────────────

function hostOf(url) {
  try { return new URL(url).host; } catch (e) { return url || ""; }
}

function renderAccount(st) {
  const acc = st.account, cfInfo = acc.confluence, llmInfo = acc.llm;
  const user = $("#st-user");
  user.textContent = acc.registered ? `사용자: ${acc.name}` : "사용자 미등록 — 등록하기";
  user.className = "chip user " + (acc.registered ? "ok" : "warn");

  const cf = $("#st-confluence");
  if (st.confluence.configured) {
    const mismatch = acc.registered && cfInfo.verified_as && !cfInfo.verified_as.includes(acc.name);
    cf.textContent = cfInfo.verified_as ? `Confluence: ${cfInfo.verified_as}의 토큰 ${cfInfo.secret}`
      : `Confluence 토큰 ${cfInfo.secret} (주인 확인 안 됨)`;
    cf.className = "chip " + (!cfInfo.verified_as || mismatch ? "warn" : "ok");
    cf.title = `${cfInfo.source} · ${hostOf(cfInfo.url)} · ${st.confluence.auth} · 전송 ${st.confluence.transport}` +
      (cfInfo.verified_at ? ` · ${cfInfo.verified_at} 확인` : "") +
      (mismatch ? " · 등록한 이름과 토큰 주인이 다릅니다!" : "");
  } else {
    cf.textContent = "Confluence 토큰 없음";
    cf.className = "chip warn";
    cf.title = "사용자 등록에서 Confluence 주소와 개인 토큰을 넣으세요";
  }

  const llm = $("#st-llm");
  llm.textContent = st.llm.configured ? `LLM: ${st.llm.model} @ ${hostOf(st.llm.endpoint) || st.llm.backend}` : "LLM 미설정";
  llm.className = "chip " + (st.llm.configured ? "ok" : "warn");
  llm.title = st.llm.configured ? `${llmInfo.source || st.llm.backend} · ${st.llm.endpoint}` +
    (llmInfo.secret ? ` · API 키 ${llmInfo.secret}` : "") : "사용자 등록에서 온프렘 LLM 주소·모델명을 넣으세요";
}

function openAccount() {
  const acc = state.status.account, d = acc.defaults;
  $("#acc-protection").textContent = acc.protection;
  $("#acc-name").value = acc.name || "";
  $("#acc-cf-url").value = d.confluence_url || "";
  $("#acc-cf-user").value = d.confluence_username || "";
  $("#acc-llm-url").value = d.llm_base_url || "";
  $("#acc-llm-model").value = d.llm_model || "";
  $("#acc-cf-token").value = $("#acc-llm-key").value = "";
  const hasToken = acc.registered && acc.confluence.secret;
  $("#acc-cf-token").placeholder = hasToken ? `저장됨 ${acc.confluence.secret} — 바꿀 때만 입력`
    : "Confluence 프로필 → 개인 액세스 토큰에서 발급";
  $("#acc-llm-key").placeholder = acc.llm.secret ? `저장됨 ${acc.llm.secret} — 바꿀 때만 입력` : "";
  $("#acc-llm-clear-row").classList.toggle("hidden", !acc.llm.secret);
  $("#acc-llm-clear").checked = false;
  $("#acc-cf-state").textContent = acc.confluence.verified_as
    ? `토큰 주인(Confluence 확인): ${acc.confluence.verified_as} · ${acc.confluence.verified_at}` : "";
  $("#acc-verify").classList.toggle("hidden", !hasToken);
  $("#acc-delete").classList.toggle("hidden", !acc.registered);
  $("#acc-error").textContent = "";
  $("#account-dlg").showModal();
}

async function accountAction(path, body, button) {
  button.disabled = true;
  $("#acc-error").textContent = "";
  try {
    const r = await api(path, body);
    await loadStatus();
    if (r.verify_error) {
      openAccount();
      $("#acc-error").textContent = r.verify_error + "\n(저장은 되었습니다 — 주소·토큰을 고친 뒤 다시 저장하세요)";
      return;
    }
    $("#account-dlg").close();
    toast(path.endsWith("delete") ? "등록을 지웠습니다" : `저장했습니다 — ${r.account.confluence.verified_as || r.account.name}`);
  } catch (e) {
    $("#acc-error").textContent = e.message;
  } finally {
    button.disabled = false;
  }
}

$("#st-user").addEventListener("click", () => state.status && !state.status.locked && openAccount());
$("#acc-close").addEventListener("click", () => $("#account-dlg").close());
$("#acc-save").addEventListener("click", (ev) => accountAction("/api/account", {
  name: $("#acc-name").value, confluence_url: $("#acc-cf-url").value,
  confluence_username: $("#acc-cf-user").value, confluence_token: $("#acc-cf-token").value,
  llm_base_url: $("#acc-llm-url").value, llm_model: $("#acc-llm-model").value,
  llm_api_key: $("#acc-llm-key").value, clear_llm_key: $("#acc-llm-clear").checked,
}, ev.target));
$("#acc-verify").addEventListener("click", (ev) => accountAction("/api/account/verify", {}, ev.target));
$("#acc-delete").addEventListener("click", (ev) => {
  if (confirm("이 PC에 저장된 내 토큰·API 설정을 지울까요?")) accountAction("/api/account/delete", {}, ev.target);
});

// ── 프로파일·옵션 ────────────────────────────────────────────────────────

async function loadProfiles() {
  const data = await api("/api/profiles");
  state.schema = data.schema;
  for (const p of data.profiles) state.profiles[p.name] = p;
  const presets = data.schema.presets.filter((n) => state.profiles[n]);
  const label = (n) => state.profiles[n].label;

  $("#preset-radios").innerHTML = presets.concat(["custom"]).map((n) =>
    `<label><input type="radio" name="preset" value="${esc(n)}"><span>${
      esc(n === "custom" ? "사용자 설정" : label(n))}</span></label>`).join("");
  const opts = presets.map((n) => `<option value="${esc(n)}">${esc(label(n))}</option>`).join("");
  $("#rules-base").innerHTML = opts;
  $("#cu-base").innerHTML = opts;

  renderChecks("#polish-rules", data.schema.polish);
  renderChecks("#marker-rules", data.schema.markers);
  $("#tbl-align").innerHTML = data.schema.table_align.map((o) =>
    `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  const auto = data.schema.auto;
  $("#auto-heavy").textContent = `${auto.chars.toLocaleString()}자·표 ${auto.tables}개·입력 2개 이상`;
}

function renderChecks(target, rows) {
  $(target).innerHTML = rows.map((r) =>
    `<label class="check"><input type="checkbox" data-rule="${esc(r.key)}"> ${esc(r.label)}` +
    (r.help ? `<span class="hint">${esc(r.help)}</span>` : "") + `</label>`).join("");
}

// 직접 선택 모드의 체크박스를 한 서식의 기본 규칙으로 채운다.
function applyRules(name) {
  const p = state.profiles[name];
  if (!p) return;
  $("#rules-base").value = name;
  for (const box of $$("[data-rule]")) box.checked = !!p.text[box.dataset.rule];
  setRadio("polish", p.polish);
  $("#tbl-landscape").checked = !!p.tables.allow_landscape;
  $("#tbl-align").value = p.tables.align;
}

const COMBO_KEYS = ["font", "title_size", "size", "line_spacing", "body_scale", "table_size", "table_scale"];
const MARGIN_KEYS = ["margin_top", "margin_bottom", "margin_left", "margin_right"];
const CUSTOM_KEYS = COMBO_KEYS.concat(MARGIN_KEYS);
const FREE = "__free__";
const FREE_HINT = { font: "예: 나눔고딕", title_size: "예: 18pt", size: "예: 13pt",
  line_spacing: "예: 1.3", table_size: "예: 10pt", body_scale: "예: 95%", table_scale: "예: 85%" };

// 선택 목록 + 맨 끝 "직접 입력…"(고르면 옆에 입력칸이 나온다). datalist는 값이 들어 있으면
// 그 값으로 걸러져 목록이 안 펼쳐졌다(2026-09-29 사용자) — 그래서 select로 바꿨다.
function renderCombo(key, values, current) {
  const list = Array.from(new Set([current, ...(values || [])].filter(Boolean)));
  $(`.combo[data-key="${key}"]`).innerHTML =
    `<select id="cu-${key}">${list.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join("")}` +
    `<option value="${FREE}">직접 입력…</option></select>` +
    `<input id="cu-${key}-free" class="hidden free" placeholder="${esc(FREE_HINT[key] || "")}">`;
  $(`#cu-${key}`).value = current || list[0] || FREE;
  syncCombo(key);
}

function syncCombo(key) {
  const free = $(`#cu-${key}`).value === FREE;
  $(`#cu-${key}-free`).classList.toggle("hidden", !free);
}

function getCustom(key) {
  if (MARGIN_KEYS.includes(key)) return $(`#cu-${key}`).value.trim();
  const sel = $(`#cu-${key}`);
  return sel.value === FREE ? $(`#cu-${key}-free`).value.trim() : sel.value;
}

function setCustom(key, value) {
  if (!value) return;
  if (MARGIN_KEYS.includes(key)) { $(`#cu-${key}`).value = value; return; }
  const sel = $(`#cu-${key}`);
  if (![...sel.options].some((o) => o.value === value)) {
    sel.insertBefore(new Option(value, value), sel.querySelector(`option[value="${FREE}"]`));
  }
  sel.value = value;
  syncCombo(key);
}

// 사용자 설정 칸을 출발 서식의 값으로 채운다(자유롭게 고쳐 쓰는 출발점).
function fillCustom(base) {
  const p = state.profiles[base];
  if (!p) return;
  $("#cu-base").value = base;
  for (const k of COMBO_KEYS) renderCombo(k, (p.choices || {})[k], p.format[k]);
  for (const k of MARGIN_KEYS) $(`#cu-${k}`).value = p.format[k] || "";
}

$("#custom-panel").addEventListener("change", (ev) => {
  const key = (ev.target.id || "").replace(/^cu-/, "");
  if (COMBO_KEYS.includes(key)) {
    syncCombo(key);
    if (ev.target.value === FREE) $(`#cu-${key}-free`).focus();
  }
});

function syncPreset() {
  const preset = radio("preset");
  const custom = preset === "custom";
  $("#custom-panel").classList.toggle("hidden", !custom);
  const p = state.profiles[custom ? $("#cu-base").value : preset];
  $("#preset-summary").textContent = custom ? "출발 서식에서 바꾸고 싶은 값만 고치세요 (단위: pt, 배, 장평 %, 여백 cm)"
    : (p ? `${p.summary}` : "");
  const hasConfluence = state.inputs.some((i) => i.type === "confluence");
  const suggest = hasConfluence && preset === "default" && state.profiles.confluence;
  const hint = $("#preset-hint");
  hint.classList.toggle("hidden", !suggest);
  if (suggest) {
    hint.innerHTML = `Confluence 입력이 있습니다 — '${esc(state.profiles.confluence.label)}' 서식이 맞을 수 있습니다
      <button class="btn tiny" id="use-confluence">바꾸기</button>`;
  }
}

document.addEventListener("click", (ev) => {
  if (ev.target.id === "use-confluence") { setRadio("preset", "confluence"); syncPreset(); }
});

function setRadio(name, value) {
  const el = $(`input[name="${name}"][value="${value}"]`);
  if (el) el.checked = true;
}
const radio = (name) => ($(`input[name="${name}"]:checked`) || {}).value;

function collectOptions() {
  const mode = radio("mode");
  const dateMode = radio("date");
  const text = {};
  for (const box of $$("[data-rule]")) text[box.dataset.rule] = box.checked;
  const formats = ["docx"];
  if ($("#out-pdf").checked && !$("#out-pdf").disabled) formats.push("pdf");
  if ($("#out-md").checked) formats.push("md");
  const custom = { base: $("#cu-base").value };
  for (const k of CUSTOM_KEYS) custom[k] = getCustom(k);
  return {
    mode,
    llm: $("#use-llm").checked,
    allow_llm: $("#use-llm").checked,
    preset: radio("preset") || "default",
    custom,
    rules_base: $("#rules-base").value,
    polish: radio("polish"),
    text,
    tables: { allow_landscape: $("#tbl-landscape").checked, align: $("#tbl-align").value },
    merge: radio("merge") || "continuous",
    linked: $("#cf-linked").checked,
    title: $("#doc-title").value.trim(),
    date: dateMode === "pick" ? $("#date-pick").value : dateMode,
    date_mode: dateMode,
    formats,
    report: $("#out-report").checked,
  };
}

function saveOptions() {
  try {
    const o = collectOptions();
    delete o.title;
    localStorage.setItem(STORE_KEY, JSON.stringify(o));
  } catch (e) { /* 저장 못 해도 동작에는 지장 없음 */ }
}

function restoreOptions() {
  let o = null;
  try { o = JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { o = null; }
  const presets = state.schema.presets;
  const first = presets.includes("default") ? "default" : presets[0];
  const known = (n) => n && state.profiles[n] && presets.includes(n);
  setRadio("preset", o && (o.preset === "custom" || known(o.preset)) ? o.preset : first);
  fillCustom(o && o.custom && known(o.custom.base) ? o.custom.base : first);
  applyRules(o && known(o.rules_base) ? o.rules_base : first);
  if (!o) { syncPreset(); return syncMode(); }
  if (o.custom) for (const k of CUSTOM_KEYS) setCustom(k, o.custom[k]);
  setRadio("mode", o.mode || "auto");
  $("#use-llm").checked = !!(o.llm ?? o.allow_llm);
  if (o.polish) setRadio("polish", o.polish === "llm" ? "rules" : o.polish);  // 예전 "규칙 + LLM"
  for (const box of $$("[data-rule]")) if (o.text && box.dataset.rule in o.text) box.checked = o.text[box.dataset.rule];
  if (o.tables) { $("#tbl-landscape").checked = !!o.tables.allow_landscape; if (o.tables.align) $("#tbl-align").value = o.tables.align; }
  setRadio("merge", o.merge || (o.page_breaks ? "pages" : "continuous"));
  $("#cf-linked").checked = o.linked !== false;
  setRadio("date", o.date_mode ?? "");
  if (o.date_mode === "pick" && o.date) $("#date-pick").value = o.date;
  $("#out-pdf").checked = (o.formats || []).includes("pdf");
  $("#out-md").checked = (o.formats || []).includes("md");
  $("#out-report").checked = o.report !== false;
  syncPreset();
  syncMode();
}

function syncMode() {
  const manual = radio("mode") === "manual";
  $("#auto-panel").classList.toggle("hidden", manual);
  $("#manual-panel").classList.toggle("hidden", !manual);
  // 문장 다듬기 "안 함"이면 세부 규칙은 아예 보이지 않게(2026-09-29 사용자)
  $("#polish-rules").classList.toggle("hidden", radio("polish") === "none");
  $("#merge-box").classList.toggle("hidden", state.inputs.length < 2);
  $("#date-pick").classList.toggle("hidden", radio("date") !== "pick");
}

// ── 입력 목록 ────────────────────────────────────────────────────────────

const KIND_LABEL = { confluence: "Confluence", docx: "Word", text: "글" };

function renderInputs() {
  if (state.schema) { syncPreset(); syncMode(); }
  const list = $("#input-list");
  $("#input-empty").classList.toggle("hidden", state.inputs.length > 0);
  list.innerHTML = state.inputs.map((item, i) => {
    const title = item.title || item.name;
    const detail = item.error ? `<small class="bad">${esc(item.error)}</small>`
      : `<small>${esc(item.detail || "")}</small>`;
    return `<li>
      <span class="kind ${item.type}">${KIND_LABEL[item.type]}</span>
      <span class="name"><b title="${esc(title)}">${esc(title)}</b>${detail}</span>
      <span class="ops">
        <button class="btn tiny" data-op="up" data-i="${i}" ${i === 0 ? "disabled" : ""} title="위로">↑</button>
        <button class="btn tiny" data-op="down" data-i="${i}" ${i === state.inputs.length - 1 ? "disabled" : ""} title="아래로">↓</button>
        <button class="btn tiny" data-op="del" data-i="${i}" title="빼기">✕</button>
      </span></li>`;
  }).join("");
}

$("#input-list").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-op]");
  if (!btn) return;
  const i = Number(btn.dataset.i);
  const items = state.inputs;
  if (btn.dataset.op === "del") items.splice(i, 1);
  if (btn.dataset.op === "up" && i > 0) [items[i - 1], items[i]] = [items[i], items[i - 1]];
  if (btn.dataset.op === "down" && i < items.length - 1) [items[i + 1], items[i]] = [items[i], items[i + 1]];
  renderInputs();
});

$("#cf-add").addEventListener("click", async () => {
  const urls = $("#cf-urls").value.split(/\s+/).map((u) => u.trim()).filter(Boolean);
  if (!urls.length) return toast("Confluence 페이지 주소를 넣어 주세요", true);
  $("#cf-urls").value = "";
  const added = urls.map((url) => ({ type: "confluence", url, name: url, detail: "제목 확인 중…" }));
  state.inputs.push(...added);
  renderInputs();
  await Promise.all(added.map(async (item) => {
    if (!state.status || !state.status.confluence.configured) {
      item.detail = url_short(item.url) + " · Confluence 토큰이 없어 제목 확인 못 함 — 위의 사용자 등록에서 넣으세요";
      return;
    }
    try {
      const r = await api("/api/confluence/check", { url: item.url });
      item.title = r.title;
      item.detail = `페이지 ${r.page_id}`;
    } catch (e) {
      item.error = "확인 실패: " + e.message;
    }
    renderInputs();
  }));
  renderInputs();
});

function url_short(url) { return url.length > 60 ? url.slice(0, 57) + "…" : url; }

async function addFiles(files) {
  for (const file of files) {
    if (!/\.docx$/i.test(file.name)) { toast(`${file.name}: Word(.docx) 파일만 올릴 수 있습니다`, true); continue; }
    const item = { type: "docx", name: file.name, title: file.name.replace(/\.docx$/i, ""), detail: "올리는 중…" };
    state.inputs.push(item);
    renderInputs();
    try {
      const r = await api("/api/upload", file, { "X-Filename": encodeURIComponent(file.name) });
      item.upload_id = r.upload_id;
      item.detail = `${file.name} · ${(r.size / 1024).toFixed(0)}KB`;
    } catch (e) {
      item.error = e.message;
    }
    renderInputs();
  }
}

$("#file-input").addEventListener("change", (ev) => { addFiles(ev.target.files); ev.target.value = ""; });
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (ev) => { ev.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (ev) => { ev.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (ev) => addFiles(ev.dataTransfer.files));

$("#tx-add").addEventListener("click", () => {
  const text = $("#tx-body").value;
  if (!text.trim()) return toast("붙여 넣을 글이 없습니다", true);
  const title = $("#tx-title").value.trim();
  const first = text.trim().split("\n")[0].slice(0, 40);
  const lines = text.trim().split("\n").length;
  state.inputs.push({ type: "text", text, title, name: title || first, detail: `${lines}줄 · ${text.length.toLocaleString()}자` });
  $("#tx-body").value = ""; $("#tx-title").value = "";
  renderInputs();
});

$$(".tab").forEach((tab) => tab.addEventListener("click", () => {
  $$(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  $$(".panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== tab.dataset.tab));
}));

// ── 변환 ─────────────────────────────────────────────────────────────────

$("#convert").addEventListener("click", async () => {
  const bad = state.inputs.find((i) => i.type === "docx" && !i.upload_id);
  if (!state.inputs.length) return toast("입력을 하나 이상 추가해 주세요", true);
  if (bad) return toast(`${bad.name}: 파일이 아직 올라가지 않았거나 실패했습니다`, true);
  const options = collectOptions();
  if (options.date_mode === "pick" && !options.date) return toast("날짜를 골라 주세요", true);
  saveOptions();
  const inputs = state.inputs.map((i) => ({ type: i.type, url: i.url, upload_id: i.upload_id, name: i.name, text: i.text, title: i.title }));

  $("#convert").disabled = true;
  $("#result").classList.add("hidden");
  $("#progress").classList.remove("hidden");
  $("#progress-log").innerHTML = "<li>시작</li>";
  try {
    const { job } = await api("/api/convert", { inputs, options });
    poll(job);
  } catch (e) {
    finish({ state: "error", error: e.message });
  }
});

function poll(id) {
  clearTimeout(state.polling);
  state.polling = setTimeout(async () => {
    let job;
    try { job = await api(`/api/jobs/${id}`); } catch (e) { return finish({ state: "error", error: e.message }); }
    $("#progress-log").innerHTML = job.messages.slice(-6).map((m) => `<li>${esc(m)}</li>`).join("");
    if (job.state === "running") poll(id); else finish(job);
  }, 600);
}

function finish(job) {
  $("#convert").disabled = false;
  $("#progress").classList.add("hidden");
  const box = $("#result");
  box.classList.remove("hidden", "error");
  if (job.state !== "done") {
    box.classList.add("error");
    box.innerHTML = `<h3>변환 실패</h3><p>${esc(job.error)}</p>`;
    return;
  }
  const r = job.result;
  const files = r.files.map(fileRow).join("");
  const decision = r.decision ? `<div class="decision"><b>자동 판단 결과</b><ul>${
    r.decision.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : "";
  const notes = r.notes.filter((n) => !n.startsWith("자동 판단:"));
  const polishName = ({ none: "안 함", rules: "파이썬 규칙" }[r.polish] || r.polish) + (r.llm ? " + LLM" : "");
  box.innerHTML = `
    <h3>완료 — ${esc(r.title || r.stem)} <span class="hint">(${job.elapsed}초)</span></h3>
    <p class="hint">서식 ${esc(r.profile)} · 문장 다듬기 ${esc(polishName)} · 표 ${r.tables}개 · 문구 수정 ${r.change_count}건</p>
    <div class="files">${files}</div>
    ${decision}
    ${notes.length ? `<details open><summary>레이아웃·입력 메모 (${notes.length})</summary><ul class="notes">${
      notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></details>` : ""}
    ${r.changes.length ? `<details><summary>문구 수정 내역 (${r.change_count}건)</summary>
      <table class="changes"><tr><th>원문</th><th>수정</th><th>규칙</th></tr>${
      r.changes.map((c) => `<tr><td>${esc(c.before)}</td><td>${esc(c.after)}</td><td class="rule">${esc(c.rule)}</td></tr>`).join("")}</table></details>` : ""}`;
  loadHistory();
}

const KIND_NAME = { docx: "Word", pdf: "PDF", md: "MD", report: "변경내역" };

function fileRow(f) {
  const href = `/files/${encodeURIComponent(f.name)}`;
  const view = f.kind === "pdf" || f.kind === "md" || f.kind === "report"
    ? `<a class="btn tiny" href="${href}" target="_blank" rel="noopener">브라우저로 보기</a>` : "";
  return `<div class="file"><span class="badge ${f.kind}">${KIND_NAME[f.kind] || f.kind}</span>
    <span class="fname">${esc(f.name)}</span>
    <button class="btn tiny" data-open="${esc(f.name)}">열기</button>${view}
    <a class="btn tiny" href="${href}?download=1" download>내려받기</a>
    <button class="btn tiny" data-folder="${esc(f.name)}" title="탐색기에서 이 파일 위치 열기">폴더</button></div>`;
}

document.addEventListener("click", async (ev) => {
  const open = ev.target.closest("[data-open]");
  const folder = ev.target.closest("[data-folder]");
  if (!open && !folder) return;
  try {
    await api("/api/open", open ? { name: open.dataset.open } : { name: folder.dataset.folder, folder: true });
    toast(open ? "여는 중… (Word·PDF 뷰어 창을 확인하세요)" : "폴더를 여는 중…");
  } catch (e) {
    toast(e.message, true);
  }
});

$("#open-folder").addEventListener("click", async () => {
  try { await api("/api/open", { folder: true }); toast("폴더를 여는 중…"); } catch (e) { toast(e.message, true); }
});

// ── 최근 결과 ────────────────────────────────────────────────────────────

async function loadHistory() {
  const { items } = await api("/api/history");
  $("#history-empty").classList.toggle("hidden", items.length > 0);
  $("#history").innerHTML = items.map((g) => `<li>
    <div class="hname">${esc(g.stem)}</div><div class="htime">${esc(g.time)}</div>
    <div class="hfiles">${g.files.map((f) =>
      `<button class="btn tiny" data-open="${esc(f.name)}" title="${esc(f.name)}">${KIND_NAME[f.kind] || f.kind} 열기</button>`).join("")}
    </div></li>`).join("");
}
$("#history-refresh").addEventListener("click", loadHistory);

// ── LLM 시험 ─────────────────────────────────────────────────────────────

$("#llm-test").addEventListener("click", async () => {
  const out = $("#llm-test-out");
  out.textContent = "시험 중…";
  try {
    const r = await api("/api/llm/test", {});
    out.textContent = `성공: "${r.before}" → "${r.after}"`;
  } catch (e) {
    out.textContent = "실패: " + e.message;
  }
});

// ── 시작 ─────────────────────────────────────────────────────────────────

$("#rules-base").addEventListener("change", (ev) => { applyRules(ev.target.value); syncMode(); });
$("#cu-base").addEventListener("change", (ev) => { fillCustom(ev.target.value); syncPreset(); });
document.addEventListener("change", (ev) => {
  if (ev.target.name === "mode" || ev.target.name === "polish" || ev.target.name === "date") syncMode();
  if (ev.target.name === "preset") syncPreset();
});

class StaleServer extends Error {}
class Locked extends Error {}

(async function init() {
  renderInputs();
  try {
    await loadStatus();
    await loadProfiles();
  } catch (e) {
    if (e instanceof Locked) {
      document.body.classList.add("locked");
      $("#st-user").textContent = "잠김";
      banner("이 화면은 잠겨 있습니다.", " 서버 창(검은 창)에 표시된 주소(끝에 ?k=… 가 붙은 주소)로 여세요 — " +
        "start_webapp.bat을 실행하면 자동으로 열립니다. 다른 사람이 내 토큰으로 변환하지 못하게 막는 장치입니다.", true);
      return;
    }
    if (e instanceof StaleServer) banner(e.message, " " + RESTART_HELP);
    else if (e instanceof TypeError && /fetch/i.test(e.message)) banner("서버에 연결할 수 없습니다.", " 서버 창이 떠 있는지 확인하고, 없으면 start_webapp.bat을 실행하세요.");
    else banner("화면 정보를 불러오지 못했습니다: " + e.message, " " + RESTART_HELP);
    return;
  }
  try {
    restoreOptions();
  } catch (e) {
    // 예전 버전에서 저장한 옵션이 맞지 않으면 버리고 기본값으로
    try { localStorage.removeItem(STORE_KEY); } catch (_) { /* 무시 */ }
    restoreOptions();
  }
  loadHistory().catch(() => {});
  if (!state.status.account.registered) openAccount();  // 처음 쓰는 사람: 사용자 등록부터
})();
