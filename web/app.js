// Viewing page. Login with Keycloak (authorization code + PKCE, keycloak-js), then the API through the same origin.
// The access token stays in memory only. Video tokens come from the API and are used only for MediaMTX.
import Keycloak from "/vendor/keycloak.js";

const cfg = window.CCC_CONFIG;
const $ = (id) => document.getElementById(id);
const STATE_TEXT = { recording: "録画中", standby: "待機", disconnected: "切断" };
const KIND_TEXT = { motion: "動き", object: "物" };

const kc = new Keycloak({ url: cfg.keycloakUrl, realm: cfg.realm, clientId: cfg.clientId });
const home = `${location.origin}/`;

let me = null; // { user_id, entries: [{role, tenant_id, tenant_name}] }
let tenantId = null;
let listTimer = null;
let eventsTimer = null;
let hls = null;
let liveTimer = null;
let blobUrl = null;

// ---------------------------------------------------------------- API

class ApiError extends Error {
  constructor(status, detail) {
    super(`${status} ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

async function api(path, options = {}) {
  await kc.updateToken(30).catch(() => kc.login({ redirectUri: home }));
  const res = await fetch(cfg.apiBase + path, {
    ...options,
    headers: { Authorization: `Bearer ${kc.token}`, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (res.status === 401) {
    await kc.login({ redirectUri: home });
    throw new ApiError(401, "unauthorized");
  }
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch { /* no body */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? null : res.json();
}

// ---------------------------------------------------------------- screens

function show(which) {
  for (const id of ["list-section", "viewer-section", "notfound"]) $(id).hidden = id !== which;
  $("status").hidden = true;
}

function setStatus(text) {
  $("status").textContent = text;
  $("status").hidden = false;
}

function route() {
  const m = location.hash.match(/^#\/t\/([^/]+)\/c\/([^/]+)$/);
  return m ? { tenant: decodeURIComponent(m[1]), camera: decodeURIComponent(m[2]) } : null;
}

function stopVideo() {
  clearInterval(liveTimer);
  liveTimer = null;
  if (hls) { hls.destroy(); hls = null; }
  const v = $("video");
  v.pause();
  v.removeAttribute("src");
  v.load();
  if (blobUrl) { URL.revokeObjectURL(blobUrl); blobUrl = null; }
}

async function renderList() {
  if (!tenantId) { return; }
  const cams = await api(`/tenants/${tenantId}/cameras`);
  const body = document.querySelector("#cameras tbody");
  body.replaceChildren();
  for (const c of cams) {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = c.name;
    const state = document.createElement("td");
    state.textContent = STATE_TEXT[c.state] || c.state;
    state.className = `state ${c.state}`;
    const fps = document.createElement("td");
    fps.textContent = c.fps == null ? "—" : c.fps.toFixed(1);
    const br = document.createElement("td");
    br.textContent = c.bitrate_kbps == null ? "—" : `${Math.round(c.bitrate_kbps)} kbit/s`;
    const act = document.createElement("td");
    const a = document.createElement("a");
    a.href = `#/t/${encodeURIComponent(tenantId)}/c/${encodeURIComponent(c.id)}`;
    a.textContent = "見る";
    act.append(a);
    tr.append(name, state, fps, br, act);
    body.append(tr);
  }
  // Only a tenant administrator may add a camera (the server checks it again): the link opens Keycloak's page where the
  // code that the new camera shows is entered and approved.
  const isAdmin = me.entries.some((e) => e.tenant_id === tenantId && e.role === "tenant_admin");
  $("add-camera").hidden = !isAdmin;
  $("add-camera-link").href = `${cfg.keycloakUrl}/realms/${encodeURIComponent(cfg.realm)}/device`;
  if (cams.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.textContent = "このテナントにはカメラがありません。";
    tr.append(td);
    body.append(tr);
  }
}

async function showList() {
  stopVideo();
  clearInterval(listTimer);
  clearInterval(eventsTimer);
  show("list-section");
  await renderList().catch(handleError);
  listTimer = setInterval(() => renderList().catch(() => {}), 5000);
}

async function showCamera(tid, cid) {
  clearInterval(listTimer);
  clearInterval(eventsTimer);
  stopVideo();
  let cam;
  try {
    cam = await api(`/tenants/${encodeURIComponent(tid)}/cameras/${encodeURIComponent(cid)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) { show("notfound"); return; }
    throw e;
  }
  show("viewer-section");
  $("viewer-title").textContent = `${cam.name}（${STATE_TEXT[cam.state] || cam.state}）`;
  $("viewer-msg").textContent = "";
  $("btn-live").onclick = () => playLive(tid, cid);
  $("btn-past").onclick = () => playPast(tid, cid);
  eventsPages = 1;
  await renderEvents(tid, cid).catch(() => {});
  eventsTimer = setInterval(() => renderEvents(tid, cid).catch(() => {}), 10000);
  if (cam.state !== "disconnected") await playLive(tid, cid);
  else $("viewer-msg").textContent = "カメラは切断中です。「過去 5 分を再生」は録画があれば使えます。";
}

// ---------------------------------------------------------------- events (movement and named things in the recordings)

function eventTime(iso) {
  return new Date(iso).toLocaleString("ja-JP", { hour12: false });
}

// One answer holds 100 events (the server's default); "続きを読む" asks for the next 100 with `offset`, as Frigate's list of
// events does (docs/adr/0004). The pages shown so far are read again every 10 seconds, so the list stays current.
const EVENTS_PAGE = 100;
let eventsPages = 1;

async function readEvents(tid, cid) {
  const base = `/tenants/${encodeURIComponent(tid)}/cameras/${encodeURIComponent(cid)}/events`;
  const events = [];
  const seen = new Set();
  let full = false;
  for (let page = 0; page < eventsPages; page++) {
    const rows = await api(`${base}?limit=${EVENTS_PAGE}&offset=${page * EVENTS_PAGE}`);
    for (const e of rows) {
      if (!seen.has(e.id)) { seen.add(e.id); events.push(e); }      // an event that arrived meanwhile can move one down a page
    }
    full = rows.length === EVENTS_PAGE;
    if (!full) break;
  }
  return { events, more: full };
}

async function renderEvents(tid, cid) {
  const { events, more } = await readEvents(tid, cid);
  $("events-more").hidden = !more;
  $("events-more").onclick = () => { eventsPages += 1; renderEvents(tid, cid).catch(() => {}); };
  const body = document.querySelector("#events tbody");
  body.replaceChildren();
  for (const e of events) {
    const tr = document.createElement("tr");
    const time = document.createElement("td");
    time.textContent = eventTime(e.started_at);
    const kind = document.createElement("td");
    kind.textContent = KIND_TEXT[e.kind] || e.kind;
    const label = document.createElement("td");
    label.textContent = e.label || "—";
    const score = document.createElement("td");
    score.textContent = e.score == null ? "—" : e.score.toFixed(3);
    const act = document.createElement("td");
    const b = document.createElement("button");
    b.type = "button";
    if (e.playable) {
      b.textContent = "この時刻から再生";
      b.onclick = () => playEvent(tid, cid, e.id);
    } else {
      b.textContent = "録画は消えました";
      b.disabled = true;
    }
    act.append(b);
    tr.append(time, kind, label, score, act);
    body.append(tr);
  }
  $("events-empty").hidden = events.length > 0;
}

// ---------------------------------------------------------------- video

async function playLive(tid, cid) {
  stopVideo();
  const msg = $("viewer-msg");
  msg.textContent = "ライブを準備しています…";
  let live;
  try {
    live = await api(`/tenants/${encodeURIComponent(tid)}/cameras/${encodeURIComponent(cid)}/live`, { method: "POST" });
  } catch (e) {
    msg.textContent = e.status === 404 ? "見つかりません" : "ライブを開始できませんでした。";
    return;
  }
  if (!window.Hls || !window.Hls.isSupported()) {
    msg.textContent = "このブラウザは Authorization ヘッダー付きの HLS 再生に対応していません。";
    return;
  }
  let token = live.token;
  hls = new window.Hls({
    // every playlist and segment request carries the short video token
    xhrSetup: (xhr) => xhr.setRequestHeader("Authorization", `Bearer ${token}`),
  });
  // the token lives 5 minutes; get a new one 30 seconds before it ends (30 s is the author's choice; keycloak-js's guide
  // only uses updateToken(30) in an example call, its default is 5), so later requests stay valid
  liveTimer = setInterval(async () => {
    try {
      token = (await api(`/tenants/${encodeURIComponent(tid)}/cameras/${encodeURIComponent(cid)}/live`, { method: "POST" })).token;
    } catch { /* the next try will do */ }
  }, (5 * 60 - 30) * 1000);
  hls.on(window.Hls.Events.ERROR, (_e, data) => {
    if (data.fatal) msg.textContent = `ライブが止まりました（${data.type}）。「ライブ」をもう一度押してください。`;
  });
  hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
    msg.textContent = "ライブ";
    $("video").play().catch(() => {});
  });
  hls.loadSource(live.hls_url);
  hls.attachMedia($("video"));
}

// The answer of a playback route is an address and a short token for it: fetch the recording with the token and play it.
async function playRecording(p, what) {
  const res = await fetch(p.url, { headers: { Authorization: `Bearer ${p.token}` } });
  if (!res.ok) throw new ApiError(res.status, "playback");
  const blob = await res.blob();
  blobUrl = URL.createObjectURL(blob);
  const v = $("video");
  v.src = blobUrl;
  $("viewer-msg").textContent = `${what}（${Math.round(p.duration_seconds)} 秒）`;
  v.play().catch(() => {});
}

async function playEvent(tid, cid, eventId) {
  stopVideo();
  const msg = $("viewer-msg");
  msg.textContent = "録画を取得しています…";
  try {
    const p = await api(`/tenants/${encodeURIComponent(tid)}/cameras/${encodeURIComponent(cid)}/events/${encodeURIComponent(eventId)}/playback`, { method: "POST" });
    await playRecording(p, "出来事の録画");
  } catch (e) {
    if (e instanceof ApiError && e.status === 410) msg.textContent = "この出来事の録画は消えました。";
    else if (e instanceof ApiError && e.status === 404) msg.textContent = "見つかりません";
    else msg.textContent = "録画を再生できませんでした。";
  }
}

async function playPast(tid, cid) {
  stopVideo();
  const msg = $("viewer-msg");
  msg.textContent = "録画を取得しています…";
  try {
    const p = await api(`/tenants/${encodeURIComponent(tid)}/cameras/${encodeURIComponent(cid)}/playback`, { method: "POST" });
    await playRecording(p, "過去の録画");
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      msg.textContent = e.detail === "no_recording" ? "まだ録画がありません。" : "見つかりません";
    } else {
      msg.textContent = "録画を再生できませんでした。";
    }
  }
}

// ---------------------------------------------------------------- start-up

function handleError(e) {
  if (e instanceof ApiError && e.status === 404) show("notfound");
  else setStatus("読み込みに失敗しました。ページを更新してください。");
}

async function render() {
  const r = route();
  if (r) await showCamera(r.tenant, r.camera).catch(handleError);
  else await showList();
}

async function main() {
  const authed = await kc.init({ pkceMethod: "S256", checkLoginIframe: false, responseMode: "fragment" });
  if (!authed) {
    await kc.login({ redirectUri: home });
    return;
  }
  me = await api("/me");
  $("user").textContent = kc.tokenParsed?.preferred_username || "";
  const sel = $("tenant");
  const seen = new Set();
  for (const e of me.entries) {
    if (seen.has(e.tenant_id)) continue;
    seen.add(e.tenant_id);
    const o = document.createElement("option");
    o.value = e.tenant_id;
    o.textContent = e.tenant_name;
    sel.append(o);
  }
  tenantId = sel.value || null;
  sel.onchange = () => { tenantId = sel.value; location.hash = "#/"; render(); };
  $("logout").onclick = () => kc.logout({ redirectUri: home });
  window.addEventListener("hashchange", render);
  if (!tenantId && !route()) { setStatus("このユーザーに割り当てられたテナントがありません。"); return; }
  await render();
}

main().catch(() => setStatus("起動に失敗しました。ページを更新してください。"));
