let state = {
  classes: [],
  students: [],
  files: [],
  issues: [],
  periods: [],
  reports: [],
  selected_period_id: "",
  template: { name: "Royal IB report template", ready: false, pages: 0, fields: [] },
  reporting_period: { id: "", label: "", start: "", end: "" }
};

let currentPage = "dashboard";
let activeRequests = 0;

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[character]);
}

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function formatPeriod() {
  const { start, end } = state.reporting_period;
  if (!start || !end) return "No reporting period selected";
  return state.reporting_period.label || `${start} to ${end}`;
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function initials(name = "") {
  const pieces = name.trim().split(/\s+/).filter(Boolean);
  if (!pieces.length) return "?";
  if (pieces.length === 1) return pieces[0].slice(0, 2).toUpperCase();
  return pieces.map(piece => piece[0]).join("").slice(0, 2).toUpperCase();
}

function classById(id) {
  return state.classes.find(item => item.id === id);
}

function periodById(id) {
  return state.periods.find(item => item.id === id);
}

function periodOptions(selectedId = "") {
  return state.periods.map(period => `<option value="${period.id}" ${period.id === selectedId ? "selected" : ""}>${escapeHtml(period.label)} · ${period.start_date} to ${period.end_date}</option>`).join("");
}

function studentsForClass(classId) {
  return state.students.filter(student => student.class_id === classId);
}

function classStats(classId) {
  const students = studentsForClass(classId);
  return {
    students,
    ready: students.filter(student => student.ready).length,
    homework: students.filter(student => Number(student.homework_completed) + Number(student.homework_incomplete) > 0).length,
    comments: students.filter(student => student.latest_exam?.comment).length
  };
}

function setLoading(loading) {
  activeRequests += loading ? 1 : -1;
  activeRequests = Math.max(0, activeRequests);
  $("#loading-bar").classList.toggle("hidden", activeRequests === 0);
}

async function api(path, options = {}) {
  setLoading(true);
  try {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) }
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  } finally {
    setLoading(false);
  }
}

function toast(message, kind = "success") {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast ${kind}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.add("hidden"), 3500);
}

function openModal(content) {
  $("#modal").innerHTML = `<button class="modal-close" aria-label="Close">×</button>${content}`;
  $("#modal-backdrop").classList.remove("hidden");
  $(".modal-close").onclick = closeModal;
}

function closeModal() {
  $("#modal-backdrop").classList.add("hidden");
}

function go(page) {
  if (!document.getElementById(page)) page = "dashboard";
  currentPage = page;
  $$(".page").forEach(element => element.classList.toggle("active", element.id === page));
  $$(".main-nav a").forEach(link => link.classList.toggle("active", link.dataset.nav === page));
  const titles = {
    dashboard: ["REPORTING PERIOD", "Report overview"],
    classes: ["CLASS DIRECTORY", "Classes"],
    students: ["NORMALIZED RECORDS", "Students"],
    inbox: ["AUTOMATION LAYER", "Data inbox"],
    reports: ["GENERATED OUTPUT", "Reports by class"],
    templates: ["ACADEMY-WIDE DESIGN", "Report template"]
  };
  $("#page-kicker").textContent = titles[page][0];
  $("#page-title").textContent = titles[page][1];
  window.location.hash = page;
  window.scrollTo({ top: 0, behavior: "smooth" });
  $(".sidebar").classList.remove("open");
}

