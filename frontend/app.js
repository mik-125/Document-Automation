if (window.location.protocol === "file:") {
  window.location.replace(`http://127.0.0.1:8000/${window.location.hash || "#inbox"}`);
}

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
  period_input_status: [],
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
let pendingPdfTemplateClass = "";
let generationBusy = false;
let uploadSession = null;
const UPLOAD_DRAFT_PERIODS_KEY = "brightline.uploadDraftPeriods.v1";
const UPLOAD_LAST_PERIOD_KEY = "brightline.uploadLastPeriod.v1";

function readUploadDraftPeriods() {
  try {
    if (typeof sessionStorage === "undefined") return [];
    const stored = JSON.parse(sessionStorage.getItem(UPLOAD_DRAFT_PERIODS_KEY) || "[]");
    if (!Array.isArray(stored)) return [];
    const seen = new Set();
    return stored.slice(0, 100).flatMap(item => {
      const id = String(item?.id || "").slice(0, 120);
      const label = String(item?.label || "").trim().slice(0, 80);
      if (!id || !label || seen.has(id)) return [];
      seen.add(id);
      const date = value => /^\d{4}-\d{2}-\d{2}$/.test(String(value || "")) ? String(value) : "";
      return [{ id, label, start_date: date(item.start_date), end_date: date(item.end_date), draft: true }];
    });
  } catch (_error) {
    return [];
  }
}

function readLastUploadPeriodKey() {
  try {
    return typeof sessionStorage === "undefined" ? "" : String(sessionStorage.getItem(UPLOAD_LAST_PERIOD_KEY) || "");
  } catch (_error) {
    return "";
  }
}

let uploadDraftPeriods = readUploadDraftPeriods();
let lastUploadPeriodKey = readLastUploadPeriodKey();

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

function periodInputStatuses(periodId) {
  return state.period_input_status.filter(item => item.period_id === periodId);
}

