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
  template: { commentLabel: "Teacher's note", fallback: "No teacher comment submitted yet.", showHomework: true },
  activity: [
    { title: "Homework import matched", detail: "homework_week8.xlsx · 8 student records", time: "9 min ago" },
    { title: "Roster was updated", detail: "students_week8.csv · 8 student profiles", time: "22 min ago" },
    { title: "Week 8 started", detail: "Reporting period · August 10–16", time: "Today" }
  ]
};

let state = loadState();
let currentPage = "dashboard";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function loadState() { try { return JSON.parse(localStorage.getItem("brightline-mvp")) || clone(seed); } catch { return clone(seed); } }
function saveState() { localStorage.setItem("brightline-mvp", JSON.stringify(state)); }
function classById(id) { return state.classes.find(c => c.id === id); }
function initials(name) { return name.split(" ").map(x => x[0]).join("").slice(0, 2).toUpperCase(); }
function scoreText(student) { return student.test ? String(student.test) : "Missing"; }
function ready(student) { return Boolean(student.homework && student.test && student.comment); }
function statusClass(student) { return ready(student) ? "" : "warn"; }
function issuesFor(student) { const issues = []; if (!student.homework) issues.push("homework"); if (!student.test) issues.push("weekly test"); if (!student.comment) issues.push("teacher comment"); if (student.matched === "low") issues.push("match confirmation"); return issues; }
function uniqueIssues() { return state.students.flatMap(s => issuesFor(s).map(issue => ({ student: s, issue }))); }
function escapeHtml(value = "") { return String(value).replace(/[&<>'"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c])); }

function render() {
  const all = state.students.length, readyCount = state.students.filter(ready).length;
  $("#ready-count").textContent = readyCount;
  $("#total-count").textContent = all;
  $("#report-ready-count").textContent = readyCount;
  $("#readiness-progress").style.width = `${all ? readyCount / all * 100 : 0}%`;
  $("#readiness-copy").textContent = `${all - readyCount} records need your attention before the batch is complete.`;
  $("#generate-ready").innerHTML = `Generate ${readyCount} ready report${readyCount === 1 ? "" : "s"} <span>→</span>`;
  $("#inbox-badge").textContent = uniqueIssues().length;
  $("#issue-count").textContent = uniqueIssues().length;
  $("#next-action-title").textContent = uniqueIssues().length ? `Resolve ${uniqueIssues().length} data issue${uniqueIssues().length === 1 ? "" : "s"}` : "All data is ready";
  $("#report-count").textContent = state.reports.length;
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
function classData(cls) { const students = state.students.filter(s => s.classId === cls.id), readyCount = students.filter(ready).length; return { students, readyCount, issues: students.reduce((n, s) => n + issuesFor(s).length, 0) }; }
function renderClassHealth() {
  $("#class-health-list").innerHTML = state.classes.map(cls => { const d = classData(cls); return `<div class="class-row" data-class="${cls.id}"><span class="class-status">${cls.color}</span><div><h3>${escapeHtml(cls.name)}</h3><p>${d.readyCount} of ${d.students.length} reports ready · ${escapeHtml(cls.teacher)}</p></div><span class="issue-amount ${d.issues ? "" : "good"}">${d.issues ? `${d.issues} issues` : "Ready"} →</span></div>`; }).join("");
}
function renderActivity() { $("#activity-list").innerHTML = state.activity.slice(0, 4).map(a => `<div class="activity-row"><span class="activity-dot"></span><div><h3>${escapeHtml(a.title)}</h3><p>${escapeHtml(a.detail)}</p></div><time>${escapeHtml(a.time)}</time></div>`).join(""); }
function renderClasses() {
  $("#class-grid").innerHTML = state.classes.map(cls => { const d = classData(cls); return `<article class="class-card" data-class="${cls.id}"><div class="class-card-top"><span class="subject">${escapeHtml(cls.subject)}</span><span class="${d.issues ? "status-pill warn" : "status-pill"}">${d.issues ? `${d.issues} to review` : "Ready"}</span></div><h2>${escapeHtml(cls.name)}</h2><p>${escapeHtml(cls.teacher)} · Week 8 · ${d.students.length} students</p><div class="class-stats"><div><b>${d.readyCount}/${d.students.length}</b><small>reports ready</small></div><div><b>${d.students.filter(s => s.homework).length}/${d.students.length}</b><small>homework</small></div><div><b>${d.students.filter(s => s.test).length}/${d.students.length}</b><small>test scores</small></div></div></article>`; }).join("");
}
function renderStudents() {
  const filter = $("#student-class-filter"), selected = filter.value || "all";
  filter.innerHTML = `<option value="all">All classes</option>${state.classes.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("")}`;
  filter.value = selected;
  const query = $("#student-search").value.trim().toLowerCase();
  const students = state.students.filter(s => (selected === "all" || s.classId === selected) && `${s.name} ${s.school}`.toLowerCase().includes(query));
  $("#students-table").innerHTML = students.map(s => `<tr><td><div class="student-name"><span class="student-avatar">${initials(s.name)}</span>${escapeHtml(s.name)}<small>${escapeHtml(s.school)} · G${escapeHtml(s.grade)}</small></div></td><td>${escapeHtml(classById(s.classId)?.name || "Unassigned")}</td><td>${escapeHtml(s.homework || "Missing")}</td><td>${scoreText(s)}</td><td>${s.comment ? "Complete" : "Missing"}</td><td><span class="status-pill ${statusClass(s)}">${ready(s) ? "Ready" : `${issuesFor(s).length} issue${issuesFor(s).length > 1 ? "s" : ""}`}</span></td><td><button class="table-action" data-edit="${s.id}">Edit</button></td></tr>`).join("") || `<tr><td colspan="7" class="muted">No students match this search.</td></tr>`;
}
function renderFiles() {
  $("#upload-count").textContent = `${state.files.length} files`;
  $("#file-list").innerHTML = state.files.map(f => `<div class="file-row"><span class="file-icon">${escapeHtml(f.source)}</span><div class="file-info"><strong>${escapeHtml(f.name)}</strong><small>${escapeHtml(f.type)} · ${escapeHtml(f.count)}</small></div><span class="status-pill ${f.state === "review" ? "warn" : f.state === "error" ? "error" : ""}">${f.state === "complete" ? "Matched" : f.state === "review" ? "Review" : "Processing"}</span></div>`).join("");
}
function renderIssues() {
  const issues = uniqueIssues();
  $("#issue-list").innerHTML = issues.slice(0, 4).map(({student, issue}) => `<div class="issue-item" data-issue="${student.id}"><strong>${escapeHtml(student.name)}</strong><span>${issue === "match confirmation" ? "Confirm possible student match" : `Missing ${issue}`}</span></div>`).join("") || `<div class="issue-item"><strong>All clear</strong><span>Every available report is ready.</span></div>`;
}
function renderReports() {
  $("#reports-table").innerHTML = state.reports.slice().reverse().map(r => `<tr><td><div class="student-name"><span class="student-avatar">${initials(r.studentName)}</span>${escapeHtml(r.studentName)}</div></td><td>${escapeHtml(r.className)}</td><td>${escapeHtml(r.createdAt)}</td><td>${escapeHtml(r.template)}</td><td><span class="status-pill">Ready</span></td><td><button class="table-action" data-report="${r.id}">Preview</button></td></tr>`).join("") || `<tr><td colspan="6" class="muted">No reports have been generated yet. Generate the ready students when you are ready.</td></tr>`;
}
function renderTemplate() {
  $("#template-comment-label").value = state.template.commentLabel;
  $("#template-fallback").value = state.template.fallback;
  $("#template-show-homework").checked = state.template.showHomework;
  const example = state.students[0]; $("#report-canvas").innerHTML = reportMarkup(example, true);
}
function reportMarkup(student, templateMode = false) {
  const cls = classById(student.classId) || { name: "Unassigned" };
  const comment = student.comment || state.template.fallback;
  return `<article class="report-sheet"><div class="report-header"><div><h2>Weekly learning report</h2><p>${escapeHtml(cls.name)} · Week 8 · August 10–16</p></div><strong>HANEUL<br/>ACADEMY</strong></div><div class="report-student"><div><b>${escapeHtml(student.name)}</b><br/><span>${escapeHtml(student.school)} · Grade ${escapeHtml(student.grade)}</span></div><div><strong>Teacher</strong><br/>${escapeHtml(cls.teacher)}</div></div>${state.template.showHomework ? `<section><span class="eyebrow">HOMEWORK TREND</span><div class="chart-placeholder"><div class="bars"><i style="height:35%"></i><i style="height:65%"></i><i style="height:52%"></i><i style="height:78%"></i><i style="height:90%"></i></div></div></section>` : ""}<section><span class="eyebrow">WEEKLY TEST</span><p><strong>${student.test || "—"}</strong> <span class="muted">latest score</span></p></section><section><span class="eyebrow">${escapeHtml(state.template.commentLabel)}</span><div class="report-comment">${escapeHtml(comment)}</div></section><section><span class="eyebrow">NEXT WEEK GOAL</span><p>${escapeHtml(student.goal || "Set a goal with your teacher.")}</p></section>${templateMode ? "" : `<p class="muted" style="margin-top:22px">Prepared by Brightline Reports · PDF-ready preview</p>`}</article>`;
}

function openModal(content) { $("#modal").innerHTML = `<button class="modal-close" aria-label="Close">×</button>${content}`; $("#modal-backdrop").classList.remove("hidden"); $(".modal-close").onclick = closeModal; }
function closeModal() { $("#modal-backdrop").classList.add("hidden"); }
function toast(message) { const t = $("#toast"); t.textContent = message; t.classList.remove("hidden"); clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add("hidden"), 3000); }
function newId(prefix) { return `${prefix}-${Date.now().toString(36)}`; }
function go(page) { currentPage = page; $$(".page").forEach(p => p.classList.toggle("active", p.id === page)); $$(".main-nav a").forEach(a => a.classList.toggle("active", a.dataset.nav === page)); const names = {dashboard:["REPORTING PERIOD","Week 8 reports"],classes:["CLASS DIRECTORY","Classes"],students:["PEOPLE","Students"],inbox:["AUTOMATION LAYER","Data inbox"],reports:["GENERATED OUTPUT","Reports"],templates:["REPORT DESIGN","Templates"]}; $("#page-kicker").textContent = names[page][0]; $("#page-title").textContent = names[page][1]; window.location.hash = page; window.scrollTo({top:0,behavior:"smooth"}); }

function openStudentModal(id) {
  const student = state.students.find(s => s.id === id); if (!student) return;
  openModal(`<h2>Edit student record</h2><p>Direct edits and imported data share this same normalized record.</p><form id="student-form"><div class="form-grid"><label>Student name<input name="name" value="${escapeHtml(student.name)}" required /></label><label>School<input name="school" value="${escapeHtml(student.school)}" /></label><label>Grade<input name="grade" value="${escapeHtml(student.grade)}" /></label><label>Class<select name="classId">${state.classes.map(c => `<option value="${c.id}" ${c.id === student.classId ? "selected" : ""}>${escapeHtml(c.name)}</option>`).join("")}</select></label><label>Homework value<input name="homework" value="${escapeHtml(student.homework)}" placeholder="A, Complete, 100..." /></label><label>Weekly test score<input type="number" name="test" value="${student.test || ""}" /></label></div><label>Teacher comment<textarea name="comment">${escapeHtml(student.comment)}</textarea></label><div class="modal-footer"><button type="button" class="secondary-button" id="delete-student">Delete</button><button class="primary-button">Save student</button></div></form>`);
  $("#student-form").onsubmit = e => { e.preventDefault(); const form = new FormData(e.target); Object.assign(student,{ name:form.get("name").trim(),school:form.get("school").trim(),grade:form.get("grade").trim(),classId:form.get("classId"),homework:form.get("homework").trim(),test:Number(form.get("test")) || 0,comment:form.get("comment").trim() }); state.activity.unshift({title:"Student data updated",detail:`${student.name} · direct entry`,time:"Just now"}); closeModal();render();toast("Student record saved"); };
  $("#delete-student").onclick = () => { if (!confirm(`Remove ${student.name}?`)) return; state.students = state.students.filter(s => s.id !== id); closeModal(); render(); toast("Student removed"); };
}
function openClassModal(id) {
  const cls = classById(id), d = classData(cls); openModal(`<h2>${escapeHtml(cls.name)}</h2><p>${escapeHtml(cls.subject)} · ${escapeHtml(cls.teacher)} · Week 8</p><div class="table-card"><table><thead><tr><th>Student</th><th>HW</th><th>Test</th><th>Comment</th><th></th></tr></thead><tbody>${d.students.map(s=>`<tr><td>${escapeHtml(s.name)}</td><td>${escapeHtml(s.homework || "—")}</td><td>${scoreText(s)}</td><td>${s.comment ? "Complete" : "Missing"}</td><td><button class="table-action" data-edit="${s.id}">Edit</button></td></tr>`).join("")}</tbody></table></div><div class="modal-footer"><button class="secondary-button" id="class-inbox">Upload class data</button><button class="primary-button" id="class-generate">Generate ready reports</button></div>`); $("#class-inbox").onclick=()=>{closeModal();go("inbox")}; $("#class-generate").onclick=()=>generateReports(d.students.filter(ready)); }
function addStudent() { const firstClass = state.classes[0]?.id || ""; const s = {id:newId("s"),name:"New student",school:"",grade:"",classId:firstClass,homework:"",test:0,comment:"",goal:"",matched:"high"};state.students.push(s);openStudentModal(s.id); }
function addClass() { openModal(`<h2>Create a class</h2><p>Classes are the home for students, teacher fields, imports, templates, and reporting periods.</p><form id="class-form"><div class="form-grid"><label>Subject<input name="subject" placeholder="IB Mathematics" required /></label><label>Class name<input name="name" placeholder="AA HL · Class B" required /></label><label>Teacher<input name="teacher" placeholder="Teacher name" required /></label><label>Short label<input name="color" placeholder="IB" maxlength="2" required /></label></div><div class="modal-footer"><button class="primary-button">Create class</button></div></form>`); $("#class-form").onsubmit=e=>{e.preventDefault();const f=new FormData(e.target),c={id:newId("class"),subject:f.get("subject"),name:f.get("name"),teacher:f.get("teacher"),color:f.get("color").toUpperCase()};state.classes.push(c);state.activity.unshift({title:"Class created",detail:`${c.name} · direct setup`,time:"Just now"});closeModal();render();toast("Class created");}; }

function generateReports(students = state.students.filter(ready)) {
  if (!students.length) return toast("No complete student records to generate.");
  students.forEach(s => { if (!state.reports.some(r => r.studentId === s.id)) state.reports.push({id:newId("r"),studentId:s.id,studentName:s.name,className:classById(s.classId)?.name || "Unassigned",createdAt:"Just now",template:"Week 8 report"}); });
  state.activity.unshift({title:"Reports generated",detail:`${students.length} student report${students.length === 1 ? "" : "s"} · Week 8`,time:"Just now"}); closeModal();render();go("reports");toast(`${students.length} PDF-ready reports generated`);
}
function previewReport(id) { const r=state.reports.find(r=>r.id===id), s=state.students.find(s=>s.id===r?.studentId); if(!s)return; openModal(`<h2>${escapeHtml(s.name)}'s report</h2><p>Use your browser's print dialog to save this report as a PDF.</p><div class="report-canvas" style="min-height:0;padding:12px">${reportMarkup(s)}</div><div class="modal-footer"><button class="secondary-button" id="print-report">Print / save as PDF</button></div>`); $("#print-report").onclick=()=>window.print(); }

function classify(headers, name) { const source = `${headers.join(" ")} ${name}`.toLowerCase(); if(/homework|\bhw\b|assignment/.test(source)) return "homework"; if(/test|score|exam|quiz/.test(source)) return "test"; if(/comment|feedback|note/.test(source)) return "comment"; if(/student|name|school|grade|class/.test(source)) return "roster"; return "unknown"; }
function parseCSV(text) { const lines=text.replace(/^\uFEFF/,"").trim().split(/\r?\n/); if(!lines.length)return[]; const parseLine=line=>{const out=[];let cell="",quoted=false;for(let i=0;i<line.length;i++){const c=line[i];if(c==='"'&&line[i+1]==='"'){cell+='"';i++;}else if(c==='"'){quoted=!quoted;}else if(c===","&&!quoted){out.push(cell.trim());cell="";}else cell+=c;}out.push(cell.trim());return out;};const headers=parseLine(lines.shift()).map(h=>h.trim());return lines.filter(Boolean).map(line=>Object.fromEntries(parseLine(line).map((v,i)=>[headers[i]||`field${i}`,v]))); }
function normalizedKey(key) { return key.toLowerCase().replace(/[^a-z0-9]/g,""); }
function findField(row, types) { const keys=Object.keys(row);const k=keys.find(k=>types.some(t=>normalizedKey(k).includes(t)));return k?row[k]:""; }
function importRows(rows, kind, filename) {
  if (!rows.length) return toast("No data rows were found in that file.");
  let changed=0, unresolved=0;
  rows.forEach(row => {
    const name=findField(row,["studentname","name","student"]), school=findField(row,["school"]), grade=findField(row,["grade"]), className=findField(row,["classname","class","course","subject"]);
    let s=state.students.find(x=>x.name.toLowerCase()===name.toLowerCase() && (!school || !x.school || x.school.toLowerCase()===school.toLowerCase()));
    if(kind==="roster") { if(!s && name){let cls=state.classes.find(c=>c.name.toLowerCase()===className.toLowerCase());if(!cls)cls=state.classes[0];s={id:newId("s"),name,school,grade,classId:cls?.id||"",homework:"",test:0,comment:"",goal:"",matched:"high"};state.students.push(s);changed++;} else if(s){Object.assign(s,{school:school||s.school,grade:grade||s.grade});changed++;} return; }
    if(!s){unresolved++;return;} if(kind==="homework"){s.homework=findField(row,["homework","hw","assignment","result"])||s.homework;changed++;} if(kind==="test"){s.test=Number(findField(row,["score","test","quiz","exam"]))||s.test;changed++;} if(kind==="comment"){s.comment=findField(row,["comment","feedback","note"])||s.comment;changed++;}
  });
  const typeMap={roster:"Student roster",homework:"Homework performance",test:"Weekly test scores",comment:"Teacher comments",unknown:"Unclassified data"};state.files.unshift({id:newId("f"),name:filename,type:typeMap[kind],count:unresolved?`${changed} matched · ${unresolved} unresolved`:`${changed} records matched`,state:unresolved||kind==="unknown"?"review":"complete",source:"CSV"});state.activity.unshift({title:"Data imported",detail:`${filename} · ${changed} records recognized`,time:"Just now"});render();toast(`${changed} records imported${unresolved ? ` · ${unresolved} need review` : ""}`);
}
function processFile(file) { if(!/\.(csv|txt)$/i.test(file.name)){state.files.unshift({id:newId("f"),name:file.name,type:"File received",count:"Open in review to map data",state:"review",source:file.name.split(".").pop().toUpperCase().slice(0,4)});render();toast(`${file.name} added to the review queue`);return;} const reader=new FileReader();reader.onload=()=>{const rows=parseCSV(reader.result);const kind=classify(Object.keys(rows[0]||{}),file.name);openModal(`<h2>We recognized this file</h2><p><strong>${escapeHtml(file.name)}</strong> looks like <strong>${({roster:"a student roster",homework:"homework performance",test:"weekly test scores",comment:"teacher comments",unknown:"unclassified data"})[kind]}</strong>. Confirm to import its ${rows.length} data rows.</p><div class="table-card"><table><thead><tr>${Object.keys(rows[0]||{}).slice(0,4).map(h=>`<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.slice(0,3).map(row=>`<tr>${Object.values(row).slice(0,4).map(v=>`<td>${escapeHtml(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div><div class="modal-footer"><button class="secondary-button" id="cancel-import">Cancel</button><button class="primary-button" id="confirm-import">Confirm import</button></div>`);$("#cancel-import").onclick=closeModal;$("#confirm-import").onclick=()=>{closeModal();importRows(rows,kind,file.name);};};reader.readAsText(file); }

function resolveIssue(id) { const s=state.students.find(s=>s.id===id);if(!s)return;const missing=issuesFor(s); openModal(`<h2>Review ${escapeHtml(s.name)}</h2><p>Only the missing or uncertain information is shown here. Your decision improves this student's normalized record.</p>${missing.includes("match confirmation")?`<label class="match-option selected"><input type="radio" checked /> Match to ${escapeHtml(s.name)} · ${escapeHtml(s.school)} · Grade ${escapeHtml(s.grade)}</label>`:""}<form id="issue-form"><div class="form-grid">${missing.includes("homework")?`<label>Homework value<input name="homework" placeholder="A, Complete, 100..." /></label>`:""}${missing.includes("weekly test")?`<label>Weekly test score<input type="number" name="test" placeholder="e.g. 87" /></label>`:""}</div>${missing.includes("teacher comment")?`<label>Teacher comment<textarea name="comment" placeholder="Add the teacher's note for this student"></textarea></label>`:""}<div class="modal-footer"><button class="primary-button">Save decision</button></div></form>`);$("#issue-form").onsubmit=e=>{e.preventDefault();const f=new FormData(e.target);if(f.has("homework"))s.homework=f.get("homework");if(f.has("test"))s.test=Number(f.get("test"))||0;if(f.has("comment"))s.comment=f.get("comment");s.matched="high";state.activity.unshift({title:"Review decision saved",detail:`${s.name} · manual confirmation`,time:"Just now"});closeModal();render();toast("Decision saved and report readiness updated");}; }

function bindEvents() {
  document.addEventListener("click", e => {
    const nav=e.target.closest("[data-nav]");if(nav){e.preventDefault();go(nav.dataset.nav);}
    const goto=e.target.closest("[data-go]");if(goto)go(goto.dataset.go);
    const edit=e.target.closest("[data-edit]");if(edit)openStudentModal(edit.dataset.edit);
    const card=e.target.closest("[data-class]");if(card)openClassModal(card.dataset.class);
    const issue=e.target.closest("[data-issue]");if(issue)resolveIssue(issue.dataset.issue);
    const rep=e.target.closest("[data-report]");if(rep)previewReport(rep.dataset.report);
    if(e.target===$("#modal-backdrop"))closeModal();
  });
  $("#add-class").onclick=addClass; $("#add-student").onclick=addStudent; $("#generate-ready").onclick=()=>generateReports(); $("#generate-all").onclick=()=>generateReports(); $("#review-all").onclick=()=>{const first=uniqueIssues()[0];if(first)resolveIssue(first.student.id);else toast("Everything is ready.");};
  $("#open-upload").onclick=()=>$("#file-input").click();$("#browse-files").onclick=()=>$("#file-input").click();$("#file-input").onchange=e=>[...e.target.files].forEach(processFile);
  const drop=$("#drop-zone");["dragenter","dragover"].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.add("dragging");}));["dragleave","drop"].forEach(type=>drop.addEventListener(type,e=>{e.preventDefault();drop.classList.remove("dragging");}));drop.addEventListener("drop",e=>[...e.dataTransfer.files].forEach(processFile));
  $("#student-search").oninput=renderStudents;$("#student-class-filter").onchange=renderStudents;
  $("#export-students").onclick=()=>{const csv=["Student Name,School,Grade,Class,Homework,Weekly Test,Teacher Comment",...state.students.map(s=>[s.name,s.school,s.grade,classById(s.classId)?.name||"",s.homework,s.test,`"${s.comment.replace(/"/g,'""')}"`].join(","))].join("\n");const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([csv],{type:"text/csv"}));a.download="haneul-students-week8.csv";a.click();URL.revokeObjectURL(a.href);toast("Student data exported as CSV");};
  $("#save-template").onclick=()=>{Object.assign(state.template,{commentLabel:$("#template-comment-label").value.trim()||"Teacher's note",fallback:$("#template-fallback").value.trim()||"No teacher comment submitted yet.",showHomework:$("#template-show-homework").checked});render();toast("Template changes saved locally");};
  $("#reset-demo").onclick=()=>{if(confirm("Reset all local MVP changes and restore the demo data?")){state=clone(seed);render();toast("Demo data restored");}};$("#mobile-menu").onclick=()=>$(".sidebar").classList.toggle("open");
}
bindEvents(); render(); if(location.hash.slice(1) && $("#"+location.hash.slice(1)))go(location.hash.slice(1));