function render() {
  const total = state.students.length;
  const ready = state.students.filter(student => student.ready).length;
  const issueCount = state.issues.length;
  $("#ready-count").textContent = ready;
  $("#total-count").textContent = total;
  $("#readiness-progress").style.width = `${total ? ready / total * 100 : 0}%`;
  $("#readiness-copy").textContent = total
    ? `${total - ready} student record${total - ready === 1 ? "" : "s"} still need homework or a matched comment.`
    : "Import a homework CSV to begin.";
  $("#inbox-badge").textContent = issueCount;
  $("#issue-count").textContent = issueCount;
  $("#period-label").textContent = formatPeriod();
  const periodSelect = $("#global-period-select");
  periodSelect.innerHTML = state.periods.length ? periodOptions(state.selected_period_id) : `<option value="">No period yet</option>`;
  periodSelect.disabled = !state.periods.length;
  $("#academy-summary").textContent = `${state.classes.length} classes · ${total} students`;
  $("#next-action-title").textContent = !total ? "Import academy data" : issueCount ? `Resolve ${issueCount} uncertain match${issueCount === 1 ? "" : "es"}` : ready < total ? "Add comments for each class" : "Reports are ready";
  $("#next-action-copy").textContent = !total ? "Start with the multi-class homework CSV." : issueCount ? "Confirm the correct school and grade before reporting." : ready < total ? "Upload each class's weekly exam PDF." : "Download by student, class, or academy.";
  renderCoverage();
  renderClassHealth();
  renderClasses();
  renderStudents();
  renderFiles();
  renderIssues();
  renderReports();
  renderTemplate();
}

function renderCoverage() {
  const total = state.students.length;
  const dimensions = [
    ["Student records", total, total, "Name, school, grade, and class"],
    ["Homework", state.students.filter(student => Number(student.homework_completed) + Number(student.homework_incomplete) > 0).length, total, "A/B complete · F incomplete"],
    ["Weekly tests", state.students.filter(student => student.exams.length).length, total, "Exam history accumulates"],
    ["Teacher comments", state.students.filter(student => student.latest_exam?.comment).length, total, "Matched inside one selected class"]
  ];
  $("#coverage-grid").innerHTML = dimensions.map(([label, value, maximum, copy]) => {
    const complete = maximum > 0 && value === maximum;
    return `<article class="coverage-item"><div class="coverage-top"><span>${escapeHtml(label)}</span><span class="status-pill ${complete ? "" : "warn"}">${complete ? "Complete" : "Needs data"}</span></div><div class="coverage-numbers">${value} <small>/ ${maximum}</small></div><small>${escapeHtml(copy)}</small><div class="mini-progress"><i style="width:${maximum ? value / maximum * 100 : 0}%"></i></div></article>`;
  }).join("");
}

function renderClassHealth() {
  if (!state.classes.length) {
    $("#class-health-list").innerHTML = emptyState("No classes yet", "Classes appear after the homework CSV is imported.");
    return;
  }
  $("#class-health-list").innerHTML = state.classes.map((classItem, index) => {
    const stats = classStats(classItem.id);
    return `<button class="class-row" data-report-class="${classItem.id}"><span class="class-status">${String(index + 1).padStart(2, "0")}</span><div><h3>${escapeHtml(classItem.name)}</h3><p>${stats.ready} of ${stats.students.length} reports ready · ${escapeHtml(classItem.teacher || "Teacher not specified")}</p></div><span class="issue-amount ${stats.ready === stats.students.length && stats.students.length ? "good" : ""}">${stats.ready}/${stats.students.length} ready →</span></button>`;
  }).join("");
}

function renderClasses() {
  if (!state.classes.length) {
    $("#class-grid").innerHTML = emptyState("Import your first class list", "The multi-class CSV creates these sections automatically.", true);
    return;
  }
  $("#class-grid").innerHTML = state.classes.map(classItem => {
    const stats = classStats(classItem.id);
    const allReady = stats.students.length > 0 && stats.ready === stats.students.length;
    return `<article class="class-card" data-report-class="${classItem.id}"><div class="class-card-top"><span class="subject">${escapeHtml(classItem.subject || "Academy class")}</span><span class="status-pill ${allReady ? "" : "warn"}">${allReady ? "Ready" : `${stats.students.length - stats.ready} pending`}</span></div><h2>${escapeHtml(classItem.name)}</h2><p>${escapeHtml(classItem.teacher || "Teacher not specified")} · ${stats.students.length} students</p><div class="class-stats"><div><b>${stats.ready}/${stats.students.length}</b><small>reports ready</small></div><div><b>${stats.homework}/${stats.students.length}</b><small>homework</small></div><div><b>${stats.comments}/${stats.students.length}</b><small>comments</small></div></div></article>`;
  }).join("");
}

