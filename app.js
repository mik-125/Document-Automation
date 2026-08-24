const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

const EMPTY_STATE = {
  classes: [],
  students: [],
  files: [],
  issues: [],
  periods: [],
  reports: [],
  class_templates: [],
  data_status: [],
  sheet_sources: {},
  connections: {},
  supported_placeholders: [],
  selected_period_id: "",
  reporting_period: { id: "", label: "", start: "", end: "" },
};

let state = structuredClone(EMPTY_STATE);
let loadingCount = 0;
let toastTimer;
let selectedReportClass = "";
let pendingExampleClass = "";
let generationBusy = false;

function normalizeState(payload = {}) {
  return {
    ...structuredClone(EMPTY_STATE),
    ...payload,
    reporting_period: { ...EMPTY_STATE.reporting_period, ...(payload.reporting_period || {}) },
    connections: payload.connections || {},
    sheet_sources: payload.sheet_sources || {},
  };
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#039;", '"': "&quot;",
  })[character]);
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—";
}

function formatDateTime(value) {
  if (!value) return "Never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function initials(name) {
  return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join("").toUpperCase();
}

function classById(id) { return state.classes.find(item => item.id === id); }
function periodById(id) { return state.periods.find(item => item.id === id); }
function studentsForClass(id) { return state.students.filter(item => item.class_id === id); }
function templateForClass(id) { return state.class_templates.find(item => item.class_id === id); }
function statusForClass(id, periodId = "") {
  return state.data_status.find(item => item.class_id === id && (!periodId || item.period_id === periodId)) || {};
}

function periodOptions(selected = "", includeBlank = false) {
  return `${includeBlank ? '<option value="">Choose a reporting period</option>' : ""}${state.periods.map(period => `<option value="${period.id}" ${period.id === selected ? "selected" : ""}>${escapeHtml(period.label)} · ${escapeHtml(period.start_date)}–${escapeHtml(period.end_date)}</option>`).join("")}`;
}

function setLoading(active) {
  loadingCount += active ? 1 : -1;
  loadingCount = Math.max(0, loadingCount);
  $("#loading-bar").classList.toggle("hidden", loadingCount === 0);
}

function beginGeneration() {
  if (generationBusy) {
    toast("A report is already being generated. Please wait.", "error");
    return false;
  }
  generationBusy = true;
  document.body.classList.add("generation-busy");
  $("#reports").setAttribute("aria-busy", "true");
  $$('[data-preview-student], [data-download-student], [data-download-class], #preview-report, #download-class-zip, #download-all').forEach(button => { button.disabled = true; });
  return true;
}

function endGeneration() {
  generationBusy = false;
  document.body.classList.remove("generation-busy");
  $("#reports").removeAttribute("aria-busy");
  renderReports();
}

async function api(url, options = {}) {
  setLoading(true);
  try {
    const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const error = new Error(payload?.error || `Request failed (${response.status})`);
      error.code = payload?.code || "request_failed";
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  } finally {
    setLoading(false);
  }
}

async function requestBinary(url, payload = {}) {
  setLoading(true);
  try {
    const options = payload === null ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    };
    const response = await fetch(url, options);
    if (!response.ok) {
      const contentType = response.headers.get("content-type") || "";
      const error = contentType.includes("application/json") ? await response.json() : null;
      throw new Error(error?.error || `Request failed (${response.status})`);
    }
    return response.blob();
  } finally {
    setLoading(false);
  }
}

function saveBlob(blob, filename) {
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = String(filename || "download").replace(/[\\/]/g, "-");
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
}

async function downloadGenerated(url, payload, filename) {
  if (!beginGeneration()) return false;
  try {
    saveBlob(await requestBinary(url, payload), filename);
    return true;
  } catch (error) {
    toast(error.message, "error");
    return false;
  } finally {
    endGeneration();
  }
}

function showArchiveResult(result) {
  const failures = result.failed || [];
  if (!failures.length) {
    toast(`${result.generated_count} reports generated and downloaded.`);
    return;
  }
  const rows = failures.map(item => `<li><strong>${escapeHtml(item.student)}</strong><span>${escapeHtml(item.class_name || "")}</span><p>${escapeHtml(item.error)}</p></li>`).join("");
  openModal(`<span class="eyebrow">GENERATION RESULT</span><h2>${result.generated_count || 0} generated · ${result.failed_count || failures.length} failed</h2><p>${result.generated_count ? "The successful PDFs were downloaded. Review the students that need attention below." : "No ZIP was downloaded because every report failed."}</p><ul class="generation-errors">${rows}</ul><div class="modal-footer"><button class="primary-button" data-close-modal>Done</button></div>`);
}

async function generateArchive(url, fallbackFilename) {
  if (!beginGeneration()) return;
  try {
    let result;
    try {
      result = await api(url, { method: "POST", body: "{}" });
    } catch (error) {
      if (error.code !== "all_reports_failed" || !error.payload) {
        toast(error.message, "error");
        return;
      }
      showArchiveResult(error.payload);
      return;
    }
    if (result.download_url) {
      try {
        saveBlob(
          await requestBinary(result.download_url, null),
          result.filename || fallbackFilename,
        );
      } catch (error) {
        toast(error.message, "error");
        return;
      }
    }
    showArchiveResult(result);
    await loadState();
  } finally {
    endGeneration();
  }
}

async function previewGenerated(studentId, periodId) {
  if (!beginGeneration()) return;
  let previewWindow;
  try {
    previewWindow = window.open("about:blank", "_blank");
    if (!previewWindow) return toast("Allow pop-ups to open the PDF preview.", "error");
    previewWindow.opener = null;
    previewWindow.document.title = "Preparing report preview…";
    const blob = await requestBinary(`/api/reports/student/${studentId}/preview`, { period_id: periodId || "" });
    const objectUrl = URL.createObjectURL(blob);
    previewWindow.location.replace(objectUrl);
    setTimeout(() => URL.revokeObjectURL(objectUrl), 120_000);
  } catch (error) {
    previewWindow?.close();
    toast(error.message, "error");
  } finally {
    endGeneration();
  }
}

function toast(message, type = "success") {
  const element = $("#toast");
  clearTimeout(toastTimer);
  element.textContent = message;
  element.className = `toast ${type}`;
  toastTimer = setTimeout(() => element.classList.add("hidden"), 4200);
}

