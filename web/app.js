const sessionSummary = document.querySelector("#sessionSummary");
const courseStatus = document.querySelector("#courseStatus");
const courseTable = document.querySelector("#courses");
const coursePanel = document.querySelector("#coursePanel");
const mappingPanel = document.querySelector("#mappingPanel");
const selectedCourseLabel = document.querySelector("#selectedCourse");
const mappingStatus = document.querySelector("#mappingStatus");
const mappingImportSummary = document.querySelector("#mappingImportSummary");
const outcomesContainer = document.querySelector("#outcomes");
const resultsContainer = document.querySelector("#results");
const exportReportButton = document.querySelector("#exportReportButton");
const exportHtmlButton = document.querySelector("#exportHtmlButton");
const exportMappingButton = document.querySelector("#exportMappingButton");
const exportStatus = document.querySelector("#exportStatus");
const yearFilter = document.querySelector("#year");
const termFilter = document.querySelector("#term");

let selectedCourse = null;
let outcomes = [];
let assignmentGroups = [];
let latestResults = [];
let openSections = {};
let coursesLoaded = false;
let allCourses = [];

const abetDescriptions = {
  "ABET-1": "An ability to identify, formulate, and solve complex engineering problems by applying principles of engineering, science, and mathematics.",
  "ABET-2": "An ability to apply engineering design to produce solutions that meet specified needs with consideration of public health, safety, and welfare, as well as global, cultural, social, environmental, and economic factors.",
  "ABET-3": "An ability to communicate effectively with a range of audiences.",
  "ABET-4": "An ability to recognize ethical and professional responsibilities in engineering situations and make informed judgments, which must consider the impact of engineering solutions in global, economic, environmental, and societal contexts.",
  "ABET-5": "An ability to function effectively on a team whose members together provide leadership, create a collaborative environment, establish goals, plan tasks, and meet objectives.",
  "ABET-6": "An ability to develop and conduct appropriate experimentation, analyze and interpret data, and use engineering judgment to draw conclusions.",
  "ABET-7": "An ability to acquire and apply new knowledge as needed, using appropriate learning strategies.",
  "SER-1": "",
  "SER-2": "",
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

async function refreshSession() {
  const status = await api("/api/session/status");
  if (status.active_profile) {
    sessionSummary.textContent = `Connected to Canvas as ${status.active_profile}.`;
    coursePanel.classList.remove("hidden");
    if (!coursesLoaded) await loadCourses();
  } else {
    sessionSummary.textContent = "No Canvas token is active. Restart the server with --canvas-token or ABET_MAPPER_CANVAS_TOKEN.";
    coursePanel.classList.add("hidden");
    coursesLoaded = false;
    allCourses = [];
  }
}

document.querySelector("#courseFilter").addEventListener("submit", async (event) => {
  event.preventDefault();
  renderFilteredCourses();
});

async function loadCourses() {
  courseStatus.textContent = "Loading courses from Canvas...";
  courseTable.innerHTML = "";
  try {
    const result = await api("/api/courses");
    allCourses = result.courses;
    coursesLoaded = true;
    renderFilteredCourses();
  } catch (error) {
    courseStatus.textContent = error.message;
  }
}

function renderFilteredCourses() {
  courseTable.innerHTML = "";
  const year = yearFilter.value.trim().toLowerCase();
  const term = termFilter.value.trim().toLowerCase();
  const filtered = allCourses.filter((course) => courseMatchesFilters(course, year, term));
  const applied = [];
  if (year) applied.push(`year ${year}`);
  if (term) applied.push(term);
  courseStatus.textContent = `${filtered.length} of ${allCourses.length} course(s) shown${applied.length ? ` for ${applied.join(", ")}` : ""}.`;
  for (const course of filtered) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><button type="button">Select</button></td>
      <td>${escapeHtml(course.name)}</td>
      <td>${escapeHtml(course.course_code)}</td>
      <td>${escapeHtml(course.term.name)}</td>
      <td>${course.total_students ?? ""}</td>
      <td>${course.id ?? ""}</td>
    `;
    row.querySelector("button").addEventListener("click", () => selectCourse(course));
    courseTable.append(row);
  }
}

function courseMatchesFilters(course, year, term) {
  const searchable = [
    course.name,
    course.course_code,
    course.term?.name,
    course.start_at,
    course.end_at,
  ].join(" ").toLowerCase();
  if (year && !searchable.includes(year)) return false;
  if (term && !searchable.includes(term)) return false;
  return true;
}

async function selectCourse(course) {
  selectedCourse = course;
  outcomes = [];
  assignmentGroups = [];
  latestResults = [];
  openSections = {};
  selectedCourseLabel.textContent = `${course.name} (${course.id})`;
  mappingPanel.classList.remove("hidden");
  mappingStatus.textContent = "Loading assessments and rubrics from Canvas...";
  clearMappingImportSummary();
  resultsContainer.innerHTML = "";
  updateExportState();
  renderOutcomes();
  await loadAssignmentsForSelectedCourse();
}

function resetMapping() {
  selectedCourse = null;
  outcomes = [];
  assignmentGroups = [];
  latestResults = [];
  openSections = {};
  mappingPanel.classList.add("hidden");
  selectedCourseLabel.textContent = "No course selected.";
  outcomesContainer.innerHTML = "";
  resultsContainer.innerHTML = "";
  clearMappingImportSummary();
  updateExportState();
}

async function loadAssignmentsForSelectedCourse() {
  if (!selectedCourse) return;
  try {
    const result = await api(`/api/courses/${selectedCourse.id}/assignments`);
    assignmentGroups = result.assignment_groups;
    mappingStatus.textContent = "Assessments loaded. Add an ABET outcome, then choose evidence.";
    renderOutcomes();
  } catch (error) {
    mappingStatus.textContent = error.message;
  }
}

document.querySelector("#outcomeForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = document.querySelector("#outcomeName");
  const name = input.value.trim();
  if (!name) {
    mappingStatus.textContent = "Enter an outcome name.";
    return;
  }
  outcomes.push({
    id: crypto.randomUUID(),
    name,
    description: abetDescriptions[name] || "",
    assignment_ids: [],
    evaluation: {
      meets_threshold: 70,
      criteria: [],
    },
  });
  markResultsStale();
  mappingStatus.textContent = `${outcomes.length} outcome(s) defined.`;
  renderOutcomes();
});

document.querySelector("#mappingFile")?.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (!file) return;
  if (!selectedCourse) {
    mappingStatus.textContent = "Select a course before loading a mapping.";
    return;
  }
  if (!assignmentGroups.length) {
    mappingStatus.textContent = "Wait for assessments and rubrics to load before loading a mapping.";
    return;
  }
  if (outcomes.length && !window.confirm("Replace the current outcome setup with this mapping?")) return;
  try {
    const mapping = JSON.parse(await file.text());
    const importResult = importMapping(mapping);
    outcomes = importResult.outcomes;
    latestResults = [];
    resultsContainer.innerHTML = "";
    updateExportState();
    renderOutcomes();
    mappingStatus.textContent = importSummaryText(importResult, file.name);
    renderMappingImportSummary(importResult, file.name);
  } catch (error) {
    clearMappingImportSummary();
    mappingStatus.textContent = `Mapping import failed: ${error.message}`;
  }
});

document.querySelector("#calculateButton").addEventListener("click", async () => {
  if (!selectedCourse) return;
  for (const outcome of outcomes) ensureOutcomeCriteria(outcome);
  const assignmentIds = [...new Set(outcomes.flatMap((outcome) => outcome.assignment_ids))];
  if (assignmentIds.length === 0) {
    mappingStatus.textContent = "Choose at least one assessment for an outcome.";
    return;
  }
  mappingStatus.textContent = "Loading grading data and calculating attainment. This can take a while...";
  resultsContainer.innerHTML = "";
  try {
    const preparedOutcomes = outcomes.map((outcome) => outcomeWithCourseScoreMode(outcome));
    const result = await api(`/api/courses/${selectedCourse.id}/calculate-attainment`, {
      method: "POST",
      body: JSON.stringify({
        course: selectedCourse,
        outcomes: preparedOutcomes,
        score_mode: selectedCourseScoreMode(),
        strategy: selectedAttainmentStrategy(),
        assignment_ids: assignmentIds,
        selected_assignments: getSelectedAssignments(assignmentIds),
      }),
    });
    mappingStatus.textContent = "Attainment calculated. Local JSON snapshot saved.";
    latestResults = result.results;
    updateExportState();
    renderResults(result.results);
  } catch (error) {
    mappingStatus.textContent = error.message;
  }
});

exportReportButton.addEventListener("click", () => exportReport("excel"));
exportHtmlButton.addEventListener("click", () => exportReport("html"));
exportMappingButton.addEventListener("click", exportMappingJson);

async function exportReport(format) {
  if (!selectedCourse || latestResults.length === 0) {
    mappingStatus.textContent = "Calculate attainment before exporting.";
    return;
  }
  const label = format === "html" ? "HTML report" : "Excel report";
  const endpoint = format === "html" ? "export-html" : "export-report";
  mappingStatus.textContent = `Writing ${label}...`;
  setExportStatus(`Writing ${label}...`, true, format);
  try {
    const result = await api(`/api/courses/${selectedCourse.id}/${endpoint}`, {
      method: "POST",
      body: JSON.stringify({
        course: selectedCourse,
        outcomes: outcomes.map((outcome) => outcomeWithCourseScoreMode(outcome)),
        score_mode: selectedCourseScoreMode(),
        strategy: selectedAttainmentStrategy(),
        results: latestResults,
      }),
    });
    const reportSummary = exportReportSummary(result, label);
    mappingStatus.textContent = reportSummary;
    setExportStatus(`Done. ${reportSummary}`, false, format);
  } catch (error) {
    mappingStatus.textContent = error.message;
    setExportStatus(`Export failed: ${error.message}`, false, format);
  }
}

function renderOutcomes() {
  outcomesContainer.innerHTML = "";
  exportMappingButton.disabled = !selectedCourse || outcomes.length === 0;
  if (outcomes.length === 0) {
    outcomesContainer.innerHTML = `<div class="muted">No outcomes yet.</div>`;
    return;
  }
  for (const outcome of outcomes) outcomesContainer.append(createOutcomePanel(outcome));
}

function exportMappingJson() {
  if (!selectedCourse || outcomes.length === 0) {
    mappingStatus.textContent = "Select a course and add at least one outcome before exporting a mapping.";
    return;
  }
  for (const outcome of outcomes) ensureOutcomeCriteria(outcome);
  const academicYear = courseAcademicYear(selectedCourse);
  const mapping = {
    schema_version: 2,
    exported_at: new Date().toISOString(),
    academic_year: academicYear || null,
    course: {
      id: selectedCourse.id,
      name: selectedCourse.name || "",
      course_code: selectedCourse.course_code || "",
      term: selectedCourse.term || {},
      start_at: selectedCourse.start_at || null,
      end_at: selectedCourse.end_at || null,
    },
    score_mode: "scaled",
    attainment_strategy: "canvasore_kpi",
    outcomes: outcomes.map((outcome) => ({
      name: outcome.name,
      description: outcome.description || "",
      meets_threshold: Number(outcome.evaluation.meets_threshold ?? 70),
      items: outcome.evaluation.criteria
        .filter((criterion) => criterion.selected)
        .map((criterion) => ({
          assignment_name: criterion.assignment_name,
          source: criterion.source,
          criterion_description: criterion.description,
          meets_threshold: Number(criterion.meets_threshold ?? outcome.evaluation.meets_threshold ?? 70),
        })),
    })),
  };
  const courseLabel = selectedCourse.course_code || selectedCourse.name || `course-${selectedCourse.id}`;
  const filename = `ABET_Mapping_${safeFilenamePart(courseLabel)}_${academicYear || "year-unknown"}.json`;
  const blob = new Blob([JSON.stringify(mapping, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
  mappingStatus.textContent = `Mapping JSON exported as ${filename}.`;
}

function courseAcademicYear(course) {
  const values = [
    course?.term?.name,
    course?.name,
    course?.course_code,
    course?.term?.start_at,
    course?.start_at,
    course?.term?.end_at,
    course?.end_at,
  ];
  for (const value of values) {
    const match = String(value || "").match(/\b(20\d{2})\b/);
    if (match) return match[1];
  }
  return "";
}

function safeFilenamePart(value) {
  return String(value || "course")
    .replace(/[^A-Za-z0-9_.-]+/g, "_")
    .replace(/^_+|_+$/g, "") || "course";
}

function createOutcomePanel(outcome) {
  ensureOutcomeCriteria(outcome);
  const panel = document.createElement("details");
  panel.className = "outcome-panel";
  panel.dataset.openKey = `outcome:${outcome.id}`;
  panel.open = sectionIsOpen(panel.dataset.openKey, true);
  panel.innerHTML = `
    <summary class="outcome-summary">
      <span>${escapeHtml(outcome.name)} <span class="muted">${outcome.assignment_ids.length} assessment(s)</span></span>
      <button type="button" class="danger small-button" data-role="delete-outcome">Delete</button>
    </summary>
    <div class="outcome-body">
      <div class="muted">${escapeHtml(outcome.description || "")}</div>
      <details class="setup-section" data-open-key="assignment-setup:${outcome.id}" ${sectionIsOpen(`assignment-setup:${outcome.id}`, true) ? "open" : ""}>
        <summary>
          <span class="summary-line">
            <span>Assessments</span>
            <span class="muted">${outcome.assignment_ids.length} selected</span>
          </span>
        </summary>
        <div class="assignment-list">${assignmentSelectionHtml(outcome)}</div>
      </details>
      <details class="setup-section evaluation-setup" data-open-key="evaluation-setup:${outcome.id}" ${sectionIsOpen(`evaluation-setup:${outcome.id}`, true) ? "open" : ""}>
        <summary>
          <span class="summary-line">
            <span>Evaluation Setup</span>
            <span class="muted">${evidenceStatusText(outcome)}</span>
          </span>
        </summary>
        <div class="evaluation-body">
          <div class="form-grid compact-grid">
            <label>
              Outcome / default Attains threshold
              <input data-role="meets" type="number" min="0" max="100" step="1" value="${outcome.evaluation.meets_threshold}">
            </label>
          </div>
          <div class="criteria-list">${criteriaHtml(outcome)}</div>
        </div>
      </details>
    </div>
  `;
  wireOutcomePanel(panel, outcome);
  return panel;
}

function assignmentSelectionHtml(outcome) {
  if (!assignmentGroups.length) return `<div class="muted">Assessments are loading.</div>`;
  return assignmentGroups.map((group) => `
    <details data-open-key="assignments:${outcome.id}:${group.id}" ${sectionIsOpen(`assignments:${outcome.id}:${group.id}`, true) ? "open" : ""}>
      <summary>${escapeHtml(group.name)}</summary>
      ${group.assignments.map((assignment) => `
        <label class="assignment-check">
          <input type="checkbox" data-role="assignment" data-assignment-id="${assignment.id}" ${outcome.assignment_ids.includes(String(assignment.id)) ? "checked" : ""}>
          <span>${escapeHtml(assignment.name)}</span>
          <span class="muted">${assignment.points_possible ?? ""} pts · ${assignment.rubric.length} criteria</span>
        </label>
      `).join("")}
    </details>
  `).join("");
}

function criteriaHtml(outcome) {
  if (outcome.assignment_ids.length === 0) return `<div class="muted">Select assessments first.</div>`;
  if (outcome.evaluation.criteria.length === 0) return `<div class="muted">No criteria or assessment scores available.</div>`;
  return outcome.assignment_ids.map((assignmentId) => {
    const assignment = findAssignment(assignmentId);
    const items = outcome.evaluation.criteria.filter((criterion) => criterion.assignment_id === String(assignmentId));
    const whole = items.find((criterion) => criterion.source === "assignment");
    const rubricItems = items.filter((criterion) => criterion.source === "rubric");
    return `
      <details class="evaluation-assignment" data-open-key="evaluation-assignment:${outcome.id}:${assignmentId}" ${sectionIsOpen(`evaluation-assignment:${outcome.id}:${assignmentId}`, true) ? "open" : ""}>
        <summary>${escapeHtml(assignment?.name || "Assessment")} <span class="muted">${assignment?.points_possible ?? ""} pts</span></summary>
        ${criterionRowHtml(whole)}
        ${rubricItems.length ? `
          <details data-open-key="rubric:${outcome.id}:${assignmentId}" ${sectionIsOpen(`rubric:${outcome.id}:${assignmentId}`, true) ? "open" : ""}>
            <summary>Rubric Criteria (${rubricItems.length})</summary>
            ${rubricItems.map(criterionRowHtml).join("")}
          </details>
        ` : ""}
      </details>
    `;
  }).join("");
}

function criterionRowHtml(criterion) {
  if (!criterion) return "";
  return `
    <div class="criteria-row">
      <input type="checkbox" data-role="criterion" data-criterion-id="${escapeAttr(criterion.id)}" ${criterion.selected ? "checked" : ""}>
      <div>
        <div>${escapeHtml(criterion.description)}</div>
        <div class="muted">${criterion.source === "assignment" ? "Whole assessment" : "Rubric criterion"}</div>
      </div>
      <span class="muted">${criterion.points ?? ""} pts</span>
      <label>
        Attains threshold
        <input data-role="item-meets" data-criterion-id="${escapeAttr(criterion.id)}" type="number" min="0" max="100" step="1" value="${criterion.meets_threshold}">
      </label>
    </div>
  `;
}

function wireOutcomePanel(panel, outcome) {
  panel.querySelectorAll("details[data-open-key]").forEach((details) => {
    details.addEventListener("toggle", () => {
      openSections[details.dataset.openKey] = details.open;
    });
  });
  panel.addEventListener("toggle", (event) => {
    if (event.target !== panel) return;
    openSections[panel.dataset.openKey] = panel.open;
  });
  panel.querySelector("[data-role='delete-outcome']").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    deleteOutcome(outcome.id);
  });
  panel.querySelectorAll("[data-role='assignment']").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const assignmentId = String(checkbox.dataset.assignmentId);
      if (checkbox.checked && !outcome.assignment_ids.includes(assignmentId)) outcome.assignment_ids.push(assignmentId);
      if (!checkbox.checked) {
        outcome.assignment_ids = outcome.assignment_ids.filter((id) => id !== assignmentId);
        outcome.evaluation.criteria = outcome.evaluation.criteria.filter((criterion) => criterion.assignment_id !== assignmentId);
      }
      markResultsStale();
      ensureOutcomeCriteria(outcome);
      renderOutcomes();
    });
  });
  panel.querySelector("[data-role='meets']").addEventListener("input", (event) => {
    const previousThreshold = Number(outcome.evaluation.meets_threshold ?? 70);
    const nextThreshold = Number(event.target.value || 70);
    outcome.evaluation.meets_threshold = nextThreshold;
    for (const criterion of outcome.evaluation.criteria) {
      if (Number(criterion.meets_threshold ?? previousThreshold) === previousThreshold) {
        criterion.meets_threshold = nextThreshold;
      }
    }
    markResultsStale();
  });
  panel.querySelectorAll("[data-role='criterion']").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const criterion = outcome.evaluation.criteria.find((item) => item.id === checkbox.dataset.criterionId);
      if (!criterion) return;
      criterion.selected = checkbox.checked;
      enforceWholeAssignmentExclusivity(outcome, criterion);
      markResultsStale();
      renderOutcomes();
    });
  });
  panel.querySelectorAll("[data-role='item-meets']").forEach((input) => {
    input.addEventListener("input", () => {
      const criterion = outcome.evaluation.criteria.find((item) => item.id === input.dataset.criterionId);
      if (criterion) criterion.meets_threshold = Number(input.value || outcome.evaluation.meets_threshold || 70);
      markResultsStale();
    });
  });
}

function deleteOutcome(outcomeId) {
  const outcome = outcomes.find((item) => item.id === outcomeId);
  outcomes = outcomes.filter((item) => item.id !== outcomeId);
  markResultsStale();
  for (const key of Object.keys(openSections)) {
    if (key.includes(outcomeId)) delete openSections[key];
  }
  mappingStatus.textContent = `${outcome?.name || "Outcome"} deleted. Recalculate attainment before exporting.`;
  renderOutcomes();
}

function markResultsStale() {
  latestResults = [];
  resultsContainer.innerHTML = "";
  if (exportStatus) exportStatus.textContent = "";
  updateExportState();
}

function updateExportState() {
  if (exportReportButton) exportReportButton.disabled = latestResults.length === 0;
  if (exportHtmlButton) exportHtmlButton.disabled = latestResults.length === 0;
}

function setExportStatus(message, busy, format) {
  if (exportStatus) exportStatus.textContent = message;
  exportReportButton.disabled = busy || latestResults.length === 0;
  exportHtmlButton.disabled = busy || latestResults.length === 0;
  exportReportButton.textContent = busy && format === "excel" ? "Exporting..." : "Export Excel Report";
  exportHtmlButton.textContent = busy && format === "html" ? "Exporting..." : "Export HTML Report";
}

function exportReportSummary(result, label) {
  const files = Array.isArray(result.report_files) ? result.report_files : [];
  if (!files.length) return `${label} saved: ${result.report_file}`;
  if (files.length === 1) return `${label} saved: ${files[0].report_file}`;
  return `${files.length} ${label}s saved: ${files.map((file) => `${file.scope}: ${file.report_file}`).join("; ")}`;
}

function sectionIsOpen(key, defaultValue) {
  return Object.prototype.hasOwnProperty.call(openSections, key) ? openSections[key] : defaultValue;
}

function ensureOutcomeCriteria(outcome) {
  const existing = new Map(outcome.evaluation.criteria.map((criterion) => [criterion.id, criterion]));
  const next = [];
  for (const assignmentId of outcome.assignment_ids) {
    const assignment = findAssignment(assignmentId);
    if (!assignment) continue;
    const wholeId = `${assignment.id}:assignment-score`;
    next.push(existing.get(wholeId) || {
      id: wholeId,
      assignment_id: String(assignment.id),
      assignment_name: assignment.name,
      criterion_id: null,
      description: "Whole assessment score",
      points: assignment.points_possible,
      selected: assignment.rubric.length === 0,
      source: "assignment",
      meets_threshold: outcome.evaluation.meets_threshold,
    });
    for (const criterion of assignment.rubric) {
      const id = `${assignment.id}:${criterion.id || criterion.description}`;
      next.push(existing.get(id) || {
        id,
        assignment_id: String(assignment.id),
        assignment_name: assignment.name,
        criterion_id: criterion.id,
        description: criterion.description,
        points: criterion.points,
        selected: true,
        source: "rubric",
        meets_threshold: outcome.evaluation.meets_threshold,
      });
    }
  }
  outcome.evaluation.criteria = next;
  for (const criterion of next.filter((item) => item.source === "assignment" && item.selected)) {
    enforceWholeAssignmentExclusivity(outcome, criterion);
  }
}

function enforceWholeAssignmentExclusivity(outcome, changedCriterion) {
  if (!changedCriterion.selected) return;
  for (const criterion of outcome.evaluation.criteria) {
    if (criterion.assignment_id !== changedCriterion.assignment_id || criterion.id === changedCriterion.id) continue;
    if (changedCriterion.source === "assignment" || criterion.source === "assignment") {
      criterion.selected = false;
    }
  }
}

function evidenceStatusText(outcome) {
  const selected = outcome.evaluation.criteria.filter((criterion) => criterion.selected);
  if (selected.length === 0) return "No evaluation items selected.";
  return `${selected.length} KPI/evidence item(s) selected.`;
}

function findAssignment(assignmentId) {
  for (const group of assignmentGroups) {
    const assignment = group.assignments.find((item) => String(item.id) === String(assignmentId));
    if (assignment) return assignment;
  }
  return null;
}

function getSelectedAssignments(assignmentIds) {
  return assignmentIds.map((assignmentId) => findAssignment(assignmentId)).filter(Boolean);
}

function selectedCourseScoreMode() {
  return "scaled";
}

function selectedAttainmentStrategy() {
  return "canvasore_kpi";
}

function importMapping(mapping) {
  const mappedOutcomes = Array.isArray(mapping?.outcomes) ? mapping.outcomes : [];
  if (!mappedOutcomes.length) throw new Error("No outcomes found in mapping JSON.");
  const summary = { matched: 0, missing: [], ambiguous: [] };
  const imported = mappedOutcomes.map((mappedOutcome) => {
    const outcome = {
      id: crypto.randomUUID(),
      name: mappedOutcome.name || "ABET Outcome",
      description: mappedOutcome.description || abetDescriptions[mappedOutcome.name] || "",
      assignment_ids: [],
      evaluation: {
        meets_threshold: Number(mappedOutcome.meets_threshold ?? 70),
        criteria: [],
      },
    };
    const selectedSpecs = [];
    for (const item of mappingItemsForOutcome(mappedOutcome)) {
      const assignmentResult = findAssignmentByName(item.assignment_name);
      if (assignmentResult.status === "missing") {
        summary.missing.push(`${outcome.name}: ${item.assignment_name || "Unnamed assessment"}`);
        continue;
      }
      if (assignmentResult.status === "ambiguous") {
        summary.ambiguous.push(`${outcome.name}: ${item.assignment_name}`);
        continue;
      }
      const assignment = assignmentResult.assignment;
      if (!outcome.assignment_ids.includes(String(assignment.id))) outcome.assignment_ids.push(String(assignment.id));
      selectedSpecs.push({ item, assignment });
    }

    ensureOutcomeCriteria(outcome);
    for (const criterion of outcome.evaluation.criteria) {
      criterion.selected = false;
    }
    for (const spec of selectedSpecs) {
      const criterionResult = findCriterionForMapping(outcome, spec.assignment, spec.item);
      if (criterionResult.status === "missing") {
        summary.missing.push(`${outcome.name}: ${spec.assignment.name} / ${spec.item.criterion_description || "Whole assessment score"}`);
        continue;
      }
      if (criterionResult.status === "ambiguous") {
        summary.ambiguous.push(`${outcome.name}: ${spec.assignment.name} / ${spec.item.criterion_description}`);
        continue;
      }
      const criterion = criterionResult.criterion;
      criterion.selected = true;
      criterion.meets_threshold = Number(spec.item.meets_threshold ?? outcome.evaluation.meets_threshold ?? 70);
      enforceWholeAssignmentExclusivity(outcome, criterion);
      summary.matched += 1;
    }
    return outcome;
  });
  return { outcomes: imported, summary };
}

function mappingItemsForOutcome(mappedOutcome) {
  if (Array.isArray(mappedOutcome.items)) return mappedOutcome.items;
  const criteria = mappedOutcome.evaluation?.criteria;
  if (!Array.isArray(criteria)) return [];
  return criteria
    .filter((criterion) => criterion.selected)
    .map((criterion) => ({
      assignment_name: criterion.assignment_name,
      source: criterion.source,
      criterion_description: criterion.description,
      meets_threshold: criterion.meets_threshold,
    }));
}

function importSummaryText(result, fileName) {
  const { matched, missing, ambiguous } = result.summary;
  const parts = [`Loaded ${result.outcomes.length} outcome(s) from ${fileName}.`, `Matched ${matched} item(s).`];
  const unresolved = missing.length + ambiguous.length;
  if (unresolved) parts.push(`${unresolved} item(s) need review below.`);
  else parts.push("All saved evidence items were matched.");
  if (!matched) parts.push(`Loaded assessment names include: ${availableAssignmentNames().slice(0, 5).join("; ") || "none"}.`);
  return parts.join(" ");
}

function clearMappingImportSummary() {
  if (!mappingImportSummary) return;
  mappingImportSummary.innerHTML = "";
  mappingImportSummary.className = "mapping-import-summary hidden";
}

function renderMappingImportSummary(result, fileName) {
  if (!mappingImportSummary) return;
  const { matched, missing, ambiguous } = result.summary;
  const unresolved = missing.length + ambiguous.length;
  mappingImportSummary.className = `mapping-import-summary ${unresolved ? "warning" : "success"}`;

  if (!unresolved) {
    mappingImportSummary.innerHTML = `
      <strong>All saved evidence items were matched successfully.</strong>
      <p>${matched} item(s) from ${escapeHtml(fileName)} are ready to use.</p>
    `;
    return;
  }

  const sections = [];
  if (missing.length) {
    sections.push(`
      <h3>Not found (${missing.length})</h3>
      <ul>${missing.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    `);
  }
  if (ambiguous.length) {
    sections.push(`
      <h3>Multiple possible matches (${ambiguous.length})</h3>
      <ul>${ambiguous.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    `);
  }
  mappingImportSummary.innerHTML = `
    <strong>${unresolved} saved evidence item(s) could not be matched automatically.</strong>
    <p>${matched} item(s) matched. Items listed below were not selected and should be reviewed.</p>
    <details>
      <summary>Open the complete unmatched-item list (${unresolved})</summary>
      <div class="mapping-import-list">${sections.join("")}</div>
    </details>
  `;
}

function findAssignmentByName(name) {
  const target = normalizeMappingText(name);
  const matches = [];
  const fuzzyMatches = [];
  for (const group of assignmentGroups) {
    for (const assignment of group.assignments) {
      const candidate = normalizeMappingText(assignment.name);
      if (candidate === target) matches.push(assignment);
      else if (assignmentNameSimilarity(candidate, target) >= 0.8) fuzzyMatches.push(assignment);
    }
  }
  if (matches.length === 1) return { status: "matched", assignment: matches[0] };
  if (matches.length > 1) return { status: "ambiguous", matches };
  if (fuzzyMatches.length === 1) return { status: "matched", assignment: fuzzyMatches[0] };
  if (fuzzyMatches.length > 1) return { status: "ambiguous", matches: fuzzyMatches };
  return { status: "missing" };
}

function findCriterionForMapping(outcome, assignment, item) {
  const description = normalizeMappingText(item.criterion_description);
  const source = item.source === "assignment" || ["whole assignment score", "whole assessment score"].includes(description) ? "assignment" : "rubric";
  const candidates = outcome.evaluation.criteria.filter((criterion) => criterion.assignment_id === String(assignment.id) && criterion.source === source);
  if (source === "assignment") {
    const whole = candidates.find((criterion) => criterion.source === "assignment");
    return whole ? { status: "matched", criterion: whole } : { status: "missing" };
  }
  const target = normalizeMappingText(item.criterion_description);
  const matches = candidates.filter((criterion) => normalizeMappingText(criterion.description) === target);
  if (matches.length === 1) return { status: "matched", criterion: matches[0] };
  if (matches.length > 1) return { status: "ambiguous", matches };
  return { status: "missing" };
}

function normalizeMappingText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/&amp;/g, "&")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

function availableAssignmentNames() {
  return assignmentGroups.flatMap((group) => group.assignments.map((assignment) => assignment.name));
}

function assignmentNameSimilarity(candidate, target) {
  if (!candidate || !target) return 0;
  if (candidate.includes(target) || target.includes(candidate)) return 0.9;
  const candidateTokens = new Set(candidate.split(" ").filter(Boolean));
  const targetTokens = new Set(target.split(" ").filter(Boolean));
  const shared = [...targetTokens].filter((token) => candidateTokens.has(token)).length;
  return shared / Math.max(targetTokens.size, 1);
}

function outcomeWithCourseScoreMode(outcome) {
  return {
    ...outcome,
    evaluation: {
      ...(outcome.evaluation || {}),
      score_mode: selectedCourseScoreMode(),
    },
  };
}

function renderResults(results) {
  resultsContainer.innerHTML = "";
  for (const result of results) {
    const attainedCount = Number(result.counts.meets || 0);
    const attainedPercent = Number(result.percentages.meets || 0);
    const card = document.createElement("section");
    card.className = "result-card";
    card.innerHTML = `
      <h2>${escapeHtml(result.outcome_name)}</h2>
      <div class="muted">Attains threshold ${result.meets_threshold}% · ${escapeHtml(result.score_mode_label || "")} · ${escapeHtml(result.strategy_label || "")}</div>
      <div class="overall-attained">
        Overall Attained:
        <strong>${result.overall_attained_count}/${result.overall_known_count}</strong>
        <strong>${result.overall_attained_percent}%</strong>
        <span class="muted">(Attains, excluding Unknown)</span>
      </div>
      <div class="summary-grid">
        ${summaryCell("Attains", attainedCount, attainedPercent, "meets")}
        ${summaryCell("Does Not Meet", result.counts.does_not_meet || 0, result.percentages.does_not_meet || 0, "does_not_meet")}
        ${summaryCell("Unknown", result.counts.unknown || 0, result.percentages.unknown || 0, "unknown")}
      </div>
      <details open>
        <summary>Criteria Overview (${result.criterion_stats.length})</summary>
        ${criterionOverviewTable(result.criterion_stats, result.meets_threshold)}
      </details>
      <details class="student-details">
        <summary>Student Details (${result.students.length})</summary>
        <div class="student-detail-list"></div>
      </details>
    `;
    const details = card.querySelector(".student-detail-list");
    for (const student of result.students) details.append(createStudentResultRow(student));
    resultsContainer.append(card);
  }
}

function summaryCell(label, count, percent, className) {
  return `
    <div class="summary-cell ${className}">
      <span>${label}</span>
      <strong>${count}</strong>
      <span>${percent}%</span>
    </div>
  `;
}

function criterionOverviewTable(criteria, defaultMeetsThreshold) {
  if (!criteria || criteria.length === 0) return `<div class="muted">No criteria selected.</div>`;
  return `
    <table>
      <thead>
        <tr>
          <th>Assessment</th>
          <th>Item</th>
          <th>Attains threshold</th>
          <th>Overall Attained</th>
          <th>Attains</th>
          <th>Does Not Meet</th>
          <th>Unknown</th>
        </tr>
      </thead>
      <tbody>
        ${criteria.map((criterion) => `
          <tr>
            <td>${escapeHtml(criterion.assignment_name)}</td>
            <td>${escapeHtml(displayEvidenceDescription(criterion.description))}</td>
            <td>${criterion.meets_threshold || defaultMeetsThreshold}%</td>
            <td>${criterion.percentages.attained}%</td>
            <td>${criterion.counts.meets} (${criterion.percentages.meets}%)</td>
            <td>${criterion.counts.does_not_meet} (${criterion.percentages.does_not_meet}%)</td>
            <td>${criterion.counts.unknown} (${criterion.percentages.unknown}%)</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function createStudentResultRow(student) {
  const row = document.createElement("details");
  row.className = "student-row";
  const score = studentAttainmentValue(student);
  row.innerHTML = `
    <summary>
      <span class="badge ${student.category_key}">${student.category}</span>
      ${escapeHtml(student.student_name || student.student_id)} · ${score}
    </summary>
    <table>
      <thead>
        <tr>
          <th>Assessment</th>
          <th>Item</th>
          <th>Score</th>
          <th>Raw Rubric</th>
          <th>Rubric Total</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        ${student.details.map((detail) => `
          <tr>
            <td>${escapeHtml(detail.assignment_name)}</td>
            <td>${escapeHtml(displayEvidenceDescription(detail.description))}</td>
            <td>${detail.score_percent === null ? "" : `${detail.score_percent}%`}</td>
            <td>${detail.raw_rubric_points ?? ""}</td>
            <td>${detail.rubric_point_sum ?? ""}</td>
            <td><span class="badge ${detail.category_key}">${escapeHtml(detail.category)}</span> ${detail.included ? "" : escapeHtml(detail.reason)}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
  return row;
}

function studentAttainmentValue(student) {
  const minimum = student.attainment_min_percent;
  const maximum = student.attainment_max_percent;
  if (minimum !== null && minimum !== undefined && maximum !== null && maximum !== undefined) {
    return Number(minimum) === Number(maximum) ? `${minimum}%` : `${minimum}–${maximum}%`;
  }
  return student.score_percent === null || student.score_percent === undefined
    ? "n/a"
    : `${student.score_percent}%`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function displayEvidenceDescription(value) {
  return String(value || "").toLowerCase() === "whole assignment score"
    ? "Whole assessment score"
    : value;
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

setDefaultCourseYear();

refreshSession().catch((error) => {
  sessionSummary.textContent = error.message;
});

function setDefaultCourseYear() {
  if (!yearFilter || yearFilter.value.trim()) return;
  yearFilter.value = String(new Date().getFullYear());
}