function renderStudents() {
  const filter = $("#student-class-filter");
  const selected = filter.value || "all";
  filter.innerHTML = `<option value="all">All classes</option>${state.classes.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("")}`;
  filter.value = state.classes.some(item => item.id === selected) ? selected : "all";
  const query = $("#student-search").value.trim().toLowerCase();
  const students = state.students.filter(student => {
    const classMatch = filter.value === "all" || student.class_id === filter.value;
    const searchMatch = `${student.name} ${student.school} ${student.grade}`.toLowerCase().includes(query);
    return classMatch && searchMatch;
  });
  $("#students-table").innerHTML = students.length ? students.map(student => {
    const exam = student.latest_exam;
    const homework = student.homework_percent === null ? "Missing" : `${formatNumber(student.homework_percent)}%`;
    const test = exam ? `${escapeHtml(exam.label)} · ${formatNumber(exam.score)}/${formatNumber(exam.max_score)}` : "Missing";
    return `<tr><td><div class="student-name"><span class="student-avatar">${escapeHtml(initials(student.name))}</span><span>${escapeHtml(student.name)}<small>${escapeHtml(student.school || "School missing")} · ${escapeHtml(student.grade || "Grade missing")}</small></span></div></td><td>${escapeHtml(student.class_name)}</td><td>${escapeHtml(student.period_label || "Not selected")}</td><td>${homework}</td><td>${test}</td><td>${exam?.comment ? "Complete" : "Missing"}</td><td><span class="status-pill ${student.ready ? "" : "warn"}">${student.ready ? "Ready" : "Needs data"}</span></td></tr>`;
  }).join("") : `<tr><td colspan="7">${emptyState("No matching students", state.students.length ? "Try a different search or class." : "Import the homework CSV to create student records.")}</td></tr>`;
}

function renderFiles() {
  $("#upload-count").textContent = `${state.files.length} file${state.files.length === 1 ? "" : "s"}`;
  if (!state.files.length) {
    $("#file-list").innerHTML = emptyState("No files imported", "Start with the multi-class homework CSV.");
    return;
  }
  const labels = { homework_csv: "Student and homework data", comments_pdf: "Teacher comments", report_template: "Report template" };
  const icons = { homework_csv: "CSV", comments_pdf: "PDF", report_template: "PDF" };
  $("#file-list").innerHTML = state.files.map(file => `<div class="file-row"><span class="file-icon">${icons[file.kind] || "FILE"}</span><div class="file-info"><strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(labels[file.kind] || file.kind)} · ${escapeHtml(file.summary)}${file.period_label ? ` · ${escapeHtml(file.period_label)}` : ""}</small></div><div class="file-meta"><span class="status-pill ${file.state === "review" ? "warn" : ""}">${file.state === "review" ? "Review" : "Imported"}</span><time>${escapeHtml(formatDateTime(file.created_at))}</time></div></div>`).join("");
}

function renderIssues() {
  if (!state.issues.length) {
    $("#issue-list").innerHTML = `<div class="review-empty"><span>✓</span><strong>All clear</strong><p>No uncertain student matches are waiting.</p></div>`;
    return;
  }
  $("#issue-list").innerHTML = state.issues.map(issue => `<button class="issue-item" data-issue="${issue.id}"><strong>${escapeHtml(issue.subject_name)}</strong><span>${issue.kind === "ambiguous_comment" ? `${issue.candidates.length} possible students` : "No exact student match"} · ${escapeHtml(issue.source_name)}${issue.period_label ? ` · ${escapeHtml(issue.period_label)}` : ""}</span><b>Review →</b></button>`).join("");
}

