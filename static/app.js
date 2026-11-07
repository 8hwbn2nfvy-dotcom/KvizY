/* KvizY — весь интерфейс. Без сборки и библиотек.
   Устройство:
   1. Общее: api(), экранирование, всплывашки, ошибки с «Повторить», меню.
   2. Роутер по адресу после # (#/create, #/play/<id> …). Каждая страница — функция page*(…).
   3. Страницы: вход, главная, создать квиз, прогресс создания, прохождение, итог, история, квиз,
      статистика, график роста, предметы, предмет, настройки.
   Правило: страницу целиком рисуем один раз; потом меняем только нужные кусочки (по таймеру — тоже),
   чтобы не сбивалась прокрутка и не стирался введённый текст. */
"use strict";

const PALETTE_NAMES = {
  classic: "Классика", dark: "Тёмная", beige: "Бежевая", bluegray: "Серо-голубая", sage: "Шалфей",
  terminal: "Терминал", sakura: "Сакура", desert: "Пустыня", deep: "Глубина", neon: "Неон",
};
const PALETTE_BG = {classic:"#ffffff",dark:"#121212",beige:"#f4ede1",bluegray:"#edf1f5",sage:"#edf1ea",terminal:"#050a05",sakura:"#fff4f6",desert:"#f2e5cf",deep:"#0a1828",neon:"#0c0a19"};
const DIFF = { easy: "лёгкий", medium: "средний", hard: "на размышление" };
const PURPOSE = { quiz: "создание квиза", replace: "замена вопросов", check_open: "проверка ответов", subject_quiz: "квиз по предмету", ping: "проверка связи", review: "разбор ошибок" };

