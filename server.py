from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from report_writer import write_attainment_html, write_attainment_report


APP_ROOT = Path(__file__).resolve().parent
WEB_ROOT = APP_ROOT / "web"
DATA_DIR = Path(
    os.environ.get("ABET_MAPPER_DATA_DIR", str(APP_ROOT / "ABET Mapper Data"))
).expanduser()
SESSIONS_FILE = DATA_DIR / "sessions.json"
PROJECTS_DIR = DATA_DIR / "projects"
REPORTS_DIR = DATA_DIR / "reports"
DEFAULT_CANVAS_BASE_URL = "https://canvas.asu.edu"


class AppError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class CanvasProfile:
    name: str
    base_url: str
    token: str

    @property
    def api_base(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1"

    def public_dict(self) -> dict[str, str]:
        return {"name": self.name, "base_url": self.base_url}


class SessionStore:
    def __init__(self, path: Path):
        self.path = path
        self.active_profile: str | None = None
        self.tokens: dict[str, str] = {}
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"profiles": {}}
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, data: dict[str, Any]) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.chmod(self.path, 0o600)

    def list_profiles(self) -> list[dict[str, str]]:
        data = self._read()
        profiles = []
        for name, profile in sorted(data.get("profiles", {}).items()):
            profiles.append({"name": name, "base_url": profile["base_url"]})
        return profiles

    def save_profile(self, profile: CanvasProfile) -> None:
        data = self._read()
        data.setdefault("profiles", {})[profile.name] = {
            "base_url": profile.base_url.rstrip("/"),
        }
        self._write(data)
        self.active_profile = profile.name
        self.tokens[profile.name] = profile.token

    def load_profile_token(self, profile: CanvasProfile) -> None:
        data = self._read()
        data.setdefault("profiles", {})[profile.name] = {
            "base_url": profile.base_url.rstrip("/"),
        }
        self._write(data)
        self.active_profile = profile.name
        self.tokens[profile.name] = profile.token

    def use_profile(self, name: str) -> dict[str, str]:
        profile = self.get_profile_metadata(name)
        self.active_profile = name
        return profile

    def get_active_profile(self) -> CanvasProfile:
        if not self.active_profile:
            profiles = self.list_profiles()
            if len(profiles) == 1:
                self.active_profile = profiles[0]["name"]
            else:
                raise AppError(HTTPStatus.UNAUTHORIZED, "No Canvas session is selected.")
        return self.get_profile(self.active_profile)

    def get_profile(self, name: str) -> CanvasProfile:
        raw = self.get_profile_metadata(name)
        token = self.tokens.get(name)
        if not token:
            raise AppError(
                HTTPStatus.UNAUTHORIZED,
                f"Session profile '{name}' has no active token. Restart the server with --canvas-token or ABET_MAPPER_CANVAS_TOKEN.",
            )
        return CanvasProfile(name=name, base_url=raw["base_url"], token=token)

    def get_profile_metadata(self, name: str) -> dict[str, str]:
        data = self._read()
        raw = data.get("profiles", {}).get(name)
        if not raw:
            raise AppError(HTTPStatus.NOT_FOUND, f"Session profile '{name}' was not found.")
        return {"name": name, "base_url": raw["base_url"]}

    def clear_active(self) -> None:
        self.active_profile = None


