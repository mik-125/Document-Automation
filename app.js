const seed = {
  classes: [
    { id: "math-a", subject: "IB Mathematics", name: "AA HL · Class A", teacher: "Sora Park", color: "IB" },
    { id: "econ-a", subject: "IB Economics", name: "Economics · Class A", teacher: "Daniel Kim", color: "EC" },
    { id: "sat-a", subject: "SAT Preparation", name: "SAT Math · Class A", teacher: "Mina Lee", color: "SA" }
  ],
  students: [
    { id: "s1", name: "Minseo Kim", school: "KIS", grade: "10", classId: "math-a", homework: "A", test: 91, comment: "Approaches complex problems with confidence. Keep showing your working clearly.", goal: "Practise integration by parts.", matched: "high" },
    { id: "s2", name: "Seojun Lee", school: "SIS", grade: "11", classId: "math-a", homework: "A", test: 87, comment: "Strong consistency this week; revisit logarithm transformations.", goal: "Complete logarithm review set.", matched: "high" },
    { id: "s3", name: "Jiwon Park", school: "Chadwick", grade: "10", classId: "math-a", homework: "B", test: 0, comment: "", goal: "Bring questions to office hours.", matched: "medium" },
    { id: "s4", name: "Yuna Choi", school: "KIS", grade: "11", classId: "econ-a", homework: "A", test: 88, comment: "Explains economic models accurately and listens carefully to feedback.", goal: "Connect diagrams to written analysis.", matched: "high" },
    { id: "s5", name: "Ethan Jung", school: "Dulwich", grade: "11", classId: "econ-a", homework: "A", test: 94, comment: "Excellent use of real-world examples in written responses.", goal: "Maintain timed writing practice.", matched: "high" },
    { id: "s6", name: "Sujin Han", school: "SIS", grade: "10", classId: "sat-a", homework: "A", test: 1160, comment: "Improving steadily in algebra. Focus next on pacing across sections.", goal: "Finish the 25-minute mini test.", matched: "high" },
    { id: "s7", name: "Noah Kang", school: "KIS", grade: "10", classId: "sat-a", homework: "C", test: 1080, comment: "", goal: "Review mistakes with teacher.", matched: "low" },
    { id: "s8", name: "Hana Seo", school: "Chadwick", grade: "11", classId: "sat-a", homework: "A", test: 1230, comment: "Clear progress in data analysis and a more deliberate checking process.", goal: "Practise advanced problem solving.", matched: "high" }
  ],
  files: [
    { id: "f1", name: "students_week8.csv", type: "Student roster", count: "8 student profiles identified", state: "complete", source: "CSV" },
    { id: "f2", name: "homework_week8.xlsx", type: "Homework performance", count: "8 matched to students", state: "complete", source: "XLSX" },
    { id: "f3", name: "teacher_comments.pdf", type: "Teacher comments", count: "2 records need review", state: "review", source: "PDF" }
  ],
  reports: [],
  weeks: [8],
  currentWeek: 8,
  classSelectedWeeks: { "math-a": 8, "econ-a": 8, "sat-a": 8 },
  template: { commentLabel: "Teacher's note", fallback: "No teacher comment submitted yet.", showHomework: true },
  activity: [],
  classExports: {}
};