function openModal(html) {
  $("#modal").innerHTML = html;
  $("#modal-backdrop").classList.remove("hidden");
  requestAnimationFrame(() => $("#modal").querySelector("input, select, button")?.focus());
}

function closeModal() {
  $("#modal-backdrop").classList.add("hidden");
  $("#modal").innerHTML = "";
}

function emptyState(title, copy, large = false) {
  return `<div class="empty-state ${large ? "large" : ""}"><span>◇</span><strong>${escapeHtml(title)}</strong><p>${escapeHtml(copy)}</p></div>`;
}

const PAGE_COPY = {
  inbox: ["REPORT INPUTS", "Data Inbox"],
  templates: ["CLASS DESIGNS", "Templates"],
  reports: ["GENERATED OUTPUT", "Reports"],
  connections: ["API ACCESS", "Connections"],
};

function go(page) {
  if (!PAGE_COPY[page]) page = "inbox";
  $$(".page").forEach(element => element.classList.toggle("active", element.id === page));
  $$('[data-nav]').forEach(element => element.classList.toggle("active", element.dataset.nav === page));
  $("#page-kicker").textContent = PAGE_COPY[page][0];
  $("#page-title").textContent = PAGE_COPY[page][1];
  history.replaceState(null, "", `#${page}`);
  $("#sidebar").classList.remove("open");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderPeriods() {
  const global = $("#global-period-select");
  global.innerHTML = state.periods.length ? periodOptions(state.selected_period_id) : '<option value="">No period yet</option>';
  global.disabled = !state.periods.length;
}

function renderSourceStatuses() {
  const assignmentReady = state.data_status.some(item => item.assignment_count > 0);
  const testsReady = state.data_status.some(item => item.weekly_test_count > 0);
  const commentsReady = state.data_status.some(item => item.comment_count > 0);
  const statuses = [
    ["#assignment-source-status", assignmentReady, assignmentReady ? "Imported" : "Start here"],
    ["#test-source-status", testsReady, testsReady ? "Imported" : "After assignment"],
    ["#comments-source-status", commentsReady, commentsReady ? "Imported" : "Last step"],
  ];
  statuses.forEach(([selector, ready, label]) => {
    const element = $(selector);
    element.textContent = label;
    element.className = `status-pill ${ready ? "" : "neutral"}`;
  });
}

function renderReadiness() {
  if (!state.classes.length) {
    $("#data-readiness").innerHTML = emptyState("No academy data yet", "Import the Assignment file or Sheet to create classes and students.", true);
    return;
  }
  const cell = (ready, count, total, label) => `<div class="check-cell ${ready ? "done" : ""}"><span class="check-mark">${ready ? "✓" : "·"}</span><span>${count}/${total} ${escapeHtml(label)}</span></div>`;
  $("#data-readiness").innerHTML = `<div class="readiness-row header"><span>Class and period</span><span>Assignment</span><span>Weekly Test</span><span>Comments</span><span>Template</span></div>${state.classes.map(classItem => {
    const status = statusForClass(classItem.id, classItem.selected_period_id);
    const total = status.student_count || 0;
    return `<div class="readiness-row"><div class="class-cell"><strong>${escapeHtml(classItem.name)}</strong><small>${escapeHtml(classItem.selected_period_label || "No reporting period")}</small></div>${cell(status.assignment_ready, status.assignment_count || 0, total, "ready")}${cell(status.weekly_test_ready, status.weekly_test_count || 0, total, "ready")}${cell(status.comments_ready, status.comment_count || 0, total, "ready")}${cell(status.template_ready, status.template_ready ? 1 : 0, 1, "connected")}</div>`;
  }).join("")}`;
}

function renderFiles() {
  $("#upload-count").textContent = `${state.files.length} import${state.files.length === 1 ? "" : "s"}`;
  $("#inbox-badge").textContent = state.issues.length;
  if (!state.files.length) {
    $("#file-list").innerHTML = emptyState("Nothing imported", "Your product starts empty. Import Assignment data when you are ready.");
    return;
  }
  const labels = {
    assignment_csv: "Assignment CSV",
    assignment_sheet: "Assignment · Google Sheets",
    weekly_test_csv: "Weekly Test CSV",
    weekly_test_sheet: "Weekly Test · Google Sheets",
    comments_csv: "Comments CSV",
    comments_sheet: "Comments · Google Sheets",
    comments_pdf: "Comments PDF",
    example_report: "Example report",
  };
  $("#file-list").innerHTML = state.files.map(file => `<div class="file-row"><span class="file-icon">${file.source_provider === "google_sheets" ? "API" : (file.name.split(".").pop() || "FILE").slice(0, 4).toUpperCase()}</span><div class="file-info"><strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(labels[file.kind] || file.kind)} · ${escapeHtml(file.summary)}${file.period_label ? ` · ${escapeHtml(file.period_label)}` : ""}</small></div><div class="file-meta"><span class="status-pill ${file.state === "review" ? "warn" : ""}">${file.state === "review" ? "Review" : "Imported"}</span><time>${escapeHtml(formatDateTime(file.created_at))}</time></div></div>`).join("");
}

function renderIssues() {
  $("#issue-count").textContent = state.issues.length;
  if (!state.issues.length) {
    $("#issue-list").innerHTML = '<div class="review-empty"><span>✓</span><strong>All clear</strong><p>No uncertain student matches are waiting.</p></div>';
    return;
  }
  $("#issue-list").innerHTML = state.issues.map(issue => `<button class="issue-item" data-issue="${issue.id}"><strong>${escapeHtml(issue.subject_name)}</strong><span>${issue.candidates.length ? `${issue.candidates.length} possible matches` : "No exact match"} · ${escapeHtml(issue.source_name)}</span><b>Review →</b></button>`).join("");
}

function renderTemplates() {
  const body = $("#templates-table");
  if (!state.classes.length) {
    body.innerHTML = `<tr><td colspan="6">${emptyState("No classes yet", "Import Assignment data first; classes appear here automatically.", true)}</td></tr>`;
    return;
  }
  body.innerHTML = state.classes.map(classItem => {
    const template = templateForClass(classItem.id) || {};
    const placeholders = template.placeholders || [];
    const statusClass = template.status === "ready" ? "" : template.status === "missing" ? "neutral" : "warn";
    const statusLabel = template.status === "ready" ? "Ready" : template.status === "needs_test" ? "Retest" : template.status === "needs_mapping" ? "Needs fields" : "Not connected";
    return `<tr><td><strong>${escapeHtml(classItem.name)}</strong><small>${escapeHtml(classItem.teacher || "Teacher not set")}</small></td><td class="template-name">${template.document_id ? `<a href="${escapeHtml(template.url)}" target="_blank" rel="noopener">${escapeHtml(template.name || "Google Docs template")}</a><small>Last checked ${escapeHtml(formatDateTime(template.last_tested_at))}</small>` : '<strong>No Google Doc</strong><small>Paste a template link to connect</small>'}</td><td><strong>${template.example_count || 0} PDF${template.example_count === 1 ? "" : "s"}</strong><small>Reference only</small></td><td><div class="placeholder-list">${placeholders.length ? placeholders.slice(0, 5).map(name => `<span class="placeholder-chip">{{${escapeHtml(name)}}}</span>`).join("") : '<span class="muted">No fields checked</span>'}</div></td><td><span class="status-pill ${statusClass}">${statusLabel}</span>${template.missing_required?.length ? `<small>Missing ${escapeHtml(template.missing_required.join(", "))}</small>` : template.error ? `<small>${escapeHtml(template.error)}</small>` : ""}</td><td><div class="table-actions"><button class="table-action" data-template-connect="${classItem.id}">${template.document_id ? "Replace" : "Connect"}</button><button class="table-action" data-template-test="${classItem.id}" ${template.document_id ? "" : "disabled"}>Test</button><button class="table-action" data-template-example="${classItem.id}">Add example</button><button class="table-action" data-template-map="${classItem.id}">Fields</button>${template.document_id ? `<button class="table-action" data-template-disconnect="${classItem.id}">Disconnect</button>` : ""}</div></td></tr>`;
  }).join("");
}

function reportReason(student) {
  if (!student.assignment_ready) return "Assignment missing";
  if (!student.weekly_test_ready) return "Weekly Test missing";
  if (!student.comments_ready) return "Comment missing";
  if (!student.template_ready) return "Template missing";
  if (!student.google_ready) return "Google output missing";
  return "Ready";
}

function renderReports() {
  if (!selectedReportClass || !classById(selectedReportClass)) selectedReportClass = state.classes[0]?.id || "";
  const selectedClass = classById(selectedReportClass);
  const selectedPeriod = selectedClass?.selected_period_id || state.selected_period_id;
  $("#report-class-select").innerHTML = `<option value="">Choose a class</option>${state.classes.map(item => `<option value="${item.id}" ${item.id === selectedReportClass ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}`;
  $("#report-period-select").innerHTML = state.periods.length ? periodOptions(selectedPeriod, true) : '<option value="">No periods yet</option>';
  $("#report-period-select").disabled = !selectedReportClass || !state.periods.length;
  const status = selectedClass ? statusForClass(selectedClass.id, selectedPeriod) : {};
  const checks = [
    ["Assignment", status.assignment_ready], ["Weekly Test", status.weekly_test_ready], ["Comments", status.comments_ready], ["Template", status.template_ready], ["Google output", status.google_ready],
  ];
  $("#report-checklist").innerHTML = checks.map(([label, ready]) => `<div class="check-card ${ready ? "done" : ""}"><span>${ready ? "✓" : "·"}</span><b>${escapeHtml(label)}</b></div>`).join("");
  const selectedStudents = selectedClass ? studentsForClass(selectedClass.id) : [];
  const firstReady = selectedStudents.find(student => student.report_ready);
  const previewButton = $("#preview-report");
  previewButton.classList.toggle("disabled", !firstReady);
  previewButton.disabled = !firstReady;
  previewButton.dataset.studentId = firstReady?.id || "";
  previewButton.dataset.periodId = firstReady?.period_id || "";
  const classZip = $("#download-class-zip");
  classZip.classList.toggle("disabled", !firstReady);
  classZip.disabled = !firstReady;
  classZip.dataset.classId = firstReady ? selectedClass.id : "";
  const readyCount = state.students.filter(student => student.report_ready).length;
  $("#download-all").classList.toggle("disabled", readyCount === 0);
  $("#download-all").disabled = readyCount === 0;

  if (!state.classes.length) {
    $("#reports-by-class").innerHTML = emptyState("No reports to prepare", "Import Assignment, Weekly Test, and Comments data first.", true);
  } else {
    $("#reports-by-class").innerHTML = state.classes.map(classItem => {
      const students = studentsForClass(classItem.id);
      const ready = students.filter(student => student.report_ready).length;
      const rows = students.map(student => {
        const exam = student.latest_exam;
        const attempted = Number(student.homework_completed || 0) + Number(student.homework_incomplete || 0);
        const missed = attempted || Number(student.homework_excluded || 0) ? formatNumber(student.homework_incomplete || 0) : student.homework_percent == null ? "—" : "Unavailable";
        return `<div class="report-student-row"><div class="student-name"><span class="student-avatar">${escapeHtml(initials(student.name))}</span><span><strong>${escapeHtml(student.name)}</strong><small>${escapeHtml(student.school || "School missing")} · ${escapeHtml(student.grade || "Grade missing")}</small></span></div><div class="report-data"><span>Assignments missed<b>${missed}</b></span><span>Weekly Test<b>${exam ? `${formatNumber(exam.score)}${exam.max_score === 100 ? "%" : exam.max_score == null ? "" : `/${formatNumber(exam.max_score)}`}` : "—"}</b></span></div><span class="status-pill ${student.report_ready ? "" : "warn"}">${escapeHtml(reportReason(student))}</span><div class="row-actions">${student.report_ready ? `<button class="table-action" data-preview-student="${student.id}" data-period-id="${student.period_id || ""}">Preview</button><button class="table-action" data-download-student="${student.id}" data-period-id="${student.period_id || ""}" data-filename="${escapeHtml(student.name)}-report.pdf">Download PDF</button>` : '<button class="table-action disabled">Needs data</button>'}</div></div>`;
      }).join("");
      return `<section class="report-class-section"><header><div><span class="eyebrow">${escapeHtml(classItem.subject || "CLASS REPORTS")}</span><h2>${escapeHtml(classItem.name)}</h2><p>${ready} of ${students.length} students ready · ${escapeHtml(classItem.selected_period_label || "No period")}</p></div><button class="secondary-button ${ready ? "" : "disabled"}" data-download-class="${classItem.id}" data-filename="${escapeHtml(classItem.name)}-reports.zip" ${ready ? "" : "disabled"}>Download class ZIP</button></header>${rows || emptyState("No students in this class", "Import an Assignment file containing this class.")}</section>`;
    }).join("");
  }
  $("#report-history-count").textContent = `${state.reports.length} report${state.reports.length === 1 ? "" : "s"}`;
  $("#report-history-table").innerHTML = state.reports.length ? state.reports.map(report => `<tr><td>${escapeHtml(report.student_name)}</td><td>${escapeHtml(report.class_name)}</td><td>${escapeHtml(report.period_label)}</td><td>Revision ${report.revision}</td><td>${escapeHtml(formatDateTime(report.generated_at))}</td><td><a class="table-action" href="/api/reports/archive/${report.id}">Download</a></td></tr>`).join("") : `<tr><td colspan="6">${emptyState("No report history", "Generated PDFs will appear here.")}</td></tr>`;
}

function renderConnections() {
  const sheets = state.connections.google_sheets || {};
  const docs = state.connections.google_docs || {};
  const makeedu = state.connections.makeedu || {};
  const anyConnected = sheets.status === "connected" || docs.status === "connected";
  $("#nav-connection-dot").className = `connection-dot ${anyConnected ? "connected" : (sheets.error || docs.error ? "error" : "")}`;
  const status = connection => {
    if (connection.status === "connected") return ["Connected", ""];
    if (connection.status === "configured") return ["Configured", "warn"];
    if (connection.status === "needs_auth") return ["Sign in needed", "warn"];
    if (connection.status === "needs_setup") return ["Folder needed", "warn"];
    if (connection.status === "error") return ["Connection error", "warn"];
    if (connection.status === "unavailable") return ["Unavailable", "neutral"];
    return ["Disconnected", "neutral"];
  };
  const card = ({ key, short, title, copy, connection, primary }) => {
    const [label, style] = status(connection);
    const disabled = connection.available === false;
    const primaryLabel = connection.authorized ? primary : "Connect Google account";
    const actions = disabled
      ? `<a class="secondary-button button-link" href="${escapeHtml(connection.support_url || "https://makeedu.co.kr/faq.jsp")}" target="_blank" rel="noopener">Contact MakeEdu</a>`
      : `<button class="primary-button" data-connection-action="configure" data-service="${key}">${primaryLabel}</button><button class="secondary-button" data-connection-action="test" data-service="${key}" ${connection.authorized ? "" : "disabled"}>Test connection</button>${connection.authorized ? `<button class="secondary-button" data-connection-action="disconnect" data-service="${key}">Disconnect</button>` : ""}`;
    return `<article class="connection-card"><div class="connection-card-head"><span class="connection-logo">${short}</span><span class="status-pill ${style}">${label}</span></div><h2>${title}</h2><p>${copy}</p><div class="connection-meta"><span>Account<b>${escapeHtml(connection.account_name || "Not connected")}</b></span><span>${key === "google_docs" ? "Output folder" : "Last sync"}<b>${escapeHtml(key === "google_docs" ? (connection.resource_name || "Not configured") : formatDateTime(connection.last_synced_at))}</b></span></div>${connection.error ? `<p class="connection-error">${escapeHtml(connection.error)}</p>` : ""}<div class="connection-actions">${actions}</div></article>`;
  };
  $("#connections-grid").innerHTML = [
    card({ key: "makeedu", short: "ME", title: "MakeEdu", copy: "Direct synchronization remains blocked until MakeEdu supplies official API documentation and academy access.", connection: makeedu, primary: "Configure" }),
    card({ key: "google_sheets", short: "GS", title: "Google Sheets", copy: "Read Assignment, Weekly Test, and Comments data from Sheets in your connected Google account.", connection: sheets, primary: "Reconnect account" }),
    card({ key: "google_docs", short: "GD", title: "Google Docs + Drive", copy: "Copy class templates, replace controlled fields, and save PDFs in your My Drive.", connection: docs, primary: docs.status === "needs_setup" ? "Create output folder" : "Reconnect account" }),
  ].join("");
}