function renderReports() {
  const ready = state.students.filter(student => student.ready).length;
  $("#report-class-count").textContent = state.classes.length;
  $("#report-ready-count").textContent = ready;
  $("#report-period").textContent = state.reporting_period.label || "Class-specific";
  $("#download-all").classList.toggle("disabled", ready === 0);
  $("#report-history-count").textContent = `${state.reports.length} archived`;
  $("#report-history-table").innerHTML = state.reports.length ? state.reports.map(report => `<tr><td>${escapeHtml(report.student_name)}</td><td>${escapeHtml(report.class_name)}</td><td>${escapeHtml(report.period_label)}</td><td>Revision ${report.revision}</td><td>${escapeHtml(formatDateTime(report.generated_at))}</td><td><a class="table-action button-link" href="/api/reports/archive/${report.id}">Download</a></td></tr>`).join("") : `<tr><td colspan="6">${emptyState("No archived reports", "Downloaded student and class reports will appear here.")}</td></tr>`;
  if (!state.classes.length) {
    $("#reports-by-class").innerHTML = emptyState("No report classes yet", "Import the homework CSV, then add each class's comments PDF.", true);
    return;
  }
  $("#reports-by-class").innerHTML = state.classes.map(classItem => {
    const stats = classStats(classItem.id);
    const rows = stats.students.map(student => {
      const exam = student.latest_exam;
      const reason = !student.homework.length ? "Homework missing" : !exam ? "Weekly test missing" : !exam.comment ? "Comment missing" : "Ready";
      const query = student.period_id ? `?period_id=${encodeURIComponent(student.period_id)}` : "";
      return `<div class="report-student-row"><div class="student-name"><span class="student-avatar">${escapeHtml(initials(student.name))}</span><span>${escapeHtml(student.name)}<small>${escapeHtml(student.school)} · ${escapeHtml(student.grade)}</small></span></div><div class="report-data"><span>Homework <b>${student.homework_percent === null ? "—" : `${formatNumber(student.homework_percent)}%`}</b></span><span>Latest test <b>${exam ? `${formatNumber(exam.score)}/${formatNumber(exam.max_score)}` : "—"}</b></span></div><span class="status-pill ${student.ready ? "" : "warn"}">${escapeHtml(reason)}</span><div class="row-actions">${student.ready ? `<button class="table-action" data-preview-student="${student.id}" data-period-id="${student.period_id}">Preview</button><a class="table-action button-link" href="/api/reports/student/${student.id}${query}">Download PDF</a>` : `<button class="table-action disabled" data-go="inbox">Add data</button>`}</div></div>`;
    }).join("");
    return `<section class="report-class-section" id="report-class-${classItem.id}"><header><div><span class="eyebrow">${escapeHtml(classItem.subject || "CLASS REPORTS")}</span><h2>${escapeHtml(classItem.name)}</h2><p>${stats.ready} of ${stats.students.length} students ready · ${escapeHtml(classItem.teacher || "Teacher not specified")}</p></div><div class="class-report-actions"><label>Class period<select data-class-period="${classItem.id}" ${state.periods.length ? "" : "disabled"}>${periodOptions(classItem.selected_period_id)}</select></label><a class="secondary-button button-link ${stats.ready ? "" : "disabled"}" href="/api/reports/class/${classItem.id}.zip">Download class ZIP</a></div></header><div class="report-student-list">${rows || emptyState("No students in this class", "Import an updated CSV to add students.")}</div></section>`;
  }).join("");
}