let state = loadState();
if (!Array.isArray(state.weeks)) state.weeks = [8];
if (!state.currentWeek && state.weeks.length) state.currentWeek = state.weeks[state.weeks.length - 1];
let currentPage = "dashboard";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const workspaceChannel = typeof window !== "undefined" && "BroadcastChannel" in window ? new BroadcastChannel("brightline-workspace") : null;
let applyingWorkspaceUpdate = false;
let pendingNewStudentId = null;
let selectedStudentIds = new Set();

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function loadState() { try { return JSON.parse(localStorage.getItem("brightline-mvp")) || clone(seed); } catch { return clone(seed); } }
function weeklyFields(student) { return { homework: student.homework || "", test: Number(student.test) || 0, comment: student.comment || "", goal: student.goal || "", matched: student.matched || "high" }; }
function captureCurrentWeekData() { if (!state.currentWeek) return; state.weekData ||= {}; state.weekData[state.currentWeek] = Object.fromEntries(state.students.map(student => [student.id, weeklyFields(student)])); }
function applyWeekData(week) { const records = week && state.weekData?.[week] ? state.weekData[week] : {}; state.students.forEach(student => Object.assign(student, records[student.id] || { homework: "", test: 0, comment: "", goal: "", matched: "high" })); }
function ensureWeeklyState() { state.weekData ||= {}; state.weekDatesByNumber ||= { 8: { start: "2026-08-10", end: "2026-08-16" } }; state.activity ||= []; state.classExports ||= {}; state.classSelectedWeeks ||= {}; const defaultClassWeek = state.weeks.includes(Number(state.currentWeek)) ? Number(state.currentWeek) : state.weeks.length ? Math.max(...state.weeks) : null; state.classes.forEach(cls => { if (!state.weeks.includes(Number(state.classSelectedWeeks[cls.id]))) state.classSelectedWeeks[cls.id] = defaultClassWeek; }); if (state.currentWeek && !state.weekData[state.currentWeek]) captureCurrentWeekData(); state.files.forEach(file => { if (file.week == null) file.week = 8; }); state.reports.forEach(report => { if (report.week == null) report.week = 8; }); }
function saveState() { captureCurrentWeekData(); localStorage.setItem("brightline-mvp", JSON.stringify(state)); if (!applyingWorkspaceUpdate) workspaceChannel?.postMessage({ type: "workspace_state", state }); }
function classById(id) { return state.classes.find(c => c.id === id); }
function initials(name) { return name.split(" ").map(x => x[0]).join("").slice(0, 2).toUpperCase(); }
function classBadge(name, subject = "") { const words = String(name || subject).match(/[A-Za-z0-9]+/g) || []; if (!words.length) return "CL"; if (words[0].length <= 2) return words[0].slice(0, 2).toUpperCase(); return words.filter(word => word.length > 1).slice(0, 2).map(word => word[0]).join("").toUpperCase() || "CL"; }
function scoreText(student) { return student.test ? String(student.test) : "Missing"; }
function ready(student) { return Boolean(student.homework && student.test && student.comment); }
function reportComplete(student, week = state.currentWeek) { return state.reports.some(report => report.studentId === student.id && report.week === week); }
function statusClass(student) { return ready(student) ? "" : "warn"; }
function issuesFor(student) { const issues = []; if (!student.homework) issues.push("homework"); if (!student.test) issues.push("weekly test"); if (!student.comment) issues.push("teacher comment"); if (student.matched === "low") issues.push("match confirmation"); return issues; }
function uniqueIssues() { return state.students.flatMap(s => issuesFor(s).map(issue => ({ student: s, issue }))); }
function escapeHtml(value = "") { return String(value).replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c])); }
function weekLabel(week = state.currentWeek) { return week ? `Week ${week}` : "No week selected"; }
function formatIsoDate(value) { if (!value) return "Date not set"; return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(`${value}T00:00:00Z`)); }
function weekDates(week = state.currentWeek) { if (!week) return "No reporting week selected"; const dates = state.weekDatesByNumber?.[week]; if (!dates?.start || !dates?.end) return "Dates not set"; return `${formatIsoDate(dates.start)}–${formatIsoDate(dates.end)}, ${dates.end.slice(0,4)}`; }
function selectedClassWeek(classId) { const savedWeek = Number(state.classSelectedWeeks?.[classId]); return state.weeks.includes(savedWeek) ? savedWeek : null; }
function studentAtWeek(student, week) { if (!student) return null; if (Number(week) === Number(state.currentWeek)) return student; const weekly = state.weekData?.[week]?.[student.id] || { homework: "", test: 0, comment: "", goal: "", matched: "high" }; return { ...student, ...weekly }; }
function nextWeekDates() { const latest = state.weeks.length ? Math.max(...state.weeks) : null; const previousEnd = latest ? state.weekDatesByNumber?.[latest]?.end : null; const start = previousEnd ? new Date(`${previousEnd}T00:00:00Z`) : new Date(); start.setUTCDate(start.getUTCDate() + (previousEnd ? 1 : -((start.getUTCDay() + 6) % 7))); const end = new Date(start); end.setUTCDate(end.getUTCDate() + 6); return { start: start.toISOString().slice(0,10), end: end.toISOString().slice(0,10) }; }
function renderWeekSelector() { const menu = $("#week-menu"); $("#period-date").textContent = weekDates(); $("#active-week-summary").textContent = weekLabel(); $("#template-week-title").textContent = `${weekLabel()} report`; if (currentPage === "dashboard") $("#page-title").textContent = state.currentWeek ? `${weekLabel()} reports` : "Select a reporting week"; const rows = state.weeks.slice().sort((a,b)=>a-b).map(week => `<div class="week-menu-row"><button class="week-option ${week === state.currentWeek ? "active" : ""}" data-week="${week}"><span>Week ${week}<small>${escapeHtml(weekDates(week))}</small></span>${week === state.currentWeek ? "<span>✓</span>" : ""}</button><button class="week-edit" data-edit-week-date="${week}" aria-label="Edit dates for Week ${week}">✎</button><button class="week-delete" data-delete-week="${week}" aria-label="Delete Week ${week}">×</button></div>`).join(""); menu.innerHTML = `${rows || "<div class=\"week-empty\">No weeks yet</div>"}<button class="week-option add-week" data-add-week="true">＋ Add week</button>`; }
function selectWeek(week) { captureCurrentWeekData(); state.currentWeek = Number(week); applyWeekData(state.currentWeek); $("#week-menu").classList.remove("open"); $("#period-button").setAttribute("aria-expanded", "false"); render(); toast(`${weekLabel()} selected`); }
function addWeek() { captureCurrentWeekData(); const nextWeek = state.weeks.length ? Math.max(...state.weeks) + 1 : 1; const dates = nextWeekDates(); state.weeks.push(nextWeek); state.weekDatesByNumber[nextWeek] = dates; state.weekData[nextWeek] = {}; state.currentWeek = nextWeek; applyWeekData(nextWeek); state.activity.unshift({ title: `${weekLabel()} created`, detail: `Reporting period · ${weekDates()}`, time: "Just now", week: nextWeek }); $("#week-menu").classList.remove("open"); $("#period-button").setAttribute("aria-expanded", "false"); render(); toast(`${weekLabel()} added`); }
function deleteWeek(week) { const weekNumber = Number(week); const label = `Week ${weekNumber}`; if (!confirm(`Delete ${label}? This removes its dates and weekly data from this MVP.`)) return; state.weeks = state.weeks.filter(item => item !== weekNumber); delete state.weekDatesByNumber[weekNumber]; delete state.weekData[weekNumber]; state.files = state.files.filter(file => file.week !== weekNumber); state.reports = state.reports.filter(report => report.week !== weekNumber); state.activity = state.activity.filter(item => item.week !== weekNumber); Object.keys(state.classExports).filter(key => key.startsWith(`${weekNumber}:`)).forEach(key => delete state.classExports[key]); const fallbackClassWeek = state.weeks.length ? Math.max(...state.weeks) : null; Object.keys(state.classSelectedWeeks).forEach(classId => { if (Number(state.classSelectedWeeks[classId]) === weekNumber) state.classSelectedWeeks[classId] = fallbackClassWeek; }); if (state.currentWeek === weekNumber) { state.currentWeek = state.weeks.length ? Math.max(...state.weeks) : null; applyWeekData(state.currentWeek); } render(); toast(`${label} deleted`); }
function editWeekDates(week) { const weekNumber = Number(week); const dates = state.weekDatesByNumber[weekNumber] || { start: "", end: "" }; $("#week-menu").classList.remove("open"); $("#period-button").setAttribute("aria-expanded", "false"); openModal(`<h2>Edit Week ${weekNumber} dates</h2><p>These dates identify every upload, readiness result, and report for this reporting week.</p><form id="week-date-form"><div class="form-grid"><label>Start date<input type="date" name="start" value="${escapeHtml(dates.start)}" required /></label><label>End date<input type="date" name="end" value="${escapeHtml(dates.end)}" required /></label></div><div class="modal-footer"><button class="primary-button">Save dates</button></div></form>`); $("#week-date-form").onsubmit = event => { event.preventDefault(); const form = new FormData(event.target); const start = form.get("start"), end = form.get("end"); if (end < start) return toast("End date must be after the start date"); state.weekDatesByNumber[weekNumber] = { start, end }; state.activity.unshift({ title: `Week ${weekNumber} dates updated`, detail: weekDates(weekNumber), time: "Just now", week: weekNumber }); closeModal(); render(); toast(`Week ${weekNumber} dates saved`); }; }