function render() {
  renderPeriods();
  renderSourceStatuses();
  renderReadiness();
  renderFiles();
  renderIssues();
  renderTemplates();
  renderReports();
  renderConnections();
}

async function loadState() {
  try {
    state = normalizeState(await api("/api/state"));
    render();
  } catch (error) {
    toast(error.message, "error");
    $(".server-status").classList.add("error");
    $(".server-status").innerHTML = "<i></i> Local service unavailable";
  }
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  return btoa(binary);
}

async function chooseAssignmentCsv(file) {
  if (!file) return;
  const text = await file.text();
  try {
    const inspection = await api("/api/csv/inspect", { method: "POST", body: JSON.stringify({ text }) });
    const dates = inspection.dates.map(item => `<option value="${item.iso}">${escapeHtml(item.label)} · ${item.iso}</option>`).join("");
    openModal(`<span class="eyebrow">ASSIGNMENT CSV</span><h2>Select the reporting week</h2><p><strong>${escapeHtml(file.name)}</strong> contains ${inspection.record_count} student-class records across ${inspection.class_count} classes.</p><form id="assignment-import-form"><label><span>Period name</span><input id="assignment-period-name" value="Week ${state.periods.length + 1}" required /></label><div class="form-grid"><label><span>Start date</span><select id="assignment-period-start" required>${dates}</select></label><label><span>End date</span><select id="assignment-period-end" required>${dates}</select></label></div><div class="calculation-note">Existing academy rule: A and B are completed, F is incomplete, and X or blank is excluded.</div><div class="modal-footer"><button type="button" class="secondary-button" data-close-modal>Cancel</button><button class="primary-button">Import Assignment</button></div></form>`);
    $("#assignment-period-end").value = inspection.dates.at(-1).iso;
    $("#assignment-import-form").onsubmit = async event => {
      event.preventDefault();
      try {
        const payload = { filename: file.name, text, period_label: $("#assignment-period-name").value.trim(), period_start: $("#assignment-period-start").value, period_end: $("#assignment-period-end").value };
        let result;
        try {
          result = await api("/api/csv/import", { method: "POST", body: JSON.stringify(payload) });
        } catch (error) {
          if (error.code !== "replacement_confirmation_required" || !confirm(`${error.message}\n\nReplace the existing Assignment data?`)) throw error;
          result = await api("/api/csv/import", { method: "POST", body: JSON.stringify({ ...payload, confirm_replace: true }) });
        }
        state = normalizeState(result.state);
        closeModal(); render(); toast(result.summary);
      } catch (error) { toast(error.message, "error"); }
    };
  } catch (error) {
    try {
      const preview = await api("/api/tabular/preview", { method: "POST", body: JSON.stringify({ text }) });
      openMappingModal({ kind: "assignment", source: "manual", filename: file.name, text, preview });
    } catch (fallbackError) { toast(fallbackError.message || error.message, "error"); }
  } finally {
    $("#assignment-file-input").value = "";
  }
}