function renderTemplate() {
  const template = state.template;
  $("#template-name").textContent = template.name || "Royal IB report template";
  $("#template-pages").textContent = template.pages || "—";
  $("#template-field-count").textContent = template.fields?.length || 0;
  $("#template-status").textContent = template.ready ? "Ready" : "Missing";
  $("#template-status").classList.toggle("warn", !template.ready);
  $("#template-fields").innerHTML = template.fields?.length
    ? template.fields.map(field => `<span>✓ ${escapeHtml(field)}</span>`).join("")
    : `<div class="field-warning">No AcroForm fields detected. The fixed-position report overlay can still use this PDF design.</div>`;
}

function emptyState(title, copy, compact = false) {
  return `<div class="empty-state ${compact ? "large" : ""}"><span>◇</span><strong>${escapeHtml(title)}</strong><p>${escapeHtml(copy)}</p></div>`;
}

async function loadState() {
  try {
    state = await api("/api/state");
    render();
  } catch (error) {
    toast(error.message, "error");
    $(".server-status").classList.add("error");
    $(".server-status").innerHTML = "<i></i> Local service unavailable";
  }
}

async function chooseHomeworkCsv(file) {
  if (!file) return;
  try {
    const text = await file.text();
    const inspection = await api("/api/csv/inspect", { method: "POST", body: JSON.stringify({ filename: file.name, text }) });
    const options = inspection.dates.map(item => `<option value="${item.iso}">${escapeHtml(item.label)} · ${item.iso}</option>`).join("");
    const suggestedLabel = `Week ${state.periods.length + 1}`;
    openModal(`<span class="eyebrow">CSV IMPORT</span><h2>Select the reporting period</h2><p><strong>${escapeHtml(file.name)}</strong> contains ${inspection.record_count} student-class records across ${inspection.class_count} classes.</p><form id="csv-import-form"><label>Period name<input id="period-name" value="${escapeHtml(suggestedLabel)}" placeholder="Week 8" required /></label><div class="form-grid"><label>Start date<select id="period-start" required>${options}</select></label><label>End date<select id="period-end" required>${options}</select></label></div><div class="calculation-note"><strong>Homework rule</strong><span>A and B = completed · F = incomplete · X and blank = excluded</span></div><div class="modal-footer"><button type="button" class="secondary-button" id="cancel-import">Cancel</button><button class="primary-button">Import and update students</button></div></form>`);
    $("#period-end").value = inspection.dates.at(-1).iso;
    $("#cancel-import").onclick = closeModal;
    $("#csv-import-form").onsubmit = async event => {
      event.preventDefault();
      const periodStart = $("#period-start").value;
      const periodEnd = $("#period-end").value;
      if (periodStart > periodEnd) return toast("The start date must be before the end date.", "error");
      try {
        const result = await api("/api/csv/import", { method: "POST", body: JSON.stringify({ filename: file.name, text, period_label: $("#period-name").value.trim(), period_start: periodStart, period_end: periodEnd }) });
        state = result.state;
        closeModal();
        render();
        toast(result.summary);
      } catch (error) { toast(error.message, "error"); }
    };
  } catch (error) {
    toast(error.message, "error");
  } finally {
    $("#homework-file-input").value = "";
  }
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

async function chooseCommentsPdf(file) {
  if (!file) return;
  if (!state.classes.length) {
    toast("Import the homework CSV before adding comments.", "error");
    $("#comments-file-input").value = "";
    return;
  }
  if (!state.periods.length) {
    toast("Import the homework CSV to create a reporting period first.", "error");
    $("#comments-file-input").value = "";
    return;
  }
  const classOptions = state.classes.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
  const defaultLabel = file.name.replace(/\.pdf$/i, "").trim();
  openModal(`<span class="eyebrow">COMMENTS PDF</span><h2>Identify this weekly exam</h2><p>Each comments PDF must represent one class and one reporting period. Names will only be matched inside the selected class.</p><form id="comments-import-form"><div class="form-grid"><label>Class<select id="comments-class" required><option value="">Choose a class</option>${classOptions}</select></label><label>Reporting period<select id="comments-period" required><option value="">Choose a period</option>${periodOptions()}</select></label></div><label>Exam label<input id="comments-label" value="${escapeHtml(defaultLabel)}" placeholder="Weekly Exam 1" required /></label><div class="safety-note">Duplicate or missing names will be sent to the review queue with school and grade options.</div><div class="modal-footer"><button type="button" class="secondary-button" id="cancel-import">Cancel</button><button class="primary-button">Match comments</button></div></form>`);
  $("#comments-class").onchange = event => {
    const selectedClass = classById(event.target.value);
    if (selectedClass?.selected_period_id) $("#comments-period").value = selectedClass.selected_period_id;
  };
  $("#cancel-import").onclick = closeModal;
  $("#comments-import-form").onsubmit = async event => {
    event.preventDefault();
    try {
      const pdfBase64 = arrayBufferToBase64(await file.arrayBuffer());
      const result = await api("/api/comments/import", { method: "POST", body: JSON.stringify({ filename: file.name, class_id: $("#comments-class").value, period_id: $("#comments-period").value, label: $("#comments-label").value.trim(), pdf_base64: pdfBase64 }) });
      state = result.state;
      closeModal();
      render();
      toast(result.summary, state.issues.length ? "warning" : "success");
    } catch (error) { toast(error.message, "error"); }
  };
  $("#comments-file-input").value = "";
}

function reviewIssue(issueId) {
  const issue = state.issues.find(item => item.id === issueId);
  if (!issue) return;
  const classStudents = studentsForClass(issue.class_id);
  const candidateIds = new Set(issue.candidates.map(candidate => candidate.id));
  const ordered = [...classStudents].sort((first, second) => {
    const candidateDifference = Number(candidateIds.has(second.id)) - Number(candidateIds.has(first.id));
    return candidateDifference || first.name.localeCompare(second.name);
  });
  const options = ordered.map(student => `<option value="${student.id}">${candidateIds.has(student.id) ? "Possible match · " : ""}${escapeHtml(student.name)} · ${escapeHtml(student.school)} · ${escapeHtml(student.grade)}</option>`).join("");
  openModal(`<span class="eyebrow">MANUAL REVIEW</span><h2>Match ${escapeHtml(issue.subject_name)}</h2><p>The PDF provided a name but not the student's school or grade. Choose the correct record from <strong>${escapeHtml(classById(issue.class_id)?.name || "this class")}</strong>.</p><form id="issue-form"><label>Student record<select id="issue-student" required><option value="">Choose using school and grade</option>${options}</select></label><div class="source-note"><span>Source</span><strong>${escapeHtml(issue.source_name)}</strong><small>${escapeHtml(issue.payload.label || "Weekly exam")}</small></div><div class="modal-footer"><button type="button" class="secondary-button" id="cancel-review">Cancel</button><button class="primary-button">Confirm match</button></div></form>`);
  $("#cancel-review").onclick = closeModal;
  $("#issue-form").onsubmit = async event => {
    event.preventDefault();
    try {
      const result = await api(`/api/issues/${issue.id}/resolve`, { method: "POST", body: JSON.stringify({ student_id: $("#issue-student").value }) });
      state = result.state;
      closeModal();
      render();
      toast("Student match confirmed.");
    } catch (error) { toast(error.message, "error"); }
  };
}

async function chooseTemplate(file) {
  if (!file) return;
  openModal(`<span class="eyebrow">REPLACE TEMPLATE</span><h2>Use ${escapeHtml(file.name)}?</h2><p>This PDF will become the academy-wide design for every class. It must be portrait orientation. The current template remains available in Git.</p><div class="modal-footer"><button class="secondary-button" id="cancel-template">Cancel</button><button class="primary-button" id="confirm-template">Activate template</button></div>`);
  $("#cancel-template").onclick = closeModal;
  $("#confirm-template").onclick = async () => {
    try {
      const pdfBase64 = arrayBufferToBase64(await file.arrayBuffer());
      const result = await api("/api/template/upload", { method: "POST", body: JSON.stringify({ filename: file.name, pdf_base64: pdfBase64 }) });
      state.template = result.template;
      closeModal();
      renderTemplate();
      $("#template-preview").src = `/api/template/preview?v=${Date.now()}`;
      toast("Academy template activated.");
    } catch (error) { toast(error.message, "error"); }
  };
  $("#template-file-input").value = "";
}

function bindEvents() {
  document.addEventListener("click", event => {
    const nav = event.target.closest("[data-nav]");
    if (nav) { event.preventDefault(); go(nav.dataset.nav); return; }
    const goto = event.target.closest("[data-go]");
    if (goto) { event.preventDefault(); go(goto.dataset.go); return; }
    const classLink = event.target.closest("[data-report-class]");
    if (classLink) {
      go("reports");
      requestAnimationFrame(() => document.getElementById(`report-class-${classLink.dataset.reportClass}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
      return;
    }
    const issue = event.target.closest("[data-issue]");
    if (issue) { reviewIssue(issue.dataset.issue); return; }
    const preview = event.target.closest("[data-preview-student]");
    if (preview) { window.open(`/api/reports/student/${preview.dataset.previewStudent}/preview?period_id=${encodeURIComponent(preview.dataset.periodId || "")}`, "_blank", "noopener"); return; }
    const disabled = event.target.closest(".disabled");
    if (disabled?.matches("a")) event.preventDefault();
    if (event.target === $("#modal-backdrop")) closeModal();
  });

  document.addEventListener("change", async event => {
    try {
      if (event.target.matches("#global-period-select")) {
        const result = await api("/api/periods/select", { method: "POST", body: JSON.stringify({ period_id: event.target.value }) });
        state = result.state;
        render();
        toast("Active reporting period changed. Class selections were preserved.");
      }
      if (event.target.matches("[data-class-period]")) {
        const classId = event.target.dataset.classPeriod;
        const result = await api(`/api/classes/${classId}/period`, { method: "POST", body: JSON.stringify({ period_id: event.target.value }) });
        state = result.state;
        render();
        toast(`${classById(classId)?.name || "Class"} now shows ${periodById(event.target.value)?.label || "the selected period"}.`);
      }
    } catch (error) {
      toast(error.message, "error");
      await loadState();
    }
  });

  $("#upload-homework").onclick = $("#guide-upload-csv").onclick = () => $("#homework-file-input").click();
  $("#upload-comments").onclick = $("#guide-upload-pdf").onclick = () => $("#comments-file-input").click();
  $("#guide-review").onclick = () => state.issues[0] ? reviewIssue(state.issues[0].id) : toast("The review queue is clear.");
  $("#homework-file-input").onchange = event => chooseHomeworkCsv(event.target.files[0]);
  $("#comments-file-input").onchange = event => chooseCommentsPdf(event.target.files[0]);
  $("#upload-template").onclick = () => $("#template-file-input").click();
  $("#template-file-input").onchange = event => chooseTemplate(event.target.files[0]);
  $("#student-search").oninput = renderStudents;
  $("#student-class-filter").onchange = renderStudents;
  $("#mobile-menu").onclick = () => $(".sidebar").classList.add("open");
  $("#sidebar-close").onclick = () => $(".sidebar").classList.remove("open");
  $("#reset-data").onclick = async () => {
    if (!confirm("Permanently clear imported students, exams, and report history from this Mac? The PDF template will be kept.")) return;
    try {
      await api("/api/reset", { method: "POST", body: JSON.stringify({ confirm: true }) });
      await loadState();
      toast("Local student data cleared.");
    } catch (error) { toast(error.message, "error"); }
  };
}

bindEvents();
go(window.location.hash.slice(1) || "dashboard");
loadState();