ensureWeeklyState();
applyWeekData(state.currentWeek);

function render() {
  const all = state.students.length, readyCount = state.students.filter(ready).length;
  $("#academy-class-count").textContent = `Seoul · ${state.classes.length} ${state.classes.length === 1 ? "class" : "classes"}`;
  $("#ready-count").textContent = readyCount;
  $("#total-count").textContent = all;
  $("#report-ready-count").textContent = readyCount;
  $("#readiness-progress").style.width = `${all ? readyCount / all * 100 : 0}%`;
  $("#readiness-copy").textContent = `${all - readyCount} records need your attention before the batch is complete.`;
  $("#generate-ready").innerHTML = `Generate ${readyCount} ready report${readyCount === 1 ? "" : "s"} <span>→</span>`;
  $("#inbox-badge").textContent = uniqueIssues().length;
  $("#issue-count").textContent = uniqueIssues().length;
  $("#next-action-title").textContent = uniqueIssues().length ? `Resolve ${uniqueIssues().length} data issue${uniqueIssues().length === 1 ? "" : "s"}` : "All data is ready";
  $("#report-count").textContent = state.reports.filter(report => report.week === state.currentWeek).length;
  renderWeekSelector();
  renderCoverage(); renderClassHealth(); renderActivity(); renderClasses(); renderStudents(); renderFiles(); renderIssues(); renderReports(); renderTemplate();
  saveState();
}