const FIELD_LABELS = {
  student_id: "Student ID", student_name: "Student name", class_name: "Class name", grade: "Grade or year", school: "School", teacher: "Teacher",
  assignment_completed: "Assignments completed", assignment_total: "Total assignments", assignment_percent: "Assignment percent",
  weekly_test_label: "Test label", weekly_test_score: "Weekly test percentage", weekly_test_max: "Maximum score", comment: "Teacher comment", reporting_week: "Reporting week",
};

function fieldsForKind(kind) {
  const base = ["student_name", "class_name", "school"];
  if (kind === "assignment") return base;
  if (kind === "weekly_test") return [...base, "weekly_test_score"];
  return [...base, "comment"];
}

function requiredFields(kind) {
  return kind === "weekly_test" ? ["student_name", "class_name", "weekly_test_score"] : kind === "comments" ? ["student_name", "class_name", "comment"] : ["student_name", "class_name"];
}

function mappingSelect(field, preview, selected, required) {
  const options = preview.headers.map((header, index) => {
    const examples = [...new Set(preview.sample_rows.map(row => String(row[index] || "").trim()).filter(Boolean))]
      .slice(0, 2)
      .map(value => value.length > 30 ? `${value.slice(0, 29)}…` : value)
      .join(", ");
    const label = examples ? `${header} — ${examples}` : header;
    return `<option value="${escapeHtml(header)}" ${header === selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
  return `<label class="${required ? "required" : ""}"><span>${escapeHtml(FIELD_LABELS[field])}</span><select data-map-field="${field}"><option value="">Not used</option>${options}</select></label>`;
}

function sampleTable(preview) {
  return `<div class="sample-table"><table><thead><tr>${preview.headers.map(header => `<th>${escapeHtml(header)}</th>`).join("")}</tr></thead><tbody>${preview.sample_rows.slice(0, 4).map(row => `<tr>${preview.headers.map((_, index) => `<td>${escapeHtml(row[index] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function openMappingModal(context) {
  const { kind, preview } = context;
  if (kind !== "assignment" && !state.periods.length) {
    toast("Import Assignment data first to create a reporting period.", "error");
    return;
  }
  const saved = state.sheet_sources[kind]?.mapping || {};
  const suggested = { ...(preview.suggested_mapping || {}), ...saved };
  const required = requiredFields(kind);
  const fields = fieldsForKind(kind);
  const periodValue = state.selected_period_id || state.periods[0]?.id || "";
  const newPeriodSelected = kind === "assignment" && !periodValue;
  const createPeriodOption = kind === "assignment" ? '<option value="">Create a new reporting period</option>' : "";
  const datedColumns = preview.headers.filter(header => /^(?:\d{4}\/)?\d{1,2}\/\d{1,2}$/.test(header));
  const assignmentNote = kind === "assignment" ? `<div class="safety-note">Assignments missed will be counted automatically from <strong>F</strong> across ${datedColumns.length} dated column${datedColumns.length === 1 ? "" : "s"}. A, B, X, blanks, and other text are not counted as missed.</div>` : "";
  openModal(`<span class="eyebrow">${context.source === "google" ? "GOOGLE SHEETS" : "CSV"} · ${escapeHtml(kind.replace("_", " "))}</span><h2>Confirm column mapping</h2><p>${preview.row_count} rows found. Choose the column for each field; examples appear after each column name.</p>${sampleTable(preview)}<form id="mapping-form"><div class="mapping-grid">${fields.map(field => mappingSelect(field, preview, suggested[field] || "", required.includes(field))).join("")}</div>${assignmentNote}<label><span>Reporting period</span><select id="mapping-period">${createPeriodOption}${periodOptions(periodValue)}</select></label><div class="form-grid ${newPeriodSelected ? "" : "hidden"}" id="new-period-fields"><label><span>Period name</span><input id="mapping-period-label" value="Week ${state.periods.length + 1}" /></label><span></span><label><span>Start date</span><input id="mapping-period-start" type="date" /></label><label><span>End date</span><input id="mapping-period-end" type="date" /></label></div><div class="modal-footer"><button type="button" class="secondary-button" data-close-modal>Cancel</button><button class="primary-button">Import ${escapeHtml(kind.replace("_", " "))}</button></div></form>`);
  const periodSelect = $("#mapping-period");
  if (!newPeriodSelected && periodValue) periodSelect.value = periodValue;
  const toggleNewPeriod = () => $("#new-period-fields").classList.toggle("hidden", kind !== "assignment" || Boolean(periodSelect.value));
  if (kind === "assignment") periodSelect.onchange = toggleNewPeriod;
  toggleNewPeriod();
  $("#mapping-form").onsubmit = async event => {
    event.preventDefault();
    const mapping = Object.fromEntries($$("[data-map-field]").filter(select => select.value).map(select => [select.dataset.mapField, select.value]));
    const missing = required.filter(field => !mapping[field]);
    if (missing.length) return toast(`Map ${FIELD_LABELS[missing[0]]} before importing.`, "error");
    if (kind !== "assignment" && !periodSelect.value) return toast("Choose an existing reporting period.", "error");
    const period = periodSelect.value ? { period_id: periodSelect.value } : { period_label: $("#mapping-period-label").value.trim(), period_start: $("#mapping-period-start").value, period_end: $("#mapping-period-end").value };
    try {
      const url = context.source === "google" ? "/api/google/sheets/sync" : "/api/tabular/import";
      const payload = context.source === "google"
        ? { kind, spreadsheet: context.spreadsheet, worksheet: context.worksheet, header_row: context.header_row, mapping, ...period }
        : { kind, filename: context.filename, text: context.text, mapping, ...period };
      let result;
      try {
        result = await api(url, { method: "POST", body: JSON.stringify(payload) });
      } catch (error) {
        if (error.code !== "replacement_confirmation_required" || !confirm(`${error.message}\n\nReplace the existing data?`)) throw error;
        result = await api(url, { method: "POST", body: JSON.stringify({ ...payload, confirm_replace: true }) });
      }
      state = normalizeState(result.state);
      closeModal(); render(); toast(result.summary);
    } catch (error) { toast(error.message, "error"); }
  };
}

async function chooseTabularFile(file, kind) {
  if (!file) return;
  try {
    const text = await file.text();
    const preview = await api("/api/tabular/preview", { method: "POST", body: JSON.stringify({ text }) });
    openMappingModal({ kind, source: "manual", filename: file.name, text, preview });
  } catch (error) { toast(error.message, "error"); }
}

function openGoogleSheetImport(kind) {
  const connection = state.connections.google_sheets || {};
  if (!connection.configured || connection.status === "disconnected") {
    toast("Connect and test your Google account first.", "error"); go("connections"); return;
  }
  if (kind !== "assignment" && !state.periods.length) return toast("Import Assignment data first.", "error");
  const saved = state.sheet_sources[kind] || {};
  openModal(`<span class="eyebrow">GOOGLE SHEETS · ${escapeHtml(kind.replace("_", " "))}</span><h2>Choose the worksheet</h2><p>Paste a Sheet that <strong>${escapeHtml(connection.account_name || "your connected account")}</strong> can open. Only mapped columns will be imported.</p><form id="sheet-preview-form"><label><span>Google Sheets URL or ID</span><input id="sheet-url" value="${escapeHtml(saved.spreadsheet_id || "")}" placeholder="https://docs.google.com/spreadsheets/d/..." required /></label><div class="form-grid"><label><span>Worksheet name</span><input id="sheet-tab" value="${escapeHtml(saved.worksheet || "")}" placeholder="Leave blank to use the first tab" /></label><label><span>Header row</span><input id="sheet-header-row" type="number" min="1" step="1" value="${escapeHtml(saved.header_row || 1)}" required /></label></div><div class="safety-note">Header row is the spreadsheet row containing labels such as Student name and Class name.</div><div class="modal-footer"><button type="button" class="secondary-button" data-close-modal>Cancel</button><button class="primary-button">Preview columns</button></div></form>`);
  $("#sheet-preview-form").onsubmit = async event => {
    event.preventDefault();
    const spreadsheet = $("#sheet-url").value.trim();
    const worksheet = $("#sheet-tab").value.trim();
    const header_row = Number.parseInt($("#sheet-header-row").value, 10);
    try {
      const preview = await api("/api/google/sheets/preview", { method: "POST", body: JSON.stringify({ spreadsheet, worksheet, header_row }) });
      openMappingModal({ kind, source: "google", spreadsheet, worksheet: preview.worksheet, header_row: preview.header_row, preview });
    } catch (error) { toast(error.message, "error"); }
  };
}

async function chooseCommentsFile(file) {
  if (!file) return;
  try {
    if (/\.pdf$/i.test(file.name) || file.type === "application/pdf") {
      openCommentsPdfModal(file);
    } else {
      await chooseTabularFile(file, "comments");
    }
  } finally {
    $("#comments-file-input").value = "";
  }
}

function openCommentsPdfModal(file) {
  if (!state.classes.length || !state.periods.length) return toast("Import Assignment and Weekly Test data first.", "error");
  openModal(`<span class="eyebrow">COMMENTS PDF</span><h2>Identify this class file</h2><p>One PDF must represent one class and one reporting period. Uncertain names go to the review queue.</p><form id="comments-pdf-form"><div class="form-grid"><label><span>Class</span><select id="comments-class" required><option value="">Choose a class</option>${state.classes.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")}</select></label><label><span>Reporting period</span><select id="comments-period" required><option value="">Choose a period</option>${periodOptions()}</select></label></div><label><span>Comment label</span><input id="comments-label" value="${escapeHtml(file.name.replace(/\.pdf$/i, ""))}" required /></label><div class="modal-footer"><button type="button" class="secondary-button" data-close-modal>Cancel</button><button class="primary-button">Match comments</button></div></form>`);
  $("#comments-class").onchange = event => {
    const selected = classById(event.target.value);
    if (selected?.selected_period_id) $("#comments-period").value = selected.selected_period_id;
  };
  $("#comments-pdf-form").onsubmit = async event => {
    event.preventDefault();
    try {
      const pdfBase64 = arrayBufferToBase64(await file.arrayBuffer());
      const payload = { filename: file.name, class_id: $("#comments-class").value, period_id: $("#comments-period").value, label: $("#comments-label").value.trim(), pdf_base64: pdfBase64 };
      let result;
      try {
        result = await api("/api/comments/pdf/import", { method: "POST", body: JSON.stringify(payload) });
      } catch (error) {
        if (error.code !== "replacement_confirmation_required" || !confirm(`${error.message}\n\nReplace the existing comments?`)) throw error;
        result = await api("/api/comments/pdf/import", { method: "POST", body: JSON.stringify({ ...payload, confirm_replace: true }) });
      }
      state = normalizeState(result.state);
      closeModal(); render(); toast(result.summary, state.issues.length ? "warning" : "success");
    } catch (error) { toast(error.message, "error"); }
  };
}

function reviewIssue(issueId) {
  const issue = state.issues.find(item => item.id === issueId);
  if (!issue) return;
  const candidateIds = new Set(issue.candidates.map(item => item.id));
  const students = [...studentsForClass(issue.class_id)].sort((a, b) => Number(candidateIds.has(b.id)) - Number(candidateIds.has(a.id)) || a.name.localeCompare(b.name));
  openModal(`<span class="eyebrow">MANUAL REVIEW</span><h2>Match ${escapeHtml(issue.subject_name)}</h2><p>Choose the correct student inside <strong>${escapeHtml(classById(issue.class_id)?.name || "this class")}</strong>.</p><form id="issue-form"><label><span>Student record</span><select id="issue-student" required><option value="">Choose using school and grade</option>${students.map(student => `<option value="${student.id}">${candidateIds.has(student.id) ? "Possible match · " : ""}${escapeHtml(student.name)} · ${escapeHtml(student.school)} · ${escapeHtml(student.grade)}</option>`).join("")}</select></label><div class="source-note"><span>Source</span><strong>${escapeHtml(issue.source_name)}</strong><small>${escapeHtml(issue.payload.label || "Teacher comment")}</small></div><div class="modal-footer"><button type="button" class="secondary-button" data-close-modal>Cancel</button><button class="primary-button">Confirm match</button></div></form>`);
  $("#issue-form").onsubmit = async event => {
    event.preventDefault();
    try {
      const payload = { student_id: $("#issue-student").value };
      let result;
      try {
        result = await api(`/api/issues/${issue.id}/resolve`, { method: "POST", body: JSON.stringify(payload) });
      } catch (error) {
        if (error.code !== "replacement_confirmation_required" || !confirm(`${error.message}\n\nReplace the existing comment?`)) throw error;
        result = await api(`/api/issues/${issue.id}/resolve`, { method: "POST", body: JSON.stringify({ ...payload, confirm_replace: true }) });
      }
      state = normalizeState(result.state);
      closeModal(); render(); toast("Student match confirmed.");
    } catch (error) { toast(error.message, "error"); }
  };
}

function connectTemplate(classId) {
  const classItem = classById(classId);
  const template = templateForClass(classId) || {};
  const connection = state.connections.google_docs || {};
  if (!connection.configured || connection.status === "disconnected") {
    toast("Connect and test your Google account first.", "error"); go("connections"); return;
  }
  openModal(`<span class="eyebrow">GOOGLE DOCS TEMPLATE</span><h2>${escapeHtml(classItem?.name || "Class")}</h2><p>Paste a Google Doc that <strong>${escapeHtml(connection.account_name || "your connected account")}</strong> can open.</p><form id="template-connect-form"><label><span>Google Docs URL or ID</span><input id="template-document" value="${escapeHtml(template.document_id || "")}" placeholder="https://docs.google.com/document/d/..." required /></label><div class="safety-note">Brightline copies this document. It never overwrites the original template.</div><div class="modal-footer"><button type="button" class="secondary-button" data-close-modal>Cancel</button><button class="primary-button">Inspect and connect</button></div></form>`);
  $("#template-connect-form").onsubmit = async event => {
    event.preventDefault();
    try {
      const result = await api(`/api/templates/${classId}/connect`, { method: "POST", body: JSON.stringify({ document: $("#template-document").value.trim() }) });
      state = normalizeState(result.state);
      closeModal(); render(); toast(result.message, result.missing_required?.length || result.invalid_placeholders?.length ? "warning" : "success");
    } catch (error) { toast(error.message, "error"); }
  };
}

function showTemplateFields(classId = "") {
  const template = classId ? templateForClass(classId) : null;
  const supported = state.supported_placeholders.length ? state.supported_placeholders : ["student_name", "student_id", "class_name", "grade_or_year", "school", "reporting_week", "assignment_summary", "assignment_percent", "weekly_test_score", "weekly_test_summary", "teacher_comment"];
  openModal(`<span class="eyebrow">CONTROLLED FIELD MAPPING</span><h2>${classId ? escapeHtml(classById(classId)?.name || "Template fields") : "Supported placeholders"}</h2><p>Put these exact placeholders in ordinary Google Docs text or tables. Brightline preserves the surrounding font and formatting.</p><div class="placeholder-modal-list">${supported.map(name => `<code>{{${escapeHtml(name)}}}</code>`).join("")}</div>${template ? `<div class="safety-note">Detected: ${escapeHtml((template.placeholders || []).join(", ") || "none")}<br />Required but missing: ${escapeHtml((template.missing_required || []).join(", ") || "none")}<br />Invalid fields: ${escapeHtml((template.invalid_placeholders || []).join(", ") || "none")}</div>` : ""}<div class="modal-footer"><button class="primary-button" data-close-modal>Done</button></div>`);
}

async function testTemplate(classId) {
  try {
    const result = await api(`/api/templates/${classId}/test`, { method: "POST", body: JSON.stringify({}) });
    state = normalizeState(result.state); render(); toast(result.message, result.missing_required?.length || result.invalid_placeholders?.length ? "warning" : "success");
  } catch (error) { toast(error.message, "error"); }
}

async function disconnectTemplate(classId) {
  if (!confirm(`Disconnect the Google Docs template from ${classById(classId)?.name || "this class"}? The original Google Doc will not be deleted.`)) return;
  try {
    const result = await api(`/api/templates/${classId}/disconnect`, { method: "POST", body: JSON.stringify({}) });
    state = normalizeState(result.state); render(); toast("Template disconnected.");
  } catch (error) { toast(error.message, "error"); }
}

function addExample(classId) {
  pendingExampleClass = classId;
  $("#example-report-input").click();
}

async function uploadExample(file) {
  if (!file || !pendingExampleClass) return;
  try {
    const result = await api(`/api/templates/${pendingExampleClass}/example`, { method: "POST", body: JSON.stringify({ filename: file.name, pdf_base64: arrayBufferToBase64(await file.arrayBuffer()) }) });
    state = normalizeState(result.state); render(); toast("Example report saved as a private local reference.");
  } catch (error) { toast(error.message, "error"); }
  finally { pendingExampleClass = ""; $("#example-report-input").value = ""; }
}

async function configureGoogle(service) {
  const connection = state.connections[service] || {};
  if (!connection.oauth_client_configured) {
    toast("Set GOOGLE_OAUTH_CLIENT_FILE and restart Brightline first.", "error");
    return;
  }
  if (connection.authorized && service === "google_docs" && connection.status === "needs_setup") {
    await testGoogle(service);
    return;
  }
  window.location.assign(connection.connect_url || "/api/google/oauth/start");
}

async function testGoogle(service) {
  try {
    const result = await api("/api/connections/google/test", { method: "POST", body: JSON.stringify({ service }) });
    state = normalizeState(result.state); render(); toast(result.message);
  } catch (error) { await loadState(); toast(error.message, "error"); }
}

async function disconnectGoogle() {
  if (!confirm("Disconnect your Google account from Brightline? Existing Google files will not be deleted.")) return;
  try {
    const result = await api("/api/connections/google/disconnect", { method: "POST", body: JSON.stringify({}) });
    state = normalizeState(result.state); render(); toast("Google disconnected inside Brightline.");
  } catch (error) { toast(error.message, "error"); }
}

function bindEvents() {
  document.addEventListener("click", async event => {
    const nav = event.target.closest("[data-nav]");
    if (nav) { event.preventDefault(); go(nav.dataset.nav); return; }
    const goto = event.target.closest("[data-go]");
    if (goto) { event.preventDefault(); go(goto.dataset.go); return; }
    const close = event.target.closest("[data-close-modal]");
    if (close) { event.preventDefault(); closeModal(); return; }
    const manual = event.target.closest("[data-manual-kind]");
    if (manual) {
      const inputs = { assignment: "#assignment-file-input", weekly_test: "#weekly-test-file-input", comments: "#comments-file-input" };
      $(inputs[manual.dataset.manualKind]).click(); return;
    }
    const sheet = event.target.closest("[data-sheet-kind]");
    if (sheet) { openGoogleSheetImport(sheet.dataset.sheetKind); return; }
    const issue = event.target.closest("[data-issue]");
    if (issue) { reviewIssue(issue.dataset.issue); return; }
    const connect = event.target.closest("[data-template-connect]");
    if (connect) { connectTemplate(connect.dataset.templateConnect); return; }
    const test = event.target.closest("[data-template-test]");
    if (test) { await testTemplate(test.dataset.templateTest); return; }
    const example = event.target.closest("[data-template-example]");
    if (example) { addExample(example.dataset.templateExample); return; }
    const mapping = event.target.closest("[data-template-map]");
    if (mapping) { showTemplateFields(mapping.dataset.templateMap); return; }
    const disconnect = event.target.closest("[data-template-disconnect]");
    if (disconnect) { await disconnectTemplate(disconnect.dataset.templateDisconnect); return; }
    const connectionAction = event.target.closest("[data-connection-action]");
    if (connectionAction) {
      const { connectionAction: action, service } = connectionAction.dataset;
      if (action === "configure") await configureGoogle(service);
      if (action === "test") await testGoogle(service);
      if (action === "disconnect") await disconnectGoogle();
      return;
    }
    const preview = event.target.closest("[data-preview-student]");
    if (preview) { await previewGenerated(preview.dataset.previewStudent, preview.dataset.periodId); return; }
    const studentDownload = event.target.closest("[data-download-student]");
    if (studentDownload) {
      const downloaded = await downloadGenerated(
        `/api/reports/student/${studentDownload.dataset.downloadStudent}/download`,
        { period_id: studentDownload.dataset.periodId || "" },
        studentDownload.dataset.filename || "student-report.pdf",
      );
      if (downloaded) await loadState();
      return;
    }
    const classDownload = event.target.closest("[data-download-class]");
    if (classDownload) {
      await generateArchive(
        `/api/reports/class/${classDownload.dataset.downloadClass}/download`,
        classDownload.dataset.filename || "class-reports.zip",
      );
      return;
    }
    if (event.target.closest(".disabled")?.matches("a")) event.preventDefault();
    if (event.target === $("#modal-backdrop")) closeModal();
  });

  document.addEventListener("change", async event => {
    try {
      if (event.target.matches("#global-period-select") && event.target.value) {
        const result = await api("/api/periods/select", { method: "POST", body: JSON.stringify({ period_id: event.target.value }) });
        state = normalizeState(result.state); render(); toast("Active reporting period changed.");
      }
      if (event.target.matches("#report-class-select")) {
        selectedReportClass = event.target.value; renderReports();
      }
      if (event.target.matches("#report-period-select") && selectedReportClass && event.target.value) {
        const result = await api(`/api/classes/${selectedReportClass}/period`, { method: "POST", body: JSON.stringify({ period_id: event.target.value }) });
        state = normalizeState(result.state); render(); toast("Class reporting period changed.");
      }
    } catch (error) { toast(error.message, "error"); await loadState(); }
  });

  $("#assignment-file-input").onchange = event => chooseAssignmentCsv(event.target.files[0]);
  $("#weekly-test-file-input").onchange = async event => { await chooseTabularFile(event.target.files[0], "weekly_test"); event.target.value = ""; };
  $("#comments-file-input").onchange = event => chooseCommentsFile(event.target.files[0]);
  $("#example-report-input").onchange = event => uploadExample(event.target.files[0]);
  $("#show-placeholder-guide").onclick = () => showTemplateFields();
  $("#preview-report").onclick = async event => {
    if (event.currentTarget.classList.contains("disabled")) return;
    await previewGenerated(event.currentTarget.dataset.studentId, event.currentTarget.dataset.periodId);
  };
  $("#download-class-zip").onclick = async event => {
    if (event.currentTarget.classList.contains("disabled")) return;
    const classItem = classById(event.currentTarget.dataset.classId);
    await generateArchive(
      `/api/reports/class/${event.currentTarget.dataset.classId}/download`,
      `${classItem?.name || "class"}-reports.zip`,
    );
  };
  $("#download-all").onclick = async event => {
    if (event.currentTarget.classList.contains("disabled")) return;
    await generateArchive("/api/reports/all/download", "all-class-reports.zip");
  };
  $("#sidebar-toggle").onclick = () => {
    if (window.matchMedia("(max-width: 900px)").matches) $("#sidebar").classList.remove("open");
    else $("#app-shell").classList.add("sidebar-collapsed");
    $("#sidebar-toggle").setAttribute("aria-expanded", "false");
  };
  $("#sidebar-open").onclick = () => {
    $("#app-shell").classList.remove("sidebar-collapsed");
    if (window.matchMedia("(max-width: 900px)").matches) $("#sidebar").classList.add("open");
    $("#sidebar-toggle").setAttribute("aria-expanded", "true");
  };
  $("#reset-data").onclick = async () => {
    if (!confirm("Clear imported students, reporting periods, local examples, and report history? Google credentials and Google Drive files are not deleted.")) return;
    try {
      const result = await api("/api/reset", { method: "POST", body: JSON.stringify({ confirm: true }) });
      state = normalizeState(result.state); render(); toast("Local student data cleared.");
    } catch (error) { toast(error.message, "error"); }
  };
}

bindEvents();
go(window.location.hash.slice(1) || "inbox");
loadState();