function statusForClassPeriod(classId, periodId) {
  return state.period_input_status.find(item => item.class_id === classId && item.period_id === periodId) || {};
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
  const skipped = result.skipped || [];
  if (!failures.length && !skipped.length) {
    toast(`${result.generated_count} reports generated and downloaded.`);
    return;
  }
  const rows = [...failures.map(item => ({ ...item, reason: item.error, status: "Failed" })), ...skipped.map(item => ({ ...item, status: "Skipped" }))]
    .map(item => `<li><strong>${escapeHtml(item.student)}</strong><span>${escapeHtml(item.class_name || "")} · ${item.status}</span><p>${escapeHtml(item.reason)}</p></li>`).join("");
  openModal(`<span class="eyebrow">GENERATION RESULT</span><h2>${result.generated_count || 0} generated · ${result.skipped_count || skipped.length} skipped · ${result.failed_count || failures.length} failed</h2><p>${result.generated_count ? "The successful PDFs were downloaded. Every roster student is accounted for below and in generation-summary.json." : "No ZIP was downloaded because no report could be generated."}</p><ul class="generation-errors">${rows}</ul><div class="modal-footer"><button class="primary-button" data-close-modal>Done</button></div>`);
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

let lockedPageScroll = null;

function lockPageScroll() {
  if (lockedPageScroll) return;
  const appShell = $("#app-shell");
  lockedPageScroll = {
    x: window.scrollX,
    y: window.scrollY,
    activeElement: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    bodyTop: document.body.style.top,
    bodyLeft: document.body.style.left,
    scrollbarWidth: document.body.style.getPropertyValue("--modal-scrollbar-width"),
    appShellWasInert: appShell.inert,
  };
  const scrollbarWidth = Math.max(0, window.innerWidth - document.documentElement.clientWidth);
  document.body.style.setProperty("--modal-scrollbar-width", `${scrollbarWidth}px`);
  document.documentElement.classList.add("modal-open");
  document.body.classList.add("modal-open");
  document.body.style.top = `-${lockedPageScroll.y}px`;
  document.body.style.left = `-${lockedPageScroll.x}px`;
  appShell.inert = true;
}

function unlockPageScroll() {
  if (!lockedPageScroll) return;
  const scrollLock = lockedPageScroll;
  lockedPageScroll = null;
  document.documentElement.classList.remove("modal-open");
  document.body.classList.remove("modal-open");
  if (scrollLock.bodyTop) document.body.style.top = scrollLock.bodyTop;
  else document.body.style.removeProperty("top");
  if (scrollLock.bodyLeft) document.body.style.left = scrollLock.bodyLeft;
  else document.body.style.removeProperty("left");
  if (scrollLock.scrollbarWidth) document.body.style.setProperty("--modal-scrollbar-width", scrollLock.scrollbarWidth);
  else document.body.style.removeProperty("--modal-scrollbar-width");
  $("#app-shell").inert = scrollLock.appShellWasInert;
  const previousScrollBehavior = document.documentElement.style.scrollBehavior;
  document.documentElement.style.scrollBehavior = "auto";
  window.scrollTo(scrollLock.x, scrollLock.y);
  document.documentElement.style.scrollBehavior = previousScrollBehavior;
  requestAnimationFrame(() => {
    if (scrollLock.activeElement?.isConnected) scrollLock.activeElement.focus({ preventScroll: true });
  });
}

function openModal(html) {
  lockPageScroll();
  $("#modal").className = "modal";
  $("#modal").innerHTML = html;
  $("#modal").scrollTop = 0;
  $("#modal-backdrop").classList.remove("hidden");
  requestAnimationFrame(() => $("#modal").querySelector("input, select, button")?.focus({ preventScroll: true }));
}

function closeModal() {
  if (uploadSession) {
    captureUploadFields();
    if (uploadSession.periodKey) lastUploadPeriodKey = uploadSession.periodKey;
    persistUploadDraftPeriods();
  }
  uploadSession = null;
  $("#modal-backdrop").classList.add("hidden");
  $("#modal").className = "modal";
  $("#modal").innerHTML = "";
  unlockPageScroll();
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
  const periodId = state.selected_period_id || state.periods[0]?.id || "";
  const current = periodInputStatuses(periodId);
  const assignmentReady = current.some(item => item.assignment_count > 0);
  const testsReady = current.some(item => item.weekly_test_count > 0);

  const statuses = [
    ["#assignment-source-status", assignmentReady && testsReady, assignmentReady ? (testsReady ? "Imported" : "Test scores missing") : "Start here"],
    ...Object.entries(COMMENT_SECTIONS).map(([kind, section]) => {
      const ready = current.some(item => item[`${section}_comment_count`] > 0);
      return [`#${kind}-source-status`, ready, ready ? "Imported" : kind === "homework_comment" ? "Required" : "Choose 3 or 4, or both"];
    }),
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
  $("#data-readiness").innerHTML = `<div class="readiness-row header"><span>Class and period</span><span>Assignment</span><span>Weekly Test</span><span>과제이행도 + (3 or 4)</span><span>Template</span></div>${state.classes.map(classItem => {
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
    comments_shared: "Shared 과제이행도 comment",
    example_report: "Example report",
    report_template: "Company PDF template",
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
    body.innerHTML = `<tr><td colspan="2">${emptyState("No classes yet", "Import Assignment data first; classes appear here automatically.", true)}</td></tr>`;
    return;
  }
  body.innerHTML = state.classes.map(classItem => {
    const template = templateForClass(classItem.id) || {};
    const pdf = template.pdf_template;
    const profile = template.example_profile || {};
    const exampleCount = template.example_count || 0;
    const sampleSummary = exampleCount
      ? `Content reference · ${profile.content_guide?.sections?.length || 0} section observations${profile.content_guide?.requires_review ? " · Review analysis" : ""}`
      : "Upload a completed report to guide content placement; teacher wording is preserved.";
    const activeVersionNote = pdf?.status !== "published" && pdf?.active_version
      ? ` · Version ${pdf.active_version} remains active`
      : "";
    const pdfSummary = pdf
      ? `Original design · Version ${pdf.version} · ${pdf.page_count} page${pdf.page_count === 1 ? "" : "s"} · ${pdf.layout_review_required ? "Review original PDF mapping" : pdf.status === "published" ? "Published" : "Mapping confirmation required"}${activeVersionNote}`
      : "Upload the clean company PDF before generating reports.";
    const pdfActions = `<button class="table-action" type="button" data-template-pdf-upload="${classItem.id}">${pdf ? "Upload new version" : "Upload PDF template"}</button>${pdf ? `<button class="table-action" type="button" data-template-pdf-map="${classItem.id}">${pdf.status === "published" ? "Review mapping" : "Confirm mapping"}</button><a class="table-action" href="${escapeHtml(pdf.preview_url)}" target="_blank" rel="noopener">Open PDF</a><button class="table-action danger" type="button" data-template-pdf-disconnect="${classItem.id}">Remove</button>` : ""}`;
    return `<tr><td class="template-class-cell"><strong>${escapeHtml(classItem.name)}</strong><small>${escapeHtml(classItem.teacher || "Teacher not set")}</small></td><td class="template-sample-cell"><div class="template-output-stack"><div class="template-sample-layout"><div class="template-sample-summary"><strong>${escapeHtml(pdf?.name || "No company template")}</strong><small>${escapeHtml(pdfSummary)}</small></div><div class="template-sample-actions">${pdfActions}</div></div><div class="template-sample-layout template-example-layout"><div class="template-sample-summary"><strong>${exampleCount ? `${exampleCount} completed sample${exampleCount === 1 ? "" : "s"}` : "No completed sample"}</strong><small>${escapeHtml(sampleSummary)}</small></div><div class="template-sample-actions"><button class="table-action" type="button" data-template-example="${classItem.id}">Upload sample report</button>${exampleCount ? `<button class="table-action" type="button" data-template-analysis="${classItem.id}">View analysis</button>` : ""}</div></div></div></td></tr>`;
  }).join("");
}

function reportReason(student) {
  if (student.routing_review_required) return "Comment routing review required";
  if (student.report_ready && student.missing_test_feedback_assumption) return "Ready · no test feedback";
  if (!student.assignment_ready) return "Assignment missing";
  if (!student.weekly_test_ready) return "Weekly Test missing";
  if (!student.homework_comment_ready) return "과제이행도 comment missing";
  if (!student.weekly_test_comment_ready && !student.general_comment_ready) return "Weekly test or general comment missing";
  if (!student.template_ready) return "Template missing";
  if (!(student.output_ready ?? student.google_ready)) return "Report output missing";
  return "Ready";
}

function showRoutingReview(studentId) {
  const student = state.students.find(item => item.id === studentId);
  if (!student) return;
  const decisions = student.comment_placement || [];
  const rows = decisions.length ? decisions.map(decision => {
    const confidence = `${Math.round(Number(decision.confidence || 0) * 100)}%`;
    const signals = (decision.signals || []).join(" · ");
    return `<tr><td>${decision.passage_index}</td><td>${escapeHtml(decision.destination || "review")}</td><td><span class="status-pill ${decision.requires_review ? "warn" : ""}">${confidence}</span></td><td>${escapeHtml(signals)}</td></tr><tr class="routing-passage-row"><td colspan="4"><pre>${escapeHtml(decision.passage || "")}</pre></td></tr>`;
  }).join("") : `<tr><td colspan="4">No comment passages were found.</td></tr>`;
  const scoreWarning = student.missing_test_score_warning
    ? `<div class="safety-note warning"><strong>Review warning:</strong> A Weekly Test score is stored, but the missed-test notice will be used because no passage matched the Weekly Test section.</div>`
    : "";
  openModal(`<span class="eyebrow">PASSAGE ROUTING</span><h2>${escapeHtml(student.name)}</h2><p>Every source passage is shown with its destination, confidence, and matching signals. Low-confidence passages keep their original upload section and block generation.</p>${scoreWarning}<div class="table-card routing-table"><table><thead><tr><th>#</th><th>Section</th><th>Confidence</th><th>Why</th></tr></thead><tbody>${rows}</tbody></table></div><div class="modal-footer"><button type="button" class="primary-button" data-close-modal>Close</button></div>`);
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
    ["Assignment", status.assignment_ready], ["Weekly Test", status.weekly_test_ready], ["과제이행도 + (3 or 4)", status.comments_ready], ["Template", status.template_ready], ["Report output", status.output_ready ?? status.google_ready],
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
    $("#reports-by-class").innerHTML = emptyState("No reports to prepare", "Import homework + scores, 과제이행도 comments, and weekly test or general comments first.", true);
  } else {
    $("#reports-by-class").innerHTML = state.classes.map(classItem => {
      const students = studentsForClass(classItem.id);
      const ready = students.filter(student => student.report_ready).length;
      const rows = students.map(student => {
        const exam = student.latest_exam;
        const attempted = Number(student.homework_completed || 0) + Number(student.homework_incomplete || 0);
        const missed = attempted || Number(student.homework_excluded || 0) ? formatNumber(student.homework_incomplete || 0) : student.homework_percent == null ? "—" : "Unavailable";
        const routingButton = `<button class="table-action" data-routing-student="${student.id}">Routing</button>`;
        const reportActions = student.report_ready ? `<button class="table-action" data-preview-student="${student.id}" data-period-id="${student.period_id || ""}">Preview</button><button class="table-action" data-download-student="${student.id}" data-period-id="${student.period_id || ""}" data-filename="${escapeHtml(student.name)}-report.pdf">Download PDF</button>` : '<button class="table-action disabled">Needs review</button>';
        return `<div class="report-student-row"><div class="student-name"><span class="student-avatar">${escapeHtml(initials(student.name))}</span><span><strong>${escapeHtml(student.name)}</strong><small>${escapeHtml(student.school || "School missing")} · ${escapeHtml(student.grade || "Grade missing")}</small></span></div><div class="report-data"><span>Assignments missed<b>${missed}</b></span><span>Weekly Test<b>${exam ? `${formatNumber(exam.score)}${exam.max_score === 100 ? "%" : exam.max_score == null ? "" : `/${formatNumber(exam.max_score)}`}` : "—"}</b></span></div><span class="status-pill ${student.report_ready && !student.missing_test_score_warning ? "" : "warn"}">${escapeHtml(reportReason(student))}</span><div class="row-actions">${routingButton}${reportActions}</div></div>`;
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
  const anyConnected = sheets.status === "connected" || docs.status === "connected";
  $("#nav-connection-dot").className = `connection-dot ${anyConnected ? "connected" : (sheets.error || docs.error ? "error" : "")}`;
  const status = connection => {
    if (connection.status === "connected") return ["Connected", ""];
    if (connection.status === "configured") return ["Configured", "warn"];
    if (connection.status === "needs_auth") return ["Sign in needed", "warn"];
    if (connection.status === "needs_setup") return ["Folder needed", "warn"];
    if (connection.status === "error") return ["Connection error", "warn"];
    return ["Disconnected", "neutral"];
  };
  const card = ({ key, short, title, copy, connection, primary }) => {
    const [label, style] = status(connection);
    const primaryLabel = connection.authorized ? primary : "Connect Google account";
    const actions = `<button class="primary-button" data-connection-action="configure" data-service="${key}">${primaryLabel}</button><button class="secondary-button" data-connection-action="test" data-service="${key}" ${connection.authorized ? "" : "disabled"}>Test connection</button>${connection.authorized ? `<button class="secondary-button" data-connection-action="disconnect" data-service="${key}">Disconnect</button>` : ""}`;
    return `<article class="connection-card"><div class="connection-card-head"><span class="connection-logo">${short}</span><span class="status-pill ${style}">${label}</span></div><h2>${title}</h2><p>${copy}</p><div class="connection-meta"><span>Account<b>${escapeHtml(connection.account_name || "Not connected")}</b></span><span>${key === "google_docs" ? "Output folder" : "Last sync"}<b>${escapeHtml(key === "google_docs" ? (connection.resource_name || "Not configured") : formatDateTime(connection.last_synced_at))}</b></span></div>${connection.error ? `<p class="connection-error">${escapeHtml(connection.error)}</p>` : ""}<div class="connection-actions">${actions}</div></article>`;
  };
  $("#connections-grid").innerHTML = [
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
    reconcileUploadDraftPeriods();
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

const COMMENT_SECTIONS = { homework_comment: "homework", weekly_test_comment: "weekly_test", general_comment: "general" };

const UPLOAD_COPY = {
  assignment: {
    eyebrow: "HOMEWORK + TEST SCORES",
    title: "Import this week’s student data",
    copy: "Use one Google Sheet with student details, dated homework marks, and a Test score or weekly test 점수 (%) column. Percentages use 0–100. Blank scores preserve existing results; students without a score remain incomplete.",
  },
  homework_comment: {
    eyebrow: "02 · REQUIRED",
    title: "과제이행도 comment",
    copy: "Upload one searchable PDF with every student’s homework comment, or write one comment for everyone in the selected class and week. It appears beside the homework graph.",
  },
  weekly_test_comment: {
    eyebrow: "03 · CHOOSE 3 OR 4, OR BOTH",
    title: "Weekly test comment",
    copy: "Upload one searchable PDF with every student’s weekly test comment. It appears beside the weekly test graph, continuing below when needed.",
  },
  general_comment: {
    eyebrow: "04 · CHOOSE 3 OR 4, OR BOTH",
    title: "General comment",
    copy: "Upload one searchable PDF with every student’s general comment. It appears in the general comment section.",
  },
};

function persistUploadDraftPeriods() {
  try {
    if (typeof sessionStorage === "undefined") return;
    sessionStorage.setItem(UPLOAD_DRAFT_PERIODS_KEY, JSON.stringify(uploadDraftPeriods));
    if (lastUploadPeriodKey) sessionStorage.setItem(UPLOAD_LAST_PERIOD_KEY, lastUploadPeriodKey);
  } catch (_error) {
    // Uploads still work when browser storage is unavailable; only draft week persistence is skipped.
  }
}

function clearUploadDraftPeriods() {
  uploadDraftPeriods.splice(0);
  lastUploadPeriodKey = "";
  try {
    if (typeof sessionStorage === "undefined") return;
    sessionStorage.removeItem(UPLOAD_DRAFT_PERIODS_KEY);
    sessionStorage.removeItem(UPLOAD_LAST_PERIOD_KEY);
  } catch (_error) {
    // Local data is still cleared when browser storage is unavailable.
  }
}

function normalizedPeriodLabel(period) {
  return String(period?.label || "").trim().toLocaleLowerCase();
}

function matchingSavedPeriod(draft) {
  const label = normalizedPeriodLabel(draft);
  return state.periods.find(period => {
    const sameDates = draft.start_date && draft.end_date
      && period.start_date === draft.start_date && period.end_date === draft.end_date;
    return sameDates || (label && normalizedPeriodLabel(period) === label);
  }) || null;
}

function reconcileUploadDraftPeriods() {
  for (let index = uploadDraftPeriods.length - 1; index >= 0; index -= 1) {
    const draft = uploadDraftPeriods[index];
    const saved = matchingSavedPeriod(draft);
    if (!saved) continue;
    if (lastUploadPeriodKey === draft.id) lastUploadPeriodKey = saved.id;
    uploadDraftPeriods.splice(index, 1);
  }
  let previous = state.periods.filter(period => period.end_date).sort((a, b) => a.end_date.localeCompare(b.end_date)).at(-1) || null;
  for (const draft of uploadDraftPeriods) {
    if (!draft.start_date || !draft.end_date) Object.assign(draft, nextWeekDates(previous));
    previous = draft;
  }
  persistUploadDraftPeriods();
}

function nextWeekNumber() {
  const knownPeriods = [
    ...state.periods,
    ...(uploadSession?.draftPeriods || uploadDraftPeriods),
  ];
  return Math.max(knownPeriods.length, ...knownPeriods.map(period => Number.parseInt(String(period.label || "").match(/\bweek\s+(\d+)\b/i)?.[1] || "0", 10))) + 1;
}

function nextWeekDates(previousPeriod = null) {
  const latestEnd = previousPeriod?.end_date || state.periods.map(period => period.end_date).filter(Boolean).sort().at(-1);
  let start;
  if (latestEnd) {
    start = new Date(`${latestEnd}T00:00:00Z`);
    if (Number.isNaN(start.valueOf())) start = null;
    else start.setUTCDate(start.getUTCDate() + 1);
  }
  if (!start) {
    const today = new Date();
    start = new Date(Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()));
    start.setUTCDate(start.getUTCDate() - ((today.getDay() + 6) % 7));
  }
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + 6);
  return { start_date: start.toISOString().slice(0, 10), end_date: end.toISOString().slice(0, 10) };
}

function createDraftWeek(previousPeriod = null) {
  const number = nextWeekNumber();
  const suffix = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return { id: `draft-week-${number}-${suffix}`, label: `Week ${number}`, ...nextWeekDates(previousPeriod), draft: true };
}

function uploadTargetPeriod() {
  if (!uploadSession) return null;
  const draft = uploadSession.draftPeriods.find(period => period.id === uploadSession.periodKey);
  if (draft) return draft;
  return periodById(uploadSession.periodKey) || null;
}

function currentUploadStage() {
  return uploadSession?.stage || null;
}

function setUploadStage(stage) {
  if (uploadSession) uploadSession.stage = stage || null;
}

function uploadPrerequisite() {
  const target = uploadTargetPeriod();
  const kind = uploadSession?.kind;
  if (!target) return { ready: false, message: "Upload a source now or choose a week first. A week is required before Save." };
  if (kind === "assignment") {
    return { ready: true, message: target.draft ? "This week will be created only when Assignment is saved." : "Assignment can add or update this week's roster." };
  }
  if (target.draft) {
    return { ready: false, message: `Import Assignment for ${target.label} before adding ${kind === "weekly_test" ? "Weekly Test" : "Comments"} data.` };
  }
  const statuses = periodInputStatuses(target.id);
  if (kind === "weekly_test") {
    const ready = statuses.some(item => item.assignment_ready);
    return { ready, message: ready ? "Assignment data is ready. Every score must match this week's roster." : `Import Assignment for ${target.label} before Weekly Test.` };
  }
  const eligible = statuses.filter(item => item.comments_allowed);
  return {
    ready: Boolean(eligible.length),
    message: eligible.length
      ? `${eligible.length} class${eligible.length === 1 ? " is" : "es are"} ready for Comments. Every imported student must already have Assignment and Weekly Test data.`
      : `Import homework and test scores together for ${target.label} before Comments.`,
  };
}

function captureUploadFields() {
  const stage = currentUploadStage();
  const label = $("#upload-period-label");
  const start = $("#upload-period-start");
  const end = $("#upload-period-end");
  const target = uploadTargetPeriod();
  if (target?.draft) {
    if (label) target.label = label.value.trim() || target.label;
    if (start) target.start_date = start.value;
    if (end) target.end_date = end.value;
    persistUploadDraftPeriods();
  }
  if (!stage) return;
  const mappingSelects = $$('[data-upload-map-field]');
  if (mappingSelects.length) {
    const visibleFields = new Set(mappingSelects.map(select => select.dataset.uploadMapField));
    const retainedMapping = Object.fromEntries(
      Object.entries(stage.mapping || {}).filter(([field]) => !visibleFields.has(field)),
    );
    const visibleMapping = Object.fromEntries(
      mappingSelects.filter(select => select.value).map(select => [select.dataset.uploadMapField, select.value]),
    );
    stage.mapping = { ...retainedMapping, ...visibleMapping };
  }
  if (stage.mode === "comments_pdf" || stage.mode === "comments_shared") {
    const sharedComment = $("#upload-shared-comment");
    if (sharedComment) stage.comment = sharedComment.value.trim();
    const classSelect = $("#upload-comments-class");
    if (classSelect) stage.classId = classSelect.value;
  }
}

function uploadMappingSelect(field, preview, selected, required) {
  const options = preview.headers.map((header, index) => {
    const examples = [...new Set(preview.sample_rows.map(row => String(row[index] || "").trim()).filter(Boolean))]
      .slice(0, 2)
      .map(value => value.length > 30 ? `${value.slice(0, 29)}…` : value)
      .join(", ");
    return `<option value="${escapeHtml(header)}" ${header === selected ? "selected" : ""}>${escapeHtml(examples ? `${header} — ${examples}` : header)}</option>`;
  }).join("");
  return `<label class="${required ? "required" : ""}"><span>${escapeHtml(FIELD_LABELS[field])}</span><select data-upload-map-field="${field}"><option value="">Not used</option>${options}</select></label>`;
}

function uploadStageEditor() {
  const stage = currentUploadStage();
  const target = uploadTargetPeriod();
  if (!stage) {
    const sources = COMMENT_SECTIONS[uploadSession?.kind] ? (uploadSession.kind === "homework_comment" ? "a comments PDF or a shared comment" : "a comments PDF") : "Google Sheets";
    return `<div class="staged-source-empty">No source selected. Choose ${sources} above.</div>`;
  }
  if (stage.mode === "legacy_assignment") {
    const options = stage.inspection.dates.map(item => `<option value="${item.iso}">${escapeHtml(item.label)} · ${item.iso}</option>`).join("");
    const periodFields = target?.draft
      ? `<label><span>Week name</span><input id="upload-period-label" value="${escapeHtml(target.label)}" required /></label><div class="form-grid"><label><span>Start date</span><select id="upload-period-start" required>${options}</select></label><label><span>End date</span><select id="upload-period-end" required>${options}</select></label></div>`
      : `<div class="safety-note">This file will update <strong>${escapeHtml(target?.label || "the selected week")}</strong> (${escapeHtml(target?.start_date || "")}–${escapeHtml(target?.end_date || "")}).</div>`;
    return `<div class="safety-note">Detected ${stage.inspection.record_count} student-class records across ${stage.inspection.class_count} classes. A/B are completed, F is incomplete, and X or blank is excluded.</div>${periodFields}`;
  }
  if (stage.mode === "comments_pdf" || stage.mode === "comments_shared") {
    const shared = stage.mode === "comments_shared";
    const classOptions = state.classes.map(item => {
      const status = statusForClassPeriod(item.id, target?.id || "");
      const missing = status.comments_allowed ? "" : status.assignment_ready ? " — Weekly Test scores missing" : " — Homework missing for this week";
      return `<option value="${item.id}" ${item.id === stage.classId ? "selected" : ""} ${status.comments_allowed ? "" : "disabled"}>${escapeHtml(item.name + missing)}</option>`;
    }).join("");
    const classField = `<label><span>${shared ? "Class for this comment" : "Class in this PDF"}</span><select id="upload-comments-class" required><option value="">${shared ? "Choose a class" : "Choose the PDF’s class"}</option>${classOptions}</select></label>`;
    if (shared) return `${classField}<label><span>Comment for every student</span><textarea id="upload-shared-comment" rows="6" required>${escapeHtml(stage.comment || "")}</textarea></label><div class="safety-note">This comment applies to every student in this class for ${escapeHtml(target?.label || "the selected week")} only.</div>`;
    return `${classField}<div class="safety-note">Select the class named in your student data. Unavailable classes show what is missing for this week. Uncertain student names go to the review queue; scores found in the PDF are ignored.</div>`;
  }
  if (stage.mode === "google_sheet") {
    stage.previewRowsShown = Math.min(
      stage.preview.sample_rows.length,
      stage.previewRowsShown || SHEET_PREVIEW_RENDER_CHUNK,
    );
    return sampleTable(stage.preview, {
      worksheet: stage.worksheet,
      itemLabel: "student rows",
      progressive: true,
      visibleRows: stage.previewRowsShown,
    });
  }
  const required = requiredFields(uploadSession.kind);
  const fields = fieldsForKind(uploadSession.kind);
  const suggested = { ...(stage.preview.suggested_mapping || {}), ...(state.sheet_sources[uploadSession.kind]?.mapping || {}), ...(stage.mapping || {}) };
  const datedColumns = stage.preview.headers.filter(header => /^(?:\d{4}\/)?\d{1,2}\/\d{1,2}$/.test(header));
  const assignmentNote = uploadSession.kind === "assignment" && datedColumns.length
    ? `<div class="safety-note">Assignments missed will be counted from <strong>F</strong> across ${datedColumns.length} dated column${datedColumns.length === 1 ? "" : "s"}.</div>`
    : "";
  const periodFields = target?.draft
    ? `<label><span>Week name</span><input id="upload-period-label" value="${escapeHtml(target.label)}" required /></label><div class="form-grid"><label><span>Start date</span><input id="upload-period-start" type="date" value="${escapeHtml(target.start_date || "")}" required /></label><label><span>End date</span><input id="upload-period-end" type="date" value="${escapeHtml(target.end_date || "")}" required /></label></div>`
    : "";
  return `${sampleTable(stage.preview)}<div class="mapping-grid">${fields.map(field => uploadMappingSelect(field, stage.preview, suggested[field] || "", required.includes(field))).join("")}</div>${assignmentNote}${periodFields}`;
}

function stagedSourceRow() {
  const stage = currentUploadStage();
  if (!stage) return "";
  const target = uploadTargetPeriod();
  const extension = stage.mode === "google_sheet" ? "GS" : stage.mode === "comments_pdf" ? "PDF" : stage.mode === "comments_shared" ? "TEXT" : "CSV";
  const source = stage.mode === "google_sheet" ? "Google Sheets" : stage.mode === "comments_shared" ? "Shared comment" : "Local file";
  return `<div class="staged-source-list"><div class="staged-source-row"><span class="staged-source-icon">${extension}</span><div class="staged-source-details"><strong>${escapeHtml(stage.name)}</strong><small>${source} · staged, not imported</small></div><span class="staged-source-meta">${escapeHtml(target?.label || "Choose week")}</span><button class="staged-source-remove" type="button" data-upload-remove aria-label="Remove staged source">×</button></div></div>`;
}

function sheetSourceForm() {
  if (uploadSession?.view !== "sheet") return "";
  const saved = state.sheet_sources[uploadSession.kind] || {};
  return `<div class="source-note"><span>Google Sheet</span><strong>Preview a sheet to stage it</strong><small>Previewing does not import student data.</small></div><label><span>Google Sheets URL or ID</span><input id="upload-sheet-url" value="${escapeHtml(saved.spreadsheet_id || "")}" placeholder="https://docs.google.com/spreadsheets/d/..." required /></label><div class="button-row"><button class="primary-button" type="button" data-upload-sheet-preview>Preview and stage Sheet</button><button class="secondary-button" type="button" data-upload-sheet-cancel>Back</button></div>`;
}

function renderUploadModal() {
  if (!uploadSession) return;
  const copy = UPLOAD_COPY[uploadSession.kind];
  const target = uploadTargetPeriod();
  const prerequisite = uploadPrerequisite();
  const stage = currentUploadStage();
  if (uploadSession.confirming && stage && target) {
    const kindLabel = uploadSession.kind === "assignment" ? "Homework + test scores" : copy.title;
    const classSummary = stage.mode === "comments_pdf" || stage.mode === "comments_shared" ? `<div><span>Class</span><strong>${escapeHtml(state.classes.find(item => item.id === stage.classId)?.name || "")}</strong></div>` : "";
    openModal(`<div class="upload-modal-header"><div class="upload-modal-heading"><span class="eyebrow">CONFIRM UPLOAD</span><h2>Check the source and week</h2><p>Nothing has been imported yet. Review these details before continuing.</p></div><div class="upload-modal-controls"><button class="upload-modal-close" type="button" data-close-modal aria-label="Close without saving">×</button></div></div><div class="upload-confirmation-summary"><div><span>Source</span><strong>${escapeHtml(stage.name)}</strong></div><div><span>Data type</span><strong>${escapeHtml(kindLabel)}</strong></div><div><span>Week</span><strong>${escapeHtml(target.label)}</strong><small>${escapeHtml(target.start_date || "")}–${escapeHtml(target.end_date || "")}</small></div>${classSummary}${stage.mode === "comments_shared" ? `<div><span>Comment for every student</span><p class="shared-comment-preview">${escapeHtml(stage.comment)}</p></div>` : ""}</div><div class="safety-note">Choose Go back if the source, class, or week is not correct. Confirm upload will import this source only for the selection shown above.</div><div class="modal-footer upload-modal-footer"><button class="primary-button upload-save-button" type="button" data-upload-confirm>Confirm upload</button><button class="secondary-button upload-cancel-button" type="button" data-upload-confirm-back>Go back</button><span class="upload-modal-footer-note">This is the final check before data is imported.</span></div>`);
    $("#modal").classList.add("upload-modal");
    return;
  }
  const weekOptions = [...state.periods, ...uploadSession.draftPeriods]
    .map(period => `<option value="${period.id}" ${period.id === uploadSession.periodKey ? "selected" : ""}>${escapeHtml(period.label)}</option>`)
    .join("");
  const sourceDisabled = uploadSession.saving;
  const pdfChoice = COMMENT_SECTIONS[uploadSession.kind]
    ? `<button class="upload-source-choice" type="button" data-upload-manual ${sourceDisabled ? "disabled" : ""}><span class="upload-source-choice-icon">PDF</span><span><strong>Upload comments PDF</strong><small>One searchable PDF for one class; held locally until Save.</small></span></button>`
    : "";
  const saveDisabled = !stage || !target || !prerequisite.ready || uploadSession.saving;
  openModal(`<div class="upload-modal-header"><div class="upload-modal-heading"><span class="eyebrow">${copy.eyebrow}</span><h2>${copy.title}</h2><p>${copy.copy}</p></div><div class="upload-modal-controls"><label class="upload-week-control"><span>Week</span><select id="upload-week-select" aria-label="Week" ${uploadSession.saving ? "disabled" : ""}>${weekOptions}</select></label><button class="upload-add-week" type="button" data-upload-add-week ${uploadSession.saving ? "disabled" : ""}>+ Add week</button><button class="upload-modal-close" type="button" data-close-modal aria-label="Close without saving" ${uploadSession.saving ? "disabled" : ""}>×</button></div></div><div class="upload-modal-body"><div class="upload-prerequisite-copy ${prerequisite.ready ? "ready" : ""}"><span>${prerequisite.ready ? "✓" : "!"}</span><div>${escapeHtml(prerequisite.message)}</div></div><div class="upload-source-choices">${uploadSession.kind === "assignment" ? `<button class="upload-source-choice" type="button" data-upload-google ${sourceDisabled ? "disabled" : ""}><span class="upload-source-choice-icon">GS</span><span><strong>Import Google Sheet</strong><small>Preview the sheet before saving.</small></span></button>` : ""}${pdfChoice}${uploadSession.kind === "homework_comment" ? `<button class="upload-source-choice" type="button" data-upload-shared ${sourceDisabled ? "disabled" : ""}><span class="upload-source-choice-icon">TEXT</span><span><strong>Write one comment for everyone</strong><small>Applies only to the class and week you choose.</small></span></button>` : ""}</div>${sheetSourceForm()}${stagedSourceRow()}${uploadStageEditor()}</div><div class="modal-footer upload-modal-footer"><button class="primary-button upload-save-button ${saveDisabled ? "disabled" : ""}" type="button" data-upload-save ${saveDisabled ? "disabled" : ""}>${uploadSession.saving ? "Saving…" : "Save"}</button><button class="secondary-button upload-cancel-button" type="button" data-close-modal ${uploadSession.saving ? "disabled" : ""}>Cancel</button><span class="upload-modal-footer-note">Closing without Save discards the staged source. Added week options remain for this browser session.</span></div>`);
  $("#modal").classList.add("upload-modal");
  if (stage?.mode === "legacy_assignment" && target?.draft) {
    $("#upload-period-start").value = target.start_date || stage.inspection.dates[0]?.iso || "";
    $("#upload-period-end").value = target.end_date || stage.inspection.dates.at(-1)?.iso || "";
  }
  bindSamplePreviewScroll();
}

function openUploadModal(kind) {
  if (!UPLOAD_COPY[kind]) return;
  reconcileUploadDraftPeriods();
  if (!state.periods.length && !uploadDraftPeriods.length) {
    uploadDraftPeriods.push(createDraftWeek());
    persistUploadDraftPeriods();
  }
  const existingPeriod = periodById(state.selected_period_id) || state.periods[0];
  const availablePeriods = [...state.periods, ...uploadDraftPeriods];
  const preferredPeriod = availablePeriods.find(period => period.id === lastUploadPeriodKey);
  uploadSession = {
    kind,
    periodKey: preferredPeriod?.id || existingPeriod?.id || uploadDraftPeriods[0]?.id || "",
    draftPeriods: uploadDraftPeriods,
    stage: null,
    view: "sources",
    confirming: false,
    saving: false,
  };
  renderUploadModal();
}

function addUploadWeek() {
  if (!uploadSession) return;
  captureUploadFields();
  const previousDraft = uploadDraftPeriods.at(-1) || null;
  const draft = createDraftWeek(previousDraft);
  uploadDraftPeriods.push(draft);
  uploadSession.periodKey = draft.id;
  lastUploadPeriodKey = draft.id;
  persistUploadDraftPeriods();
  renderUploadModal();
}

async function stageManualUpload(file) {
  if (!file || !uploadSession) return;
  const session = uploadSession;
  try {
    const isPdf = /\.pdf$/i.test(file.name) || file.type === "application/pdf";
    if (!COMMENT_SECTIONS[session.kind] || !isPdf) {
      throw new Error("Choose a searchable comments PDF. CSV uploads are no longer used.");
    }
    session.stage = { mode: "comments_pdf", name: file.name, file, classId: "", label: file.name.replace(/\.pdf$/i, "") };
    session.view = "sources";
    renderUploadModal();
  } catch (error) {
    toast(error.message, "error");
  }
}

function beginGoogleUpload() {
  if (!uploadSession || uploadSession.kind !== "assignment") return;
  const connection = state.connections.google_sheets || {};
  if (!connection.configured || connection.status === "disconnected") {
    closeModal();
    toast("Connect and test your Google account first.", "error");
    go("connections");
    return;
  }
  captureUploadFields();
  uploadSession.view = "sheet";
  renderUploadModal();
}

async function previewUploadSheet() {
  if (!uploadSession) return;
  const session = uploadSession;
  const periodKey = session.periodKey;
  const target = uploadTargetPeriod();
  const spreadsheet = $("#upload-sheet-url")?.value.trim();
  if (!spreadsheet) return toast("Enter a Google Sheets URL or ID.", "error");
  try {
    const preview = await api("/api/google/sheets/preview", {
      method: "POST",
      body: JSON.stringify({
        spreadsheet,
        kind: session.kind,
        period_start: target?.draft ? target.start_date : "",
      }),
    });
    if (uploadSession !== session || session.periodKey !== periodKey) return;
    const detectedPeriod = preview.assignment_period;
    if (target?.draft && detectedPeriod?.start_date && detectedPeriod?.end_date) {
      target.start_date = detectedPeriod.start_date;
      target.end_date = detectedPeriod.end_date;
      persistUploadDraftPeriods();
    }
    session.stage = {
      mode: "google_sheet",
      name: `${preview.spreadsheet_name} · ${preview.worksheet}`,
      spreadsheet,
      worksheet: preview.worksheet,
      preview,
    };
    session.view = "sources";
    renderUploadModal();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function saveUpload(confirmed = false) {
  if (!uploadSession || uploadSession.saving) return;
  captureUploadFields();
  const session = uploadSession;
  const stage = currentUploadStage();
  if (!stage) return toast("Choose a file or Google Sheet before saving.", "error");
  const target = uploadTargetPeriod();
  if (!target) return toast("Choose a reporting week before saving.", "error");
  const prerequisite = uploadPrerequisite();
  if (!prerequisite.ready) return toast(prerequisite.message, "error");
  let period;
  if (target?.draft) {
    if (session.kind !== "assignment") return toast(`Import Assignment for ${target.label} first.`, "error");
    if (!target.start_date || !target.end_date) return toast("Choose the new week's start and end dates.", "error");
    if (target.start_date > target.end_date) return toast("The week start must be before its end.", "error");
    period = { period_label: target.label, period_start: target.start_date, period_end: target.end_date };
  } else if (target) {
    period = { period_id: target.id };
  } else {
    return toast("Choose a reporting week.", "error");
  }

  let url;
  let payload;
  if (stage.mode === "legacy_assignment") {
    url = "/api/csv/import";
    payload = {
      filename: stage.name,
      text: stage.text,
      period_label: target.label,
      period_start: target.start_date,
      period_end: target.end_date,
    };
  } else if (stage.mode === "comments_pdf") {
    if (!stage.classId) return toast("Choose the class represented by this Comments PDF.", "error");
    url = "/api/comments/pdf/import";
    payload = {
      filename: stage.name,
      class_id: stage.classId,
      period_id: target.id,
      comment_section: COMMENT_SECTIONS[session.kind],
    };
  } else if (stage.mode === "comments_shared") {
    if (session.kind !== "homework_comment") return toast("Shared comments are only available for 과제이행도.", "error");
    if (!stage.classId) return toast("Choose the class for this comment.", "error");
    if (!stage.comment?.trim()) return toast("Enter a comment for this class.", "error");
    url = "/api/comments/shared";
    payload = { class_id: stage.classId, period_id: target.id, comment: stage.comment.trim() };
  } else if (stage.mode === "google_sheet") {
    url = "/api/google/sheets/sync";
    payload = { kind: session.kind, spreadsheet: stage.spreadsheet, worksheet: stage.worksheet, ...period };
  } else {
    const required = requiredFields(session.kind);
    const missing = required.filter(field => !stage.mapping?.[field]);
    if (missing.length) return toast(`Map ${FIELD_LABELS[missing[0]]} before saving.`, "error");
    url = "/api/tabular/import";
    payload = { kind: session.kind, filename: stage.name, text: stage.text, mapping: stage.mapping, ...period };
  }

  if (COMMENT_SECTIONS[session.kind] && !statusForClassPeriod(stage.classId, target.id).comments_allowed) return toast("Import homework and test scores for this class and week first.", "error");

  if (!confirmed) {
    session.confirming = true;
    renderUploadModal();
    return;
  }

  session.confirming = false;
  session.saving = true;
  renderUploadModal();
  try {
    if (stage.mode === "comments_pdf") {
      payload.pdf_base64 = arrayBufferToBase64(await stage.file.arrayBuffer());
      if (uploadSession !== session) return;
    }
    let result;
    try {
      result = await api(url, { method: "POST", body: JSON.stringify(payload) });
    } catch (error) {
      if (error.code !== "replacement_confirmation_required" || !confirm(`${error.message}\n\nReplace the existing data?`)) throw error;
      result = await api(url, { method: "POST", body: JSON.stringify({ ...payload, confirm_replace: true }) });
    }
    if (uploadSession !== session) return;
    state = normalizeState(result.state);
    if (target.draft) {
      const savedPeriod = matchingSavedPeriod(target);
      const draftIndex = uploadDraftPeriods.findIndex(period => period.id === target.id);
      if (draftIndex >= 0) uploadDraftPeriods.splice(draftIndex, 1);
      if (savedPeriod) session.periodKey = savedPeriod.id;
    }
    lastUploadPeriodKey = session.periodKey || lastUploadPeriodKey;
    reconcileUploadDraftPeriods();
    closeModal();
    render();
    if (stage.mode === "comments_pdf") showCommentImportResult(result, session.kind);
    else toast(`${result.summary || "Upload saved."} · ${target.label}`);
  } catch (error) {
    if (uploadSession === session) {
      session.saving = false;
      renderUploadModal();
    }
    toast(error.message, "error");
  }
}

function showCommentImportResult(result, kind = "weekly_test_comment") {
  const sectionLabel = UPLOAD_COPY[kind]?.title || "Comments";
  const matching = result.matching || {};
  const missing = matching.missing_comments || [];
  const unmatched = matching.unmatched_comments || [];
  if (!missing.length && !unmatched.length) return toast(result.summary || "Comments saved.");
  const missingList = missing.length
    ? `<h3>Students without ${escapeHtml(sectionLabel)} (${missing.length})</h3><p>${kind === "homework_comment" ? "These students need a 과제이행도 comment before their reports can be generated." : "These students can still generate reports if they have a 과제이행도 comment and the other comment type (weekly test or general)."} Complete students remain available for reports.</p><ul>${missing.map(student => `<li><strong>${escapeHtml(student.name)}</strong>${student.school ? ` · ${escapeHtml(student.school)}` : ""}${student.grade ? ` · Grade ${escapeHtml(student.grade)}` : ""}</li>`).join("")}</ul>`
    : "";
  const unmatchedList = unmatched.length
    ? `<h3>PDF comments without a unique student match (${unmatched.length})</h3><p>These comments were not assigned and cannot be used in reports. Check the review queue in Data Inbox.</p><ul>${unmatched.map(comment => `<li><strong>${escapeHtml(comment.name)}</strong> · ${escapeHtml(comment.reason)}</li>`).join("")}</ul>`
    : "";
  openModal(`<span class="eyebrow">COMMENT MATCHING</span><h2>Some students still need attention</h2><p>${escapeHtml(matching.class_name || "")} · ${escapeHtml(matching.period_label || "")}</p><div class="safety-note">${escapeHtml(result.summary || "Matched comments saved.")}</div>${missingList}${unmatchedList}<div class="modal-footer"><button class="primary-button" type="button" data-close-modal>Close</button></div>`);
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

const SHEET_PREVIEW_RENDER_CHUNK = 40;

function sampleRowsHtml(headers, rows) {
  return rows.map(row => `<tr>${headers.map((_, index) => `<td title="${escapeHtml(row[index] || "")}">${escapeHtml(row[index] || "")}</td>`).join("")}</tr>`).join("");
}

function samplePreviewCountLabel(shown, total, itemLabel) {
  return shown === total ? `${total} ${itemLabel}` : `Showing ${shown} of ${total} ${itemLabel}`;
}

function samplePreviewHint(shown, available, total, itemLabel) {
  if (shown < available) return "Scroll down to load more preview rows.";
  if (available < total) return `${total - available} additional rows will still be processed when you save.`;
  return `Scroll vertically and horizontally to review all ${shown} ${itemLabel}.`;
}

function sampleTable(preview, options = {}) {
  const headers = Array.isArray(preview.headers) ? preview.headers : [];
  const rows = Array.isArray(preview.sample_rows) ? preview.sample_rows : [];
  const parsedTotal = Number.parseInt(preview.row_count, 10);
  const total = Math.max(rows.length, Number.isFinite(parsedTotal) ? parsedTotal : rows.length);
  const itemLabel = options.itemLabel || "rows";
  const worksheet = String(options.worksheet || preview.worksheet || "").trim();
  const sourceLabel = worksheet ? `Worksheet · ${worksheet}` : "Data preview";
  const requestedVisible = Number.parseInt(options.visibleRows, 10);
  const visibleCount = options.progressive
    ? Math.min(rows.length, Number.isFinite(requestedVisible) ? requestedVisible : SHEET_PREVIEW_RENDER_CHUNK)
    : rows.length;
  const visibleRows = rows.slice(0, visibleCount);
  const countLabel = samplePreviewCountLabel(visibleRows.length, total, itemLabel);
  const scrollCopy = samplePreviewHint(visibleRows.length, rows.length, total, itemLabel);
  const ariaLabel = `${sourceLabel}. ${countLabel}.`;
  const progressiveAttributes = options.progressive
    ? ` data-preview-scroll data-rendered-rows="${visibleRows.length}"`
    : "";
  return `<div class="sample-preview"><div class="sample-preview-header"><span>${escapeHtml(sourceLabel)}</span><strong data-preview-row-count>${escapeHtml(countLabel)}</strong></div><div class="sample-table" role="region" tabindex="0" aria-label="${escapeHtml(ariaLabel)}"${progressiveAttributes}><table><thead><tr>${headers.map(header => `<th title="${escapeHtml(header)}">${escapeHtml(header)}</th>`).join("")}</tr></thead><tbody>${sampleRowsHtml(headers, visibleRows)}</tbody></table></div><small class="sample-preview-hint" data-preview-row-hint>${escapeHtml(scrollCopy)}</small></div>`;
}

function appendSamplePreviewRows(scroller) {
  const stage = currentUploadStage();
  if (!stage || stage.mode !== "google_sheet") return;
  const preview = stage.preview;
  const rows = Array.isArray(preview.sample_rows) ? preview.sample_rows : [];
  const headers = Array.isArray(preview.headers) ? preview.headers : [];
  const rendered = Number.parseInt(scroller.dataset.renderedRows, 10) || 0;
  const next = Math.min(rows.length, rendered + SHEET_PREVIEW_RENDER_CHUNK);
  if (next <= rendered) return;
  scroller.querySelector("tbody")?.insertAdjacentHTML("beforeend", sampleRowsHtml(headers, rows.slice(rendered, next)));
  scroller.dataset.renderedRows = String(next);
  stage.previewRowsShown = next;
  const total = Math.max(rows.length, Number.parseInt(preview.row_count, 10) || rows.length);
  const itemLabel = "student rows";
  const count = scroller.closest(".sample-preview")?.querySelector("[data-preview-row-count]");
  const hint = scroller.closest(".sample-preview")?.querySelector("[data-preview-row-hint]");
  if (count) count.textContent = samplePreviewCountLabel(next, total, itemLabel);
  if (hint) hint.textContent = samplePreviewHint(next, rows.length, total, itemLabel);
}

function bindSamplePreviewScroll() {
  const scroller = $("#modal [data-preview-scroll]");
  if (!scroller) return;
  scroller.addEventListener("scroll", () => {
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 36) {
      appendSamplePreviewRows(scroller);
    }
  }, { passive: true });
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
      closeModal(); render(); showCommentImportResult(result);
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

const PDF_REGION_LABELS = {
  student_name: "Student name",
  class_name: "Class / subject",
  school_grade: "School and grade",
  reporting_week: "Reporting week",
  homework_chart: "Homework graph",
  homework_summary: "Homework summary",
  weekly_test_chart: "Weekly-test graph",
  weekly_test_summary: "Weekly-test summary",
  teacher_comment: "Teacher comment",
};

function addPdfTemplate(classId) {
  pendingPdfTemplateClass = classId;
  $("#company-template-input").click();
}

async function uploadPdfTemplate(file) {
  const classId = pendingPdfTemplateClass;
  pendingPdfTemplateClass = "";
  if (!classId || !file) return;
  try {
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") throw new Error("Choose a PDF template.");
    const result = await api(`/api/templates/${classId}/pdf-upload`, {
      method: "POST",
      body: JSON.stringify({ filename: file.name, pdf_base64: arrayBufferToBase64(await file.arrayBuffer()) }),
    });
    state = normalizeState(result.state);
    render();
    toast(result.message, "warning");
    showPdfMapping(classId);
  } catch (error) {
    toast(error.message, "error");
  } finally {
    $("#company-template-input").value = "";
  }
}

function mappingPercent(value) {
  return Math.round(Number(value || 0) * 10000) / 100;
}

const CONTINUATION_SOURCES = {
  teacher_comment: "General / Teacher Comment",
};

function pdfContinuationRegions(mapping, contentGuide = {}) {
  const existing = Array.isArray(mapping.continuation_regions) ? mapping.continuation_regions : [];
  const byPage = new Map(existing.map(region => [Number(region.page), { ...region }]));
  const blankPageCount = Math.max(1, Number(mapping.page_count || 1));
  const finalPage = Math.max(blankPageCount + 1, 2, ...existing.map(region => Number(region.page || 0)));
  const sampleContinuation = (contentGuide.sections || [])
    .some(section => section.section === "general" && Number(section.page || 1) > 1)
    ? "teacher_comment" : "";
  return Array.from({ length: finalPage - 1 }, (_, index) => index + 2).map(page => byPage.get(page) || {
    key: `continuation_${page}`,
    source_key: sampleContinuation,
    content_type: "text",
    page,
    template_page: Math.min(page, Number(mapping.page_count || 1)),
    box: { x: .058, y: .08, width: .884, height: .82 },
    font_name: "auto",
    font_size: 10,
    minimum_font_size: 7,
    line_spacing: 1.2,
    align: "left",
    required: false,
  });
}

function pdfRegionEditor(region, pageCount) {
  const box = region.box || {};
  const pageOptions = Array.from({ length: pageCount }, (_, index) => `<option value="${index + 1}" ${Number(region.page || 1) === index + 1 ? "selected" : ""}>Page ${index + 1}</option>`).join("");
  const fontFields = region.content_type === "text"
    ? `<label><span>Font size</span><input data-map-font-size type="number" min="7" max="36" step="0.25" value="${escapeHtml(region.font_size || 8.5)}" /></label><label><span>Minimum</span><input data-map-font-min type="number" min="7" max="36" step="0.25" value="${escapeHtml(region.minimum_font_size || 7)}" /></label><label><span>Alignment</span><select data-map-align><option value="left" ${region.align === "left" ? "selected" : ""}>Left</option><option value="center" ${region.align === "center" ? "selected" : ""}>Center</option><option value="right" ${region.align === "right" ? "selected" : ""}>Right</option></select></label>`
    : `<div class="mapping-region-type">Deterministic line graph</div>`;
  return `<article class="mapping-region-row" data-mapping-region="${escapeHtml(region.key)}"><header><div><strong>${escapeHtml(PDF_REGION_LABELS[region.key] || region.key)}</strong><small>${escapeHtml(region.content_type)}</small></div><label><span>Page</span><select data-map-page>${pageOptions}</select></label></header><div class="mapping-coordinate-grid"><label><span>Left %</span><input data-map-field="x" type="number" min="0" max="100" step="0.1" value="${mappingPercent(box.x)}" /></label><label><span>Bottom %</span><input data-map-field="y" type="number" min="0" max="100" step="0.1" value="${mappingPercent(box.y)}" /></label><label><span>Width %</span><input data-map-field="width" type="number" min="0.1" max="100" step="0.1" value="${mappingPercent(box.width)}" /></label><label><span>Height %</span><input data-map-field="height" type="number" min="0.1" max="100" step="0.1" value="${mappingPercent(box.height)}" /></label>${fontFields}</div></article>`;
}

function pdfContinuationEditor(region, pageCount) {
  const box = region.box || {};
  const sourceOptions = [`<option value="">Unused</option>`, ...Object.entries(CONTINUATION_SOURCES).map(([value, label]) => `<option value="${value}" ${region.source_key === value ? "selected" : ""}>${escapeHtml(label)}</option>`)].join("");
  const templatePages = Array.from({ length: pageCount }, (_, index) => `<option value="${index + 1}" ${Number(region.template_page || 1) === index + 1 ? "selected" : ""}>Blank page ${index + 1}</option>`).join("");
  return `<article class="mapping-region-row mapping-continuation-row" data-continuation-region="${escapeHtml(region.key)}" data-map-page="${Number(region.page)}"><header><div><strong>Continuation page ${Number(region.page)}</strong><small>Teacher comment only</small></div><label><span>Content</span><select data-map-source>${sourceOptions}</select></label><label><span>Design source</span><select data-map-template-page>${templatePages}</select></label></header><div class="mapping-coordinate-grid"><label><span>Left %</span><input data-map-field="x" type="number" min="0" max="100" step="0.1" value="${mappingPercent(box.x)}" /></label><label><span>Bottom %</span><input data-map-field="y" type="number" min="0" max="100" step="0.1" value="${mappingPercent(box.y)}" /></label><label><span>Width %</span><input data-map-field="width" type="number" min="0.1" max="100" step="0.1" value="${mappingPercent(box.width)}" /></label><label><span>Height %</span><input data-map-field="height" type="number" min="0.1" max="100" step="0.1" value="${mappingPercent(box.height)}" /></label><label><span>Font size</span><input data-map-font-size type="number" min="7" max="36" step="0.25" value="${escapeHtml(region.font_size || 10)}" /></label><label><span>Minimum</span><input data-map-font-min type="number" min="7" max="36" step="0.25" value="${escapeHtml(region.minimum_font_size || 7)}" /></label><label><span>Alignment</span><select data-map-align><option value="left" ${region.align === "left" ? "selected" : ""}>Left</option><option value="center" ${region.align === "center" ? "selected" : ""}>Center</option><option value="right" ${region.align === "right" ? "selected" : ""}>Right</option></select></label></div></article>`;
}

function showPdfMapping(classId) {
  const classItem = classById(classId);
  const pdf = templateForClass(classId)?.pdf_template;
  if (!pdf) return toast("Upload a company PDF template first.", "error");
  const mapping = pdf.mapping || {};
  const regions = (mapping.regions || []).map(region => pdfRegionEditor(region, pdf.page_count || 1)).join("");
  const guide = templateForClass(classId)?.example_profile?.content_guide || {};
  const continuations = pdfContinuationRegions(mapping, guide).map(region => pdfContinuationEditor(region, pdf.page_count || 1)).join("");
  openModal(`<div class="mapping-modal-header"><div><span class="eyebrow">PDF TEMPLATE MAPPING</span><h2>${escapeHtml(classItem?.name || "Class")} · version ${pdf.version}</h2><p>Confirm where Brightline should place each student field. Coordinates are percentages of the PDF page, measured from the bottom-left.</p></div><a class="secondary-button button-link" href="${escapeHtml(pdf.preview_url)}" target="_blank" rel="noopener">Open source PDF</a></div><label><span>Mapping name</span><input id="pdf-mapping-name" value="${escapeHtml(mapping.name || "Company report layout")}" /></label><div class="mapping-region-list">${regions}</div><section class="mapping-continuation-section"><h3>Teacher-comment continuation</h3><p>Weekly Test feedback never continues. Extra teacher-comment pages clone a selected blank-template page and are used only when the final panel is at least half full.</p><div class="mapping-region-list">${continuations}</div></section><div class="safety-note">Publish only after checking the source PDF. Brightline will never guess a missing student match or silently clip an overflowing comment.</div><div class="modal-footer"><button class="secondary-button" id="pdf-map-draft" type="button">Save draft</button><button class="primary-button" id="pdf-map-publish" type="button">Publish mapping</button><button class="secondary-button" type="button" data-close-modal>Cancel</button></div>`);
  $("#modal").classList.add("template-mapping-modal");
  const designNotice = document.createElement("p");
  designNotice.textContent = "The original PDF controls page design and mapped typography. Completed samples guide content sections only. Check these positions against the original PDF before publishing.";
  $(".mapping-region-list").before(designNotice);
  $("#pdf-map-draft").onclick = () => savePdfMapping(classId, false);
  $("#pdf-map-publish").onclick = () => savePdfMapping(classId, true);
}

function collectPdfMapping(classId) {
  const pdf = templateForClass(classId)?.pdf_template;
  const mapping = structuredClone(pdf?.mapping || {});
  mapping.name = $("#pdf-mapping-name").value.trim() || "Company report layout";
  mapping.layout = "uploaded_pdf";
  mapping.regions = (mapping.regions || []).map(region => {
    const row = document.querySelector(`[data-mapping-region="${region.key}"]`);
    const updated = { ...region, page: Number(row.querySelector("[data-map-page]").value), box: { ...region.box } };
    row.querySelectorAll("[data-map-field]").forEach(input => { updated.box[input.dataset.mapField] = Number(input.value) / 100; });
    if (region.content_type === "text") {
      updated.font_size = Number(row.querySelector("[data-map-font-size]").value);
      updated.minimum_font_size = Number(row.querySelector("[data-map-font-min]").value);
      updated.align = row.querySelector("[data-map-align]").value;
    }
    return updated;
  });
  mapping.continuation_regions = $$('[data-continuation-region]').map(row => {
    const sourceKey = row.querySelector("[data-map-source]").value;
    if (!sourceKey) return null;
    const box = {};
    row.querySelectorAll("[data-map-field]").forEach(input => { box[input.dataset.mapField] = Number(input.value) / 100; });
    return {
      key: row.dataset.continuationRegion,
      source_key: sourceKey,
      content_type: "text",
      page: Number(row.dataset.mapPage),
      template_page: Number(row.querySelector("[data-map-template-page]").value),
      box,
      font_name: "auto",
      font_size: Number(row.querySelector("[data-map-font-size]").value),
      minimum_font_size: Number(row.querySelector("[data-map-font-min]").value),
      line_spacing: 1.2,
      align: row.querySelector("[data-map-align]").value,
      required: false,
    };
  }).filter(Boolean);
  return mapping;
}

async function savePdfMapping(classId, publish) {
  const pdf = templateForClass(classId)?.pdf_template;
  if (!pdf) return;
  try {
    const result = await api(`/api/templates/${classId}/pdf-map`, {
      method: "POST",
      body: JSON.stringify({ template_id: pdf.id, mapping: collectPdfMapping(classId), publish }),
    });
    state = normalizeState(result.state);
    closeModal();
    render();
    toast(result.message, publish ? "success" : "warning");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function disconnectPdfTemplate(classId) {
  const pdf = templateForClass(classId)?.pdf_template;
  if (!pdf || !confirm(`Remove ${pdf.name} version ${pdf.version} from ${classById(classId)?.name || "this class"}? Generated report history will remain available.`)) return;
  try {
    const result = await api(`/api/templates/${classId}/pdf-disconnect`, {
      method: "POST",
      body: JSON.stringify({ template_id: pdf.id, confirm: true }),
    });
    state = normalizeState(result.state);
    render();
    toast(result.message);
  } catch (error) {
    toast(error.message, "error");
  }
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

async function uploadExamples(files) {
  const classId = pendingExampleClass;
  const selected = [...(files || [])];
  if (!selected.length || !classId) return;
  let uploaded = 0;
  const failures = [];
  try {
    for (const file of selected) {
      try {
        const result = await api(`/api/templates/${classId}/example`, { method: "POST", body: JSON.stringify({ filename: file.name, pdf_base64: arrayBufferToBase64(await file.arrayBuffer()) }) });
        state = normalizeState(result.state);
        uploaded += 1;
      } catch (error) {
        failures.push(`${file.name}: ${error.message}`);
      }
    }
    render();
    if (failures.length) {
      toast(`${uploaded} example${uploaded === 1 ? "" : "s"} analyzed; ${failures.length} could not be saved.`, "error");
    } else {
      toast(`${uploaded} example report${uploaded === 1 ? "" : "s"} saved and analyzed.`);
    }
  } finally {
    pendingExampleClass = "";
    $("#example-report-input").value = "";
  }
}

function showExampleAnalysis(classId) {
  const classItem = classById(classId);
  const template = templateForClass(classId) || {};
  const examples = template.examples || [];
  const profile = template.example_profile || {};
  const chips = values => values?.length ? values.map(value => `<span class="placeholder-chip">${escapeHtml(value)}</span>`).join("") : '<span class="muted">None detected</span>';
  const rows = examples.map(example => {
    const analysis = example.analysis || {};
    const summary = analysis.summary || {};
    return `<article class="example-analysis-row"><div><strong>${escapeHtml(example.filename)}</strong><small>${escapeHtml(formatDateTime(example.uploaded_at))}</small></div><div><span>${analysis.page_count || 0} page${analysis.page_count === 1 ? "" : "s"}${analysis.multi_page ? " · continuation layout present" : ""}</span><small>${escapeHtml((summary.font_names || []).slice(0, 4).join(", ") || "Fonts unavailable")} · ${summary.image_xobject_count || 0} image/possible graph region${summary.image_xobject_count === 1 ? "" : "s"}</small></div><button class="table-action" type="button" data-example-delete="${escapeHtml(example.id)}" data-example-class="${classId}">Delete</button></article>`;
  }).join("");
  openModal(`<span class="eyebrow">SAMPLE REPORT ANALYSIS</span><h2>${escapeHtml(classItem?.name || "Class")} sample reports</h2><p>Brightline compares structural clues across the completed PDF reports uploaded for this class.</p><div class="analysis-summary-grid"><div><span>Samples</span><strong>${profile.sample_count || 0}</strong></div><div><span>Multi-page</span><strong>${profile.multi_page_count || 0}</strong></div><div><span>Image / graph regions</span><strong>${profile.image_xobject_count || 0}</strong></div></div><div class="analysis-section"><strong>Common fonts</strong><div class="placeholder-list">${chips(profile.common_fonts)}</div></div><div class="analysis-section"><strong>Likely report sections</strong><div class="placeholder-list">${chips(profile.section_clues)}</div></div><div class="example-analysis-list">${rows || emptyState("No sample reports", "Upload a completed PDF for this class.")}</div><div class="safety-note">Clues are advisory: font names and coordinates can be approximate, image-heavy areas are only possible graph regions, and scanned PDFs need future OCR.</div><div class="modal-footer"><button class="secondary-button" type="button" data-template-example="${classId}">Upload another sample</button><button class="primary-button" data-close-modal>Done</button></div>`);
  const guide = profile.content_guide || {};
  const sectionNames = { homework: "Homework / learning feedback", weekly_test: "Weekly test feedback", general: "General teacher feedback" };
  const observations = (guide.sections || []).map(section => `<article class="analysis-section"><strong>${escapeHtml(sectionNames[section.section] || section.section)}</strong><p>Sample heading: ${escapeHtml(section.label)} · Page ${section.page} → ${escapeHtml(PDF_REGION_LABELS[section.target_region] || section.target_region)}</p><small>${escapeHtml((section.topics || []).join(" · ") || "No reliable content topics extracted")} · ${section.text_characters || 0} characters observed${section.graph_type_labels?.length ? ` · Chart labels: ${escapeHtml(section.graph_type_labels.join(", "))}` : ""}</small></article>`).join("");
  const contentGuide = document.createElement("div");
  const typography = (guide.sections || []).filter(section => section.preferred_font_size).map(section => `<p>${escapeHtml(sectionNames[section.section] || section.section)} · Sample page ${section.page}: ${escapeHtml((section.body_fonts || []).join(", "))}, ${Number(section.preferred_font_size).toFixed(1)} pt · ${Math.round(Number(section.text_fill_ratio || 0) * 100)}% panel fill. Text never shrinks below 7 pt.</p>`).join("");
  contentGuide.innerHTML = `<h3>Content placement guidance</h3><p>Original template = design. Completed sample = content reference. Brightline uses section headings and class-specific content patterns to separate test overviews from detailed teacher feedback, preserving the source wording. Review the preview for ambiguous passages.</p>${observations || "<p>No reliable section headings were extracted. Review the sample before relying on automatic placement.</p>"}<h3>Typography and pagination</h3>${typography}<p>Weekly Test feedback stays in its original box. Additional teacher-comment pages are kept only when the final comment panel is at least half filled. Missing test feedback uses the academy-requested missed-test message and a single-page report; stored scores remain unchanged.</p>${(guide.warnings || []).map(warning => `<p class="safety-note">${escapeHtml(warning)}</p>`).join("")}`;
  $(".example-analysis-list").before(contentGuide);
  $("#modal > p").textContent = "Brightline analyzes each class's sample headings, content topics, fonts and image positions. Sample wording and student facts are never copied into another student's report.";
  $("#modal > .safety-note").textContent = "The original template always controls design. Unreadable text and uncertain sections need review. Recognized original panels allow automatic fitting and continuation; other layouts need confirmed mapping. Missing sample fonts use a Unicode fallback. Graph images alone do not establish graph type.";
}

async function deleteExample(classId, exampleId) {
  const template = templateForClass(classId) || {};
  const example = (template.examples || []).find(item => item.id === exampleId);
  if (!example || !confirm(`Delete ${example.filename} from this class's local examples?`)) return;
  try {
    const result = await api(`/api/templates/${classId}/example-delete`, { method: "POST", body: JSON.stringify({ example_id: exampleId, confirm: true }) });
    state = normalizeState(result.state);
    render();
    showExampleAnalysis(classId);
    toast("Example report deleted.");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function configureGoogle(service) {
  const connection = state.connections[service] || {};
  if (!connection.oauth_client_configured) {
    toast(connection.error || "Google setup is missing. Add your OAuth client JSON using the setup instructions below.", "error");
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
    const upload = event.target.closest("[data-upload-kind]");
    if (upload) { event.preventDefault(); openUploadModal(upload.dataset.uploadKind); return; }
    if (event.target.closest("[data-upload-add-week]")) { event.preventDefault(); addUploadWeek(); return; }
    if (event.target.closest("[data-upload-google]")) { event.preventDefault(); beginGoogleUpload(); return; }
    if (event.target.closest("[data-upload-manual]")) {
      event.preventDefault();
      if (COMMENT_SECTIONS[uploadSession?.kind]) $("#comments-file-input")?.click();
      return;
    }
    if (event.target.closest("[data-upload-shared]")) {
      event.preventDefault();
      if (uploadSession?.kind === "homework_comment") {
        setUploadStage({ mode: "comments_shared", name: "Shared 과제이행도 comment", classId: "", comment: "" });
        uploadSession.view = "sources";
        renderUploadModal();
      }
      return;
    }
    if (event.target.closest("[data-upload-remove]")) {
      event.preventDefault();
      if (uploadSession) { setUploadStage(null); uploadSession.view = "sources"; renderUploadModal(); }
      return;
    }
    if (event.target.closest("[data-upload-sheet-preview]")) { event.preventDefault(); await previewUploadSheet(); return; }
    if (event.target.closest("[data-upload-sheet-cancel]")) {
      event.preventDefault();
      if (uploadSession) { uploadSession.view = "sources"; renderUploadModal(); }
      return;
    }
    if (event.target.closest("[data-upload-confirm]")) { event.preventDefault(); await saveUpload(true); return; }
    if (event.target.closest("[data-upload-confirm-back]")) {
      event.preventDefault();
      if (uploadSession) { uploadSession.confirming = false; renderUploadModal(); }
      return;
    }
    if (event.target.closest("[data-upload-save]")) { event.preventDefault(); await saveUpload(); return; }
    const manual = event.target.closest("[data-manual-kind]");
    if (manual) {
      if (manual.dataset.manualKind === "comments") $("#comments-file-input")?.click();
      return;
    }
    const sheet = event.target.closest("[data-sheet-kind]");
    if (sheet) { openGoogleSheetImport(sheet.dataset.sheetKind); return; }
    const issue = event.target.closest("[data-issue]");
    if (issue) { reviewIssue(issue.dataset.issue); return; }
    const pdfUpload = event.target.closest("[data-template-pdf-upload]");
    if (pdfUpload) { addPdfTemplate(pdfUpload.dataset.templatePdfUpload); return; }
    const pdfMap = event.target.closest("[data-template-pdf-map]");
    if (pdfMap) { showPdfMapping(pdfMap.dataset.templatePdfMap); return; }
    const pdfDisconnect = event.target.closest("[data-template-pdf-disconnect]");
    if (pdfDisconnect) { await disconnectPdfTemplate(pdfDisconnect.dataset.templatePdfDisconnect); return; }
    const connect = event.target.closest("[data-template-connect]");
    if (connect) { connectTemplate(connect.dataset.templateConnect); return; }
    const test = event.target.closest("[data-template-test]");
    if (test) { await testTemplate(test.dataset.templateTest); return; }
    const example = event.target.closest("[data-template-example]");
    if (example) { if (example.closest("#modal")) closeModal(); addExample(example.dataset.templateExample); return; }
    const analysis = event.target.closest("[data-template-analysis]");
    if (analysis) { showExampleAnalysis(analysis.dataset.templateAnalysis); return; }
    const exampleDelete = event.target.closest("[data-example-delete]");
    if (exampleDelete) { await deleteExample(exampleDelete.dataset.exampleClass, exampleDelete.dataset.exampleDelete); return; }
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
    const routing = event.target.closest("[data-routing-student]");
    if (routing) { showRoutingReview(routing.dataset.routingStudent); return; }
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
    if (event.target === $("#modal-backdrop") && !uploadSession?.saving) closeModal();
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
      if (event.target.matches("#upload-week-select") && uploadSession) {
        captureUploadFields();
        uploadSession.periodKey = event.target.value;
        if (uploadSession.periodKey) lastUploadPeriodKey = uploadSession.periodKey;
        persistUploadDraftPeriods();
        uploadSession.view = "sources";
        renderUploadModal();
      }
    } catch (error) { toast(error.message, "error"); await loadState(); }
  });

  $$('[data-upload-file-kind]').forEach(input => {
    input.onchange = async event => {
      const file = event.target.files[0];
      event.target.value = "";
      if (!uploadSession || !COMMENT_SECTIONS[uploadSession.kind]) return;
      await stageManualUpload(file);
    };
  });
  $("#example-report-input").onchange = event => uploadExamples(event.target.files);
  $("#company-template-input").onchange = event => uploadPdfTemplate(event.target.files[0]);
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
      clearUploadDraftPeriods();
      state = normalizeState(result.state); render(); toast("Local student data cleared.");
    } catch (error) { toast(error.message, "error"); }
  };
}

bindEvents();
go(window.location.hash.slice(1) || "inbox");
loadState();