function renderCoverage() {
  const dimensions = [
    ["Student records", state.students.length, state.students.length, "Roster data is complete"],
    ["Homework", state.students.filter(s => s.homework).length, state.students.length, "Preserving academy grades"],
    ["Weekly test", state.students.filter(s => s.test).length, state.students.length, "Scores matched by student"],
    ["Teacher comments", state.students.filter(s => s.comment).length, state.students.length, "Class-specific comment fields"]
  ];
  $("#coverage-grid").innerHTML = dimensions.map(([name, value, total, copy]) => `<article class="coverage-item"><div class="coverage-top"><span>${name}</span><span class="${value === total ? "status-pill" : "status-pill warn"}">${value === total ? "Complete" : "Needs review"}</span></div><div class="coverage-numbers">${value} <small>/ ${total}</small></div><small>${copy}</small><div class="mini-progress"><i style="width:${total ? value / total * 100 : 0}%"></i></div></article>`).join("");
}
function classData(cls, week = state.currentWeek) { const students = state.students.filter(s => s.classId === cls.id).map(student => studentAtWeek(student, week)), readyCount = students.filter(ready).length; return { students, readyCount, issues: students.reduce((n, s) => n + issuesFor(s).length, 0) }; }
function renderClassHealth() {
  $("#class-health-list").innerHTML = state.classes.map(cls => { const d = classData(cls); return `<div class="class-row" data-class="${cls.id}"><span class="class-status">${cls.color}</span><div><h3>${escapeHtml(cls.name)}</h3><p>${d.readyCount} of ${d.students.length} reports ready · ${escapeHtml(cls.teacher)}</p></div><span class="issue-amount ${d.issues ? "" : "good"}">${d.issues ? `${d.issues} issues` : "Ready"} →</span></div>`; }).join("");
}
function renderActivity() { const activities = state.activity.filter(item => item.type === "report_export" && item.week === state.currentWeek).slice(0, 4); $("#activity-list").innerHTML = activities.map(a => `<div class="activity-row"><span class="activity-dot"></span><div><h3>${escapeHtml(a.title)}</h3><p>${escapeHtml(a.detail)}</p></div><time>${escapeHtml(a.time)}</time></div>`).join("") || `<div class="activity-row"><span class="activity-dot"></span><div><h3>No completed class reports yet</h3><p>Completed class reports for ${escapeHtml(weekLabel())} will appear here.</p></div></div>`; }
function renderClasses() {
  $("#class-grid").innerHTML = state.classes.map(cls => { const classWeek = selectedClassWeek(cls.id), d = classData(cls, classWeek); return `<article class="class-card" data-class="${cls.id}"><div class="class-card-top"><span class="subject">${escapeHtml(cls.subject)}</span><span class="${d.issues ? "status-pill warn" : "status-pill"}">${d.issues ? `${d.issues} to review` : "Ready"}</span></div><h2>${escapeHtml(cls.name)}</h2><p>${escapeHtml(cls.teacher)} · ${escapeHtml(weekLabel(classWeek))} · ${d.students.length} students</p><div class="class-stats"><div><b>${d.readyCount}/${d.students.length}</b><small>reports ready</small></div><div><b>${d.students.filter(s => s.homework).length}/${d.students.length}</b><small>homework</small></div><div><b>${d.students.filter(s => s.test).length}/${d.students.length}</b><small>test scores</small></div></div></article>`; }).join("");
}
function renderStudents() {
  const filter = $("#student-class-filter"), selected = filter.value || "all";
  filter.innerHTML = `<option value="all">All classes</option>${state.classes.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("")}`;
  filter.value = selected;
  const query = $("#student-search").value.trim().toLowerCase();
  const students = state.students.filter(s => (selected === "all" || s.classId === selected) && `${s.name} ${s.school}`.toLowerCase().includes(query));
  selectedStudentIds = new Set([...selectedStudentIds].filter(id => state.students.some(student => student.id === id)));
  $("#students-table").innerHTML = students.map(s => `<tr><td><input class="student-select" type="checkbox" data-select-student="${s.id}" aria-label="Select ${escapeHtml(s.name)}" ${selectedStudentIds.has(s.id) ? "checked" : ""} /></td><td><div class="student-name"><span class="student-avatar">${initials(s.name)}</span>${escapeHtml(s.name)}<small>${escapeHtml(s.school)} · G${escapeHtml(s.grade)}</small></div></td><td>${escapeHtml(classById(s.classId)?.name || "Unassigned")}</td><td><span class="status-pill ${reportComplete(s) ? "" : "warn"}">${reportComplete(s) ? "Complete" : "Incomplete"}</span></td><td><button class="table-action" data-edit="${s.id}">Edit</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">No students match this search.</td></tr>`;
  const selectAll = $("#select-all-students"), selectedVisibleCount = students.filter(student => selectedStudentIds.has(student.id)).length;
  selectAll.checked = Boolean(students.length && selectedVisibleCount === students.length);
  selectAll.indeterminate = selectedVisibleCount > 0 && selectedVisibleCount < students.length;
  selectAll.onchange = event => { students.forEach(student => event.target.checked ? selectedStudentIds.add(student.id) : selectedStudentIds.delete(student.id)); renderStudents(); };
  $$("[data-select-student]").forEach(input => input.onchange = event => { event.target.checked ? selectedStudentIds.add(event.target.dataset.selectStudent) : selectedStudentIds.delete(event.target.dataset.selectStudent); renderStudents(); });
  const deleteButton = $("#delete-selected-students");
  deleteButton.disabled = selectedStudentIds.size === 0;
  deleteButton.textContent = "Delete";
}
function renderFiles() {
  const files = state.files.filter(file => file.week === state.currentWeek);
  $("#upload-count").textContent = `${files.length} files · ${weekLabel()}`;
  $("#file-list").innerHTML = files.map(f => `<div class="file-row"><span class="file-icon">${escapeHtml(f.source)}</span><div class="file-info"><strong>${escapeHtml(f.name)}</strong><small>${escapeHtml(f.type)} · ${escapeHtml(f.count)}</small></div><span class="status-pill ${f.state === "review" ? "warn" : f.state === "error" ? "error" : ""}">${f.state === "complete" ? "Matched" : f.state === "review" ? "Review" : "Processing"}</span></div>`).join("") || `<div class="file-row"><div class="file-info"><strong>No files for ${escapeHtml(weekLabel())}</strong><small>Upload data while this week is selected.</small></div></div>`;
}
function renderIssues() {
  const issues = uniqueIssues();
  $("#issue-list").innerHTML = issues.slice(0, 4).map(({student, issue}) => `<div class="issue-item" data-issue="${student.id}"><strong>${escapeHtml(student.name)}</strong><span>${issue === "match confirmation" ? "Confirm possible student match" : `Missing ${issue}`}</span></div>`).join("") || `<div class="issue-item"><strong>All clear</strong><span>Every available report is ready.</span></div>`;
}
function renderReports() {
  const reports = state.reports.filter(report => report.week === state.currentWeek);
  $("#reports-table").innerHTML = reports.slice().reverse().map(r => `<tr><td><div class="student-name"><span class="student-avatar">${initials(r.studentName)}</span>${escapeHtml(r.studentName)}</div></td><td>${escapeHtml(r.className)}</td><td>${escapeHtml(r.createdAt)}</td><td>${escapeHtml(r.template)}</td><td><span class="status-pill">Ready</span></td><td><button class="table-action" data-report="${r.id}">Preview</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">No reports have been generated for ${escapeHtml(weekLabel())}.</td></tr>`;
}
function renderTemplate() {
  $("#template-comment-label").value = state.template.commentLabel;
  $("#template-fallback").value = state.template.fallback;
  $("#template-show-homework").checked = state.template.showHomework;
  const example = state.students[0]; $("#report-canvas").innerHTML = reportMarkup(example, true);
}
function reportMarkup(student, templateMode = false, options = {}) {
  student ||= { name: "Student name", school: "School", grade: "—", classId: "", homework: "", test: 0, comment: "", goal: "" };
  const reportWeek = Number(options.week ?? state.currentWeek);
  const cls = options.classSnapshot || classById(student.classId) || { name: "Unassigned", teacher: "—" };
  const comment = student.comment || state.template.fallback;
  return `<article class="report-sheet"><div class="report-header"><div><h2>Weekly learning report</h2><p>${escapeHtml(cls.name)} · ${escapeHtml(weekLabel(reportWeek))} · ${escapeHtml(weekDates(reportWeek))}</p></div><strong>HANEUL<br/>ACADEMY</strong></div><div class="report-student"><div><b>${escapeHtml(student.name)}</b><br/><span>${escapeHtml(student.school)} · Grade ${escapeHtml(student.grade)}</span></div><div><strong>Teacher</strong><br/>${escapeHtml(cls.teacher)}</div></div>${state.template.showHomework ? `<section><span class="eyebrow">HOMEWORK TREND</span><div class="chart-placeholder"><div class="bars"><i style="height:35%"></i><i style="height:65%"></i><i style="height:52%"></i><i style="height:78%"></i><i style="height:90%"></i></div></div></section>` : ""}<section><span class="eyebrow">WEEKLY TEST</span><p><strong>${student.test || "—"}</strong> <span class="muted">latest score</span></p></section><section><span class="eyebrow">${escapeHtml(state.template.commentLabel)}</span><div class="report-comment">${escapeHtml(comment)}</div></section><section><span class="eyebrow">NEXT WEEK GOAL</span><p>${escapeHtml(student.goal || "Set a goal with your teacher.")}</p></section>${templateMode ? "" : `<p class="muted" style="margin-top:22px">Prepared by Brightline Reports · PDF-ready preview</p>`}</article>`;
}

function openModal(content) { $("#modal").innerHTML = `<button class="modal-close" aria-label="Close">×</button>${content}`; $("#modal-backdrop").classList.remove("hidden"); $(".modal-close").onclick = closeModal; }
function closeModal() { if (pendingNewStudentId) { const id = pendingNewStudentId; pendingNewStudentId = null; state.students = state.students.filter(student => student.id !== id); Object.values(state.weekData || {}).forEach(records => delete records[id]); render(); } $("#modal-backdrop").classList.add("hidden"); }
function toast(message) { const t = $("#toast"); t.textContent = message; t.classList.remove("hidden"); clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add("hidden"), 3000); }
function newId(prefix) { return `${prefix}-${Date.now().toString(36)}`; }
function go(page) { currentPage = page; $$(".page").forEach(p => p.classList.toggle("active", p.id === page)); $$(".main-nav a").forEach(a => a.classList.toggle("active", a.dataset.nav === page)); const names = {dashboard:["REPORTING PERIOD",state.currentWeek ? `${weekLabel()} reports` : "Select a reporting week"],classes:["CLASS DIRECTORY","Classes"],students:["PEOPLE","Students"],inbox:["AUTOMATION LAYER","Data inbox"],reports:["GENERATED OUTPUT","Reports"],templates:["REPORT DESIGN","Templates"]}; $("#page-kicker").textContent = names[page][0]; $("#page-title").textContent = names[page][1]; window.location.hash = page; window.scrollTo({top:0,behavior:"smooth"}); }

function openStudentModal(id, options = {}) {
  const editWeek = Number(options.week ?? state.currentWeek); if (!editWeek) return toast("Select or add a week before editing weekly student data.");
  const student = state.students.find(s => s.id === id); if (!student) return;
  captureCurrentWeekData();
  const displayedStudent = studentAtWeek(student, editWeek);
  openModal(`<h2>Edit student record</h2><p>Update this student's profile and class assignment.</p><form id="student-form"><div class="form-grid"><label>Student name<input name="name" value="${escapeHtml(displayedStudent.name)}" required /></label><label>School<input name="school" value="${escapeHtml(displayedStudent.school)}" /></label><label>Grade<input name="grade" value="${escapeHtml(displayedStudent.grade)}" /></label><label>Class<select name="classId">${state.classes.map(c => `<option value="${c.id}" ${c.id === displayedStudent.classId ? "selected" : ""}>${escapeHtml(c.name)}</option>`).join("")}</select></label></div><div class="modal-footer">${options.isNew ? "" : '<button type="button" class="secondary-button" id="delete-student">Delete</button>'}<button class="primary-button">Save student</button></div></form>`);
  if (!displayedStudent.classId) { const classSelect = $("#student-form select[name=classId]"); classSelect.insertAdjacentHTML("afterbegin", `<option value="">Unassigned</option>`); classSelect.value = ""; }
  $("#student-form").onsubmit = e => { e.preventDefault(); const form = new FormData(e.target); const weekly = { homework:displayedStudent.homework || "",test:Number(displayedStudent.test) || 0,comment:displayedStudent.comment || "",goal:displayedStudent.goal || "",matched:displayedStudent.matched || "high" }; Object.assign(student,{ name:form.get("name").trim(),school:form.get("school").trim(),grade:form.get("grade").trim(),classId:form.get("classId") }); state.weekData[editWeek] ||= {}; state.weekData[editWeek][student.id] = weekly; if (editWeek === Number(state.currentWeek)) Object.assign(student, weekly); state.activity.unshift({title:"Student data updated",detail:`${student.name} · ${weekLabel(editWeek)} · direct entry`,time:"Just now",week:editWeek}); pendingNewStudentId = null; render(); if (options.returnClassId && classById(options.returnClassId)) openClassModal(options.returnClassId, editWeek); else closeModal(); toast("Student record saved"); };
  if (!options.isNew) $("#delete-student").onclick = () => { if (!confirm(`Remove ${student.name}? Existing report history will be preserved.`)) return; archiveReportsForStudents([student]); state.students = state.students.filter(s => s.id !== id); Object.values(state.weekData).forEach(records => delete records[id]); closeModal(); render(); toast("Student removed; report history preserved"); };
}
function openClassModal(id, requestedWeek) {
  const cls = classById(id); if (!cls) return;
  captureCurrentWeekData();
  const classWeek = state.weeks.includes(Number(requestedWeek)) ? Number(requestedWeek) : selectedClassWeek(id);
  if (classWeek) state.classSelectedWeeks[id] = classWeek;
  const d = classData(cls, classWeek), allReady = d.students.length > 0 && d.students.every(ready);
  const weekOptions = state.weeks.slice().sort((a,b)=>a-b).map(week => `<option value="${week}" ${week === classWeek ? "selected" : ""}>Week ${week} · ${escapeHtml(weekDates(week))}</option>`).join("");
  openModal(`<div class="class-modal-heading"><div><h2>${escapeHtml(cls.name)}</h2><p>${escapeHtml(cls.subject)} · ${escapeHtml(cls.teacher)}</p></div><label class="class-week-picker">Reporting week<select id="class-week-select" ${state.weeks.length ? "" : "disabled"}>${weekOptions || '<option>No weeks available</option>'}</select></label></div><div class="table-card"><table><thead><tr><th>Student</th><th>HW</th><th>Test</th><th>Comment</th><th></th></tr></thead><tbody>${d.students.map(s=>`<tr><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.homework || "—")}</td><td>${scoreText(s)}</td><td>${s.comment ? "Complete" : "Missing"}</td><td><button class="table-action" data-edit="${s.id}" data-edit-week="${classWeek || ""}" data-return-class="${cls.id}">Edit</button></td></tr>`).join("")}</tbody></table></div><p>${allReady ? `All ${d.students.length} students are ready. Exporting will mark this class complete for ${escapeHtml(weekLabel(classWeek))}.` : `${d.students.length - d.readyCount} student report${d.students.length - d.readyCount === 1 ? "" : "s"} must be completed before class export for ${escapeHtml(weekLabel(classWeek))}.`}</p><div class="modal-footer"><button class="danger-button" id="class-delete">Delete class</button><button class="secondary-button" id="class-inbox">Upload class data</button><button class="primary-button" id="class-export">Export class reports</button></div>`);
  $("#class-delete").insertAdjacentHTML("afterend", `<button class="secondary-button" id="class-edit">Edit class</button>`);
  if (state.weeks.length) $("#class-week-select").onchange=event=>{state.classSelectedWeeks[id]=Number(event.target.value);renderClasses();saveState();openClassModal(id,event.target.value);toast(`${cls.name} now shows ${weekLabel(Number(event.target.value))}`);};
  $("#class-delete").onclick=()=>deleteClass(cls.id);
  $("#class-edit").onclick=()=>editClass(cls.id);
  $("#class-inbox").onclick=()=>{closeModal();go("inbox")};
  $("#class-export").onclick=()=>{if(!allReady)return toast(`Complete all ${cls.name} student reports before export.`);generateReports(d.students,{completedClass:cls,week:classWeek});};
}
function archiveReportsForStudents(students, classSnapshot) { captureCurrentWeekData(); const studentsById = new Map(students.map(student => [student.id, student])); state.reports.forEach(report => { const student = studentsById.get(report.studentId); if (!student) return; report.studentSnapshot ||= clone(studentAtWeek(student, report.week)); const cls = classSnapshot || classById(student.classId); report.classSnapshot ||= cls ? { id: cls.id, name: cls.name, subject: cls.subject, teacher: cls.teacher } : { name: report.className || "Archived class", teacher: "—" }; }); }
function deleteSelectedStudents() { const students = state.students.filter(student => selectedStudentIds.has(student.id)); if (!students.length) return; if (!confirm(`Delete ${students.length} selected student${students.length === 1 ? "" : "s"}? Their weekly data will be deleted, while generated report history remains.`)) return; archiveReportsForStudents(students); const studentIds = new Set(students.map(student => student.id)); state.students = state.students.filter(student => !studentIds.has(student.id)); Object.values(state.weekData).forEach(records => studentIds.forEach(studentId => delete records[studentId])); selectedStudentIds.clear(); render(); toast(`${students.length} student${students.length === 1 ? "" : "s"} deleted; report history preserved`); }
function deleteClass(id) { const cls = classById(id); if (!cls) return; const students = state.students.filter(student => student.classId === id); if (!confirm(`Delete ${cls.name}? Its ${students.length} student${students.length === 1 ? "" : "s"} and all of their weekly data will also be deleted. Generated report history will remain.`)) return; archiveReportsForStudents(students, cls); const studentIds = new Set(students.map(student => student.id)); state.students = state.students.filter(student => !studentIds.has(student.id)); Object.values(state.weekData).forEach(records => studentIds.forEach(studentId => delete records[studentId])); state.classes = state.classes.filter(item => item.id !== id); delete state.classSelectedWeeks[id]; closeModal(); render(); toast(`${cls.name} and ${students.length} student${students.length === 1 ? "" : "s"} deleted; report history preserved`); }
function editClass(id) { const cls = classById(id); if (!cls) return; openModal(`<h2>Edit class</h2><p>Update the class details used throughout rosters, weekly views, and future reports.</p><form id="edit-class-form"><div class="form-grid"><label>Subject<input name="subject" value="${escapeHtml(cls.subject)}" required /></label><label>Class name<input name="name" value="${escapeHtml(cls.name)}" required /></label><label>Teacher<input name="teacher" value="${escapeHtml(cls.teacher)}" required /></label></div><div class="modal-footer"><button type="button" class="secondary-button" id="cancel-class-edit">Back</button><button class="primary-button">Save class</button></div></form>`); $("#cancel-class-edit").onclick=()=>openClassModal(id); $("#edit-class-form").onsubmit=event=>{event.preventDefault();const form=new FormData(event.target),subject=form.get("subject").trim(),name=form.get("name").trim();Object.assign(cls,{subject,name,teacher:form.get("teacher").trim(),color:classBadge(name,subject)});render();openClassModal(id);toast(`${cls.name} updated`);}; }
function addStudent() { if(!state.currentWeek)return toast("Select or add a week before adding student data."); const firstClass = state.classes[0]?.id || ""; const s = {id:newId("s"),name:"New student",school:"",grade:"",classId:firstClass,homework:"",test:0,comment:"",goal:"",matched:"high"};state.students.push(s);pendingNewStudentId = s.id;openStudentModal(s.id,{isNew:true}); }
function addClass() { openModal(`<h2>Create a class</h2><p>Classes are the home for students, teacher fields, imports, templates, and reporting periods.</p><form id="class-form"><div class="form-grid"><label>Subject<input name="subject" placeholder="IB Mathematics" required /></label><label>Class name<input name="name" placeholder="AA HL · Class B" required /></label><label>Teacher<input name="teacher" placeholder="Teacher name" required /></label></div><div class="modal-footer"><button class="primary-button">Create class</button></div></form>`); $("#class-form").onsubmit=e=>{e.preventDefault();const f=new FormData(e.target),subject=f.get("subject").trim(),name=f.get("name").trim(),c={id:newId("class"),subject,name,teacher:f.get("teacher").trim(),color:classBadge(name,subject)};state.classes.push(c);state.classSelectedWeeks[c.id]=state.weeks.includes(Number(state.currentWeek)) ? Number(state.currentWeek) : state.weeks.length ? Math.max(...state.weeks) : null;state.activity.unshift({title:"Class created",detail:`${c.name} · direct setup`,time:"Just now"});closeModal();render();toast("Class created");}; }