const S = { boot: null, ready: false };   // общее состояние: данные /api/bootstrap
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const view = $("#view");

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}
const num = (v) => String(Math.round((+v || 0) * 10) / 10).replace(".", ",");
const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)} %`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- API ----------
class ApiError extends Error {
  constructor(message, status, kind) { super(message); this.status = status; this.kind = kind; }
}
let wakeTimer = null;
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", credentials: "same-origin", headers: {} };
  if (opts.json !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.json); }
  if (opts.form) init.body = opts.form;
  // Если сервер долго молчит (просыпается после простоя) — показываем «подключаюсь…».
  if (!S.ready) wakeTimer = setTimeout(() => { const t = $("#boot-text"); if (t) t.textContent = "подключаюсь… сервер просыпается"; }, 1500);
  let resp;
  try {
    resp = await fetch(path, init);
  } catch (e) {
    throw new ApiError("Нет связи с сервером. Проверь интернет и нажми «Повторить».", 0, "network");
  } finally { clearTimeout(wakeTimer); }
  let data = null;
  try { data = await resp.json(); } catch (e) { data = null; }
  if (resp.status === 401 && !opts.noAuthRedirect) { showLogin(); throw new ApiError("Нужно войти", 401, "auth"); }
  if (!resp.ok) {
    const msg = (data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `Сервер ответил ${resp.status}`;
    throw new ApiError(msg, resp.status, resp.status >= 500 ? "server" : "client");
  }
  return data;
}

// ---------- Мелочи интерфейса ----------
let toastTimer = null;
function toast(text, ms = 3500) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
  t.textContent = text; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}
/** Ошибка не молчит: что случилось + кнопка «Повторить». */
function errorBox(target, err, retry, title = "Не получилось") {
  const box = document.createElement("div");
  box.className = "error-box"; box.setAttribute("role", "alert");
  box.innerHTML = `<b>${esc(title)}</b><div>${esc(err && err.message ? err.message : err)}</div>` +
    (retry ? `<button class="btn small" type="button">Повторить</button>` : "");
  if (retry) $("button", box).onclick = () => { box.remove(); retry(); };
  if (typeof target === "string") target = $(target);
  target.innerHTML = ""; target.appendChild(box);
  return box;
}
function confirmModal(text, okText = "Да", danger = false) {
  return new Promise((resolve) => {
    const shade = document.createElement("div");
    shade.className = "modal-shade";
    shade.innerHTML = `<div class="modal" role="dialog" aria-modal="true"><p>${esc(text)}</p>
      <div class="actions"><button class="btn secondary" data-a="no">Отмена</button>
      <button class="btn ${danger ? "danger" : ""}" data-a="yes">${esc(okText)}</button></div></div>`;
    shade.onclick = (e) => {
      const a = e.target.dataset && e.target.dataset.a;
      if (e.target === shade || a) { shade.remove(); resolve(a === "yes"); }
    };
    document.body.appendChild(shade);
  });
}
function setTitle(t) { $("#top-title").textContent = t || "KvizY"; document.title = t ? `${t} — KvizY` : "KvizY"; }
function loading(text = "загружаю…") { view.innerHTML = `<div class="center"><div class="spinner"></div><div class="muted">${esc(text)}</div></div>`; }
function applyPalette(p) {
  document.documentElement.setAttribute("data-palette", p);
  $('meta[name="theme-color"]').setAttribute("content", PALETTE_BG[p] || "#ffffff");
  try { localStorage.setItem("kvizy.palette", p); } catch (e) {}
}
function bars(items, sub = false) {
  return items.map((t) => `<div class="bar-row${sub ? " sub" : ""}"><div class="lbl"><span>${esc(t.name)}</span><span class="pct-chip">${pct(t.pct)}</span></div>
    <div class="bar"><i style="width:${Math.max(0, Math.min(100, t.pct || 0))}%"></i></div></div>`).join("");
}

// ---------- Меню ----------
function setMenu(open) {
  document.body.classList.toggle("menu-open", open);
  $("#menu-btn").setAttribute("aria-expanded", String(open));
}
$("#menu-btn").onclick = () => setMenu(!document.body.classList.contains("menu-open"));
$("#drawer-shade").onclick = () => setMenu(false);
$$("#drawer a").forEach((a) => (a.onclick = () => setMenu(false)));

// ---------- Роутер ----------
const routes = [
  [/^\/?$/, pageHome], [/^\/create$/, pageCreate], [/^\/progress\/(\w+)$/, pageProgress],
  [/^\/play\/(\w+)$/, pagePlay], [/^\/result\/(\w+)$/, pageResult], [/^\/history$/, pageHistory],
  [/^\/quiz\/(\w+)$/, pageQuiz], [/^\/stats$/, pageStats], [/^\/growth$/, pageGrowth],
  [/^\/subjects$/, pageSubjects], [/^\/subject\/(\w+)$/, pageSubject], [/^\/settings$/, pageSettings],
];
let leaveHooks = [];
function onLeave(fn) { leaveHooks.push(fn); }
function go(path) { if (location.hash === "#" + path) route(); else location.hash = path; }
async function route() {
  leaveHooks.forEach((f) => { try { f(); } catch (e) {} }); leaveHooks = [];
  if (!S.ready) return;
  const path = location.hash.replace(/^#/, "") || "/";
  const nav = path.split("/")[1] || "home";
  $$("#drawer a").forEach((a) => a.classList.toggle("active", a.dataset.nav === (nav === "" ? "home" : nav) ||
    (a.dataset.nav === "history" && nav === "quiz") || (a.dataset.nav === "subjects" && nav === "subject")));
  window.scrollTo(0, 0);
  for (const [re, fn] of routes) {
    const m = path.match(re);
    if (m) { try { await fn(...m.slice(1)); } catch (e) { if (e.kind !== "auth") errorBox(view, e, route); } return; }
  }
  pageHome();
}
window.addEventListener("hashchange", route);

// ---------- Запуск ----------
async function boot() {
  const bootEl = $("#boot");
  bootEl.hidden = false;
  try {
    S.boot = await api("/api/bootstrap");
    applyPalette(S.boot.palette || "classic");
    S.ready = true;
    bootEl.hidden = true;
    route();
  } catch (e) {
    if (e.kind === "auth") return;
    bootEl.hidden = true;
    S.ready = false;
    setTitle("KvizY");
    errorBox(view, e, boot, "Не удалось подключиться");
  }
}
function showLogin() {
  S.ready = false;
  $("#boot").hidden = true;
  setTitle("Вход");
  view.innerHTML = `<div class="home-hero"><div class="logo">KvizY</div><p class="muted">Введи пароль один раз — дальше этот телефон или планшет будет входить сам.</p></div>
    <form id="login" class="stack" autocomplete="on">
      <label class="field"><span>Логин</span><input type="text" name="username" autocomplete="username" autocapitalize="off" spellcheck="false" placeholder="владелец — оставь пустым">
        <small>Логин тебе выдаёт владелец приложения. Если приложение твоё — оставь пустым.</small></label>
      <label class="field"><span>Пароль</span><input type="password" name="password" autocomplete="current-password" required></label>
      <button class="btn block" type="submit">Войти</button><div id="login-err"></div></form>`;
  const f = $("#login");
  f.onsubmit = async (e) => {
    e.preventDefault();
    const btn = $("button", f); btn.disabled = true;
    try {
      await api("/api/login", { method: "POST", json: { login: f.username.value, password: f.password.value }, noAuthRedirect: true });
      boot();
    } catch (err) { errorBox("#login-err", err, null, "Не вошли"); btn.disabled = false; }
  };
}

// ======================= СТРАНИЦЫ =======================

function pageHome() {
  setTitle("KvizY");
  const h = new Date().getHours();
  const hello = h < 6 ? "Доброй ночи" : h < 12 ? "Доброе утро" : h < 18 ? "Добрый день" : "Добрый вечер";
  view.innerHTML = `<div class="home-hero"><div class="logo">KvizY</div><h1>${hello}!</h1>
    <p class="muted">Загрузи лекцию или конспект — сделаю квиз и покажу, какие темы стоит повторить.</p></div>
    <div class="stack"><a class="btn block" href="#/create">Создать квиз</a>
    <a class="btn secondary block" href="#/history">История квизов</a></div>`;
}

// ---------- Создать квиз ----------
function pageCreate() {
  setTitle("Создать квиз");
  const f = S.boot.form || {};
  const lim = S.boot.limits;
  const files = [];
  const choices = lim.choices;
  const count0 = f.count || 10;
  view.innerHTML = `<h1>Новый квиз</h1>
  <form id="cf" autocomplete="off">
    <label class="field"><span>Предмет</span><input type="text" name="subject" list="subj-list" required value="${esc(f.subject || "")}" placeholder="например, Химия">
      <datalist id="subj-list">${S.boot.subjects.map((s) => `<option value="${esc(s)}">`).join("")}</datalist></label>
    <label class="field"><span>Тема (название квиза)</span><input type="text" name="topic" required placeholder="например, Электролиз"></label>
    <div class="panel"><b>Материал</b>
      <div class="field"><span><b>Файлы</b></span><label class="btn secondary block" style="margin-top:6px">Выбрать файлы<input type="file" id="file-in" multiple accept=".pdf,.docx,.pptx,.txt,.md,application/pdf" hidden></label>
        <small class="help">До ${lim.max_files} файлов, до ${lim.max_file_mb} МБ каждый: PDF, Word (.docx), PowerPoint (.pptx), .txt</small></div>
      <ul class="files" id="file-list"></ul>
      <label class="field"><span>Или вставь текст</span><textarea name="text" placeholder="Конспект, выдержки…"></textarea></label>
      <label class="field"><span>Ссылки на страницы</span><textarea name="urls" rows="2" style="min-height:70px" placeholder="по одной в строке"></textarea></label>
    </div>
    <label class="field"><span>Откуда и докуда читать</span><input type="text" name="range" placeholder="например: со слайда 5 до 12 · только 2-й файл · с раздела «Электролиз» до конца">
      <small>Можно оставить пустым — тогда весь материал.</small></label>
    <div class="field"><span><b>Число вопросов</b></span>
      <div class="seg" id="count-seg" style="margin-top:6px">${choices.map((c) => `<button type="button" data-v="${c}" class="${c === count0 ? "on" : ""}">${c}</button>`).join("")}
        <button type="button" data-v="custom" class="${choices.includes(count0) ? "" : "on"}">своё</button></div>
      <input type="number" name="count_custom" min="1" max="${lim.max_questions}" value="${count0}" ${choices.includes(count0) ? "hidden" : ""} style="margin-top:8px"></div>
    <div class="field"><span><b>Сложность</b></span>
      <div class="seg" id="diff-seg" style="margin-top:6px">${Object.entries(DIFF).map(([k, v]) => `<button type="button" data-v="${k}" class="${(f.difficulty || "medium") === k ? "on" : ""}">${v}</button>`).join("")}</div></div>
    <label class="toggle"><span>Таймер <small class="muted">(${lim.timer.easy} с / ${lim.timer.medium} с / ${lim.timer.hard / 60} мин на вопрос)</small></span><input type="checkbox" name="timer" ${f.timer ? "checked" : ""}><span class="sw"></span></label>
    <label class="toggle"><span>Подсказки <small class="muted">(у вопросов «вне файла» и «на логику»)</small></span><input type="checkbox" name="hints" ${f.hints ? "checked" : ""}><span class="sw"></span></label>
    <div id="cf-err"></div>
    <button class="btn block" type="submit" style="margin-top:12px">Создать квиз</button>
  </form>`;
  const form = $("#cf");
  const seg = (id) => { const el = $(id); el.onclick = (e) => { const b = e.target.closest("button"); if (!b) return; $$("button", el).forEach((x) => x.classList.toggle("on", x === b)); el.dispatchEvent(new Event("change")); }; return el; };
  const countSeg = seg("#count-seg");
  countSeg.addEventListener("change", () => { form.count_custom.hidden = $(".on", countSeg).dataset.v !== "custom"; });
  seg("#diff-seg");
  const renderFiles = () => {
    $("#file-list").innerHTML = files.map((fl, i) => `<li><span>${esc(fl.name)}</span><small class="muted">${(fl.size / 1048576).toFixed(1)} МБ</small><button type="button" class="x-btn" data-i="${i}" aria-label="Убрать">×</button></li>`).join("");
  };
  $("#file-in").onchange = (e) => {
    for (const fl of e.target.files) {
      if (files.length >= lim.max_files) { toast(`Не больше ${lim.max_files} файлов`); break; }
      if (fl.size > lim.max_file_mb * 1048576) { toast(`«${fl.name}» больше ${lim.max_file_mb} МБ`); continue; }
      if (!/\.(pdf|docx|pptx|txt|md)$/i.test(fl.name)) { toast(`«${fl.name}»: такой тип не подходит`); continue; }
      files.push(fl);
    }
    e.target.value = ""; renderFiles();
  };
  $("#file-list").onclick = (e) => { const b = e.target.closest("button[data-i]"); if (b) { files.splice(+b.dataset.i, 1); renderFiles(); } };
  form.onsubmit = async (e) => {
    e.preventDefault();
    const cv = $(".on", countSeg).dataset.v;
    const count = cv === "custom" ? parseInt(form.count_custom.value, 10) : parseInt(cv, 10);
    if (!count || count < 1 || count > lim.max_questions) { toast(`Число вопросов — от 1 до ${lim.max_questions}`); return; }
    const urls = form.urls.value.split(/\s+/).map((u) => u.trim()).filter(Boolean);
    if (!files.length && !form.text.value.trim() && !urls.length) { errorBox("#cf-err", "Добавь материал: файл, текст или ссылку.", null, "Нет материала"); return; }
    const body = {
      subject: form.subject.value, topic: form.topic.value, count, difficulty: $(".on", $("#diff-seg")).dataset.v,
      timer: form.timer.checked, hints: form.hints.checked, source_range: form.range.value, text: form.text.value, urls, uploads: [],
    };
    runCreation(body, files.slice());
  };
}

/** Создание квиза со статусами: загрузка файлов → чтение → вопросы → проверка. */
async function runCreation(body, files) {
  setTitle("Делаю квиз");
  view.innerHTML = `<h1>${esc(body.topic)}</h1><div class="panel"><div class="row"><div class="spinner" id="cr-spin"></div><b id="cr-now">загружаю файлы…</b></div>
    <ul class="status-lines" id="cr-lines"></ul><div id="cr-warn"></div></div><div id="cr-err"></div>
    <p class="muted small">Не закрывай приложение, пока квиз делается: обычно это 20–90 секунд.</p>`;
  const lines = $("#cr-lines");
  const step = (text) => {
    $$("li.now", lines).forEach((li) => { li.classList.remove("now"); li.classList.add("done"); });
    const li = document.createElement("li"); li.className = "now"; li.textContent = text; lines.appendChild(li);
    $("#cr-now").textContent = text;
  };
  const fail = (err, retry) => { $("#cr-spin").hidden = true; errorBox("#cr-err", err, retry); };
  try {
    if (files.length) {
      const part = S.boot.limits.part_bytes;
      for (let i = 0; i < files.length; i++) {
        step(files.length > 1 ? `загружаю файлы… (${i + 1} из ${files.length})` : "загружаю файл…");
        let uploadId = "";
        const fl = files[i];
        for (let n = 0, off = 0; off < fl.size || (fl.size === 0 && n === 0); n++, off += part) {
          const fd = new FormData();
          fd.append("upload_id", uploadId); fd.append("n", String(n)); fd.append("filename", fl.name);
          fd.append("part", fl.slice(off, off + part), fl.name);
          uploadId = (await api("/api/uploads", { method: "POST", form: fd })).upload_id;
          if (fl.size === 0) break;
        }
        body.uploads.push(uploadId);
      }
    }
    step("читаю файлы…");
    const made = await api("/api/quizzes", { method: "POST", json: body });
    S.boot.form = { subject: body.subject, count: body.count, difficulty: body.difficulty, timer: body.timer, hints: body.hints };
    if (!S.boot.subjects.includes(body.subject.trim())) S.boot.subjects.push(body.subject.trim());
    const warn = [...(made.warnings || [])];
    if (made.range_note) warn.push(`Взял только часть: ${made.range_note}.`);
    if (made.range_rest) warn.push(`Указание «${made.range_rest}» передал ИИ.`);
    $("#cr-warn").innerHTML = warn.map((w) => `<div class="warn-box">${esc(w)}</div>`).join("");
    await generateWithStatus(made.quiz_id, step, fail);
  } catch (err) {
    fail(err, () => runCreation(body, files));
  }
}

/** Шаг «вопросы от ИИ»: долгий запрос + опрос статуса раз в 1,5 с (меняется только строка статуса). */
async function generateWithStatus(quizId, step, fail) {
  step("делаю вопросы…");
  let done = false;
  let last = "делаю вопросы…";
  const poll = async () => {
    while (!done) {
      await sleep(1500);
      if (done) break;
      try {
        const st = await api(`/api/quizzes/${quizId}/status`);
        if (st.status_text && st.status_text !== last && st.status === "running") { last = st.status_text; step(st.status_text); }
        if (st.status === "error" && !done) { done = true; fail(new Error(st.error || "Ошибка"), () => retryGenerate(quizId)); }
      } catch (e) { /* связь мигнула — попробуем на следующем круге */ }
    }
  };
  poll();
  try {
    const res = await api(`/api/quizzes/${quizId}/generate`, { method: "POST" });
    done = true;
    if (res.warnings && res.warnings.length) toast(res.warnings.join(" "), 6000);
    step("готово!");
    go(`/play/${quizId}`);
  } catch (err) {
    if (done) return;
    // Запрос мог оборваться (связь), а квиз всё равно доделывается — ждём по статусу.
    if (err.kind === "network") {
      for (let i = 0; i < 200 && !done; i++) {
        await sleep(2000);
        try {
          const st = await api(`/api/quizzes/${quizId}/status`);
          if (st.status === "ready") { done = true; go(`/play/${quizId}`); return; }
          if (st.status === "error") break;
        } catch (e) { /* ещё нет связи */ }
      }
    }
    done = true;
    fail(err, () => retryGenerate(quizId));
  }
}
function retryGenerate(quizId) { go(`/progress/${quizId}`); }

async function pageProgress(quizId) {
  setTitle("Делаю квиз");
  view.innerHTML = `<h1>Квиз</h1><div class="panel"><div class="row"><div class="spinner" id="cr-spin"></div><b id="cr-now">…</b></div>
    <ul class="status-lines" id="cr-lines"></ul></div><div id="cr-err"></div>`;
  const lines = $("#cr-lines");
  const step = (text) => {
    $$("li.now", lines).forEach((li) => { li.classList.remove("now"); li.classList.add("done"); });
    const li = document.createElement("li"); li.className = "now"; li.textContent = text; lines.appendChild(li);
    $("#cr-now").textContent = text;
  };
  const fail = (err, retry) => { $("#cr-spin").hidden = true; errorBox("#cr-err", err, retry); };
  const st = await api(`/api/quizzes/${quizId}/status`);
  if (st.status === "ready") { go(`/play/${quizId}`); return; }
  if (st.status === "running") {
    step(st.status_text || "делаю вопросы…");
    for (;;) {
      await sleep(2000);
      if (!location.hash.includes(quizId)) return;
      const s2 = await api(`/api/quizzes/${quizId}/status`);
      if (s2.status === "ready") { go(`/play/${quizId}`); return; }
      if (s2.status === "error") { fail(new Error(s2.error), () => pageProgress(quizId)); return; }
      if (s2.status_text) $("#cr-now").textContent = s2.status_text;
    }
  }
  await generateWithStatus(quizId, step, fail);
}

// ---------- Прохождение ----------
async function pagePlay(quizId) {
  setTitle("Квиз");
  loading("готовлю карточки…");
  const at = await api(`/api/quizzes/${quizId}/attempts`, { method: "POST" });
  setTitle(at.title);
  const st = { at, idx: 0, streak: 0, start: Date.now(), cardStart: Date.now(), timer: null, answered: false, busy: false };
  view.innerHTML = `<div class="play-top"><span class="counter" id="pl-count"></span><div class="progress"><i id="pl-bar"></i></div>
      <span class="streak" id="pl-streak" title="Правильных подряд"></span></div>
    <div class="timer" id="pl-timer" hidden><i></i></div><div class="timer-text" id="pl-timer-text" hidden></div>
    <div class="card-stage" id="stage"></div>`;
  onLeave(() => clearInterval(st.timer));
  const n = at.questions.length;
  const updTop = () => {
    $("#pl-count").textContent = `${Math.min(st.idx + 1, n)} из ${n}`;
    $("#pl-bar").style.width = `${(st.idx / n) * 100}%`;
    $("#pl-streak").textContent = st.streak > 0 ? `серия ${st.streak} 🔥` : "серия 0";
  };
  const secondsFor = (q) => at.seconds[q.difficulty] || at.default_seconds;

  const startTimer = (q) => {
    clearInterval(st.timer);
    if (!at.timer) return;
    const total = secondsFor(q);
    const end = Date.now() + total * 1000;
    const bar = $("#pl-timer"); const txt = $("#pl-timer-text");
    bar.hidden = false; txt.hidden = false;
    const tick = () => {
      const left = Math.max(0, Math.ceil((end - Date.now()) / 1000));
      $("i", bar).style.width = `${(left / total) * 100}%`;
      bar.classList.toggle("low", left <= Math.min(10, total / 4));
      txt.textContent = left >= 60 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : `${left} с`;
      if (left <= 0) { clearInterval(st.timer); if (!st.answered) submit({ timeout: true }); }
    };
    tick(); st.timer = setInterval(tick, 500);
  };

  const showCard = (animate = true) => {
    const q = at.questions[st.idx];
    st.answered = false; st.cardStart = Date.now();
    updTop();
    const stage = $("#stage");
    const old = $(".card", stage);
    const card = document.createElement("div");
    card.className = "card" + (animate ? " enter" : "");
    card.innerHTML = cardHTML(q, at);
    const put = () => { stage.innerHTML = ""; stage.appendChild(card); bindCard(card, q); startTimer(q); };
    if (old && animate) { old.classList.add("leave"); setTimeout(put, 200); } else put();
  };

  let current = { getResponse: () => ({}) };
  const bindCard = (card, q) => {
    current = bindInputs(card, q);
    const ans = $("[data-act=answer]", card);
    ans.onclick = () => { const r = current.getResponse(); if (r === null) { toast("Сначала выбери ответ"); return; } submit(r); };
    const hb = $("[data-act=hint]", card);
    if (hb) hb.onclick = () => { $(".hint-box", card).hidden = false; hb.hidden = true; };
    const rb = $("[data-act=replace]", card);
    if (rb) rb.onclick = () => doReplace(q, rb);
  };

  const doReplace = async (q, btn) => {
    if (st.busy || st.answered) return;
    st.busy = true; btn.disabled = true; btn.textContent = "…";
    clearInterval(st.timer);
    try {
      const r = await api(`/api/questions/${q.id}/replace`, { method: "POST" });
      toast(r.message, 4000);
      if (r.changed) { at.questions[st.idx] = r.question; showCard(false); }
      else { at.questions[st.idx].replace_count = r.question.replace_count; showCard(false); }
    } catch (e) {
      toast(`Замена не вышла: ${e.message}`, 6000);
      showCard(false);
    } finally { st.busy = false; }
  };

  const submit = async (response) => {
    if (st.answered || st.busy) return;
    st.answered = true; st.busy = true;
    clearInterval(st.timer);
    const card = $("#stage .card");
    const q = at.questions[st.idx];
    card.classList.add("locked");
    const btns = $(".actions", card);
    btns.innerHTML = `<div class="row"><div class="spinner"></div><span class="muted">${q.qtype === "open" && !response.timeout ? "проверяю ответ…" : "секунду…"}</span></div>`;
    let res;
    try {
      res = await api(`/api/attempts/${at.attempt_id}/answer`, { method: "POST", json: { question_id: q.id, response, time_sec: Math.round((Date.now() - st.cardStart) / 1000) } });
    } catch (e) {
      st.answered = false; st.busy = false; card.classList.remove("locked");
      btns.innerHTML = ""; const holder = document.createElement("div"); btns.appendChild(holder);
      errorBox(holder, e, () => submit(response), "Ответ не отправился");
      return;
    }
    st.busy = false;
    showVerdict(card, q, res, response);
  };

  const showVerdict = (card, q, res, response) => {
    $$(".verdict", card).forEach((v) => v.remove());
    const btns = $(".actions", card);
    if (res.status === "unchecked") {
      card.classList.remove("is-ok", "is-bad");
      btns.innerHTML = `<div class="warn-box" style="flex-basis:100%">Не удалось проверить ответ: ${esc(res.note)}. Засчитать?</div>
        <button class="btn small" data-m="full">Засчитать</button><button class="btn small secondary" data-m="half">Наполовину</button>
        <button class="btn small secondary" data-m="no">Не засчитывать</button><button class="btn small secondary" data-m="retry">Проверить ещё раз</button>`;
      btns.onclick = async (e) => {
        const m = e.target.dataset && e.target.dataset.m;
        if (!m) return;
        btns.onclick = null;
        try {
          const r2 = m === "retry"
            ? await api(`/api/attempts/${at.attempt_id}/recheck`, { method: "POST", json: { question_id: q.id } })
            : await api(`/api/attempts/${at.attempt_id}/manual`, { method: "POST", json: { question_id: q.id, accept: m } });
          showVerdict(card, q, r2, response);
        } catch (err) { toast(err.message); showVerdict(card, q, res, response); }
      };
      return;
    }
    const ok = res.correct;
    card.classList.add(ok ? "is-ok" : "is-bad");
    st.streak = ok ? st.streak + 1 : 0;
    const v = document.createElement("div");
    v.className = "verdict" + (res.partial ? " partial" : "");
    v.innerHTML = ok ? `<span class="ic">✓</span>Верно` : res.partial ? `<span class="ic">½</span>Частично: ${num(res.score)} из ${num(res.max)}` :
      response.timeout ? `<span class="ic">✕</span>Время вышло` : `<span class="ic">✕</span>Неверно`;
    btns.before(v);
    updTop();
    const last = st.idx >= n - 1;
    btns.innerHTML = `<button class="btn block" data-act="next">${last ? "Итог" : "Дальше"}</button>`;
    $("[data-act=next]", btns).onclick = async () => {
      if (!last) { st.idx++; showCard(true); return; }
      const nb = $("[data-act=next]", btns); nb.disabled = true; nb.textContent = "считаю…";
      try {
        const r = await api(`/api/attempts/${at.attempt_id}/finish`, { method: "POST", json: { duration: Math.round((Date.now() - st.start) / 1000) } });
        go(`/result/${r.attempt_id}`);
      } catch (e) { nb.disabled = false; nb.textContent = "Итог"; toast(`Не посчиталось: ${e.message}. Нажми ещё раз.`, 6000); }
    };
  };

  showCard(false);
}

function cardHTML(q, at) {
  const tags = [`<span class="tag">${esc(q.topic || "")}${q.subtopic ? " · " + esc(q.subtopic) : ""}</span>`, `<span class="tag">${esc(q.type_name)}</span>`];
  if (q.flag_name) tags.push(`<span class="tag flag">${esc(q.flag_name)}</span>`);
  const left = at.max_replaces - (q.replace_count || 0);
  let body = "";
  const o = q.options;
  if (q.qtype === "single" || q.qtype === "multi") {
    body = `<div class="opts">${o.map((t, i) => `<button type="button" class="opt ${q.qtype === "multi" ? "square" : ""}" data-i="${i}"><span class="mark"></span><span>${esc(t)}</span></button>`).join("")}</div>`;
    if (q.qtype === "multi") body = `<p class="muted small" style="margin-top:-8px">Можно выбрать несколько.</p>` + body;
  } else if (q.qtype === "tf") {
    body = `<div class="opts"><button type="button" class="opt" data-v="1"><span class="mark"></span>Верно</button><button type="button" class="opt" data-v="0"><span class="mark"></span>Неверно</button></div>`;
  } else if (q.qtype === "open") {
    body = `<textarea data-in placeholder="Ответь своими словами"></textarea>`;
  } else if (q.qtype === "fill") {
    body = `<input type="text" data-in placeholder="Пропущенное слово" autocapitalize="off" autocomplete="off" spellcheck="false">`;
  } else if (q.qtype === "match") {
    body = o.left.map((l, i) => `<div class="match-row"><b>${esc(l)}</b><select data-i="${i}"><option value="">— выбери —</option>${o.right.map((r) => `<option>${esc(r)}</option>`).join("")}</select></div>`).join("");
  } else if (q.qtype === "order") {
    body = `<p class="muted small" style="margin-top:-8px">Стрелками поставь по порядку.</p><div class="ord"><ol>${o.items.map((t) => `<li data-t="${esc(t)}"><span>${esc(t)}</span><button type="button" data-d="-1" aria-label="Выше">↑</button><button type="button" data-d="1" aria-label="Ниже">↓</button></li>`).join("")}</ol></div>`;
  }
  const hint = at.hints && q.hint ? `<button type="button" class="btn small secondary" data-act="hint" style="margin-top:12px">Подсказка</button><div class="hint-box" hidden>${esc(q.hint)}</div>` : "";
  return `<button type="button" class="replace-btn" data-act="replace" ${left <= 0 ? "disabled" : ""} title="Заменить вопрос (осталось ${left})" aria-label="Заменить вопрос">↻ ${left}</button>
    <div class="q-meta">${tags.join("")}</div><div class="q-text">${esc(q.text)}</div>${body}${hint}
    <div class="actions"><button class="btn block" data-act="answer">Ответить</button></div>`;
}

/** Привязать поля карточки. getResponse() → ответ или null (если ещё ничего не выбрано). */
function bindInputs(card, q) {
  if (q.qtype === "single" || q.qtype === "tf") {
    let chosen = null;
    $$(".opt", card).forEach((b) => (b.onclick = () => { $$(".opt", card).forEach((x) => x.classList.toggle("on", x === b)); chosen = b; }));
    return { getResponse: () => (chosen === null ? null : q.qtype === "tf" ? { value: chosen.dataset.v === "1" } : { choice: +chosen.dataset.i }) };
  }
  if (q.qtype === "multi") {
    $$(".opt", card).forEach((b) => (b.onclick = () => b.classList.toggle("on")));
    return { getResponse: () => { const c = $$(".opt.on", card).map((b) => +b.dataset.i); return c.length ? { choices: c } : null; } };
  }
  if (q.qtype === "open" || q.qtype === "fill") {
    const inp = $("[data-in]", card);
    if (q.qtype === "fill") inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("[data-act=answer]", card).click(); } };
    return { getResponse: () => (inp.value.trim() ? { text: inp.value.trim() } : null) };
  }
  if (q.qtype === "match") {
    return { getResponse: () => { const v = $$("select", card).map((s) => s.value); return v.some(Boolean) ? { right: v } : null; } };
  }
  if (q.qtype === "order") {
    const ol = $("ol", card);
    ol.onclick = (e) => {
      const b = e.target.closest("button[data-d]"); if (!b) return;
      const li = b.closest("li"); const d = +b.dataset.d;
      if (d < 0 && li.previousElementSibling) ol.insertBefore(li, li.previousElementSibling);
      if (d > 0 && li.nextElementSibling) ol.insertBefore(li.nextElementSibling, li);
    };
    return { getResponse: () => ({ order: $$("li", ol).map((li) => li.dataset.t) }) };
  }
  return { getResponse: () => ({}) };
}

// ---------- Итог ----------
async function pageResult(attemptId) {
  setTitle("Итог");
  loading();
  const r = await api(`/api/attempts/${attemptId}`);
  setTitle("Итог: " + r.title);
  const topics = Object.entries(r.by_topic || {}).map(([name, v]) => ({ name, pct: v.pct }));
  const subs = Object.values(r.by_subtopic || {});
  const topicBlock = topics.map((t) => {
    const ss = subs.filter((s) => s.topic === t.name).map((s) => ({ name: s.subtopic, pct: s.pct }));
    return bars([t]) + (ss.length >= 2 ? bars(ss, true) : "");
  }).join("");
  const mins = r.duration ? `${Math.floor(r.duration / 60)} мин ${r.duration % 60} с` : "";
  view.innerHTML = `<h1>${esc(r.title)}</h1>
    <div class="panel"><div class="big-pct">${pct(r.pct)}</div><div class="muted">${num(r.score)} из ${num(r.max)} баллов${mins ? " · " + mins : ""}</div>
    ${r.unchecked ? `<div class="warn-box">Не проверено открытых ответов: ${r.unchecked}. Они пока считаются как 0.</div>` : ""}</div>
    <h2>По темам</h2><div class="panel bars">${topicBlock || '<span class="muted">нет данных</span>'}</div>
    ${r.mistakes ? `<h2>Разбор ошибок</h2><div class="panel" id="review"></div>` : ""}
    <h2>Все карточки</h2><p class="muted small">У ошибок сразу видно правильный ответ и объяснение. Тапни на карточку — увидишь всё подробно и цитату.</p>
    <ul class="list" id="cards">${r.cards.map((c, i) => `<li><button type="button" class="item ${c.correct ? "ok" : "bad"}" data-i="${i}">
      <div class="t">${i + 1}. ${esc(c.text)}</div><div class="s"><span>${c.correct ? "✓ верно" : c.score > 0 ? "½ частично" : "✕ неверно"}</span><span>${num(c.score)} из ${num(c.max)}</span><span>${esc(c.topic || "")}</span></div>
      ${c.correct ? "" : `<div class="why"><div><b>Правильно:</b> ${esc(c.right_answer)}</div>${c.explanation ? `<div style="margin-top:6px">${esc(c.explanation)}</div>` : ""}</div>`}
      <div class="detail" hidden></div></button></li>`).join("")}</ul>
    <div class="actions"><a class="btn" href="#/play/${r.quiz_id}">Пройти снова</a><a class="btn secondary" href="#/history">В историю</a></div>`;
  if (r.mistakes) renderReview(r);
  $("#cards").onclick = (e) => {
    const b = e.target.closest("button.item"); if (!b) return;
    const d = $(".detail", b); const c = r.cards[+b.dataset.i];
    if (!d.innerHTML) {
      d.innerHTML = `<dl><dt>Вопрос</dt><dd>${esc(c.text)}</dd><dt>Твой ответ</dt><dd>${esc(c.your_answer)}</dd>
        <dt>Правильный ответ</dt><dd>${esc(c.right_answer)}</dd><dt>Баллы</dt><dd>${num(c.score)} из ${num(c.max)}${c.note ? " · " + esc(c.note) : ""}</dd>
        ${c.explanation ? `<dt>Объяснение</dt><dd>${esc(c.explanation)}</dd>` : ""}
        ${c.quote ? `<dt>Из материала</dt><dd><blockquote>${esc(c.quote)}</blockquote></dd>` : ""}
        ${c.flag_name ? `<dt>Пометка</dt><dd>${esc(c.flag_name)}</dd>` : ""}</dl>`;
    }
    d.hidden = !d.hidden;
  };
}

/** Разбор ошибок: по кнопке ИИ объясняет темы, где были ошибки. Готовый разбор хранится и показывается сразу. */
function renderReview(r) {
  const box = $("#review");
  const show = (rv) => {
    if (!rv.topics || !rv.topics.length) { box.innerHTML = `<p>${esc(rv.message || "Разбирать нечего.")}</p>`; return; }
    box.innerHTML = rv.topics.map((t) => `<div class="review-topic"><h3>${esc(t.topic)}</h3>
      <p>${esc(t.explain)}</p>
      ${t.key_points && t.key_points.length ? `<b>Запомни:</b><ul>${t.key_points.map((k) => `<li>${esc(k)}</li>`).join("")}</ul>` : ""}
      ${t.mistake ? `<p><b>Где была ошибка:</b> ${esc(t.mistake)}</p>` : ""}
      ${t.example ? `<p><b>Пример:</b> ${esc(t.example)}</p>` : ""}
      ${t.reread ? `<b>Перечитай:</b><blockquote>${esc(t.reread)}</blockquote>` : ""}</div>`).join("");
  };
  if (r.review) { show(r.review); return; }
  box.innerHTML = `<p class="muted">ИИ объяснит простыми словами темы, где ты ошибся (${r.mistakes} ошиб.): суть, что запомнить, где была ошибка, пример и что перечитать.</p>
    <button class="btn block" id="review-go">Разобрать ошибки</button>`;
  $("#review-go").onclick = async () => {
    box.innerHTML = `<div class="row"><div class="spinner"></div><span class="muted">разбираю ошибки… (10–30 секунд)</span></div>`;
    try { show(await api(`/api/attempts/${r.attempt_id}/review`, { method: "POST" })); }
    catch (e) { errorBox(box, e, () => renderReview(r), "Разбор не получился"); }
  };
}

// ---------- История ----------
async function pageHistory() {
  setTitle("История");
  loading();
  const list = await api("/api/quizzes");
  if (!list.length) { view.innerHTML = `<h1>История</h1><p class="muted">Квизов пока нет.</p><a class="btn block" href="#/create">Создать квиз</a>`; return; }
  view.innerHTML = `<h1>История</h1><ul class="list">${list.map((q) => `<li><a class="item" href="#/quiz/${q.id}">
    <div class="t">${esc(q.topic)}</div><div class="s"><span>${fmtDate(q.date)}</span><span>${esc(q.subject)}</span>${q.kind === "subject" ? "<span>по предмету</span>" : ""}
    ${q.status === "ready" ? `<span>лучший <span class="pct-chip">${pct(q.best_pct)}</span></span><span>последний <span class="pct-chip">${pct(q.last_pct)}</span></span>` :
      q.status === "error" ? `<span class="status-chip error">ошибка</span>` : `<span class="status-chip running">делается…</span>`}</div></a></li>`).join("")}</ul>`;
}

async function pageQuiz(quizId) {
  setTitle("Квиз");
  loading();
  const q = await api(`/api/quizzes/${quizId}`);
  setTitle(q.topic);
  const p = q.params || {};
  const sources = (q.sources || []).map((s) => s.kind === "url" ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>` : esc(s.name)).join(", ");
  view.innerHTML = `<h1>${esc(q.topic)}</h1>
    <div class="panel kv"><span>Предмет</span><span class="v">${esc(q.subject)}</span>
      <span>Создан</span><span class="v">${fmtDate(q.date)}</span><span>Вопросов</span><span class="v">${q.questions}</span>
      <span>Сложность</span><span class="v">${DIFF[p.difficulty] || ""}</span><span>Таймер / подсказки</span><span class="v">${p.timer ? "да" : "нет"} / ${p.hints ? "да" : "нет"}</span>
      <span>Лучший / последний</span><span class="v">${pct(q.best_pct)} / ${pct(q.last_pct)}</span></div>
    ${sources ? `<p class="small muted">Материал: ${sources}</p>` : ""}
    ${q.range_note ? `<p class="small muted">Взята часть: ${esc(q.range_note)}</p>` : ""}
    ${(q.warnings || []).map((w) => `<div class="warn-box">${esc(w)}</div>`).join("")}
    <div id="q-state"></div>
    <div class="actions" id="q-actions"></div>
    <h2>Попытки</h2>${q.attempts.length ? `<ul class="list">${q.attempts.map((a) => `<li><a class="item" href="#/result/${a.id}"><div class="t"><span class="pct-chip">${pct(a.pct)}</span> · ${num(a.score)} из ${num(a.max)}</div><div class="s">${fmtDate(a.date)}</div></a></li>`).join("")}</ul>` : '<p class="muted">Попыток пока нет.</p>'}
    <div class="actions" style="margin-top:28px"><button class="btn danger" id="q-del">Удалить квиз</button></div>`;
  const acts = $("#q-actions");
  if (q.status === "ready") acts.innerHTML = `<a class="btn" href="#/play/${q.id}">${q.attempts.length ? "Пройти снова" : "Пройти"}</a>`;
  else if (q.status === "error") { errorBox("#q-state", q.error || "Квиз не доделан", () => go(`/progress/${q.id}`), "Квиз не доделан"); }
  else acts.innerHTML = `<a class="btn secondary" href="#/progress/${q.id}">Смотреть, как делается</a>`;
  $("#q-del").onclick = async () => {
    if (!(await confirmModal(`Удалить квиз «${q.topic}»? Сотрутся его текст, все попытки и его вопросы из квизов по предмету. Вернуть нельзя.`, "Удалить", true))) return;
    try { await api(`/api/quizzes/${q.id}`, { method: "DELETE" }); toast("Квиз удалён"); go("/history"); }
    catch (e) { toast(`Не удалилось: ${e.message}`, 6000); }
  };
}