class CanvasClient:
    def __init__(self, profile: CanvasProfile):
        self.profile = profile
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {profile.token}",
                "Accept": "application/json",
            }
        )

    def get_paginated(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        url = f"{self.profile.api_base}/{path.lstrip('/')}"
        results: list[dict[str, Any]] = []
        while url:
            response = self.session.get(url, params=params, timeout=30)
            params = None
            if response.status_code in (401, 403):
                raise AppError(HTTPStatus.UNAUTHORIZED, "Canvas rejected this session. Check the token or permissions.")
            if response.status_code >= 400:
                raise AppError(HTTPStatus.BAD_GATEWAY, f"Canvas API error {response.status_code}: {response.text[:300]}")
            payload = response.json()
            if isinstance(payload, list):
                results.extend(payload)
            elif isinstance(payload, dict):
                results.append(payload)
            else:
                raise AppError(HTTPStatus.BAD_GATEWAY, "Canvas returned an unexpected response.")
            url = response.links.get("next", {}).get("url")
        return results

    def verify(self) -> dict[str, Any]:
        users = self.get_paginated("users/self")
        return users[0] if users else {}

    def courses(self, year: str | None = None, term: str | None = None) -> list[dict[str, Any]]:
        courses = self.get_paginated(
            "courses",
            {
                "per_page": 100,
                "enrollment_state": "active",
                "include[]": ["term", "total_students"],
            },
        )
        return [
            course_summary(course)
            for course in courses
            if course_matches_year(course, year) and course_matches_term(course, term)
        ]

    def assignments(self, course_id: str) -> list[dict[str, Any]]:
        groups = self.get_paginated(
            f"courses/{course_id}/assignment_groups",
            {
                "per_page": 100,
                "include[]": ["assignments"],
            },
        )
        return assignment_group_summaries(groups)

    def submissions(self, course_id: str, assignment_ids: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for assignment_id in assignment_ids:
            submissions = self.get_paginated(
                f"courses/{course_id}/assignments/{assignment_id}/submissions",
                {
                    "per_page": 100,
                    "include[]": ["rubric_assessment", "submission_history", "user"],
                },
            )
            result[assignment_id] = [submission_summary(item) for item in submissions]
        return result

    def sections(self, course_id: str) -> list[dict[str, Any]]:
        enrollments = self.get_paginated(
            f"courses/{course_id}/enrollments",
            {
                "per_page": 100,
                "type[]": "StudentEnrollment",
                "include[]": ["user", "section"],
            },
        )
        return section_summaries(enrollments)


def course_summary(course: dict[str, Any]) -> dict[str, Any]:
    term = course.get("term") or {}
    return {
        "id": course.get("id"),
        "name": course.get("name") or "",
        "course_code": course.get("course_code") or "",
        "workflow_state": course.get("workflow_state") or "",
        "start_at": course.get("start_at"),
        "end_at": course.get("end_at"),
        "term": {
            "id": term.get("id"),
            "name": term.get("name") or "",
            "start_at": term.get("start_at"),
            "end_at": term.get("end_at"),
        },
        "total_students": course.get("total_students"),
    }


def assignment_group_summaries(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries = []
    for group in groups:
        assignments = []
        for assignment in group.get("assignments") or []:
            assignments.append(assignment_summary(assignment, group))
        summaries.append(
            {
                "id": group.get("id"),
                "name": group.get("name") or "",
                "position": group.get("position"),
                "group_weight": group.get("group_weight"),
                "rules": group.get("rules") or {},
                "assignments": assignments,
            }
        )
    return summaries


def assignment_summary(assignment: dict[str, Any], group: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": assignment.get("id"),
        "name": assignment.get("name") or "",
        "assignment_group_id": group.get("id"),
        "assignment_group_name": group.get("name") or "",
        "points_possible": assignment.get("points_possible"),
        "due_at": assignment.get("due_at"),
        "workflow_state": assignment.get("workflow_state"),
        "submission_types": assignment.get("submission_types") or [],
        "quiz_id": assignment.get("quiz_id"),
        "rubric": rubric_summary(assignment.get("rubric") or []),
    }


def rubric_summary(rubric: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for criterion in rubric:
        rows.append(
            {
                "id": criterion.get("id"),
                "description": criterion.get("description") or "",
                "long_description": criterion.get("long_description") or "",
                "points": criterion.get("points"),
            }
        )
    return rows


def submission_summary(submission: dict[str, Any]) -> dict[str, Any]:
    user = submission.get("user") or {}
    return {
        "id": submission.get("id"),
        "assignment_id": submission.get("assignment_id"),
        "user_id": submission.get("user_id"),
        "user_name": user.get("sortable_name") or user.get("name") or "",
        "user_login_id": user.get("login_id") or "",
        "user_short_name": user.get("short_name") or "",
        "workflow_state": submission.get("workflow_state"),
        "grade_matches_current_submission": submission.get("grade_matches_current_submission"),
        "score": submission.get("score"),
        "entered_score": submission.get("entered_score"),
        "grade": submission.get("grade"),
        "excused": submission.get("excused"),
        "missing": submission.get("missing"),
        "late_policy_status": submission.get("late_policy_status"),
        "late": submission.get("late"),
        "submitted_at": submission.get("submitted_at"),
        "graded_at": submission.get("graded_at"),
        "rubric_assessment": submission.get("rubric_assessment") or {},
    }


def section_summaries(enrollments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: dict[str, dict[str, Any]] = {}
    for enrollment in enrollments:
        user_id = str(enrollment.get("user_id") or "")
        if not user_id:
            continue
        section = enrollment.get("section") or {}
        section_id = str(
            enrollment.get("course_section_id")
            or section.get("id")
            or enrollment.get("course_section_id")
            or "unknown"
        )
        section_name = (
            section.get("name")
            or enrollment.get("course_section_name")
            or f"Section {section_id}"
        )
        entry = sections.setdefault(
            section_id,
            {
                "id": section_id,
                "name": section_name,
                "student_ids": [],
            },
        )
        if user_id not in entry["student_ids"]:
            entry["student_ids"].append(user_id)
    return sorted(sections.values(), key=lambda item: item.get("name") or "")


def save_project_snapshot(course: dict[str, Any], payload: dict[str, Any]) -> Path:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    course_id = str(course.get("id") or "unknown")
    course_label = course.get("course_code") or course.get("name") or f"course-{course_id}"
    year = course_academic_year(course) or "year-unknown"
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(course_label)).strip("_") or f"course-{course_id}"
    path = PROJECTS_DIR / f"ABET_Snapshot_{safe_label}_{year}.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    return path


def course_academic_year(course: dict[str, Any]) -> str | None:
    term = course.get("term") or {}
    values = [
        term.get("name"),
        course.get("name"),
        course.get("course_code"),
        term.get("start_at"),
        course.get("start_at"),
        term.get("end_at"),
        course.get("end_at"),
    ]
    for value in values:
        match = re.search(r"\b(20\d{2})\b", str(value or ""))
        if match:
            return match.group(1)
    return None


def course_matches_year(course: dict[str, Any], year: str | None) -> bool:
    if not year:
        return True
    if not re.fullmatch(r"\d{4}", year):
        raise AppError(HTTPStatus.BAD_REQUEST, "Year must be four digits.")
    search_values = [
        course.get("name"),
        course.get("course_code"),
        course.get("start_at"),
        course.get("end_at"),
        (course.get("term") or {}).get("name"),
        (course.get("term") or {}).get("start_at"),
        (course.get("term") or {}).get("end_at"),
    ]
    short_year = year[-2:]
    year_patterns = {
        year,
        f"'{short_year}",
        f"-{short_year}",
        f" {short_year}",
    }
    return any(any(pattern in str(value) for pattern in year_patterns) for value in search_values if value)


def course_matches_term(course: dict[str, Any], term: str | None) -> bool:
    if not term:
        return True
    term = term.lower()
    if term not in {"spring", "summer", "fall"}:
        raise AppError(HTTPStatus.BAD_REQUEST, "Term must be spring, summer, or fall.")
    aliases = {
        "spring": {"spring", "spr"},
        "summer": {"summer", "sum"},
        "fall": {"fall"},
    }[term]
    search_values = [
        course.get("name"),
        course.get("course_code"),
        (course.get("term") or {}).get("name"),
    ]
    return any(any(alias in str(value).lower() for alias in aliases) for value in search_values if value)


session_store = SessionStore(SESSIONS_FILE)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            if parsed.path == "/":
                self.path = "/index.html"
            return super().do_GET()
        self._handle_api("GET", parsed.path, parse_qs(parsed.query))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        self._handle_api("POST", parsed.path, parse_qs(parsed.query))

    def _handle_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        try:
            if method == "GET" and path == "/api/session/status":
                self._json({"active_profile": session_store.active_profile, "profiles": session_store.list_profiles()})
            elif method == "POST" and path == "/api/session/login":
                payload = self._read_json()
                profile = CanvasProfile(
                    name=clean_profile_name(payload.get("name") or "ASU Canvas"),
                    base_url=(payload.get("base_url") or DEFAULT_CANVAS_BASE_URL).rstrip("/"),
                    token=payload.get("token") or "",
                )
                if not profile.token.strip():
                    raise AppError(HTTPStatus.BAD_REQUEST, "Canvas API token is required.")
                user = CanvasClient(profile).verify()
                session_store.save_profile(profile)
                self._json({"profile": profile.public_dict(), "user": user})
            elif method == "POST" and path == "/api/session/use":
                payload = self._read_json()
                profile = session_store.use_profile(clean_profile_name(payload.get("name") or ""))
                self._json({"profile": profile})
            elif method == "POST" and path == "/api/session/logout":
                session_store.clear_active()
                self._json({"active_profile": None})
            elif method == "GET" and path == "/api/courses":
                profile = session_store.get_active_profile()
                year = first_query_value(query, "year")
                term = first_query_value(query, "term")
                courses = CanvasClient(profile).courses(year, term)
                self._json({"courses": courses, "filters": {"year": year, "term": term}})
            elif method == "GET" and re.fullmatch(r"/api/courses/\d+/assignments", path):
                profile = session_store.get_active_profile()
                course_id = path.split("/")[3]
                groups = CanvasClient(profile).assignments(course_id)
                self._json({"assignment_groups": groups})
            elif method == "POST" and re.fullmatch(r"/api/courses/\d+/calculate-attainment", path):
                profile = session_store.get_active_profile()
                course_id = path.split("/")[3]
                payload = self._read_json()
                assignment_ids = normalize_assignment_ids(payload.get("assignment_ids") or [])
                if not assignment_ids:
                    raise AppError(HTTPStatus.BAD_REQUEST, "Choose at least one assessment.")
                client = CanvasClient(profile)
                submissions = client.submissions(course_id, assignment_ids)
                outcomes = payload.get("outcomes") or []
                course = payload.get("course") or {"id": course_id}
                strategy_config = {"strategy": "canvasore_kpi", "score_mode": "scaled"}
                results = calculate_attainment(outcomes, submissions, strategy_config)
                snapshot = {
                    "schema_version": 1,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "course_id": course_id,
                    "course_name": course.get("name") or course.get("course_code") or "",
                    "academic_year": course_academic_year(course),
                    "course": course,
                    "outcomes": outcomes,
                    "assignment_ids": assignment_ids,
                    "selected_assignments": payload.get("selected_assignments") or [],
                    "strategy": strategy_config,
                    "submissions_by_assignment": submissions,
                    "results": results,
                }
                path = save_project_snapshot(course, snapshot)
                self._json({"results": results, "snapshot_file": str(path)})
            elif method == "POST" and re.fullmatch(r"/api/courses/\d+/export-(report|html)", path):
                profile = session_store.get_active_profile()
                course_id = path.split("/")[3]
                html_format = path.endswith("/export-html")
                extension = ".html" if html_format else ".xlsx"
                writer = write_attainment_html if html_format else write_attainment_report
                payload = self._read_json()
                course = payload.get("course") or {"id": course_id}
                outcomes = payload.get("outcomes") or []
                results = payload.get("results") or []
                if not results:
                    raise AppError(HTTPStatus.BAD_REQUEST, "Calculate attainment before exporting a report.")
                metadata = {
                    "score_mode": payload.get("score_mode"),
                    "strategy": payload.get("strategy"),
                }
                report_path = report_output_path(course, extension=extension)
                writer(
                    report_path,
                    course,
                    outcomes,
                    results,
                    metadata,
                )
                report_files = [{"scope": "Overall", "report_file": str(report_path)}]
                sections = CanvasClient(profile).sections(course_id)
                section_exports = []
                for section in sections:
                    section_results = filter_results_for_students(results, set(section.get("student_ids") or []))
                    if results_have_students(section_results):
                        section_exports.append((section, section_results))
                if len(section_exports) > 1:
                    for section, section_results in section_exports:
                        section_course = course_for_section(course, section)
                        section_path = report_output_path(section_course, section.get("name") or "Section", extension)
                        writer(
                            section_path,
                            section_course,
                            outcomes,
                            section_results,
                            metadata,
                        )
                        report_files.append({"scope": section.get("name") or "Section", "report_file": str(section_path)})
                self._json({"report_file": str(report_path), "report_files": report_files, "format": "html" if html_format else "excel"})
            else:
                raise AppError(HTTPStatus.NOT_FOUND, "API endpoint not found.")
        except AppError as exc:
            self._json({"error": exc.message}, exc.status)
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw)

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def clean_profile_name(value: str) -> str:
    value = value.strip()
    if not value:
        raise AppError(HTTPStatus.BAD_REQUEST, "Session profile name is required.")
    return value


def first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    return values[0].strip() if values and values[0].strip() else None


def normalize_assignment_ids(values: list[Any]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def report_output_path(course: dict[str, Any], suffix: str | None = None, extension: str = ".xlsx") -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    course_name = course.get("course_code") or course.get("name") or f"course-{course.get('id', 'unknown')}"
    name_parts = [str(course_name)]
    if suffix:
        name_parts.append(str(suffix))
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", "_".join(name_parts)).strip("_")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return REPORTS_DIR / f"ABET_Report_{safe_name}_{stamp}{extension}"


def course_for_section(course: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    section_name = section.get("name") or "Section"
    result = dict(course)
    result["section"] = {"id": section.get("id"), "name": section_name}
    return result


def filter_results_for_students(results: list[dict[str, Any]], student_ids: set[str]) -> list[dict[str, Any]]:
    filtered = []
    for result in results:
        next_result = json.loads(json.dumps(result))
        next_result["students"] = [
            student
            for student in next_result.get("students") or []
            if str(student.get("student_id") or "") in student_ids
        ]
        refresh_result_summary(next_result)
        filtered.append(next_result)
    return filtered


def refresh_result_summary(result: dict[str, Any]) -> None:
    students = result.get("students") or []
    counts = {"meets": 0, "does_not_meet": 0, "unknown": 0}
    for student in students:
        key = student.get("category_key") or "unknown"
        if key not in counts:
            key = "unknown"
        counts[key] += 1
    total_known = counts["meets"] + counts["does_not_meet"]
    result["counts"] = counts
    result["overall_attained_count"] = counts["meets"]
    result["overall_known_count"] = total_known
    result["overall_attained_percent"] = round((counts["meets"] / total_known) * 100, 1) if total_known else 0
    result["percentages"] = {
        "meets": round((counts["meets"] / total_known) * 100, 1) if total_known else 0,
        "does_not_meet": round((counts["does_not_meet"] / total_known) * 100, 1) if total_known else 0,
        "unknown": round((counts["unknown"] / len(students)) * 100, 1) if students else 0,
    }
    result["criterion_stats"] = calculate_criterion_stats(students)


def results_have_students(results: list[dict[str, Any]]) -> bool:
    return any(result.get("students") for result in results)


def calculate_attainment(
    outcomes: list[dict[str, Any]],
    submissions_by_assignment: dict[str, list[dict[str, Any]]],
    strategy_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    results = []
    strategy_config = strategy_config or {}
    submissions_index = index_submissions(submissions_by_assignment)
    for outcome in outcomes:
        evaluation = outcome.get("evaluation") or {}
        strategy = attainment_strategy(strategy_config)
        criteria = [item for item in evaluation.get("criteria", []) if item.get("selected")]
        students = sorted(
            {
                student_id
                for assignment in submissions_index.values()
                for student_id, submission in assignment.items()
                if not is_canvas_test_student(submission)
            }
        )
        student_results = []
        counts = {"meets": 0, "does_not_meet": 0, "unknown": 0}
        for student_id in students:
            student_result = calculate_student(outcome, criteria, submissions_index, student_id, strategy_config)
            counts[student_result["category_key"]] += 1
            student_results.append(student_result)
        total_known = counts["meets"] + counts["does_not_meet"]
        criterion_stats = calculate_criterion_stats(student_results)
        results.append(
            {
                "outcome_id": outcome.get("id"),
                "outcome_name": outcome.get("name") or "",
                "meets_threshold": number_or_default(evaluation.get("meets_threshold"), 70),
                "score_mode": score_mode(evaluation),
                "score_mode_label": score_mode_label(evaluation),
                "strategy": strategy,
                "strategy_label": attainment_strategy_label(strategy_config),
                "overall_attained_count": counts["meets"],
                "overall_known_count": total_known,
                "overall_attained_percent": round(
                    (counts["meets"] / total_known) * 100,
                    1,
                )
                if total_known
                else 0,
                "counts": counts,
                "percentages": {
                    key: round((value / total_known) * 100, 1) if total_known else 0
                    for key, value in counts.items()
                    if key != "unknown"
                }
                | {"unknown": round((counts["unknown"] / len(students)) * 100, 1) if students else 0},
                "criterion_stats": criterion_stats,
                "students": student_results,
            }
        )
    return results


def index_submissions(submissions_by_assignment: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for assignment_id, submissions in submissions_by_assignment.items():
        result[str(assignment_id)] = {}
        for submission in submissions:
            if submission.get("grade_matches_current_submission") is False:
                continue
            user_id = str(submission.get("user_id") or "")
            if user_id:
                result[str(assignment_id)][user_id] = submission
    return result


def calculate_student(
    outcome: dict[str, Any],
    criteria: list[dict[str, Any]],
    submissions_index: dict[str, dict[str, dict[str, Any]]],
    student_id: str,
    strategy_config: dict[str, Any],
) -> dict[str, Any]:
    evaluation = outcome.get("evaluation") or {}
    meets = number_or_default(evaluation.get("meets_threshold"), 70)
    details = []
    student_name = ""
    for criterion in criteria:
        assignment_id = str(criterion.get("assignment_id") or "")
        submission = submissions_index.get(assignment_id, {}).get(student_id)
        if submission:
            student_name = student_name or submission.get("user_name") or ""
        detail = calculate_criterion(criterion, submission)
        if detail["included"]:
            item_meets = number_or_default(criterion.get("meets_threshold"), meets)
            detail["category_key"] = category_key_for_score(detail["score_percent"], item_meets)
            detail["category"] = label_for_category(detail["category_key"])
        else:
            detail["category_key"] = "unknown"
            detail["category"] = "Unknown"
        details.append(detail)
    minimum_score, maximum_score, category_key = classify_student_result(details, meets)
    category = label_for_category(category_key)
    score = minimum_score if minimum_score == maximum_score else None

    return {
        "student_id": student_id,
        "student_name": student_name,
        "score_percent": score,
        "attainment_min_percent": minimum_score,
        "attainment_max_percent": maximum_score,
        "category": category,
        "category_key": category_key,
        "strategy": attainment_strategy(strategy_config),
        "details": details,
    }


def classify_student_result(
    details: list[dict[str, Any]],
    meets: float,
) -> tuple[float | None, float | None, str]:
    if not details:
        return None, None, "unknown"

    # Match the original CanvasOre KPI rollup. The lower bound assumes every
    # Unknown item does not attain; the upper bound assumes every Unknown item
    # attains. Classify only when both possibilities lead to the same result.
    met_count = sum(1 for detail in details if detail.get("category_key") == "meets")
    unknown_count = sum(1 for detail in details if detail.get("category_key") == "unknown")
    minimum_ratio = (met_count / len(details)) * 100
    maximum_ratio = ((met_count + unknown_count) / len(details)) * 100
    minimum_percent = round(minimum_ratio, 1)
    maximum_percent = round(maximum_ratio, 1)
    if minimum_ratio >= meets:
        category_key = "meets"
    elif maximum_ratio < meets:
        category_key = "does_not_meet"
    else:
        category_key = "unknown"
    return minimum_percent, maximum_percent, category_key


def calculate_criterion(criterion: dict[str, Any], submission: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "assignment_id": criterion.get("assignment_id"),
        "assignment_name": criterion.get("assignment_name") or "",
        "criterion_id": criterion.get("criterion_id"),
        "description": criterion.get("description") or "",
        "meets_threshold": criterion.get("meets_threshold"),
    }
    if not submission:
        return base | {"included": False, "reason": "No submission record", "score_percent": None}
    assignment_score = number_or_none(submission.get("entered_score"))
    if assignment_score is None:
        assignment_score = number_or_none(submission.get("score"))
    if assignment_score is None and (submission_is_missing(submission) or submission_is_excused(submission)):
        return base | {"included": False, "reason": zero_score_unknown_reason(submission), "score_percent": None}
    if assignment_score is not None and assignment_score == 0 and zero_score_is_unknown(submission):
        return base | {"included": False, "reason": zero_score_unknown_reason(submission), "score_percent": None}

    criterion_id = criterion.get("criterion_id")
    if criterion_id:
        rubric_assessment = submission.get("rubric_assessment") or {}
        rubric_score = rubric_score_for(rubric_assessment, str(criterion_id))
        points = number_or_none(criterion.get("points"))
        if not points:
            return base | {"included": False, "reason": "Rubric points unavailable", "score_percent": None}
        if rubric_score is None:
            rubric_score = 0.0
        rubric_sum = rubric_point_sum(rubric_assessment)
        score_points = 0.0 if rubric_sum == 0 or assignment_score is None else rubric_score * (assignment_score / rubric_sum)
        return base | {
            "included": True,
            "reason": "",
            "score_percent": round((score_points / points) * 100, 1),
            "points_earned": round(score_points, 3),
            "raw_rubric_points": rubric_score,
            "rubric_point_sum": round(rubric_sum, 3),
            "points_possible": points,
        }

    points = number_or_none(criterion.get("points"))
    if assignment_score is None or not points:
        return base | {"included": False, "reason": "Assessment score unavailable", "score_percent": None}
    return base | {
        "included": True,
        "reason": "",
        "score_percent": round((assignment_score / points) * 100, 1),
        "points_earned": assignment_score,
        "points_possible": points,
    }


def rubric_score_for(rubric_assessment: dict[str, Any], criterion_id: str) -> float | None:
    raw = rubric_assessment.get(criterion_id)
    if isinstance(raw, dict):
        return number_or_none(raw.get("points"))
    return None


def zero_score_is_unknown(submission: dict[str, Any]) -> bool:
    return bool(submission_is_missing(submission) or submission_is_excused(submission) or not has_submission_evidence(submission))


def zero_score_unknown_reason(submission: dict[str, Any]) -> str:
    if submission_is_excused(submission):
        return "Excused assessment with no points"
    if submission_is_missing(submission):
        return "Missing assessment with no points"
    return "No submission evidence and no points"


def submission_is_missing(submission: dict[str, Any]) -> bool:
    return bool(submission.get("missing") or str(submission.get("late_policy_status") or "").lower() == "missing")


def submission_is_excused(submission: dict[str, Any]) -> bool:
    return bool(submission.get("excused") or str(submission.get("late_policy_status") or "").lower() == "excused")


def has_submission_evidence(submission: dict[str, Any]) -> bool:
    if submission.get("submitted_at"):
        return True
    workflow_state = str(submission.get("workflow_state") or "").lower()
    return workflow_state in {"submitted", "pending_review"}


def rubric_point_sum(rubric_assessment: dict[str, Any]) -> float:
    total = 0.0
    for raw in rubric_assessment.values():
        if not isinstance(raw, dict):
            continue
        points = number_or_none(raw.get("points"))
        if points is not None:
            total += points
    return total


def calculate_criterion_stats(student_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for student in student_results:
        for detail in student.get("details", []):
            key = str(detail.get("criterion_id") or f"{detail.get('assignment_id')}:assignment-score")
            if key not in stats:
                stats[key] = {
                    "criterion_key": key,
                    "assignment_name": detail.get("assignment_name") or "",
                    "description": detail.get("description") or "",
                    "meets_threshold": detail.get("meets_threshold"),
                    "counts": {"meets": 0, "does_not_meet": 0, "unknown": 0},
                }
            stats[key]["counts"][detail.get("category_key") or "unknown"] += 1

    result = []
    for item in stats.values():
        counts = item["counts"]
        total_known = counts["meets"] + counts["does_not_meet"]
        item["percentages"] = {
            "meets": round((counts["meets"] / total_known) * 100, 1) if total_known else 0,
            "does_not_meet": round((counts["does_not_meet"] / total_known) * 100, 1) if total_known else 0,
            "attained": round((counts["meets"] / total_known) * 100, 1) if total_known else 0,
            "unknown": round((counts["unknown"] / sum(counts.values())) * 100, 1) if sum(counts.values()) else 0,
        }
        result.append(item)
    return result


def category_key_for_score(score: float | None, meets: float) -> str:
    if score is None:
        return "unknown"
    if score >= meets:
        return "meets"
    return "does_not_meet"


def label_for_category(category_key: str) -> str:
    return {
        "meets": "Attains",
        "does_not_meet": "Does Not Meet",
        "unknown": "Unknown",
    }.get(category_key, "Unknown")


def score_mode(evaluation: dict[str, Any]) -> str:
    return "scaled"


def score_mode_label(evaluation: dict[str, Any]) -> str:
    return "CanvasOre compatible scaled scores"


def attainment_strategy(config: dict[str, Any]) -> str:
    return "canvasore_kpi"


def attainment_strategy_label(config: dict[str, Any]) -> str:
    return "CanvasOre KPI count"


def number_or_default(value: Any, default: float) -> float:
    parsed = number_or_none(value)
    return default if parsed is None else parsed


def number_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def is_canvas_test_student(submission: dict[str, Any]) -> bool:
    text = " ".join(
        str(submission.get(key) or "")
        for key in ("user_name", "user_login_id", "user_short_name")
    ).lower()
    return any(marker in text for marker in ("test student", "student view", "teststudent"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local ABET Mapper web app.")
    parser.add_argument("--host", default=os.environ.get("ABET_MAPPER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ABET_MAPPER_PORT", "8765")))
    parser.add_argument("--canvas-token", default=os.environ.get("ABET_MAPPER_CANVAS_TOKEN"))
    parser.add_argument("--canvas-url", default=os.environ.get("ABET_MAPPER_CANVAS_URL", DEFAULT_CANVAS_BASE_URL))
    parser.add_argument("--profile-name", default=os.environ.get("ABET_MAPPER_PROFILE_NAME", "ASU Canvas"))
    args = parser.parse_args()

    if args.canvas_token:
        session_store.load_profile_token(
            CanvasProfile(
                name=clean_profile_name(args.profile_name or "ASU Canvas"),
                base_url=(args.canvas_url or DEFAULT_CANVAS_BASE_URL).rstrip("/"),
                token=args.canvas_token,
            )
        )

    host = args.host
    port = args.port
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"ABET Mapper MVP running at http://{host}:{port}")
    if args.canvas_token:
        print(f"Canvas session preloaded for {session_store.active_profile}.")
    server.serve_forever()


if __name__ == "__main__":
    main()