function generateReports(students = state.students.filter(ready), options = {}) {
  const reportWeek = Number(options.week ?? state.currentWeek);
  if (!reportWeek) return toast("Select or add a week before generating reports.");
  if (!students.length) return toast("No complete student records to generate.");
  students.forEach(s => { const cls = classById(s.classId), studentSnapshot = clone(s), classSnapshot = cls ? { id: cls.id, name: cls.name, subject: cls.subject, teacher: cls.teacher } : { name: "Unassigned", teacher: "—" }; const existing = state.reports.find(r => r.studentId === s.id && r.week === reportWeek); if (existing) { existing.studentSnapshot ||= studentSnapshot; existing.classSnapshot ||= classSnapshot; } else state.reports.push({id:newId("r"),studentId:s.id,studentName:s.name,className:cls?.name || "Unassigned",createdAt:"Just now",template:`${weekLabel(reportWeek)} report`,week:reportWeek,studentSnapshot,classSnapshot}); });
  if (options.completedClass) {
    const exportKey = `${reportWeek}:${options.completedClass.id}`;
    const previousExport = state.classExports[exportKey];
    const revision = previousExport ? previousExport.revision + 1 : 1;
    state.classExports[exportKey] = { week: reportWeek, classId: options.completedClass.id, revision, exportedAt: new Date().toISOString(), exportedBy: "Jiyoon Yoo" };
    state.activity.unshift({type:"report_export",title:`${weekLabel(reportWeek)} report of ${options.completedClass.name} complete`,detail:previousExport ? `Revision ${revision} · ${students.length}/${students.length} student reports complete` : `${students.length}/${students.length} student reports complete`,time:"Just now",week:reportWeek,classId:options.completedClass.id,revision,createdAt:new Date().toISOString()});
  }
  closeModal();render();go("reports");toast(options.completedClass ? `${options.completedClass.name} fully completed for ${weekLabel(reportWeek)}` : `${students.length} PDF-ready reports generated for ${weekLabel(reportWeek)}`);
}
function previewReport(id) { const r=state.reports.find(r=>r.id===id); if(!r)return; const liveStudent=state.students.find(s=>s.id===r.studentId), s=r.studentSnapshot || (liveStudent ? studentAtWeek(liveStudent,r.week) : {name:r.studentName,school:"Archived",grade:"—",classId:"",homework:"",test:0,comment:"",goal:""}), cls=r.classSnapshot || (liveStudent ? classById(liveStudent.classId) : {name:r.className,teacher:"—"}); openModal(`<h2>${escapeHtml(s.name)}'s report</h2><p>Use your browser's print dialog to save this report as a PDF.</p><div class="report-canvas" style="min-height:0;padding:12px">${reportMarkup(s,false,{week:r.week,classSnapshot:cls})}</div><div class="modal-footer"><button class="secondary-button" id="print-report">Print / save as PDF</button></div>`); $("#print-report").onclick=()=>window.print(); }