// ---------- Статистика ----------
function topicsBlock(topics) {
  if (!topics.length) return '<p class="muted">Пока нет пройденных квизов.</p>';
  return `<div class="panel bars">${topics.map((t) => bars([{ name: t.key, pct: t.pct }]) + (t.subtopics.length ? bars(t.subtopics, true) : "")).join("")}</div>`;
}
async function pageStats() {
  setTitle("Статистика");
  loading();
  const s = await api("/api/stats");
  view.innerHTML = `<h1>Статистика</h1>
    <a class="btn block secondary" href="#/growth">График роста</a>
    <h2>По темам</h2><p class="muted small">Сначала слабые. Считается по последней попытке каждого квиза.</p>${topicsBlock(s.topics)}
    <h2>Попытки</h2>${s.history.length ? `<ul class="list">${s.history.map((h) => `<li><a class="item" href="#/result/${h.attempt_id}"><div class="t">${esc(h.title)}</div>
      <div class="s"><span>${fmtDate(h.date)}</span><span>${esc(h.subject)}</span><span class="pct-chip">${pct(h.pct)}</span></div></a></li>`).join("")}</ul>` : '<p class="muted">Пока пусто.</p>'}`;
}

async function pageGrowth() {
  setTitle("График роста");
  loading();
  const s = await api("/api/stats");
  const pts = s.growth;
  view.innerHTML = `<h1>График роста</h1><p class="muted small">Процент правильных в каждой попытке, по времени.</p><div class="panel" id="chart"></div>`;
  if (pts.length < 2) { $("#chart").innerHTML = '<p class="muted">Нужно хотя бы две попытки.</p>'; return; }
  const W = 420, H = 260, L = 34, R = 10, T = 12, B = 30;
  const x = (i) => L + (i * (W - L - R)) / (pts.length - 1);
  const y = (v) => T + (1 - v / 100) * (H - T - B);
  const grid = [0, 25, 50, 75, 100].map((v) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`).join("");
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.pct || 0).toFixed(1)}`).join(" ");
  const step = Math.max(1, Math.ceil(pts.length / 4));
  const labels = pts.map((p, i) => (i % step === 0 || i === pts.length - 1) ? `<text x="${x(i)}" y="${H - 10}" text-anchor="middle">${new Date(p.date + "Z").toLocaleDateString("ru-RU", { day: "numeric", month: "numeric" })}</text>` : "").join("");
  const dots = pts.map((p, i) => `<circle class="dot" cx="${x(i)}" cy="${y(p.pct || 0)}" r="4"><title>${esc(p.title)}: ${pct(p.pct)}</title></circle>`).join("");
  $("#chart").innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="График процента по попыткам">${grid}<path class="line" d="${path}"/>${dots}${labels}</svg>`;
}

// ---------- Предметы ----------
async function pageSubjects() {
  setTitle("Предметы");
  loading();
  const list = await api("/api/subjects");
  if (!list.length) { view.innerHTML = `<h1>Предметы</h1><p class="muted">Предметов пока нет — они появятся, когда создашь квиз.</p><a class="btn block" href="#/create">Создать квиз</a>`; return; }
  view.innerHTML = `<h1>Предметы</h1><ul class="list">${list.map((s) => `<li><a class="item" href="#/subject/${s.id}"><div class="t">${esc(s.name)}</div>
    <div class="s">${s.quizzes.length} квиз(ов)</div><div class="s">${s.quizzes.slice(0, 6).map((q) => `<span>${esc(q.topic)} ${q.last_pct !== null && q.last_pct !== undefined ? `· <span class="pct-chip">${pct(q.last_pct)}</span>` : ""}</span>`).join("")}</div></a></li>`).join("")}</ul>`;
}

async function pageSubject(id) {
  setTitle("Предмет");
  loading();
  const s = await api(`/api/subjects/${id}`);
  setTitle(s.name);
  const lim = S.boot.limits;
  const f = S.boot.form || {};
  view.innerHTML = `<h1>${esc(s.name)}</h1>
    <div class="panel kv"><span>Квизов</span><span class="v">${s.normal_count}</span><span>Попыток</span><span class="v">${s.attempts}</span>
      <span>Средний последний результат</span><span class="v">${pct(s.avg_last_pct)}</span><span>Вопросов в запасе</span><span class="v">${s.pool}</span></div>
    <div class="panel"><b>Квиз по предмету</b><p class="muted small">Смесь: часть — твои старые вопросы (сначала где ошибался), часть — новые по всем материалам предмета.</p>
      <div class="seg" id="sq-count">${lim.choices.map((c) => `<button type="button" data-v="${c}" class="${c === (f.count || 10) ? "on" : ""}">${c}</button>`).join("")}</div>
      <div class="seg" id="sq-diff" style="margin-top:8px">${Object.entries(DIFF).map(([k, v]) => `<button type="button" data-v="${k}" class="${(f.difficulty || "medium") === k ? "on" : ""}">${v}</button>`).join("")}</div>
      <label class="toggle"><span>Таймер</span><input type="checkbox" id="sq-timer" ${f.timer ? "checked" : ""}><span class="sw"></span></label>
      <label class="toggle"><span>Подсказки</span><input type="checkbox" id="sq-hints" ${f.hints ? "checked" : ""}><span class="sw"></span></label>
      <button class="btn block" id="sq-go">Квиз по предмету</button><div id="sq-err"></div></div>
    <h2>Слабые темы</h2>${topicsBlock(s.topics)}
    <h2>Квизы предмета</h2><ul class="list">${s.quizzes.map((q) => `<li><a class="item" href="#/quiz/${q.id}"><div class="t">${esc(q.topic)}</div><div class="s">${q.kind === "subject" ? "<span>по предмету</span>" : ""}<span>последний ${pct(q.last_pct)}</span><span>попыток ${q.attempts}</span></div></a></li>`).join("")}</ul>`;
  for (const id2 of ["#sq-count", "#sq-diff"]) { const el = $(id2); el.onclick = (e) => { const b = e.target.closest("button"); if (b) $$("button", el).forEach((x) => x.classList.toggle("on", x === b)); }; }
  $("#sq-go").onclick = async () => {
    const btn = $("#sq-go"); btn.disabled = true;
    try {
      const r = await api(`/api/subjects/${id}/quiz`, { method: "POST", json: { count: +$("#sq-count .on").dataset.v, difficulty: $("#sq-diff .on").dataset.v, timer: $("#sq-timer").checked, hints: $("#sq-hints").checked } });
      go(`/progress/${r.quiz_id}`);
    } catch (e) { btn.disabled = false; errorBox("#sq-err", e, () => $("#sq-go").click()); }
  };
}

// ---------- Настройки ----------
async function pageSettings() {
  setTitle("Настройки");
  loading();
  if (S.boot.me && S.boot.me.role === "guest") return pageSettingsGuest();
  const [st, usage, guests] = await Promise.all([api("/api/settings"), api("/api/usage"), api("/api/users")]);
  const ai = st.ai;
  const fieldNames = { folder_id: "ID каталога Yandex Cloud", model: "Модель", base_url: "Адрес API (base URL)", scope: "Scope" };
  const secretNames = { api_key: "API-ключ", auth_key: "Ключ авторизации" };
  view.innerHTML = `<h1>Настройки</h1>
  <h2>ИИ</h2><div class="panel" id="ai-panel">
    <label class="field"><span>Провайдер</span><select id="ai-prov">${Object.entries(ai.providers).map(([k, v]) => `<option value="${k}" ${k === ai.provider ? "selected" : ""}>${esc(v.title)}</option>`).join("")}</select></label>
    <div id="ai-fields"></div>
    <div class="actions"><button class="btn" id="ai-save">Сохранить</button><button class="btn secondary" id="ai-check">Проверить связь</button></div>
    <div id="ai-msg"></div>
    ${ai.key_secret_set ? "" : '<div class="warn-box">На сервере не задан KEY_SECRET — ключи можно задать только через переменные окружения.</div>'}
  </div>
  <h2>Палитра</h2><div class="palettes" id="pals">${st.palettes.map((p) => `<button type="button" class="pal ${p === st.palette ? "on" : ""}" data-p="${p}" data-palette="${p}" style="background:var(--bg);color:var(--text)">${esc(PALETTE_NAMES[p] || p)}
    <span class="sw"><i style="background:var(--accent)"></i><i style="background:var(--accent-2)"></i><i style="background:var(--ok)"></i><i style="background:var(--bad)"></i></span></button>`).join("")}</div>
  <h2>Пароль</h2><form class="panel" id="pw" autocomplete="off">
    <p class="muted small">Сейчас пароль ${st.password_source === "settings" ? "задан здесь, в настройках" : "из настроек сервера (KVIZY_PASSWORD)"}. После смены все другие устройства выйдут.</p>
    <label class="field"><span>Старый пароль</span><input type="password" name="old" autocomplete="current-password" required></label>
    <label class="field"><span>Новый пароль</span><input type="password" name="new1" autocomplete="new-password" minlength="6" required></label>
    <label class="field"><span>Ещё раз новый</span><input type="password" name="new2" autocomplete="new-password" minlength="6" required></label>
    <button class="btn block" type="submit">Сменить пароль</button><div id="pw-msg"></div></form>
  <h2>Тестеры</h2><div class="panel" id="guests"></div>
  <h2>Расходы за месяц</h2><div class="panel" id="usage"></div>
  <div class="actions" style="margin-top:24px"><button class="btn secondary" id="logout">Выйти на этом устройстве</button></div>`;
  bindLogout();
  renderGuests(guests);

  const renderFields = () => {
    const name = $("#ai-prov").value;
    const p = ai.providers[name];
    const def = st.default_prices[name] || [0, 0];
    let html = "";
    for (const [f, v] of Object.entries(p.fields)) {
      if (f === "model" && p.models.length) {
        const known = p.models.some(([id]) => id === v);
        html += `<label class="field"><span>Модель</span><select data-f="model">${p.models.map(([id, t]) => `<option value="${esc(id)}" ${id === v ? "selected" : ""}>${esc(t)}</option>`).join("")}<option value="__custom" ${known ? "" : "selected"}>другая…</option></select>
          <input type="text" data-f="model_custom" value="${known ? "" : esc(v)}" ${known ? "hidden" : ""} placeholder="точное имя модели" style="margin-top:8px"></label>`;
      } else if (name !== "mock") {
        html += `<label class="field"><span>${esc(fieldNames[f] || f)}</span><input type="text" data-f="${f}" value="${esc(v)}" autocapitalize="off" spellcheck="false"></label>`;
      }
    }
    for (const [f, info] of Object.entries(p.secrets)) {
      html += `<div class="field"><span><b>${esc(secretNames[f] || f)}</b></span>
        ${info.set ? `<div class="row" style="margin:6px 0"><span class="ok-text">ключ задан ✓</span><span class="muted small">${info.source === "env" ? "(из переменных сервера)" : ""}</span>
          ${info.source === "db" ? `<button type="button" class="btn small danger" data-del="${f}">Удалить ключ</button>` : ""}</div>` : ""}
        ${info.broken ? '<div class="warn-box">Сохранённый ключ не расшифровывается (сменился KEY_SECRET). Введи ключ заново.</div>' : ""}
        <input type="password" data-s="${f}" placeholder="${info.set ? "ввести новый ключ" : "вставь ключ"}" autocomplete="off" autocapitalize="off" spellcheck="false">
        <small class="help">Ключ хранится на сервере зашифрованным и здесь больше не показывается.</small></div>`;
    }
    if (name === "yandex") html += `<p class="help">Если ключ не задан, а приложение работает в Yandex Cloud, используется сервисный аккаунт контейнера.</p>`;
    if (name !== "mock") {
      const pr = p.price || {};
      html += `<div class="field"><span><b>Цена, ₽ за 1000 токенов</b></span><div class="row" style="margin-top:6px">
        <input type="number" step="0.001" min="0" data-price="in" value="${pr.in ?? ""}" placeholder="запрос: ${def[0]}" style="flex:1">
        <input type="number" step="0.001" min="0" data-price="out" value="${pr.out ?? ""}" placeholder="ответ: ${def[1]}" style="flex:1"></div>
        <small class="help">Пусто — цена по умолчанию (в подсказке). 🟡 Проверь актуальные цены у провайдера.</small></div>`;
    }
    $("#ai-fields").innerHTML = html;
    const ms = $("[data-f=model]", $("#ai-fields"));
    if (ms) ms.onchange = () => { $("[data-f=model_custom]").hidden = ms.value !== "__custom"; };
    $$("[data-del]", $("#ai-fields")).forEach((b) => (b.onclick = async () => {
      if (!(await confirmModal("Удалить сохранённый ключ?", "Удалить", true))) return;
      try { Object.assign(ai, await api(`/api/settings/ai/${name}/${b.dataset.del}`, { method: "DELETE" })); renderFields(); toast("Ключ удалён"); }
      catch (e) { toast(e.message); }
    }));
  };
  $("#ai-prov").onchange = renderFields;
  renderFields();
  const collect = () => {
    const name = $("#ai-prov").value; const box = $("#ai-fields");
    const fields = {}; $$("[data-f]", box).forEach((i) => { if (i.dataset.f !== "model_custom") fields[i.dataset.f] = i.value; });
    if (fields.model === "__custom") fields.model = $("[data-f=model_custom]", box).value;
    const secrets = {}; $$("[data-s]", box).forEach((i) => { if (i.value.trim()) secrets[i.dataset.s] = i.value.trim(); });
    const pin = $("[data-price=in]", box), pout = $("[data-price=out]", box);
    return { provider: name, fields, secrets, price: pin ? { in: pin.value, out: pout.value } : null };
  };
  const save = async () => {
    const r = await api("/api/settings/ai", { method: "PUT", json: collect() });
    Object.assign(ai, r); S.boot.provider = r.provider; renderFields();
  };
  $("#ai-save").onclick = async () => {
    const b = $("#ai-save"); b.disabled = true;
    try { await save(); $("#ai-msg").innerHTML = `<p class="ok-text">Сохранено ✓</p>`; }
    catch (e) { errorBox("#ai-msg", e, () => $("#ai-save").click()); }
    finally { b.disabled = false; }
  };
  $("#ai-check").onclick = async () => {
    const b = $("#ai-check"); b.disabled = true; $("#ai-msg").innerHTML = `<div class="row"><div class="spinner"></div><span class="muted">проверяю…</span></div>`;
    try {
      await save();
      const r = await api("/api/settings/ai/check", { method: "POST", json: { provider: $("#ai-prov").value } });
      $("#ai-msg").innerHTML = r.ok ? `<p class="ok-text">${esc(r.message)}</p>` : `<div class="error-box"><b>${esc(r.message)}</b>${r.details ? `<div class="small mono">${esc(r.details)}</div>` : ""}</div>`;
    } catch (e) { errorBox("#ai-msg", e, () => $("#ai-check").click()); }
    finally { b.disabled = false; }
  };
  $("#pals").onclick = async (e) => {
    const b = e.target.closest(".pal"); if (!b) return;
    applyPalette(b.dataset.p);
    $$(".pal").forEach((x) => x.classList.toggle("on", x === b));
    try { await api("/api/settings/palette", { method: "PUT", json: { palette: b.dataset.p } }); S.boot.palette = b.dataset.p; }
    catch (err) { toast(`Палитра не сохранилась на сервере: ${err.message}`); }
  };
  $("#pw").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target;
    if (f.new1.value !== f.new2.value) { errorBox("#pw-msg", "Новые пароли не совпадают", null); return; }
    try { await api("/api/settings/password", { method: "POST", json: { old: f.old.value, new: f.new1.value } }); f.reset(); $("#pw-msg").innerHTML = `<p class="ok-text">Пароль сменён ✓ Другие устройства вышли.</p>`; }
    catch (err) { errorBox("#pw-msg", err, null, "Пароль не сменён"); }
  };
  const share = Math.min(100, (usage.total / (usage.budget || 1)) * 100);
  $("#usage").innerHTML = `<div class="lbl" style="display:flex;justify-content:space-between"><b>${usage.total.toFixed(2)} ₽ из ${usage.budget} ₽</b><span class="muted small">${usage.month}</span></div>
    <div class="bar" style="margin:8px 0 4px"><i style="width:${share}%"></i></div><p class="muted small">Только показ — ничего не блокируется.</p>
    <label class="field"><span>Бюджет в месяц, ₽</span><div class="row"><input type="number" id="budget" min="0" step="10" value="${usage.budget}" style="flex:1"><button class="btn small secondary" id="budget-save">Сохранить</button></div></label>
    <h3>По типам запросов</h3>${usage.by_purpose.length ? `<div class="kv">${usage.by_purpose.map((p) => `<span>${esc(PURPOSE[p.purpose] || p.name)} <small class="muted">(${p.calls})</small></span><span class="v">${p.rub.toFixed(2)} ₽</span>`).join("")}</div>` : '<p class="muted">Пока ничего.</p>'}
    <h3>По людям</h3>${(usage.by_user || []).length ? `<div class="kv">${usage.by_user.map((u) => `<span>${esc(u.name)}${u.limit !== null && u.limit !== undefined ? ` <small class="muted">(лимит ${u.limit} ₽)</small>` : ""}</span><span class="v">${u.rub.toFixed(2)} ₽</span>`).join("")}</div>` : '<p class="muted">Пока ничего.</p>'}
    <h3>По квизам</h3>${usage.by_quiz.length ? `<div class="kv">${usage.by_quiz.map((q) => `<span>${esc(q.title)}</span><span class="v">${q.rub.toFixed(2)} ₽</span>`).join("")}</div>` : '<p class="muted">Пока ничего.</p>'}`;
  $("#budget-save").onclick = async () => {
    try { await api("/api/settings/budget", { method: "PUT", json: { budget: parseFloat($("#budget").value) || 0 } }); toast("Бюджет сохранён"); }
    catch (e) { toast(e.message); }
  };
}

function bindLogout() {
  $("#logout").onclick = async () => {
    if (!(await confirmModal("Выйти? Чтобы войти снова, понадобятся логин и пароль.", "Выйти"))) return;
    try { await api("/api/logout", { method: "POST" }); } catch (e) { /* всё равно выходим */ }
    showLogin();
  };
}

/** Настройки тестера: только палитра, пароль и свой расход на ИИ. */
async function pageSettingsGuest() {
  const st = await api("/api/settings");
  const u = st.my_usage || {};
  const share = u.limit ? Math.min(100, (u.spent / u.limit) * 100) : 0;
  view.innerHTML = `<h1>Настройки</h1>
    <p class="muted">Ты вошёл как <b>${esc(S.boot.me.name || S.boot.me.login)}</b>.</p>
    <h2>ИИ в этом месяце</h2><div class="panel"><b>${(u.spent || 0).toFixed(2)} ₽ из ${u.limit ?? "∞"} ₽</b>
      <div class="bar" style="margin:8px 0 4px"><i style="width:${share}%"></i></div>
      <p class="muted small">Когда лимит кончится, новые квизы и замена вопросов не будут работать до 1-го числа. Пройти уже созданные квизы можно всегда.</p></div>
    <h2>Палитра</h2><div class="palettes" id="pals">${st.palettes.map((p) => `<button type="button" class="pal ${p === st.palette ? "on" : ""}" data-p="${p}" data-palette="${p}" style="background:var(--bg);color:var(--text)">${esc(PALETTE_NAMES[p] || p)}
      <span class="sw"><i style="background:var(--accent)"></i><i style="background:var(--accent-2)"></i><i style="background:var(--ok)"></i><i style="background:var(--bad)"></i></span></button>`).join("")}</div>
    <h2>Пароль</h2><form class="panel" id="pw" autocomplete="off">
      <label class="field"><span>Старый пароль</span><input type="password" name="old" autocomplete="current-password" required></label>
      <label class="field"><span>Новый пароль</span><input type="password" name="new1" autocomplete="new-password" minlength="6" required></label>
      <label class="field"><span>Ещё раз новый</span><input type="password" name="new2" autocomplete="new-password" minlength="6" required></label>
      <button class="btn block" type="submit">Сменить пароль</button><div id="pw-msg"></div></form>
    <div class="actions" style="margin-top:24px"><button class="btn secondary" id="logout">Выйти</button></div>`;
  bindLogout();
  $("#pals").onclick = async (e) => {
    const b = e.target.closest(".pal"); if (!b) return;
    applyPalette(b.dataset.p); $$(".pal").forEach((x) => x.classList.toggle("on", x === b));
    try { await api("/api/settings/palette", { method: "PUT", json: { palette: b.dataset.p } }); S.boot.palette = b.dataset.p; }
    catch (err) { toast(`Палитра не сохранилась: ${err.message}`); }
  };
  $("#pw").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target;
    if (f.new1.value !== f.new2.value) { errorBox("#pw-msg", "Новые пароли не совпадают", null); return; }
    try { await api("/api/settings/password", { method: "POST", json: { old: f.old.value, new: f.new1.value } }); f.reset(); $("#pw-msg").innerHTML = `<p class="ok-text">Пароль сменён ✓</p>`; }
    catch (err) { errorBox("#pw-msg", err, null, "Пароль не сменён"); }
  };
}

/** Владелец: список тестеров, создание, лимит, выключение, новый пароль, удаление. */
function renderGuests(list) {
  const box = $("#guests");
  const showCreds = (login, password) => {
    const text = `Адрес: ${location.origin}\nЛогин: ${login}\nПароль: ${password}`;
    return `<div class="warn-box">Передай тестеру:<br>адрес: <b>${esc(location.origin)}</b><br>логин: <b class="mono">${esc(login)}</b><br>пароль: <b class="mono">${esc(password)}</b><br>
      <small>Пароль показывается один раз — скопируй сейчас.</small>
      <div class="actions"><button type="button" class="btn small secondary" data-copy="${esc(text)}">Скопировать</button></div></div>`;
  };
  box.innerHTML = `<p class="muted small">У каждого тестера свои квизы и статистика, твои данные ему не видны. ИИ у всех твой — расход виден ниже, в «По людям». Лимит в месяц — на каждого.</p>
    <div id="g-new-creds"></div>
    ${list.length ? `<ul class="list" style="margin:12px 0">${list.map((g) => `<li class="item" data-id="${g.id}">
      <div class="t">${esc(g.name)} <small class="muted mono">${esc(g.login)}</small> ${g.active ? "" : '<span class="status-chip error">выключен</span>'}</div>
      <div class="s"><span>ИИ: ${g.spent.toFixed(2)} из ${g.limit} ₽</span></div>
      <div class="row" style="margin-top:10px">
        <input type="number" min="0" step="10" value="${g.limit}" data-limit style="flex:1;min-width:90px" aria-label="Лимит, ₽">
        <button type="button" class="btn small secondary" data-act="limit">Лимит</button>
        <button type="button" class="btn small secondary" data-act="pass">Новый пароль</button>
        <button type="button" class="btn small secondary" data-act="toggle">${g.active ? "Выключить" : "Включить"}</button>
        <button type="button" class="btn small danger" data-act="del">Удалить</button></div>
      <div data-creds></div></li>`).join("")}</ul>` : '<p class="muted">Тестеров пока нет.</p>'}
    <form id="g-form" class="stack" autocomplete="off" style="margin-top:8px"><b>Новый тестер</b>
      <div class="row"><input type="text" name="name" placeholder="Имя (например, Маша)" style="flex:1;min-width:140px">
        <input type="text" name="login" placeholder="логин латиницей" autocapitalize="off" spellcheck="false" required style="flex:1;min-width:140px"></div>
      <div class="row"><input type="number" name="limit" min="0" step="10" value="50" style="flex:1" aria-label="Лимит в месяц, ₽"><span class="muted small">₽ в месяц на ИИ</span></div>
      <button class="btn block" type="submit">Создать аккаунт</button><div id="g-err"></div></form>`;
  box.onclick = async (e) => {
    const cp = e.target.closest("[data-copy]");
    if (cp) {
      try { await navigator.clipboard.writeText(cp.dataset.copy); toast("Скопировано"); }
      catch (err) { toast("Не скопировалось — выдели текст и скопируй вручную"); }
      return;
    }
    const b = e.target.closest("button[data-act]"); if (!b) return;
    const li = b.closest("li"); const id = li.dataset.id; const g = list.find((x) => x.id === id);
    try {
      if (b.dataset.act === "del") {
        if (!(await confirmModal(`Удалить тестера «${g.name}» и все его квизы и статистику? Вернуть нельзя.`, "Удалить", true))) return;
        await api(`/api/users/${id}`, { method: "DELETE" }); toast("Удалён");
      } else if (b.dataset.act === "pass") {
        if (!(await confirmModal(`Сделать «${g.name}» новый пароль? Старый перестанет работать.`, "Сделать"))) return;
        const r = await api(`/api/users/${id}`, { method: "PATCH", json: { new_password: true } });
        $("[data-creds]", li).innerHTML = showCreds(r.login, r.password); return;
      } else if (b.dataset.act === "toggle") {
        await api(`/api/users/${id}`, { method: "PATCH", json: { active: !g.active } });
      } else if (b.dataset.act === "limit") {
        await api(`/api/users/${id}`, { method: "PATCH", json: { limit: parseFloat($("[data-limit]", li).value) || 0 } }); toast("Лимит сохранён");
      }
      renderGuests(await api("/api/users"));
    } catch (err) { toast(err.message, 6000); }
  };
  $("#g-form").onsubmit = async (e) => {
    e.preventDefault(); const f = e.target;
    try {
      const r = await api("/api/users", { method: "POST", json: { name: f.name.value, login: f.login.value, limit: parseFloat(f.limit.value) } });
      renderGuests(await api("/api/users"));
      $("#g-new-creds").innerHTML = showCreds(r.login, r.password);
    } catch (err) { errorBox("#g-err", err, null, "Не создан"); }
  };
}

// ---------- Старт ----------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
// Внешние ссылки — в новом окне (у приложения с экрана «Домой» нет кнопки «Назад»).
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (a && /^https?:/i.test(a.getAttribute("href")) && new URL(a.href).origin !== location.origin) { a.target = "_blank"; a.rel = "noopener"; }
});
boot();