function classify(headers, name) { const source = `${headers.join(" ")} ${name}`.toLowerCase(); if(/homework|\bhw\b|assignment/.test(source)) return "homework"; if(/test|score|exam|quiz/.test(source)) return "test"; if(/comment|feedback|note/.test(source)) return "comment"; if(/student|name|school|grade|class/.test(source)) return "roster"; return "unknown"; }
function parseCSV(text) { const lines=text.replace(/^\uFEFF/,"").trim().split(/\r?\n/); if(!lines.length)return[]; const parseLine=line=>{const out=[];let cell="",quoted=false;for(let i=0;i<line.length;i++){const c=line[i];if(c==='"'&&line[i+1]==='"'){cell+='"';i++;}else if(c==='"'){quoted=!quoted;}else if(c===","&&!quoted){out.push(cell.trim());cell="";}else cell+=c;}out.push(cell.trim());return out;};const headers=parseLine(lines.shift()).map(h=>h.trim());return lines.filter(Boolean).map(line=>Object.fromEntries(parseLine(line).map((v,i)=>[headers[i]||`field${i}`,v]))); }
function normalizedKey(key) { return key.toLowerCase().replace(/[^a-z0-9]/g,""); }
function findField(row, types) { const keys=Object.keys(row);const k=keys.find(k=>types.some(t=>normalizedKey(k).includes(t)));return k?row[k]:""; }
function importRows(rows, kind, filename) {
  if (!state.currentWeek) return toast("Select or add a week before importing data.");
  if (!rows.length) return toast("No data rows were found in that file.");
  let changed=0, unresolved=0;
  rows.forEach(row => {
    const name=findField(row,["studentname","name","student"]), school=findField(row,["school"]), grade=findField(row,["grade"]), className=findField(row,["classname","class","course","subject"]);
    let s=state.students.find(x=>x.name.toLowerCase()===name.toLowerCase() && (!school || !x.school || x.school.toLowerCase()===school.toLowerCase()));
    if(kind==="roster") { if(!s && name){let cls=state.classes.find(c=>c.name.toLowerCase()===className.toLowerCase());if(!cls)cls=state.classes[0];s={id:newId("s"),name,school,grade,classId:cls?.id||"",homework:"",test:0,comment:"",goal:"",matched:"high"};state.students.push(s);changed++;} else if(s){Object.assign(s,{school:school||s.school,grade:grade||s.grade});changed++;} return; }
    if(!s){unresolved++;return;} if(kind==="homework"){s.homework=findField(row,["homework","hw","assignment","result"])||s.homework;changed++;} if(kind==="test"){s.test=Number(findField(row,["score","test","quiz","exam"]))||s.test;changed++;} if(kind==="comment"){s.comment=findField(row,["comment","feedback","note"])||s.comment;changed++;}
  });
  const typeMap={roster:"Student roster",homework:"Homework performance",test:"Weekly test scores",comment:"Teacher comments",unknown:"Unclassified data"};state.files.unshift({id:newId("f"),name:filename,type:typeMap[kind],count:unresolved?`${changed} matched · ${unresolved} unresolved`:`${changed} records matched`,state:unresolved||kind==="unknown"?"review":"complete",source:"CSV",week:state.currentWeek});state.activity.unshift({title:"Data imported",detail:`${filename} · ${changed} records recognized · ${weekLabel()}`,time:"Just now",week:state.currentWeek});render();toast(`${changed} records imported into ${weekLabel()}${unresolved ? ` · ${unresolved} need review` : ""}`);
}
function processFile(file) { if(!state.currentWeek){toast("Select or add a week before uploading data.");return;} if(!/\.(csv|txt)$/i.test(file.name)){state.files.unshift({id:newId("f"),name:file.name,type:"File received",count:"Open in review to map data",state:"review",source:file.name.split(".").pop().toUpperCase().slice(0,4),week:state.currentWeek});render();toast(`${file.name} added to ${weekLabel()}`);return;} const reader=new FileReader();reader.onload=()=>{const rows=parseCSV(reader.result);const kind=classify(Object.keys(rows[0]||{}),file.name);openModal(`<h2>We recognized this file</h2><p><strong>${escapeHtml(file.name)}</strong> looks like <strong>${({roster:"a student roster",homework:"homework performance",test:"weekly test scores",comment:"teacher comments",unknown:"unclassified data"})[kind]}</strong> for <strong>${escapeHtml(weekLabel())}</strong>. Confirm to import its ${rows.length} data rows.</p><div class="table-card"><table><thead><tr>${Object.keys(rows[0]||{}).slice(0,4).map(h=>`<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.slice(0,3).map(row=>`<tr>${Object.values(row).slice(0,4).map(v=>`<td>${escapeHtml(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div><div class="modal-footer"><button class="secondary-button" id="cancel-import">Cancel</button><button class="primary-button" id="confirm-import">Confirm import</button></div>`);$("#cancel-import").onclick=closeModal;$("#confirm-import").onclick=()=>{closeModal();importRows(rows,kind,file.name);};};reader.readAsText(file); }

function resolveIssue(id) { if(!state.currentWeek)return toast("Select or add a week before reviewing weekly data."); const s=state.students.find(s=>s.id===id);if(!s)return;const missing=issuesFor(s); openModal(`<h2>Review ${escapeHtml(s.name)}</h2><p>Only the missing or uncertain information is shown here. Your decision improves this student's normalized record for ${escapeHtml(weekLabel())}.</p>${missing.includes("match confirmation")?`<label class="match-option selected"><input type="radio" checked /> Match to ${escapeHtml(s.name)} · ${escapeHtml(s.school)} · Grade ${escapeHtml(s.grade)}</label>`:""}<form id="issue-form"><div class="form-grid">${missing.includes("homework")?`<label>Homework value<input name="homework" placeholder="A, Complete, 100..." /></label>`:""}${missing.includes("weekly test")?`<label>Weekly test score<input type="number" name="test" placeholder="e.g. 87" /></label>`:""}</div>${missing.includes("teacher comment")?`<label>Teacher comment<textarea name="comment" placeholder="Add the teacher's note for this student"></textarea></label>`:""}<div class="modal-footer"><button class="primary-button">Save decision</button></div></form>`);$("#issue-form").onsubmit=e=>{e.preventDefault();const f=new FormData(e.target);if(f.has("homework"))s.homework=f.get("homework");if(f.has("test"))s.test=Number(f.get("test"))||0;if(f.has("comment"))s.comment=f.get("comment");s.matched="high";state.activity.unshift({title:"Review decision saved",detail:`${s.name} · ${weekLabel()} · manual confirmation`,time:"Just now",week:state.currentWeek});closeModal();render();toast("Decision saved and report readiness updated");}; }

function bindEvents() {
  document.addEventListener("click", e => {
    const week=e.target.closest("[data-week]");if(week){selectWeek(week.dataset.week);return;}
    const addWeekButton=e.target.closest("[data-add-week]");if(addWeekButton){addWeek();return;}
    const editWeekButton=e.target.closest("[data-edit-week-date]");if(editWeekButton){editWeekDates(editWeekButton.dataset.editWeekDate);return;}
    const deleteWeekButton=e.target.closest("[data-delete-week]");if(deleteWeekButton){deleteWeek(deleteWeekButton.dataset.deleteWeek);return;}
    if(!e.target.closest(".period-selector")){$("#week-menu").classList.remove("open");$("#period-button").setAttribute("aria-expanded", "false");}
    const nav=e.target.closest("[data-nav]");if(nav){e.preventDefault();go(nav.dataset.nav);}
    const goto=e.target.closest("[data-go]");if(goto)go(goto.dataset.go);
    const edit=e.target.closest("[data-edit]");if(edit)openStudentModal(edit.dataset.edit,{week:edit.dataset.editWeek ? Number(edit.dataset.editWeek) : state.currentWeek,returnClassId:edit.dataset.returnClass || ""});
    const card=e.target.closest("[data-class]");if(card)openClassModal(card.dataset.class);
    const issue=e.target.closest("[data-issue]");if(issue)resolveIssue(issue.dataset.issue);
    const rep=e.target.closest("[data-report]");if(rep)previewReport(rep.dataset.report);
    if(e.target===$("#modal-backdrop"))closeModal();
  });
  $("#add-class").onclick=addClass; $("#add-student").onclick=addStudent; $("#generate-ready").onclick=()=>generateReports(); $("#generate-all").onclick=()=>generateReports(); $("#review-all").onclick=()=>{const first=uniqueIssues()[0];if(first)resolveIssue(first.student.id);else toast("Everything is ready.");};$("#period-button").onclick=e=>{e.stopPropagation();const menu=$("#week-menu");const open=menu.classList.toggle("open");$("#period-button").setAttribute("aria-expanded",String(open));};
  $("#open-upload").onclick=()=>$("#file-input").click();$("#browse-files").onclick=()=>$("#file-input").click();$("#file-input").onchange=e=>[...e.target.files].forEach(processFile);
  const drop=$("#drop-zone");["dragenter","dragover"].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.add("dragging");}));["dragleave","drop"].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.remove("dragging");}));drop.addEventListener("drop",e=>[...e.dataTransfer.files].forEach(processFile));
  $("#student-search").oninput=renderStudents;$("#student-class-filter").onchange=renderStudents;$("#delete-selected-students").onclick=deleteSelectedStudents;
  $("#save-template").onclick=()=>{Object.assign(state.template,{commentLabel:$("#template-comment-label").value.trim()||"Teacher's note",fallback:$("#template-fallback").value.trim()||"No teacher comment submitted yet.",showHomework:$("#template-show-homework").checked});render();toast("Template changes saved locally");};
  $("#reset-demo").onclick=()=>{if(confirm("Reset all local MVP changes and restore the demo data?")){state=clone(seed);ensureWeeklyState();applyWeekData(state.currentWeek);render();toast("Demo data restored");}};$("#mobile-menu").onclick=()=>$(".sidebar").classList.toggle("open");$("#sidebar-close").onclick=()=>$(".sidebar").classList.remove("open");
}
bindEvents(); render(); if(location.hash.slice(1) && $("#"+location.hash.slice(1)))go(location.hash.slice(1));
if (workspaceChannel) workspaceChannel.onmessage = event => { if (event.data?.type !== "workspace_state" || !event.data.state) return; applyingWorkspaceUpdate = true; state = clone(event.data.state); ensureWeeklyState(); applyWeekData(state.currentWeek); render(); applyingWorkspaceUpdate = false; toast("Live workspace update received"); };
