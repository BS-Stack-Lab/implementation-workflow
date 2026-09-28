#!/usr/bin/env python3
"""Keep implementation evidence local and check design/QA stage gates."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from orchestrate import focus_for


ARTIFACTS = {
    "frontend": ("frontend-design.md", "frontend-design-review.md", "frontend-code-review.md", "frontend-qa.md"),
    "backend": ("backend-design.md", "backend-design-review.md", "backend-code-review.md", "backend-qa.md"),
}
REVIEW_SLOTS = (1, 2, 3)
QA_KINDS = {"normal", "boundary", "authorization", "regression", "operations"}
RISK_SEGMENTS = {"auth", "authentication", "authorization", "permission", "permissions",
                 "security", "payment", "payments", "billing", "privacy", "migration",
                 "migrations", "deploy", "infra", "contracts", "contract", "schema",
                 "schemas", "secrets", "oauth", "token", "tokens", "credential",
                 "credentials", "identity", "crypto", "cryptography", "encryption",
                 "encrypt", "decrypt", "key", "keys", "jwt", "session", "pem",
                 "p12", "pfx", "jks", "keystore", "password", "passwords",
                 "passwd", "passcode", "mfa", "otp", "sso", "saml", "openid",
                 "oidc", "csrf", "cors", "cookie", "cookies", "tls", "ssl",
                 "cert", "certificate", "private", "ssh", "kubeconfig", "env"}
ROOT_SHARED_FILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock",
                     "uv.lock", "pyproject.toml", "package.json", "tsconfig.json",
                     "gradle.properties", ".gitmodules"}
SHARED_SEGMENTS = {"shared", "contracts", "schema", "schemas"}


def areas_for(scope: str) -> list[str]:
    return ["frontend", "backend"] if scope == "both" else [scope]


def review_areas(scope: str) -> list[str]:
    return areas_for(scope) + (["integration"] if scope == "both" else [])


def required_slots(state: dict) -> tuple[int, ...]:
    return (1,) if state.get("version", 0) >= 6 and state.get("review_depth") == "light" else REVIEW_SLOTS


def review_depth(state: dict) -> str:
    return state.get("review_depth", "full") if state.get("version", 0) >= 6 else "full"


def risk_guard(state: dict) -> None:
    if review_depth(state) != "light":
        return
    if state["scope"] == "both" or not str(state.get("risk_reason", "")).strip():
        raise ValueError("light review needs one area and a risk reason")
    repo = repo_for_area(state, state["scope"])
    base = state.get("base_commits", {}).get(state["scope"]) if state["version"] >= 7 else None
    if state["version"] >= 7 and not base:
        raise ValueError("light review needs the recorded base commit")
    head_exists = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                                 capture_output=True).returncode == 0
    changed = (git_output(repo, "diff", "--name-only", "--no-renames", base or "HEAD", "-z", "--")
               if base or head_exists else git_output(repo, "ls-files", "--cached", "-z"))
    untracked = git_output(repo, "ls-files", "--others", "--exclude-standard", "-z")
    changed_paths = set((changed + untracked).split(b"\0")) - {b""}
    if state["version"] >= 7:
        index_entries = git_output(repo, "ls-files", "--stage", "-z")
        head = subprocess.run(["git", "-C", str(repo), "ls-tree", "-r", "-z", base or "HEAD"],
                              capture_output=True)
        index_links = {raw.partition(b"\t")[2] for raw in index_entries.split(b"\0")
                       if raw.startswith(b"160000 ")}
        head_links = {raw.partition(b"\t")[2] for raw in head.stdout.split(b"\0")
                      if raw.startswith(b"160000 ")}
        for path in index_links | head_links:
            submodule = repo / os.fsdecode(path)
            status = subprocess.run(["git", "-C", str(submodule), "status", "--porcelain",
                                     "--untracked-files=all"], capture_output=True)
            if path in changed_paths or path not in index_links or not submodule.is_dir() or status.returncode \
                    or status.stdout.strip():
                raise ValueError(f"light review cannot include a changed submodule: {os.fsdecode(path)}")
    for raw in changed_paths:
        path = os.fsdecode(raw).casefold()
        parts = set(re.split(r"[/_.-]+", path))
        if parts & RISK_SEGMENTS or Path(path).name in ROOT_SHARED_FILES:
            raise ValueError(f"light review cannot include a high-risk path: {path}")


def within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def repo_for_area(state: dict, area: str) -> Path:
    if state.get("version", 0) >= 7:
        return Path(state["repo_roots"][area])
    return Path(state["repo_root"])


def checkout_identity(repo: Path) -> str:
    root = git_output(repo, "rev-parse", "--show-toplevel").decode().strip()
    if Path(root).resolve() != repo.resolve():
        raise ValueError("repository path must name the Git checkout root")
    directories = []
    for flag in ("--git-common-dir", "--git-dir"):
        value = git_output(repo, "rev-parse", flag).decode().strip()
        location = (repo / value).resolve()
        metadata = location.stat()
        directories.append(f"{location}:{metadata.st_dev}:{metadata.st_ino}")
    return "|".join(directories)


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def docs_base() -> Path:
    return Path.home() / "Documents" / "docs"


def legacy_base() -> Path:
    return Path.home() / ".codex" / "implementation-workflow-runs"


def ensure_local_run_dir(run_dir: Path, repo_root: Path, allow_legacy: bool = False) -> None:
    path = lexical_absolute(run_dir)
    bases = [docs_base()]
    if allow_legacy:
        bases.append(legacy_base())
    resolved_path = path.resolve()
    matching_base = next((base for base in bases
                          if resolved_path != base.resolve()
                          and within(resolved_path, base.resolve())), None)
    if matching_base is None:
        raise ValueError("run directory must be under ~/Documents/docs"
                         + (" or an existing legacy run" if allow_legacy else ""))
    home = lexical_absolute(Path.home())
    resolved_home = home.resolve()
    home_ancestors = {home, *home.parents, resolved_home, *resolved_home.parents}
    input_parents = {path, *path.parents, matching_base, *matching_base.parents}
    if any(parent.is_symlink() for parent in input_parents if parent not in home_ancestors):
        raise ValueError("run directory must not contain a symbolic link")
    if within(resolved_path, repo_root.resolve()):
        raise ValueError("run directory must be outside the target Git repository")
    if any((parent / ".git").exists() for parent in
           {path, *path.parents, resolved_path, *resolved_path.parents}):
        raise ValueError("run directory must not be inside any Git repository")


def directory_id(name: str, identity: str) -> str:
    readable = re.sub(r"[^\w.-]+", "-", name, flags=re.UNICODE).strip("._-")[:48] or "item"
    return f"{readable}-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:8]}"


def default_run_dir(repo_root: Path, work_item: str | None = None,
                    parent_dir: Path | None = None) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if parent_dir is None:
        origin = subprocess.run(["git", "-C", str(repo_root), "config", "--get", "remote.origin.url"],
                                capture_output=True, text=True).stdout.strip()
        branch = subprocess.run(["git", "-C", str(repo_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        if not branch:
            branch = "detached-" + git_output(repo_root, "rev-parse", "--short", "HEAD").decode().strip()
        parent_dir = (docs_base() / directory_id(repo_root.name, origin or str(repo_root))
                      / directory_id(branch, branch))
        if work_item is not None:
            parent_dir /= directory_id(work_item, work_item)
    return parent_dir / f"run-{timestamp}-{uuid.uuid4().hex[:6]}"


def read_state(run_dir: Path) -> dict:
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    if state.get("version") not in (2, 3, 4, 5, 6, 7, 8):
        raise ValueError("unsupported workflow state version")
    for area in review_areas(state["scope"]):
        repo = repo_for_area(state, area)
        ensure_local_run_dir(run_dir, repo, allow_legacy=True)
        if state["version"] >= 7 and checkout_identity(repo) != state["checkout_ids"][area]:
            raise ValueError(f"Git checkout changed for {area}")
    if state["version"] >= 6 and state.get("review_depth") not in {"light", "full"}:
        raise ValueError("v6 run needs a review depth")
    return state


def write_state(run_dir: Path, state: dict) -> None:
    target = run_dir / "state.json"
    temporary = target.with_name(f".state-{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def design_digest(run_dir: Path, area: str) -> str:
    names = [f"{area}-design.md"] if area != "integration" else ["frontend-design.md", "backend-design.md"]
    digest = hashlib.sha256()
    for name in names:
        require_artifact(run_dir, name)
        data = (run_dir / name).read_bytes()
        digest.update(name.encode("utf-8") + b"\0" + data + b"\0")
    return digest.hexdigest()


def require_artifact(run_dir: Path, name: str) -> None:
    path = run_dir / name
    if path.is_symlink() or not within(path.resolve(), run_dir.resolve()):
        raise ValueError(f"artifact must be a local file within the run directory: {name}")
    if not path.is_file() or not path.read_bytes().strip():
        raise ValueError(f"missing or empty artifact: {name}")


def artifact_digest(run_dir: Path, name: str) -> str:
    require_artifact(run_dir, name)
    return hashlib.sha256((run_dir / name).read_bytes()).hexdigest()


def evidence_digest(run_dir: Path, raw_path: str) -> tuple[str, str]:
    """Return a local relative path and digest for an immutable evidence file."""
    path = lexical_absolute(Path(raw_path))
    root = run_dir.resolve()
    if not within(path, run_dir) or not within(path.resolve(), root):
        raise ValueError("evidence must be inside the local run directory")
    if any(part.is_symlink() for part in (path, *path.parents) if within(part, run_dir)):
        raise ValueError("evidence must not use a symbolic link")
    if not path.is_file() or not path.read_bytes().strip():
        raise ValueError("evidence must be a nonempty local file")
    return str(path.relative_to(run_dir)), hashlib.sha256(path.read_bytes()).hexdigest()


def result_binding(run_dir: Path, raw_path: str, stage: str, area: str, slot: int,
                   agent_id: str, focus: str, result: str) -> dict:
    from render_result import markdown, validate

    data, path, digest = validate(run_dir, raw_path)
    expected = {"stage": stage, "area": area, "slot": slot, "agent_id": agent_id,
                "focus": focus, "result": result}
    if any(data.get(key) != value for key, value in expected.items()):
        raise ValueError("JSON result conflicts with the recorded agent outcome")
    artifact = agent_artifact(stage, area, slot)
    require_artifact(run_dir, artifact)
    if (run_dir / artifact).read_text(encoding="utf-8") != markdown(data):
        raise ValueError("rendered Markdown differs from JSON result")
    return {"result_json": path, "result_json_digest": digest}


def model_selection(model: str | None, effort: str | None,
                    reason: str | None) -> dict[str, str]:
    if not model or not model.strip() or effort not in {"low", "medium", "high", "xhigh", "max", "ultra", "unavailable"} \
            or not reason or not reason.strip():
        raise ValueError("v5 agent result needs actual model, effort, and selection reason")
    return {"model": model.strip(), "effort": effort, "model_reason": reason.strip()}


def context_selection(mode: str | None, reason: str | None) -> dict[str, str]:
    if mode not in {"isolated", "full-fallback"}:
        raise ValueError("v5 agent result needs a context mode")
    if not reason or not reason.strip():
        raise ValueError("v5 context mode needs a reason")
    return {"context_mode": mode, "context_reason": reason.strip()}


def verify_result_binding(run_dir: Path, event: dict) -> None:
    if "result_json" not in event or "result_json_digest" not in event:
        raise ValueError("v5 agent result needs structured JSON")
    stage = "design-review" if event["type"] == "review" else event["type"]
    actual = result_binding(run_dir, str(run_dir / event["result_json"]), stage,
                            event["area"], event["slot"], event["agent_id"],
                            event["focus"], event["result"])
    if actual != {"result_json": event["result_json"],
                   "result_json_digest": event["result_json_digest"]}:
        raise ValueError("structured result changed after recording")


def manifest_binding(run_dir: Path, state: dict, area: str, name: str,
                     raw_path: str, status: str, *, expected_command: str | None = None,
                     expected_digest: str | None = None,
                     expected_design_digest: str | None = None,
                     expected_plan_digests: dict | None = None) -> dict:
    from evidence_summary import read_manifest

    manifest, path, digest = read_manifest(run_dir, raw_path)
    if expected_command is None:
        checks, _ = plans(run_dir, state)
        planned = plan_check(checks, area, name)
        if planned is None:
            raise ValueError("check is not in the verification plan")
        expected_command = planned["command"]
    if expected_digest is None:
        expected_digest = code_digest_for(run_dir, state, area)
    if manifest["command"] != expected_command:
        raise ValueError("check manifest command differs from verification plan")
    if manifest["status"] != status:
        raise ValueError("check status conflicts with runner manifest")
    if manifest.get("repo_root") != str(repo_for_area(state, area).resolve()) \
            or manifest.get("area") != area or manifest.get("code_digest") != expected_digest:
        raise ValueError("check manifest came from another repository, area, or code state")
    required_design_digest = (expected_design_digest if expected_design_digest is not None
                              else design_digest(run_dir, area))
    required_plan_digests = (expected_plan_digests if expected_plan_digests is not None
                              else plan_digests(run_dir, state))
    if state["version"] >= 7 and (manifest.get("schema_version") != 2
            or manifest.get("run_id") != state["run_id"]
            or manifest.get("checkout_id") != state["checkout_ids"][area]
            or manifest.get("design_digest") != required_design_digest
            or manifest.get("plan_digests") != required_plan_digests):
        raise ValueError("check manifest differs from the current run, design, or plan")
    return {"manifest_file": path, "manifest_digest": digest,
            "attempt_id": manifest["attempt_id"],
            "evidence_file": manifest["log_file"], "evidence_digest": manifest["log_sha256"]}


def verify_manifest_binding(run_dir: Path, state: dict, event: dict, *,
                            historical: bool = False) -> None:
    if any(key not in event for key in ("manifest_file", "manifest_digest", "attempt_id")):
        raise ValueError("v5 check event needs runner manifest")
    if not event.get("planned_command"):
        raise ValueError("v5 check event lacks its historical planned command")
    actual = manifest_binding(run_dir, state, event["area"], event["name"],
                              str(run_dir / event["manifest_file"]), event["status"],
                              expected_command=event["planned_command"],
                              expected_digest=event["digest"],
                              expected_design_digest=(event.get("design_digest")
                                                      if historical else None),
                              expected_plan_digests=(event.get("plans")
                                                     if historical else None))
    if any(event.get(key) != value for key, value in actual.items()):
        raise ValueError("check manifest or log changed after recording")


def acceptance_ids(run_dir: Path, area: str) -> set[str]:
    name = f"{area}-design.md"
    require_artifact(run_dir, name)
    content = (run_dir / name).read_text(encoding="utf-8")
    section = re.search(r"(?ms)^##\s+(?:수용 기준|Acceptance Criteria)\s*$\n(.*?)(?=^##\s+|\Z)", content)
    if not section:
        raise ValueError(f"design needs a ## 수용 기준 section: {area}")
    criterion_line = re.compile(r"^\s*(?:[-*]\s*)?((?:AC-[\w.-]+)|(?:R\d+))\s*[.:)\-]")
    lines = [line for line in section.group(1).splitlines() if line.strip()]
    found = [match.group(1) for line in lines if (match := criterion_line.match(line))]
    if len(found) != len(lines):
        raise ValueError(f"every acceptance criterion needs an ID: {area}")
    if not found or len(found) != len(set(found)):
        raise ValueError(f"design needs unique acceptance criterion IDs: {area}")
    return set(found)


def validate_official_sources(run_dir: Path) -> dict:
    require_artifact(run_dir, "official-sources.json")
    value = json.loads((run_dir / "official-sources.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("official source manifest needs schema_version 1")
    status = value.get("status")
    sources = value.get("sources")
    if (not isinstance(status, str)
            or status not in {"pending", "applicable", "not_applicable"}
            or not isinstance(sources, list)):
        raise ValueError("invalid official source manifest status or sources")
    if status == "pending":
        raise ValueError("official source applicability has not been resolved")
    if status == "not_applicable":
        if sources or not str(value.get("reason", "")).strip():
            raise ValueError("not_applicable needs an empty source list and a reason")
        return value
    if not sources:
        raise ValueError("applicable implementation needs at least one official source")
    source_ids = set()
    for source in sources:
        fields = ("id", "publisher", "title", "url", "version", "checked_on",
                  "detected_version", "version_source")
        if not isinstance(source, dict) or any(not isinstance(source.get(key), str)
                                               or not source[key].strip() for key in fields):
            raise ValueError("official source entry lacks detected version or version evidence")
        if not source["url"].startswith("https://") or source["id"] in source_ids:
            raise ValueError("official source URLs must use HTTPS and IDs must be unique")
        if not isinstance(source.get("claims"), list) or not source["claims"] \
                or any(not isinstance(item, str) or not item.strip() for item in source["claims"]):
            raise ValueError("official source claims must be nonempty strings")
        if not isinstance(source.get("applied_to"), list) or not source["applied_to"] \
                or any(not isinstance(item, str) or not item.strip() for item in source["applied_to"]):
            raise ValueError("official source entries need applied files or functions")
        source_ids.add(source["id"])
    return value


def validate_scope_register(run_dir: Path, state: dict) -> dict:
    require_artifact(run_dir, "scope-register.json")
    value = json.loads((run_dir / "scope-register.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1 \
            or not isinstance(value.get("questions"), list):
        raise ValueError("scope register needs schema_version 1 and questions")
    identifiers = set()
    for question in value["questions"]:
        required = ("id", "kind", "status", "question", "paths", "impact",
                    "blocks_requested_work", "run_id", "revision", "question_digest")
        if not isinstance(question, dict) or any(key not in question for key in required):
            raise ValueError("scope question is missing required fields")
        if not isinstance(question["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", question["id"]) \
                or question["id"] in identifiers:
            raise ValueError("scope question IDs must be unique stable identifiers")
        if question["kind"] not in {"scope_extension", "required_decision"} \
                or question["status"] not in {"pending", "approved", "declined", "unanswered"}:
            raise ValueError("invalid scope question kind or status")
        expected_blocking = question["kind"] == "required_decision"
        if type(question["blocks_requested_work"]) is not bool \
                or question["blocks_requested_work"] != expected_blocking:
            raise ValueError("scope_extension is nonblocking; required_decision is blocking")
        if question["run_id"] != state["run_id"] or type(question["revision"]) is not int \
                or question["revision"] < 1 or not all(isinstance(question[key], str)
                                                       and question[key].strip()
                                                       for key in ("question", "impact", "question_digest")):
            raise ValueError("scope question is not bound to this run or has invalid text/revision")
        if question["question_digest"] != scope_question_digest(state["run_id"], question):
            raise ValueError("scope question digest does not match its run, ID, text, or revision")
        if not isinstance(question["paths"], list) or any(not isinstance(item, str) or not item.strip()
                                                          for item in question["paths"]):
            raise ValueError("scope question paths must be strings")
        if not isinstance(question.get("dependent_paths", []), list) \
                or any(not isinstance(item, str) or not item.strip()
                       for item in question.get("dependent_paths", [])):
            raise ValueError("scope question dependent_paths must be strings")
        identifiers.add(question["id"])
    return value


def plans(run_dir: Path, state: dict) -> tuple[dict, dict]:
    for name in ("verification-plan.json", "qa-plan.json"):
        require_artifact(run_dir, name)
    if state["version"] >= 8:
        validate_official_sources(run_dir)
        scope_register(run_dir, state)
    checks = json.loads((run_dir / "verification-plan.json").read_text(encoding="utf-8"))
    cases = json.loads((run_dir / "qa-plan.json").read_text(encoding="utf-8"))
    impact_map(run_dir, state)
    allowed = set(review_areas(state["scope"]))
    if not isinstance(checks, dict) or not isinstance(cases, dict) or set(checks) != allowed or set(cases) != allowed:
        raise ValueError("plans must contain exactly the active areas")
    criteria = {area: acceptance_ids(run_dir, area) for area in areas_for(state["scope"])}
    expected_slots = set(required_slots(state))
    for area in allowed:
        entries = checks[area]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"verification plan is empty: {area}")
        ids = set()
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip() \
                    or entry["id"] in ids or entry.get("kind") not in {"test", "lint", "build", "other"} \
                    or not isinstance(entry.get("command"), str) or not entry["command"].strip() \
                    or type(entry.get("required")) is not bool:
                raise ValueError(f"invalid verification plan entry: {area}")
            if not entry["required"] and not str(entry.get("reason", "")).strip():
                raise ValueError(f"optional verification needs a reason: {area} {entry['id']}")
            ids.add(entry["id"])
        if not any(item["kind"] == "test" and item["required"] for item in entries):
            raise ValueError(f"area needs a required test: {area}")
        scenarios = cases[area]
        if not isinstance(scenarios, list) or not scenarios:
            raise ValueError(f"QA plan is empty: {area}")
        scenario_ids = set()
        covered = set()
        slots = set()
        for scenario in scenarios:
            if not isinstance(scenario, dict) or scenario.get("slot") not in expected_slots \
                    or not isinstance(scenario.get("scenario_id"), str) or not scenario["scenario_id"].strip() \
                    or scenario["scenario_id"] in scenario_ids \
                    or not isinstance(scenario.get("criterion_id"), str) \
                    or not str(scenario.get("steps", "")).strip() \
                    or not str(scenario.get("expected", "")).strip():
                raise ValueError(f"invalid QA plan entry: {area}")
            if state["version"] >= 6 and scenario.get("kind") not in QA_KINDS:
                raise ValueError(f"v6 QA scenario needs a kind: {area}")
            source_area = scenario.get("source_area", area)
            if source_area not in criteria or scenario["criterion_id"] not in criteria[source_area] \
                    or (area != "integration" and source_area != area):
                raise ValueError(f"QA scenario references an undeclared criterion: {area}")
            scenario_ids.add(scenario["scenario_id"])
            slots.add(scenario["slot"])
            if area != "integration":
                covered.add(scenario["criterion_id"])
        if slots != expected_slots:
            raise ValueError(f"QA plan needs slots {sorted(expected_slots)}: {area}")
        if state["version"] >= 6 and {item["kind"] for item in scenarios} != QA_KINDS:
            raise ValueError(f"v6 QA plan needs all five scenario kinds: {area}")
        if area != "integration" and covered != criteria[area]:
            raise ValueError(f"QA plan does not cover every criterion: {area}")
    return checks, cases


def plan_digests(run_dir: Path, state: dict | None = None) -> dict[str, str]:
    names = ["verification-plan.json", "qa-plan.json"]
    if (run_dir / "impact-map.json").exists() or (run_dir / "impact-map.json").is_symlink():
        names.append("impact-map.json")
    if state is not None and state.get("version", 0) >= 8:
        names.append("official-sources.json")
    digests = {name: artifact_digest(run_dir, name) for name in names}
    if state is not None and state.get("version", 0) >= 8:
        register = scope_register_data(state)
        approved = [{key: question.get(key) for key in
                     ("id", "kind", "question", "paths", "impact", "dependent_paths", "status")}
                    for question in register["questions"] if question["status"] == "approved"]
        digests["approved-scope"] = hashlib.sha256(json.dumps(
            approved, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return digests


def scope_question_digest(run_id: str, question: dict) -> str:
    payload = {key: question[key] for key in
               ("id", "kind", "question", "paths", "impact", "dependent_paths", "run_id", "revision")}
    payload["run_id"] = run_id
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def scope_register(run_dir: Path, state: dict) -> dict:
    value = scope_register_data(state)
    path = run_dir / "scope-register.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        current = None
    if current != value:
        write_json_atomic(path, value)
    return validate_scope_register(run_dir, state)


def scope_register_data(state: dict) -> dict:
    questions = {}
    for event in state["events"]:
        if event.get("type") in {"scope-question", "scope-answer"} \
                and isinstance(event.get("question_state"), dict):
            item = event["question_state"]
            questions[item["id"]] = json.loads(json.dumps(item))
    return {"schema_version": 1, "questions": list(questions.values())}


def unresolved_required_decisions(register: dict) -> list[dict]:
    return [question for question in register["questions"]
            if question["kind"] == "required_decision"
            and question["status"] in {"declined", "unanswered"}]


def has_current_incomplete_report(run_dir: Path, state: dict, register: dict) -> bool:
    report = run_dir / "final-report.md"
    scope_file = run_dir / "scope-register.json"
    if report.is_symlink() or scope_file.is_symlink() or not report.is_file() or not scope_file.is_file():
        return False
    report_digest = hashlib.sha256(report.read_bytes()).hexdigest()
    report_binding = dict(current_binding(run_dir, state))
    report_binding["questions"] = hashlib.sha256(scope_file.read_bytes()).hexdigest()
    return any(event.get("type") == "report-published"
               and event.get("run_id") == state["run_id"]
               and event.get("status") == "incomplete"
               and event.get("binding") == report_binding
               and event.get("report_digest") == report_digest
               for event in state["events"])


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def serialized_json(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def stage_transaction_file(run_dir: Path, name: str, content: bytes) -> dict:
    with tempfile.NamedTemporaryFile(mode="wb", dir=run_dir, prefix=f".{name}-", suffix=".tmp",
                                     delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    return {"temporary": temporary.name, "target": name,
            "digest": hashlib.sha256(content).hexdigest()}


def recover_file_transaction(run_dir: Path, journal_name: str, expected_targets: set[str],
                             label: str) -> None:
    journal_path = run_dir / journal_name
    if not journal_path.exists():
        return
    if journal_path.is_symlink() or not journal_path.is_file():
        raise ValueError(f"{label} recovery journal must be a local regular file")
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("schema_version") != 1 or not isinstance(journal.get("files"), list):
        raise ValueError(f"invalid {label} recovery journal")
    if {item.get("target") for item in journal["files"] if isinstance(item, dict)} != expected_targets:
        raise ValueError(f"{label} journal has an unexpected target")
    for item in journal["files"]:
        temporary_name = item.get("temporary")
        target_name = item.get("target")
        if not isinstance(temporary_name, str) or Path(temporary_name).name != temporary_name \
                or target_name not in expected_targets:
            raise ValueError(f"{label} journal path is invalid")
        if not temporary_name.startswith(f".{target_name}-") or not temporary_name.endswith(".tmp"):
            raise ValueError(f"{label} temporary path is invalid")
        temporary = run_dir / temporary_name
        target = run_dir / target_name
        if temporary.is_symlink() or target.is_symlink():
            raise ValueError(f"{label} target cannot be a symbolic link")
        if temporary.exists():
            digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if digest != item.get("digest"):
                raise ValueError(f"staged {label} file digest differs")
            os.replace(temporary, target)
        elif not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != item.get("digest"):
            raise ValueError(f"{label} cannot be recovered from staged files")
    journal_path.unlink()


def recover_report_publish(run_dir: Path) -> None:
    recover_file_transaction(run_dir, ".report-publish.pending.json",
                             {"final-report.md", "scope-register.json", "state.json"},
                             "report publication")


def recover_legacy_migration(run_dir: Path) -> None:
    journal_path = run_dir / ".legacy-migration.pending.json"
    if not journal_path.exists():
        return
    if journal_path.is_symlink() or not journal_path.is_file():
        raise ValueError("legacy migration recovery journal must be a local regular file")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid legacy migration recovery journal") from error
    targets = {"official-sources.json", "scope-register.json", "state.json"}
    snapshot = journal.get("snapshot_target") if isinstance(journal, dict) else None
    if not isinstance(snapshot, str) or not re.fullmatch(r"legacy-state-v[2-7]\.json", snapshot):
        raise ValueError("legacy migration snapshot target is invalid")
    recover_file_transaction(run_dir, ".legacy-migration.pending.json", targets | {snapshot},
                             "legacy migration")


def changed_code_paths(state: dict) -> list[str]:
    paths = set()
    area_repos = {area: repo_for_area(state, area).resolve() for area in areas_for(state["scope"])}
    for area, repo in area_repos.items():
        base = state.get("base_commits", {}).get(area)
        if base and subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", base],
                                   capture_output=True).returncode == 0:
            tracked = git_output(repo, "diff", "--name-only", "--no-renames", base, "-z", "--")
        else:
            tracked = b""
        untracked = git_output(repo, "ls-files", "--others", "--exclude-standard", "-z")
        for raw in (tracked + b"\0" + untracked).split(b"\0"):
            if raw:
                paths.add(f"{area}:{os.fsdecode(raw)}")
    if state["scope"] == "both":
        integration_repo = repo_for_area(state, "integration").resolve()
        if integration_repo not in area_repos.values():
            base = state.get("base_commits", {}).get("integration")
            if base and subprocess.run(["git", "-C", str(integration_repo), "rev-parse", "--verify", base],
                                       capture_output=True).returncode == 0:
                tracked = git_output(integration_repo, "diff", "--name-only", "--no-renames", base, "-z", "--")
            else:
                tracked = b""
            untracked = git_output(integration_repo, "ls-files", "--others", "--exclude-standard", "-z")
            for raw in (tracked + b"\0" + untracked).split(b"\0"):
                if raw:
                    paths.add(f"integration:{os.fsdecode(raw)}")
    return sorted(paths)


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def markdown_text(value: str) -> str:
    text = markdown_cell(str(value))
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|><])", r"\\\1", text)


def changed_code_lines(state: dict, qualified_path: str) -> set[tuple[str, int]]:
    area, relative = qualified_path.split(":", 1)
    repo = repo_for_area(state, area).resolve()
    untracked = {os.fsdecode(item) for item in
                 git_output(repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
                 if item}
    path = repo / relative
    if relative in untracked and path.is_file() and not path.is_symlink():
        return {("new", line) for line in
                range(1, len(path.read_text(encoding="utf-8").splitlines()) + 1)}
    base = state.get("base_commits", {}).get(area)
    if base and subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", base],
                               capture_output=True).returncode == 0:
        result = subprocess.run(["git", "-C", str(repo), "diff", "--unified=0",
                                 "--no-renames", base, "--", relative],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"cannot inspect changed code lines: {qualified_path}")
        changed = set()
        for match in re.finditer(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", result.stdout):
            old_start, old_count, new_start, new_count = match.groups()
            old_start, new_start = int(old_start), int(new_start)
            old_count = int(old_count) if old_count is not None else 1
            new_count = int(new_count) if new_count is not None else 1
            if new_count:
                changed.update(("new", line) for line in range(new_start, new_start + new_count))
            if old_count:
                changed.update(("old", line) for line in range(old_start, old_start + old_count))
        return changed
    if path.is_file() and not path.is_symlink():
        return {("new", line) for line in
                range(1, len(path.read_text(encoding="utf-8").splitlines()) + 1)}
    return set()


def validate_report_sections(content: str, changed: list[str],
                             changed_lines: dict[str, set[tuple[str, int]]] | None = None,
                             baseline_objects: dict[str, str] | None = None) -> None:
    headings = ("## 변경 파일 및 코드 위치", "## 동작 원리", "## 검증 결과",
                "## 요청 밖 문제 및 질문")
    lines = content.splitlines()
    sections = {}
    for index, line in enumerate(lines):
        if line in headings:
            if line in sections:
                raise ValueError(f"report section is duplicated: {line}")
            end = next((position for position in range(index + 1, len(lines))
                        if lines[position].startswith("## ")), len(lines))
            body = "\n".join(lines[index + 1:end]).strip()
            if not body:
                raise ValueError(f"report section is empty: {line}")
            sections[line] = body
    if set(sections) != set(headings):
        missing = [heading for heading in headings if heading not in sections]
        raise ValueError("report draft is missing required sections: " + ", ".join(missing))
    file_section = sections[headings[0]]
    code_extensions = {".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
                       ".jsx", ".kt", ".php", ".py", ".rb", ".rs", ".scala", ".sql", ".swift",
                       ".ts", ".tsx"}
    for path in changed:
        if path not in file_section:
            raise ValueError("report draft omits changed code paths: " + path)
        suffix = Path(path.split(":", 1)[-1]).suffix.casefold()
        citation_pattern = re.escape(path) + r":(new|old):(\d+)\b(?:\s+\(baseline:([0-9a-f]{40})\))?"
        if suffix in code_extensions and not re.search(citation_pattern, file_section):
            raise ValueError("report needs a new-side or old-side line location for changed code: " + path)
        if suffix in code_extensions and changed_lines is not None:
            area = path.split(":", 1)[0]
            expected_baseline = (baseline_objects or {}).get(area)
            found_location = False
            for match in re.finditer(citation_pattern, file_section):
                side, line_number, baseline = match.groups()
                if (side == "old" and baseline != expected_baseline) or (side == "new" and baseline is not None):
                    raise ValueError("report line location has an invalid baseline: " + path)
                if (side, int(line_number)) not in changed_lines.get(path, set()):
                    raise ValueError("report line location is not a changed code line: " + path)
                found_location = True
            if not found_location:
                raise ValueError("report needs a new-side or old-side line location for changed code: " + path)


def hook_path_conflicts(state: dict) -> list[tuple[str, str, list[str]]]:
    conflicts = []
    plugin_hooks = (Path(__file__).resolve().parent.parent / "git-hooks").resolve()
    for area in review_areas(state["scope"]):
        repo = repo_for_area(state, area)
        configured = subprocess.run(
            ["git", "-C", str(repo), "config", "--show-origin", "--show-scope",
             "--get-all", "core.hooksPath"], capture_output=True, text=True)
        effective = subprocess.run(["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
                                   capture_output=True, text=True)
        if effective.returncode == 0 and effective.stdout.strip():
            value = Path(effective.stdout.strip())
            path = (value if value.is_absolute() else repo / value).resolve()
            origins = [line.strip() for line in configured.stdout.splitlines() if line.strip()]
        else:
            default = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
                                     capture_output=True, text=True)
            if default.returncode != 0 or not default.stdout.strip():
                conflicts.append((area, "확인 불가", ["Git 기본 훅 경로를 조회하지 못함"]))
                continue
            value = Path(default.stdout.strip())
            path = (value if value.is_absolute() else repo / value).resolve()
            origins = ["Git 기본 경로 (`core.hooksPath` 미설정)"]
        if path != plugin_hooks:
            conflicts.append((area, str(path), origins))
    return conflicts


def publish_report(run_dir: Path, state: dict, draft_path: Path) -> tuple[str, str]:
    draft_abs = lexical_absolute(draft_path)
    if not within(draft_abs, run_dir) or not within(draft_abs.resolve(), run_dir.resolve()) \
            or draft_abs.is_symlink() or not draft_abs.is_file():
        raise ValueError("report draft must be a regular file inside the local run directory")
    content = draft_abs.read_text(encoding="utf-8")
    sources = validate_official_sources(run_dir) if state["version"] >= 8 else None
    register = scope_register_data(state) if state["version"] >= 8 else {"questions": []}
    candidate_state = json.loads(json.dumps(state))
    for question in register["questions"]:
        if question["status"] == "pending":
            question["status"] = "unanswered"
            question["revision"] += 1
            question["question_digest"] = scope_question_digest(candidate_state["run_id"], question)
            candidate_state["events"].append({"type": "scope-answer", "run_id": candidate_state["run_id"],
                                              "question_id": question["id"], "status": "unanswered",
                                              "revision": question["revision"],
                                              "question_digest": question["question_digest"],
                                              "question_state": json.loads(json.dumps(question)),
                                              "reference": "report publication closed the pending question"})
    register = scope_register_data(candidate_state) if state["version"] >= 8 else register
    changed = changed_code_paths(state)
    changed_lines = {path: changed_code_lines(state, path) for path in changed
                     if Path(path.split(":", 1)[-1]).suffix.casefold() in
                     {".c", ".cc", ".cpp", ".cs", ".go", ".h", ".hpp", ".java", ".js",
                      ".jsx", ".kt", ".php", ".py", ".rb", ".rs", ".scala", ".sql", ".swift",
                      ".ts", ".tsx"}}
    validate_report_sections(content, changed, changed_lines, state.get("base_commits", {}))
    hook_conflicts = hook_path_conflicts(state)
    status = "incomplete" if unresolved_required_decisions(register) else "complete"
    lines = [content.rstrip(), "", "## 실행 상태", "", f"- status: **{status}**", ""]
    if hook_conflicts:
        lines += ["## 로컬 문서 guard 훅 설정", "",
                  "현재 유효한 `core.hooksPath`가 플러그인 guard 경로와 달라 guard가 자동 실행되지 않을 수 있습니다. 설정과 기존 훅은 변경하지 않았습니다.", ""]
        for area, effective_path, origins in hook_conflicts:
            lines += [f"### {area}", "", f"- 유효 경로: `{effective_path}`"]
            lines += [f"- 설정 출처: `{origin}`" for origin in origins] or ["- 설정 출처: 확인할 수 없음"]
            lines.append("")
    if register["questions"]:
        lines += ["## 요청 밖 문제 및 질문 상태", "",
                  "| ID | 종류 | 질문 | 상태 | 경로 | 영향 |", "| --- | --- | --- | --- | --- | --- |"]
        for question in register["questions"]:
            row = (question["id"], question["kind"], question["question"], question["status"],
                   ", ".join(question["paths"]), question["impact"])
            lines.append("| " + " | ".join(markdown_cell(str(value)) for value in row) + " |")
        lines.append("")
    if sources and sources.get("status") == "applicable":
        lines += ["## 공식 문서 근거", ""]
        for source in sources["sources"]:
            source_id = markdown_text(source["id"])
            source_url = quote(source["url"], safe=":/?#@!$&'*+,;=%")
            title = markdown_text(source["title"])
            version = markdown_text(source["version"])
            detected_version = markdown_text(source["detected_version"])
            version_source = markdown_text(source["version_source"])
            checked_on = markdown_text(source["checked_on"])
            claims = "; ".join(markdown_text(item) for item in source["claims"])
            applied_to = "; ".join(markdown_text(item) for item in source["applied_to"])
            lines.append(f"- [{source_id}]({source_url}) — {title} "
                         f"(문서 버전 {version}, 감지 버전 {detected_version} "
                         f"— 근거: {version_source}, 확인 {checked_on}) — "
                         f"주장: {claims} — 적용 대상: {applied_to}")
    final_content = "\n".join(lines).rstrip() + "\n"
    binding = dict(current_binding(run_dir, candidate_state))
    if state["version"] >= 8:
        binding["questions"] = hashlib.sha256(serialized_json(register)).hexdigest()
    report_digest = hashlib.sha256(final_content.encode("utf-8")).hexdigest()
    candidate_state["events"].append({"type": "report-published", "run_id": state["run_id"],
                                      "status": status, "binding": binding,
                                      "report_digest": report_digest,
                                      "published_at": datetime.now(timezone.utc).isoformat()})
    staged = [stage_transaction_file(run_dir, "final-report.md", final_content.encode("utf-8")),
              stage_transaction_file(run_dir, "scope-register.json", serialized_json(register)),
              stage_transaction_file(run_dir, "state.json", serialized_json(candidate_state))]
    write_json_atomic(run_dir / ".report-publish.pending.json",
                      {"schema_version": 1, "files": staged})
    recover_report_publish(run_dir)
    return status, report_digest


def current_design_digests(run_dir: Path, state: dict) -> dict[str, str]:
    return {area: design_digest(run_dir, area) for area in areas_for(state["scope"])}


def require_design_gate(run_dir: Path, state: dict) -> None:
    checks, cases = plans(run_dir, state)
    del checks, cases
    expected_design = current_design_digests(run_dir, state)
    expected_plans = plan_digests(run_dir, state)
    if not any(event["type"] == "design-gate-passed" and event["digests"] == expected_design
               and event["plans"] == expected_plans for event in state["events"]):
        raise ValueError("current design and plans need a passed design gate")


def plan_check(checks: dict, area: str, name: str) -> dict:
    return next((item for item in checks[area] if item["id"] == name), None)


def plan_case(cases: dict, area: str, scenario_id: str) -> dict:
    return next((item for item in cases[area] if item["scenario_id"] == scenario_id), None)


def latest_events(state: dict, event_type: str, area: str, key: str, value: str,
                  digest: str, design: str) -> list[dict]:
    return [event for event in state["events"] if event["type"] == event_type
            and event["area"] == area and event[key] == value and event["digest"] == digest
            and event["design_digest"] == design]


def unresolved_attempt(state: dict, event_type: str, resolution_type: str, area: str,
                       key: str, value: str, digest: str, design: str, plan_hashes: dict) -> bool:
    events = state["events"]
    for index, event in enumerate(events):
        if event.get("type") != event_type or event.get("area") != area or event.get(key) != value \
                or event.get("digest") != digest or event.get("design_digest") != design \
                or event.get("plans") != plan_hashes \
                or event.get("status", event.get("result")) not in {"fail", "not-run"}:
            continue
        resolved = next((j for j in range(index + 1, len(events)) if events[j].get("type") == resolution_type
                         and events[j].get("area") == area and events[j].get(key) == value
                         and events[j].get("digest") == digest and events[j].get("design_digest") == design
                         and events[j].get("plans") == plan_hashes), None)
        if resolved is None or not any(later.get("type") == event_type and later.get("area") == area
                                       and later.get(key) == value and later.get("digest") == digest
                                       and later.get("design_digest") == design
                                       and later.get("plans") == plan_hashes
                                       and later.get("status", later.get("result")) == "pass"
                                       for later in events[resolved + 1:]):
            return True
    return False


def verify_all_evidence(run_dir: Path, state: dict) -> None:
    for event in state["events"]:
        if event.get("type") not in {"check", "qa-case"} or "evidence_file" not in event:
            continue
        _, digest = evidence_digest(run_dir, str(run_dir / event["evidence_file"]))
        if digest != event["evidence_digest"]:
            raise ValueError(f"evidence changed after recording: {event['evidence_file']}")
        if state["version"] >= 5 and event["type"] == "check":
            verify_manifest_binding(run_dir, state, event, historical=True)
    if state["version"] >= 5:
        for event in state["events"]:
            if event.get("type") not in {"review", "code-review", "qa"}:
                continue
            if "result_json" not in event or "result_json_digest" not in event:
                raise ValueError("v5 agent event lacks structured result")
            path, digest = evidence_digest(run_dir, str(run_dir / event["result_json"]))
            if path != event["result_json"] or digest != event["result_json_digest"]:
                raise ValueError("historical structured result changed after recording")


def agent_artifact(stage: str, area: str, slot: int) -> str:
    if stage == "design-review":
        stem = "integration-contract-review" if area == "integration" else f"{area}-design-review"
    elif stage == "code-review":
        stem = f"{area}-code-review"
    else:
        stem = f"{area}-qa"
    return f"{stem}-{slot}.md"


def git_output(repo_root: Path, *arguments: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo_root), *arguments],
                          check=True, capture_output=True).stdout


def code_digest(repo_root: Path) -> str:
    """Fingerprint HEAD, tracked changes, and nonignored untracked files."""
    digest = hashlib.sha256()
    head = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
                          capture_output=True)
    if head.returncode == 0:
        digest.update(head.stdout)
        digest.update(git_output(repo_root, "diff", "--no-ext-diff", "--binary", "HEAD", "--"))
        names = git_output(repo_root, "ls-files", "--others", "--exclude-standard", "-z")
    else:
        digest.update(b"unborn HEAD\0")
        names = git_output(repo_root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for raw_name in sorted(set(names.split(b"\0"))):
        if not raw_name:
            continue
        path = repo_root / os.fsdecode(raw_name)
        digest.update(raw_name + b"\0")
        if path.is_symlink():
            digest.update(b"link\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def code_digest_v7(repo_root: Path, visited: frozenset[Path] = frozenset()) -> str:
    repo_root = repo_root.resolve()
    if repo_root in visited:
        raise ValueError("cyclic Git submodule checkout")
    digest = hashlib.sha256()
    digest.update(code_digest(repo_root).encode())
    entries = git_output(repo_root, "ls-files", "--stage", "-z")
    for raw in sorted(set(entries.split(b"\0")) - {b""}):
        metadata, _, path = raw.partition(b"\t")
        if not metadata.startswith(b"160000 "):
            continue
        submodule = repo_root / os.fsdecode(path)
        if not submodule.is_dir() or not (submodule / ".git").exists():
            raise ValueError(f"Git submodule is not initialized: {os.fsdecode(path)}")
        digest.update(path + b"\0")
        digest.update(checkout_identity(submodule).encode())
        digest.update(code_digest_v7(submodule, visited | {repo_root}).encode())
    return digest.hexdigest()


def glob_regex(pattern: str) -> re.Pattern[str]:
    if not pattern or pattern.startswith("/") or "\\" in pattern or any(
            part in {"", ".", ".."} for part in pattern.split("/")) \
            or not re.fullmatch(r"[A-Za-z0-9_./*?@+-]+", pattern):
        raise ValueError(f"invalid impact glob: {pattern}")
    expression = ""
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            expression += "(?:[^/]+/)*"
            index += 3
        elif pattern[index:index + 2] == "**":
            expression += ".*"
            index += 2
        elif pattern[index] == "*":
            expression += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            expression += "[^/]"
            index += 1
        else:
            expression += re.escape(pattern[index])
            index += 1
    return re.compile("^" + expression + "$")


def impact_map(run_dir: Path, state: dict) -> dict | None:
    if state["version"] < 5 or state["scope"] != "both":
        return None
    path = run_dir / "impact-map.json"
    if not path.exists() and not path.is_symlink():
        return None
    require_artifact(run_dir, "impact-map.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) - {"frontend", "backend", "shared", "criteria_paths"}:
        raise ValueError("invalid impact map keys")
    seen = set()
    for owner in ("frontend", "backend", "shared"):
        entries = value.get(owner)
        if not isinstance(entries, list):
            raise ValueError(f"impact map needs {owner} patterns")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("glob"), str) \
                    or not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
                raise ValueError("impact patterns need glob and ownership reason")
            glob_regex(entry["glob"])
            if entry["glob"] in seen:
                raise ValueError("duplicate impact glob")
            seen.add(entry["glob"])
    references = value.get("criteria_paths", {})
    if not isinstance(references, dict):
        raise ValueError("invalid criterion path references")
    for paths in references.values():
        if not isinstance(paths, list):
            raise ValueError("criterion paths must be arrays")
        for item in paths:
            if not isinstance(item, str):
                raise ValueError("criterion path must be a glob")
            glob_regex(item)
    return value


def default_shared(path: str) -> bool:
    parts = path.split("/")
    return (parts[0] in {"deploy", "infra", ".github"} or
            any(part in SHARED_SEGMENTS for part in parts[:-1]) or
            (len(parts) == 1 and parts[0] in ROOT_SHARED_FILES))


def impact_owner(path: str, mapping: dict) -> str:
    if default_shared(path):
        return "shared"
    matched = {owner for owner in ("frontend", "backend", "shared")
               if any(glob_regex(entry["glob"]).fullmatch(path)
                      for entry in mapping[owner])}
    return next(iter(matched)) if len(matched) == 1 else "shared"


def code_digest_for(run_dir: Path, state: dict, area: str) -> str:
    if state.get("version", 0) >= 7:
        if area == "integration":
            payload = {item: {"repo": state["repo_roots"][item],
                              "checkout": state["checkout_ids"][item],
                              "digest": code_digest_v7(repo_for_area(state, item))}
                       for item in review_areas(state["scope"])}
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            return hashlib.sha256(encoded).hexdigest()
        return code_digest_v7(repo_for_area(state, area))
    repo_root = Path(state["repo_root"])
    mapping = impact_map(run_dir, state)
    if mapping is None:
        return code_digest(repo_root)
    head = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
                          capture_output=True)
    if head.returncode:
        return code_digest(repo_root)
    tracked = git_output(repo_root, "diff", "--name-only", "--no-renames", "HEAD", "-z", "--")
    untracked = git_output(repo_root, "ls-files", "--others", "--exclude-standard", "-z")
    names = sorted(set((tracked + untracked).split(b"\0")) - {b""})
    digest = hashlib.sha256(head.stdout)
    for raw in names:
        name = os.fsdecode(raw)
        owner = impact_owner(name, mapping)
        if owner not in {area, "shared"} and area != "integration":
            continue
        digest.update(raw + b"\0")
        if raw in tracked.split(b"\0"):
            digest.update(git_output(repo_root, "diff", "--no-ext-diff", "--binary",
                                     "--no-renames", "HEAD", "--", name))
        path = repo_root / name
        if path.is_symlink():
            digest.update(b"link\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def check_reviews_v2(run_dir: Path, state: dict) -> None:
    for area in areas_for(state["scope"]):
        require_artifact(run_dir, f"{area}-design.md")
        require_artifact(run_dir, f"{area}-design-review.md")
        digest = design_digest(run_dir, area)
        if state["review_mode"] == "user-review" and not any(
            event["type"] == "present" and event["area"] == area and event["digest"] == digest
            for event in state["events"]
        ):
            raise ValueError(f"current design has not been presented to the user: {area}")
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-contract-review.md")

    for area in review_areas(state["scope"]):
        events = [event for event in state["events"] if event.get("area") == area]
        reviews = [(index, event) for index, event in enumerate(events) if event["type"] == "review"]
        if not reviews:
            raise ValueError(f"missing design review event: {area}")
        for index, review in reviews:
            next_review = next((later for later in range(index + 1, len(events)) if events[later]["type"] == "review"), len(events))
            if review["result"] == "changes-required" and not any(
                event["type"] == "approval" for event in events[index + 1:next_review]
            ):
                raise ValueError(f"design changes await user approval: {area}")
        _, latest = reviews[-1]
        if latest["result"] != "clear" or latest["digest"] != design_digest(run_dir, area):
            raise ValueError(f"current design needs a clear review: {area}")


def latest_slot_results(state: dict, event_type: str, area: str, digest: str) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    for event in state["events"]:
        if event["type"] == event_type and event["area"] == area and event["digest"] == digest:
            latest[event["slot"]] = event
    expected = set(required_slots(state))
    if set(latest) != expected:
        raise ValueError(f"{len(expected)} {event_type} result(s) are required for {area}")
    if len({event["agent_id"] for event in latest.values()}) != len(expected):
        raise ValueError(f"{len(expected)} distinct subagent(s) are required for {area} {event_type}")
    return latest


def approved_finding(run_dir: Path, state: dict, area: str, index: int) -> bool:
    finding = state["events"][index]
    required_plans = None
    if state["version"] >= 7:
        try:
            current_digest = design_digest(run_dir, area)
        except (OSError, ValueError):
            current_digest = None
        required_plans = (plan_digests(run_dir, state) if finding["digest"] == current_digest
                          else finding.get("plans"))
    for approval in state["events"][index + 1:]:
        if approval.get("type") != "approval" or approval.get("area") != area \
                or approval.get("digest") != finding["digest"]:
            continue
        if state["version"] < 7:
            return True
        if approval.get("run_id") == state["run_id"] \
                and approval.get("plans") == required_plans \
                and index in approval.get("finding_indexes", []):
            return True
    return False


def check_reviews_v3(run_dir: Path, state: dict) -> None:
    for area in areas_for(state["scope"]):
        require_artifact(run_dir, f"{area}-design.md")
        require_artifact(run_dir, f"{area}-design-review.md")
        digest = design_digest(run_dir, area)
        if state["review_mode"] == "user-review" and not any(
            event["type"] == "present" and event["area"] == area and event["digest"] == digest
            for event in state["events"]
        ):
            raise ValueError(f"current design has not been presented to the user: {area}")
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-contract-review.md")

    for area in review_areas(state["scope"]):
        digest = design_digest(run_dir, area)
        latest = latest_slot_results(state, "review", area, digest)
        if any(event["result"] != "clear" for event in latest.values()):
            raise ValueError(f"current design needs {len(required_slots(state))} clear review(s): {area}")
        if any(event["type"] == "review" and event["area"] == area
               and event["digest"] == digest and event["result"] == "changes-required"
               for event in state["events"]):
            raise ValueError(f"design with findings must be revised and reviewed again: {area}")
        for slot, event in latest.items():
            if state["version"] >= 4 and event.get("focus") != focus_for("design-review", area, slot, review_depth(state)):
                raise ValueError(f"design review focus does not match slot: {area} {slot}")
            if state["version"] >= 4 and event.get("plans") != plan_digests(run_dir, state):
                raise ValueError(f"design review predates the current plans: {area} {slot}")
            name = agent_artifact("design-review", area, slot)
            if artifact_digest(run_dir, name) != event["artifact_digest"]:
                raise ValueError(f"design review artifact changed after recording: {name}")
            if state["version"] >= 5:
                verify_result_binding(run_dir, event)
                model_selection(event.get("model"), event.get("effort"), event.get("model_reason"))
                context_selection(event.get("context_mode"), event.get("context_reason"))
        for index, event in enumerate(state["events"]):
            if event["type"] != "review" or event["area"] != area or event["result"] != "changes-required":
                continue
            if not approved_finding(run_dir, state, area, index):
                raise ValueError(f"design findings await user approval: {area}")


def check_reviews(run_dir: Path, state: dict) -> None:
    (check_reviews_v3 if state["version"] >= 3 else check_reviews_v2)(run_dir, state)


def check_design(run_dir: Path, state: dict) -> None:
    risk_guard(state)
    check_reviews(run_dir, state)
    if state["version"] >= 4:
        plans(run_dir, state)
    if state["review_mode"] == "user-review":
        digests = {area: design_digest(run_dir, area) for area in areas_for(state["scope"])}
        if not any(event["type"] == "accept-design" and event["digests"] == digests
                   for event in state["events"]):
            raise ValueError("current design awaits the user's explicit acceptance")


def check_agent_stage(run_dir: Path, state: dict, stage: str, area: str, digest: str) -> None:
    latest = latest_slot_results(state, stage, area, digest)
    wanted = "clear" if stage == "code-review" else "pass"
    for slot, event in latest.items():
        if state["version"] >= 4 and event.get("focus") != focus_for(stage, area, slot, review_depth(state)):
            raise ValueError(f"{stage} focus does not match slot: {area} {slot}")
        if state["version"] >= 4 and event.get("plans") != plan_digests(run_dir, state):
            raise ValueError(f"{stage} predates the current plans: {area} {slot}")
        if state["version"] >= 4:
            event_index = next(index for index in range(len(state["events"]) - 1, -1, -1)
                               if state["events"][index] is event)
            dependent_type = "check" if stage == "code-review" else "qa-case"
            if any(later.get("type") == dependent_type and later.get("area") == area
                   and later.get("digest") == digest and later.get("plans") == event["plans"]
                   and (stage == "code-review" or later.get("slot") == slot)
                   for later in state["events"][event_index + 1:]):
                raise ValueError(f"{stage} must be rerun after newer evidence: {area} slot {slot}")
            if stage == "qa" and area != "integration" and any(
                    later.get("type") == "code-review" and later.get("area") == area
                    and later.get("digest") == digest and later.get("plans") == event["plans"]
                    for later in state["events"][event_index + 1:]):
                raise ValueError(f"QA must be rerun after code review: {area} slot {slot}")
        if event["result"] != wanted:
            raise ValueError(f"{stage} result is not {wanted}: {area} slot {slot}")
        if event["design_digest"] != design_digest(run_dir, area):
            raise ValueError(f"{stage} result predates current design: {area} slot {slot}")
        name = agent_artifact(stage, area, slot)
        if artifact_digest(run_dir, name) != event["artifact_digest"]:
            raise ValueError(f"{stage} artifact changed after recording: {name}")
        if state["version"] >= 5:
            verify_result_binding(run_dir, event)
            model_selection(event.get("model"), event.get("effort"), event.get("model_reason"))
            context_selection(event.get("context_mode"), event.get("context_reason"))
    for index, event in enumerate(state["events"]):
        if event["type"] != stage or event["area"] != area or event["digest"] != digest \
                or event["design_digest"] != design_digest(run_dir, area):
            continue
        if event["result"] not in {"findings", "fail", "not-run"}:
            continue
        resolved = next((later for later in range(index + 1, len(state["events"]))
                         if state["events"][later]["type"] == "agent-resolution"
                         and state["events"][later]["stage"] == stage
                         and state["events"][later]["area"] == area
                         and state["events"][later]["slot"] == event["slot"]
                         and state["events"][later]["digest"] == digest
                         and state["events"][later]["design_digest"] == event["design_digest"]), None)
        if resolved is None or not any(
            later["type"] == stage and later["area"] == area and later["slot"] == event["slot"]
            and later["digest"] == digest and later["design_digest"] == event["design_digest"]
            and later["result"] == wanted
            for later in state["events"][resolved + 1:]
        ):
            raise ValueError(f"{stage} finding needs resolution and rerun: {area} slot {event['slot']}")


def check_v4_checks(run_dir: Path, state: dict, area: str) -> None:
    checks, _ = plans(run_dir, state)
    current_code = code_digest_for(run_dir, state, area)
    current_design = design_digest(run_dir, area)
    current_plans = plan_digests(run_dir, state)
    for item in checks[area]:
        records = latest_events(state, "check", area, "name", item["id"], current_code, current_design)
        records = [record for record in records if record.get("plans") == current_plans]
        if not records:
            raise ValueError(f"missing verification: {area} {item['id']}")
        latest = records[-1]
        if state["version"] >= 5 and latest["status"] in {"pass", "fail"}:
            verify_manifest_binding(run_dir, state, latest)
        if item["required"] and latest["status"] != "pass":
            raise ValueError(f"required verification is not passed: {area} {item['id']}")
        if latest["status"] == "fail":
            raise ValueError(f"unresolved failed verification: {area} {item['id']}")
        if latest["status"] == "not-run" and (item["required"] or not latest.get("reason")
                                                 or not latest.get("unverified")):
            raise ValueError(f"unverified check lacks an optional rationale: {area} {item['id']}")
        if (latest["status"] == "pass" or any(record["status"] == "fail" for record in records)) \
                and unresolved_attempt(
                state, "check", "check-resolution", area, "name", item["id"], current_code, current_design,
                current_plans):
            raise ValueError(f"verification failure needs resolution and rerun: {area} {item['id']}")


def check_v4_cases(run_dir: Path, state: dict, area: str, slot: int | None = None,
                   agent_id: str | None = None) -> None:
    _, cases = plans(run_dir, state)
    current_code = code_digest_for(run_dir, state, area)
    current_design = design_digest(run_dir, area)
    current_plans = plan_digests(run_dir, state)
    for scenario in cases[area]:
        if slot is not None and scenario["slot"] != slot:
            continue
        scenario_id = scenario["scenario_id"]
        records = latest_events(state, "qa-case", area, "scenario_id", scenario_id,
                                current_code, current_design)
        records = [record for record in records if record.get("plans") == current_plans]
        if not records or records[-1]["result"] != "pass":
            raise ValueError(f"QA scenario is not passed: {area} {scenario_id}")
        if records[-1]["slot"] != scenario["slot"] or (agent_id and records[-1]["agent_id"] != agent_id):
            raise ValueError(f"QA scenario owner does not match: {area} {scenario_id}")
        event_index = next(index for index in range(len(state["events"]) - 1, -1, -1)
                           if state["events"][index] is records[-1])
        if area != "integration" and any(
                later.get("type") == "code-review" and later.get("area") == area
                and later.get("digest") == current_code and later.get("plans") == current_plans
                for later in state["events"][event_index + 1:]):
            raise ValueError(f"QA scenario predates code review: {area} {scenario_id}")
        if area == "integration" and any(
                later.get("type") in {"qa", "qa-case"} and later.get("area") in areas_for(state["scope"])
                and later.get("digest") == code_digest_for(run_dir, state, later["area"])
                and later.get("plans") == current_plans
                for later in state["events"][event_index + 1:]):
            raise ValueError(f"integration QA scenario predates area QA: {scenario_id}")
        if unresolved_attempt(state, "qa-case", "qa-resolution", area, "scenario_id",
                              scenario_id, current_code, current_design, current_plans):
            raise ValueError(f"QA scenario failure needs resolution and rerun: {area} {scenario_id}")


def check_final_v4(run_dir: Path, state: dict) -> None:
    check_design(run_dir, state)
    require_design_gate(run_dir, state)
    for area in areas_for(state["scope"]):
        for name in ARTIFACTS[area][2:]:
            require_artifact(run_dir, name)
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-qa.md")
    require_artifact(run_dir, "final-report.md")
    for area in review_areas(state["scope"]):
        current_code = code_digest_for(run_dir, state, area)
        check_v4_checks(run_dir, state, area)
        if area != "integration":
            check_agent_stage(run_dir, state, "code-review", area, code_digest_for(run_dir, state, area))
        check_v4_cases(run_dir, state, area)
        check_agent_stage(run_dir, state, "qa", area, code_digest_for(run_dir, state, area))
    verify_all_evidence(run_dir, state)
    if state["version"] >= 8:
        register = scope_register(run_dir, state)
        if any(question["status"] == "pending" for question in register["questions"]):
            raise ValueError("pending scope questions must be closed in the final report")
        if unresolved_required_decisions(register):
            ids = ", ".join(question["id"] for question in unresolved_required_decisions(register))
            raise ValueError("unresolved required decision prevents completion: " + ids)
        current_report = run_dir / "final-report.md"
        report_digest = hashlib.sha256(current_report.read_bytes()).hexdigest()
        report_binding = dict(current_binding(run_dir, state))
        report_binding["questions"] = hashlib.sha256(
            (run_dir / "scope-register.json").read_bytes()).hexdigest()
        if not any(event.get("type") == "report-published"
                   and event.get("run_id") == state["run_id"]
                   and event.get("status") == "complete"
                   and event.get("binding") == report_binding
                   and event.get("report_digest") == report_digest
                   for event in state["events"]):
            raise ValueError("current final report must be atomically published for current evidence")


def check_final(run_dir: Path, state: dict) -> None:
    risk_guard(state)
    if state["version"] >= 4:
        check_final_v4(run_dir, state)
        return
    check_design(run_dir, state)
    for area in areas_for(state["scope"]):
        for name in ARTIFACTS[area][2:]:
            require_artifact(run_dir, name)
    if state["scope"] == "both":
        require_artifact(run_dir, "integration-qa.md")
    require_artifact(run_dir, "final-report.md")

    current_code = code_digest(Path(state["repo_root"])) if state["version"] == 3 else None
    if current_code is not None:
        for area in areas_for(state["scope"]):
            check_agent_stage(run_dir, state, "code-review", area, code_digest_for(run_dir, state, area))
            check_agent_stage(run_dir, state, "qa", area, code_digest_for(run_dir, state, area))
        if state["scope"] == "both":
            check_agent_stage(run_dir, state, "qa", "integration", current_code)

    for area in review_areas(state["scope"]):
        checks = [event for event in state["events"] if event["type"] == "check" and event["area"] == area]
        if not checks:
            raise ValueError(f"missing verification record: {area}")
        latest = {event["name"]: event for event in checks}
        failed = [name for name, event in latest.items() if event["status"] == "fail"]
        if failed:
            raise ValueError(f"unresolved failed checks for {area}: {', '.join(failed)}")
        if current_code is not None and any(event["digest"] != current_code for event in latest.values()):
            raise ValueError(f"verification record predates current code: {area}")
        if current_code is not None and any(event["design_digest"] != design_digest(run_dir, area)
                                            for event in latest.values()):
            raise ValueError(f"verification record predates current design: {area}")


def current_binding(run_dir: Path, state: dict) -> dict:
    return {"design": current_design_digests(run_dir, state),
            "plans": plan_digests(run_dir, state),
            "code": {area: code_digest_for(run_dir, state, area)
                     for area in review_areas(state["scope"])}}


def completed_run(run_dir: Path, state: dict) -> bool:
    if state.get("version", 0) < 7:
        return False
    try:
        binding = current_binding(run_dir, state)
        if not any(event.get("type") == "final-gate-passed"
                   and event.get("binding") == binding for event in state["events"]):
            return False
        check_final(run_dir, state)
        return True
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def legacy_run_complete(run_dir: Path, state: dict) -> bool:
    if state.get("version", 0) >= 7:
        return completed_run(run_dir, state)
    try:
        check_final(run_dir, state)
        return True
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def parse_legacy_bases(values: list[str]) -> dict[str, str]:
    parsed = {}
    for value in values:
        area, separator, reference = value.partition("=")
        if not separator or area not in {"frontend", "backend", "integration"} \
                or not reference.strip() or area in parsed:
            raise ValueError("--legacy-base must be a unique AREA=REF value")
        parsed[area] = reference.strip()
    return parsed


def migrate_run_to_v8(run_dir: Path, state: dict,
                      legacy_bases: dict[str, str] | None = None) -> dict:
    if (run_dir / ".legacy-migration.pending.json").exists():
        recover_legacy_migration(run_dir)
        return read_state(run_dir)
    if state.get("version", 0) >= 8:
        return state
    if legacy_run_complete(run_dir, state):
        raise ValueError("completed legacy run is historical; use --new-run for new work")
    original = json.loads(json.dumps(state))
    original_version = state["version"]
    snapshot_name = f"legacy-state-v{original_version}.json"
    snapshot = run_dir / snapshot_name
    snapshot_content = serialized_json(original)
    if snapshot.exists():
        saved = json.loads(snapshot.read_text(encoding="utf-8"))
        if saved != original:
            raise ValueError("legacy migration snapshot conflicts with the current state")
    roots = state.get("repo_roots") or {
        area: str(Path(state["repo_root"]).resolve()) for area in review_areas(state["scope"])
    }
    checkouts = state.get("checkout_ids") or {
        area: checkout_identity(Path(roots[area])) for area in review_areas(state["scope"])
    }
    base_commits = state.get("base_commits", {})
    missing_baselines = [area for area in review_areas(state["scope"])
                         if not base_commits.get(area)
                         or subprocess.run(["git", "-C", roots[area], "rev-parse", "--verify",
                                            base_commits[area]], capture_output=True).returncode != 0]
    legacy_bases = legacy_bases or {}
    if set(legacy_bases) != set(missing_baselines):
        if missing_baselines:
            raise ValueError("legacy migration needs a trustworthy start commit; pass "
                             "--legacy-base AREA=REF for: " + ", ".join(missing_baselines))
        if legacy_bases:
            raise ValueError("--legacy-base was supplied but the legacy run already has valid baselines")
    for area in missing_baselines:
        repo = roots[area]
        resolved = subprocess.run(["git", "-C", repo, "rev-parse", "--verify",
                                   f"{legacy_bases[area]}^{{commit}}"],
                                  capture_output=True, text=True)
        if resolved.returncode != 0:
            raise ValueError(f"legacy baseline is not a commit in the {area} repository")
        head = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "HEAD"],
                              capture_output=True, text=True)
        if head.returncode == 0 and subprocess.run(
                ["git", "-C", repo, "merge-base", "--is-ancestor", resolved.stdout.strip(), "HEAD"],
                capture_output=True).returncode != 0:
            raise ValueError(f"legacy baseline is not an ancestor of current {area} HEAD")
        base_commits[area] = resolved.stdout.strip()
    state.update({"version": 8, "repo_roots": roots, "checkout_ids": checkouts,
                  "base_commits": base_commits, "review_depth": state.get("review_depth", "full"),
                  "risk_reason": state.get("risk_reason", ""), "policy_version": 1,
                  "policy_migration": {"from_version": original_version,
                                       "at": datetime.now(timezone.utc).isoformat(),
                                       "snapshot": snapshot_name,
                                       "snapshot_digest": hashlib.sha256(snapshot_content).hexdigest()}})
    state["initial_code_digests"] = {
        area: code_digest_for(run_dir, state, area) for area in areas_for(state["scope"])
    }
    staged = [
        stage_transaction_file(run_dir, "official-sources.json",
                               serialized_json({"schema_version": 1, "status": "pending", "sources": []})),
        stage_transaction_file(run_dir, "scope-register.json",
                               serialized_json({"schema_version": 1, "questions": []})),
        stage_transaction_file(run_dir, snapshot_name, snapshot_content),
        stage_transaction_file(run_dir, "state.json", serialized_json(state)),
    ]
    write_json_atomic(run_dir / ".legacy-migration.pending.json",
                      {"schema_version": 1, "snapshot_target": snapshot_name, "files": staged})
    recover_legacy_migration(run_dir)
    return read_state(run_dir)


def workflow_status(run_dir: Path, state: dict) -> dict:
    if (state.get("version", 0) < 7 and legacy_run_complete(run_dir, state)) or completed_run(run_dir, state):
        return {"stage": "complete", "next_action": "none", "blocker": None, "complete": True}
    for area in review_areas(state["scope"]):
        findings = [(index, event) for index, event in enumerate(state["events"])
                    if event.get("type") == "review" and event.get("area") == area
                    and event.get("result") == "changes-required"]
        for index, finding in findings:
            if not approved_finding(run_dir, state, area, index):
                return {"stage": "design-review", "next_action": "wait-user",
                        "blocker": f"design findings await user approval: {area}", "complete": False}
    try:
        check_design(run_dir, state)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        message = str(error)
        if state.get("version", 0) >= 8 and "official source" in message:
            return {"stage": "design-input", "next_action": "prepare-official-sources",
                    "blocker": message, "complete": False}
        waiting = "user" in message or "approval" in message
        return {"stage": "design-review", "next_action": "wait-user" if waiting else "review-design",
                "blocker": message, "complete": False}
    try:
        require_design_gate(run_dir, state)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        return {"stage": "design-gate", "next_action": "check-design",
                "blocker": str(error), "complete": False}
    if state["version"] >= 7:
        if not any(event.get("type") == "check" for event in state["events"]) and all(
                code_digest_for(run_dir, state, area) == state["initial_code_digests"][area]
                for area in areas_for(state["scope"])):
            return {"stage": "implementation", "next_action": "implement",
                    "blocker": None, "complete": False}
    for area in review_areas(state["scope"]):
        try:
            check_v4_checks(run_dir, state, area)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            return {"stage": "verification", "next_action": "run-checks",
                    "blocker": str(error), "complete": False}
    for area in areas_for(state["scope"]):
        try:
            check_agent_stage(run_dir, state, "code-review", area,
                              code_digest_for(run_dir, state, area))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            return {"stage": "code-review", "next_action": "review-code",
                    "blocker": str(error), "complete": False}
    for area in review_areas(state["scope"]):
        try:
            check_v4_cases(run_dir, state, area)
            check_agent_stage(run_dir, state, "qa", area, code_digest_for(run_dir, state, area))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            return {"stage": "qa", "next_action": "run-qa",
                    "blocker": str(error), "complete": False}
    if state.get("version", 0) >= 8:
        try:
            register = scope_register(run_dir, state)
            unresolved = unresolved_required_decisions(register)
            current_questions = hashlib.sha256((run_dir / "scope-register.json").read_bytes()).hexdigest()
            report_binding = dict(current_binding(run_dir, state))
            report_binding["questions"] = current_questions
            if unresolved and any(event.get("type") == "report-published"
                                  and event.get("status") == "incomplete"
                                  and event.get("binding") == report_binding
                                  and event.get("report_digest") == hashlib.sha256(
                                      (run_dir / "final-report.md").read_bytes()).hexdigest()
                                  for event in state["events"]):
                return {"stage": "incomplete", "next_action": "resolve-required-decision",
                        "blocker": "required decisions remain declined or unanswered",
                        "complete": False}
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            pass
    try:
        check_final(run_dir, state)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        return {"stage": "report", "next_action": "write-report",
                "blocker": str(error), "complete": False}
    return {"stage": "final-gate", "next_action": "check-final",
            "blocker": None, "complete": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    initialize.add_argument("--repo", type=Path)
    initialize.add_argument("--frontend-repo", type=Path)
    initialize.add_argument("--backend-repo", type=Path)
    initialize.add_argument("--integration-repo", type=Path)
    initialize.add_argument("--new-run", action="store_true")
    initialize.add_argument("--scope", choices=("frontend", "backend", "both"))
    initialize.add_argument("--review-mode", choices=("immediate", "user-review"))
    initialize.add_argument("--mode-reference",
                            help="Reference to the user's answer for this implementation request")
    initialize.add_argument("--review-depth", choices=("light", "full"))
    initialize.add_argument("--risk-reason", default="")
    initialize.add_argument("--run-dir", type=Path)
    initialize.add_argument("--parent-dir", type=Path,
                            help="Existing branch or work folder under ~/Documents/docs")
    initialize.add_argument("--work-item", help="Stable name for the current work item")
    initialize.add_argument("--resume-run", type=Path, help="Resume one exact existing local run")
    initialize.add_argument("--legacy-base", action="append", default=[], metavar="AREA=REF",
                            help="Trusted start commit for a legacy run missing its baseline; repeat per area")
    for command in ("present", "review", "approve", "accept-design", "agent-result", "resolve-agent",
                    "check-record", "resolve-check", "qa-case", "resolve-qa-case",
                    "scope-question", "scope-answer", "report-publish", "check", "status"):
        sub = commands.add_parser(command)
        sub.add_argument("--run-dir", type=Path, required=True)
        if command in {"present", "review", "approve", "agent-result", "resolve-agent", "check-record",
                       "resolve-check", "qa-case", "resolve-qa-case"}:
            sub.add_argument("--area", choices=("frontend", "backend", "integration"), required=True)
        if command == "review":
            sub.add_argument("--result", choices=("clear", "changes-required"), required=True)
            sub.add_argument("--finding", default="")
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS)
            sub.add_argument("--agent-id")
            sub.add_argument("--focus")
            sub.add_argument("--result-json")
            sub.add_argument("--model")
            sub.add_argument("--effort")
            sub.add_argument("--model-reason")
            sub.add_argument("--context-mode", choices=("isolated", "full-fallback"))
            sub.add_argument("--context-reason")
        elif command in {"approve", "accept-design"}:
            sub.add_argument("--reference", required=True, help="Reference to the user's actual approval")
        elif command == "agent-result":
            sub.add_argument("--stage", choices=("code-review", "qa"), required=True)
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS, required=True)
            sub.add_argument("--agent-id", required=True)
            sub.add_argument("--result", choices=("clear", "findings", "pass", "fail", "not-run"), required=True)
            sub.add_argument("--focus")
            sub.add_argument("--result-json")
            sub.add_argument("--model")
            sub.add_argument("--effort")
            sub.add_argument("--model-reason")
            sub.add_argument("--context-mode", choices=("isolated", "full-fallback"))
            sub.add_argument("--context-reason")
        elif command == "resolve-agent":
            sub.add_argument("--stage", choices=("code-review", "qa"), required=True)
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS, required=True)
            sub.add_argument("--reference", required=True, help="Evidence that the finding was resolved")
        elif command == "check-record":
            sub.add_argument("--name", required=True)
            sub.add_argument("--status", choices=("pass", "fail", "not-run"), required=True)
            sub.add_argument("--evidence")
            sub.add_argument("--actual")
            sub.add_argument("--evidence-file")
            sub.add_argument("--manifest-file")
            sub.add_argument("--reason")
            sub.add_argument("--unverified")
        elif command == "resolve-check":
            sub.add_argument("--name", required=True)
            sub.add_argument("--reference", required=True)
        elif command == "qa-case":
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS, required=True)
            sub.add_argument("--agent-id", required=True)
            sub.add_argument("--scenario-id", required=True)
            sub.add_argument("--result", choices=("pass", "fail", "not-run"), required=True)
            sub.add_argument("--environment", required=True)
            sub.add_argument("--input", required=True)
            sub.add_argument("--actual", required=True)
            sub.add_argument("--evidence-file")
            sub.add_argument("--reason")
        elif command == "resolve-qa-case":
            sub.add_argument("--slot", type=int, choices=REVIEW_SLOTS, required=True)
            sub.add_argument("--scenario-id", required=True)
            sub.add_argument("--reference", required=True)
        elif command == "check":
            sub.add_argument("--gate", choices=("design", "final"), required=True)
        elif command == "scope-question":
            sub.add_argument("--question-id", required=True)
            sub.add_argument("--kind", choices=("scope_extension", "required_decision"), required=True)
            sub.add_argument("--question", required=True)
            sub.add_argument("--path", action="append", default=[])
            sub.add_argument("--dependent-path", action="append", default=[])
            sub.add_argument("--impact", required=True)
        elif command == "scope-answer":
            sub.add_argument("--question-id", required=True)
            sub.add_argument("--run-id", required=True)
            sub.add_argument("--revision", type=int, required=True)
            sub.add_argument("--question-digest", required=True)
            sub.add_argument("--status", choices=("approved", "declined"), required=True)
            sub.add_argument("--answer", default="")
            sub.add_argument("--reference", required=True)
        elif command == "report-publish":
            sub.add_argument("--draft-file", type=Path, required=True)
    args = parser.parse_args()

    lock_handle = None
    try:
        if args.command == "init":
            legacy_bases = parse_legacy_bases(args.legacy_base)
            if legacy_bases and (not args.resume_run or args.new_run):
                raise ValueError("--legacy-base requires exact --resume-run without --new-run")
            if args.resume_run and (args.work_item or args.run_dir or args.parent_dir or args.new_run):
                raise ValueError("--resume-run cannot be combined with run creation or work-item selection")
            if args.resume_run:
                run_dir = lexical_absolute(args.resume_run)
                state = read_state(run_dir)
                ensure_local_run_dir(run_dir, repo_for_area(state, areas_for(state["scope"])[0]), allow_legacy=True)
                lock_handle = (run_dir / ".workflow-state.lock").open("a+b")
                fcntl.flock(lock_handle, fcntl.LOCK_EX)
                recover_report_publish(run_dir)
                recover_legacy_migration(run_dir)
                state = read_state(run_dir)
                if args.scope and args.scope != state["scope"]:
                    raise ValueError("--scope conflicts with the selected run")
                if args.review_mode and args.review_mode != state["review_mode"]:
                    raise ValueError("--review-mode conflicts with the selected run")
                if args.review_depth and args.review_depth != state.get("review_depth", "full"):
                    raise ValueError("--review-depth conflicts with the selected run")
                requested_repos = {area: path.resolve() for area, path in {
                    "frontend": args.frontend_repo, "backend": args.backend_repo}.items() if path}
                if args.repo:
                    requested_repos.update({area: args.repo.resolve() for area in areas_for(state["scope"])})
                roots = state.get("repo_roots") or {
                    area: state["repo_root"] for area in review_areas(state["scope"])}
                if any(str(roots.get(area)) != str(path) for area, path in requested_repos.items()):
                    raise ValueError("repository path conflicts with the selected run")
                if legacy_run_complete(run_dir, state):
                    if legacy_bases:
                        raise ValueError("--legacy-base cannot be used with a completed historical run")
                    print(json.dumps({"run_dir": str(run_dir), "status": "complete-history"}))
                    return 0
                if state["version"] < 8:
                    state = migrate_run_to_v8(run_dir, state, legacy_bases)
                elif legacy_bases:
                    raise ValueError("--legacy-base can only be used when migrating a legacy run")
                print(run_dir)
                return 0

            if args.run_dir and args.parent_dir:
                raise ValueError("--run-dir and --parent-dir cannot be used together")
            if args.work_item is not None and args.run_dir:
                raise ValueError("--work-item cannot be combined with --run-dir")
            if args.work_item is not None and (not args.work_item.strip() or args.work_item in {".", ".."}
                    or any(separator in args.work_item for separator in ("/", "\\"))):
                raise ValueError("--work-item must be a single nonempty folder name")
            if not args.scope:
                raise ValueError("a new run or work-item resume needs --scope")
            review_depth_value = args.review_depth or "full"
            if args.scope == "both" and review_depth_value == "light":
                raise ValueError("light review needs one area and a risk reason")
            if args.scope == "both":
                if args.repo and not args.frontend_repo and not args.backend_repo:
                    selected = {"frontend": args.repo, "backend": args.repo}
                elif not args.frontend_repo or not args.backend_repo:
                    raise ValueError("both scope requires --frontend-repo and --backend-repo")
                else:
                    selected = {"frontend": args.frontend_repo, "backend": args.backend_repo}
            else:
                area_path = args.frontend_repo if args.scope == "frontend" else args.backend_repo
                area_path = area_path or args.repo
                if area_path is None:
                    raise ValueError("single-area scope requires --repo or its area repository")
                selected = {args.scope: area_path}
            selected = {area: path.resolve() for area, path in selected.items()}
            selected["integration"] = (args.integration_repo or selected.get("backend")
                                       or selected.get("frontend")).resolve()
            repo_root = selected.get("backend") or selected["frontend"]
            if args.parent_dir and not args.parent_dir.is_dir():
                raise ValueError("--parent-dir must be an existing folder")
            run_dir = lexical_absolute(args.run_dir or default_run_dir(repo_root, args.work_item, args.parent_dir))
            for area in review_areas(args.scope):
                ensure_local_run_dir(run_dir, selected[area])
            checkout_ids = {area: checkout_identity(selected[area]) for area in review_areas(args.scope)}
            parent = run_dir.parent
            with file_lock(parent / ".workflow-init.lock"):
                if args.work_item and not args.new_run:
                    work_item_states = []
                    candidates = []
                    for state_file in parent.glob("run-*/state.json"):
                        try:
                            summary = json.loads(state_file.read_text(encoding="utf-8"))
                            if summary.get("work_item") != args.work_item:
                                continue
                            previous = read_state(state_file.parent)
                        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
                            raise ValueError(f"matching work item cannot be resumed: {error}") from error
                        work_item_states.append((state_file.parent, previous))
                        roots = previous.get("repo_roots") or {
                            area: previous["repo_root"] for area in review_areas(previous["scope"])}
                        requested_roots_match = all(str(roots.get(area)) == str(path)
                                                    for area, path in selected.items()
                                                    if area in review_areas(previous["scope"]))
                        matches = (previous["scope"] == args.scope and requested_roots_match
                                   and all(previous.get("checkout_ids", {}).get(area, checkout_ids.get(area))
                                           == checkout_ids.get(area)
                                           for area in review_areas(previous["scope"])))
                        if matches and not legacy_run_complete(state_file.parent, previous):
                            if args.review_mode and args.review_mode != previous["review_mode"]:
                                raise ValueError("explicit --review-mode conflicts with the saved run")
                            if args.review_depth and args.review_depth != previous.get("review_depth", "full"):
                                raise ValueError("explicit --review-depth conflicts with the saved run")
                            candidates.append((state_file.parent, previous))
                    if len(candidates) > 1:
                        choices = ", ".join(f"{item['run_id']}={path}" for path, item in candidates)
                        raise ValueError("multiple unfinished runs match; select an exact --resume-run path: " + choices)
                    if len(candidates) == 1:
                        selected_run, previous = candidates[0]
                        with file_lock(selected_run / ".workflow-state.lock"):
                            recover_report_publish(selected_run)
                            recover_legacy_migration(selected_run)
                            current = read_state(selected_run)
                            if current["version"] < 8:
                                migrate_run_to_v8(selected_run, current, legacy_bases)
                            elif legacy_bases:
                                raise ValueError("--legacy-base can only be used when migrating a legacy run")
                        print(selected_run)
                        return 0
                    if work_item_states:
                        raise ValueError("no incomplete run matches; use --new-run for new work or --resume-run <run-dir> for existing work")
                    if legacy_bases:
                        raise ValueError("--legacy-base requires an existing legacy run")
                if not args.review_mode or args.mode_reference is None:
                    parser.error("a new run needs --review-mode and this request's --mode-reference")
                if not args.mode_reference.strip():
                    raise ValueError("a new run needs --review-mode and this request's --mode-reference")
                state = {"version": 8, "policy_version": 1, "run_id": uuid.uuid4().hex,
                         "work_item": args.work_item, "repo_root": str(repo_root),
                         "repo_roots": {area: str(root) for area, root in selected.items()},
                         "checkout_ids": checkout_ids, "scope": args.scope,
                         "review_mode": args.review_mode, "mode_reference": args.mode_reference,
                         "review_depth": review_depth_value,
                         "risk_reason": args.risk_reason.strip(), "events": []}
                state["base_commits"] = {}
                for area in review_areas(args.scope):
                    result = subprocess.run(["git", "-C", str(selected[area]), "rev-parse",
                                             "--verify", "HEAD"], capture_output=True, text=True)
                    state["base_commits"][area] = result.stdout.strip() if result.returncode == 0 else \
                        subprocess.run(["git", "-C", str(selected[area]), "mktree"], input=b"",
                                       check=True, capture_output=True).stdout.decode().strip()
                risk_guard(state)
                state["initial_code_digests"] = {
                    area: code_digest_for(run_dir, state, area) for area in areas_for(args.scope)}
                staging_dir = Path(tempfile.mkdtemp(prefix=".workflow-init-", dir=parent))
                try:
                    staging_dir.chmod(0o700)
                    write_state(staging_dir, state)
                    write_json_atomic(staging_dir / "official-sources.json",
                                      {"schema_version": 1, "status": "pending", "sources": []})
                    write_json_atomic(staging_dir / "scope-register.json",
                                      {"schema_version": 1, "questions": []})
                    os.replace(staging_dir, run_dir)
                except Exception:
                    shutil.rmtree(staging_dir, ignore_errors=True)
                    raise
                print(run_dir)
                return 0

        run_dir = lexical_absolute(args.run_dir)
        state = read_state(run_dir)
        lock_handle = (run_dir / ".workflow-state.lock").open("a+b")
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        recover_report_publish(run_dir)
        recover_legacy_migration(run_dir)
        state = read_state(run_dir)
        if args.command == "status":
            print(json.dumps(workflow_status(run_dir, state), ensure_ascii=False))
            return 0
        if args.command in {"scope-question", "scope-answer", "report-publish"} and state["version"] < 8:
            raise ValueError("scope tracking and atomic report publishing require a migrated v8 run")
        if args.command == "scope-question":
            register = scope_register(run_dir, state)
            if any(item["id"] == args.question_id for item in register["questions"]):
                raise ValueError("scope question ID must be unique")
            if not args.question.strip() or not args.impact.strip():
                raise ValueError("scope question and impact cannot be empty")
            question = {"id": args.question_id, "kind": args.kind, "status": "pending",
                        "question": args.question.strip(), "paths": args.path,
                        "dependent_paths": args.dependent_path, "impact": args.impact.strip(),
                        "blocks_requested_work": args.kind == "required_decision",
                        "run_id": state["run_id"], "revision": 1}
            question["question_digest"] = scope_question_digest(state["run_id"], question)
            question["asked_at"] = datetime.now(timezone.utc).isoformat()
            register["questions"].append(question)
            write_json_atomic(run_dir / "scope-register.json", register)
            state["events"].append({"type": "scope-question", "run_id": state["run_id"],
                                    "question_id": args.question_id, "kind": args.kind,
                                    "question_digest": question["question_digest"],
                                    "revision": question["revision"], "question_state": question})
            write_state(run_dir, state)
            print(json.dumps(question, ensure_ascii=False))
            return 0
        if args.command == "scope-answer":
            if args.run_id != state["run_id"] or not args.reference.strip():
                raise ValueError("answer must reference this run and the user's response")
            register = scope_register(run_dir, state)
            question = next((item for item in register["questions"]
                             if item["id"] == args.question_id), None)
            if question is None:
                raise ValueError("unknown scope question ID")
            reopen_decision = (question["kind"] == "required_decision"
                               and question["status"] in {"declined", "unanswered"}
                                 and has_current_incomplete_report(run_dir, state, register))
            if (question["status"] != "pending" and not reopen_decision) \
                    or question["revision"] != args.revision \
                    or question["question_digest"] != args.question_digest:
                raise ValueError("stale, duplicate, or already-closed scope answer")
            prior_digest = question["question_digest"]
            question.update({"status": args.status, "answer": args.answer.strip(),
                             "answer_reference": args.reference.strip(),
                             "answered_at": datetime.now(timezone.utc).isoformat(),
                             "revision": question["revision"] + 1})
            question["question_digest"] = scope_question_digest(state["run_id"], question)
            state["events"].append({"type": "scope-answer", "run_id": state["run_id"],
                                    "question_id": args.question_id, "status": args.status,
                                    "revision": question["revision"], "prior_revision": args.revision,
                                    "question_digest": prior_digest,
                                    "resulting_digest": question["question_digest"],
                                    "question_state": question,
                                    "answer": args.answer.strip(),
                                    "reference": args.reference.strip()})
            write_state(run_dir, state)
            write_json_atomic(run_dir / "scope-register.json", register)
            print(json.dumps({"question_id": args.question_id, "status": args.status,
                              "revision": question["revision"],
                              "question_digest": question["question_digest"]}, ensure_ascii=False))
            return 0
        if args.command == "report-publish":
            status, digest = publish_report(run_dir, state, args.draft_file)
            print(json.dumps({"status": status, "report_digest": digest,
                              "path": str(run_dir / "final-report.md")}, ensure_ascii=False))
            return 0
        if state["version"] >= 3 and args.command in {"review", "agent-result", "resolve-agent", "qa-case", "resolve-qa-case"} \
                and args.slot not in required_slots(state):
            raise ValueError("slot is outside the review depth")
        if args.command in {"present", "review", "approve", "agent-result", "resolve-agent", "check-record",
                            "resolve-check", "qa-case", "resolve-qa-case"} and args.area not in review_areas(state["scope"]):
            raise ValueError(f"area {args.area} is outside the {state['scope']} scope")
        if args.command == "present":
            if state["review_mode"] != "user-review":
                raise ValueError("present is only required in user-review mode")
            if args.area == "integration":
                raise ValueError("present each design document separately")
            state["events"].append({"type": "present", "area": args.area,
                                    "digest": design_digest(run_dir, args.area)})
            write_state(run_dir, state)
        elif args.command == "review":
            if args.result == "changes-required" and not args.finding.strip():
                raise ValueError("--finding is required for changes-required review")
            if state["version"] >= 3:
                if args.slot not in required_slots(state) or not args.agent_id or not args.agent_id.strip():
                    raise ValueError("design review requires --slot and --agent-id")
                if state["version"] >= 4 and args.focus != focus_for("design-review", args.area, args.slot, review_depth(state)):
                    raise ValueError("design review focus does not match slot")
                if state["version"] >= 4:
                    plans(run_dir, state)
                review_file = agent_artifact("design-review", args.area, args.slot)
                event = {"type": "review", "area": args.area, "slot": args.slot,
                                        "agent_id": args.agent_id.strip(), "result": args.result,
                                        "finding": args.finding, "digest": design_digest(run_dir, args.area),
                                        "artifact_digest": artifact_digest(run_dir, review_file)}
                if state["version"] >= 4:
                    event["focus"] = args.focus
                    event["plans"] = plan_digests(run_dir, state)
                if state["version"] >= 5:
                    if not args.result_json:
                        raise ValueError("v5 design review needs --result-json")
                    event.update(result_binding(run_dir, args.result_json, "design-review",
                                                args.area, args.slot, args.agent_id.strip(),
                                                args.focus, args.result))
                    if any(old.get("result_json") == event["result_json"] for old in state["events"]):
                        raise ValueError("result JSON must be unique for every attempt")
                    event.update(model_selection(args.model, args.effort, args.model_reason))
                    event.update(context_selection(args.context_mode, args.context_reason))
                state["events"].append(event)
            else:
                review_file = f"{args.area}-design-review.md" if args.area != "integration" else "integration-contract-review.md"
                require_artifact(run_dir, review_file)
                state["events"].append({"type": "review", "area": args.area, "result": args.result,
                                        "finding": args.finding, "digest": design_digest(run_dir, args.area)})
            write_state(run_dir, state)
        elif args.command == "approve":
            if not args.reference.strip():
                raise ValueError("approval reference cannot be empty")
            if state["version"] >= 3:
                digest = design_digest(run_dir, args.area)
                latest = latest_slot_results(state, "review", args.area, digest)
                if not any(event["result"] == "changes-required" for event in latest.values()):
                    raise ValueError("no design finding for this area")
                findings = [index for index, event in enumerate(state["events"])
                            if event["type"] == "review" and event["area"] == args.area
                            and event["digest"] == digest and event["result"] == "changes-required"]
                outstanding = [index for index in findings
                               if not approved_finding(run_dir, state, args.area, index)]
                if not outstanding:
                    raise ValueError("design findings already approved")
                approval = {"type": "approval", "area": args.area,
                            "reference": args.reference, "digest": digest}
                if state["version"] >= 7:
                    approval.update({"run_id": state["run_id"],
                                     "plans": plan_digests(run_dir, state),
                                     "finding_indexes": outstanding})
                state["events"].append(approval)
            else:
                events = [event for event in state["events"] if event.get("area") == args.area]
                last_review_index = next((index for index in range(len(events) - 1, -1, -1)
                                          if events[index]["type"] == "review"), None)
                last_review = events[last_review_index] if last_review_index is not None else None
                if not last_review or last_review["result"] != "changes-required" or any(
                    event["type"] == "approval" for event in events[last_review_index + 1:]
                ):
                    raise ValueError("no unapproved design finding for this area")
                if last_review["digest"] != design_digest(run_dir, args.area):
                    raise ValueError("design changed before user approval; restore the reviewed version")
                state["events"].append({"type": "approval", "area": args.area,
                                        "reference": args.reference, "digest": last_review["digest"]})
            write_state(run_dir, state)
        elif args.command == "accept-design":
            if state["review_mode"] != "user-review":
                raise ValueError("accept-design is only required in user-review mode")
            if not args.reference.strip():
                raise ValueError("acceptance reference cannot be empty")
            check_reviews(run_dir, state)
            state["events"].append({"type": "accept-design", "reference": args.reference,
                                    "digests": {area: design_digest(run_dir, area)
                                                for area in areas_for(state["scope"])}})
            write_state(run_dir, state)
        elif args.command == "agent-result":
            if state["version"] < 3:
                raise ValueError("agent-result is only available for v3 or later runs")
            if not args.agent_id.strip():
                raise ValueError("agent id cannot be empty")
            if state["version"] >= 4:
                require_design_gate(run_dir, state)
                if args.focus != focus_for(args.stage, args.area, args.slot, review_depth(state)):
                    raise ValueError("agent focus does not match slot")
            if args.stage == "code-review":
                if args.area == "integration" or args.result not in {"clear", "findings"}:
                    raise ValueError("code-review requires a frontend/backend area and clear/findings result")
                check_design(run_dir, state)
                if state["version"] >= 4:
                    check_v4_checks(run_dir, state, args.area)
            else:
                if args.result not in {"pass", "fail", "not-run"}:
                    raise ValueError("QA result must be pass, fail, or not-run")
                if state["version"] >= 4 and args.result != "pass":
                    raise ValueError("v4 QA summary requires pass; record failures with qa-case")
            current_code = code_digest_for(run_dir, state, args.area)
            if args.stage == "qa":
                if args.area == "integration":
                    for area in areas_for(state["scope"]):
                        if state["version"] >= 4:
                            check_v4_checks(run_dir, state, area)
                            check_agent_stage(run_dir, state, "code-review", area, code_digest_for(run_dir, state, area))
                            check_v4_cases(run_dir, state, area)
                        check_agent_stage(run_dir, state, "qa", area, code_digest_for(run_dir, state, area))
                else:
                    check_agent_stage(run_dir, state, "code-review", args.area, current_code)
                if state["version"] >= 4 and args.result == "pass":
                    check_v4_cases(run_dir, state, args.area, args.slot, args.agent_id.strip())
            report = agent_artifact(args.stage, args.area, args.slot)
            event = {"type": args.stage, "area": args.area,
                                    "slot": args.slot, "agent_id": args.agent_id.strip(),
                                    "result": args.result, "digest": current_code,
                                    "design_digest": design_digest(run_dir, args.area),
                                    "artifact_digest": artifact_digest(run_dir, report)}
            if state["version"] >= 4:
                event["focus"] = args.focus
                event["plans"] = plan_digests(run_dir, state)
            if state["version"] >= 5:
                if not args.result_json:
                    raise ValueError("v5 agent result needs --result-json")
                event.update(result_binding(run_dir, args.result_json, args.stage,
                                            args.area, args.slot, args.agent_id.strip(),
                                            args.focus, args.result))
                if any(old.get("result_json") == event["result_json"] for old in state["events"]):
                    raise ValueError("result JSON must be unique for every attempt")
                event.update(model_selection(args.model, args.effort, args.model_reason))
                event.update(context_selection(args.context_mode, args.context_reason))
            state["events"].append(event)
            write_state(run_dir, state)
        elif args.command == "resolve-agent":
            if state["version"] < 3 or not args.reference.strip():
                raise ValueError("agent resolution requires a nonempty reference")
            if state["version"] >= 4 and args.stage == "qa":
                raise ValueError("use resolve-qa-case for v4 QA failures")
            current_code = code_digest_for(run_dir, state, args.area)
            latest = next((event for event in reversed(state["events"])
                           if event["type"] == args.stage and event["area"] == args.area
                           and event["slot"] == args.slot and event["digest"] == current_code), None)
            if latest is None or latest["result"] not in {"findings", "fail", "not-run"} \
                    or latest["design_digest"] != design_digest(run_dir, args.area):
                raise ValueError("no current agent finding to resolve")
            state["events"].append({"type": "agent-resolution", "stage": args.stage,
                                    "area": args.area, "slot": args.slot, "digest": current_code,
                                    "design_digest": design_digest(run_dir, args.area),
                                    "reference": args.reference})
            write_state(run_dir, state)
        elif args.command == "check-record":
            if not args.name.strip():
                raise ValueError("check name cannot be empty")
            if state["version"] >= 4:
                require_design_gate(run_dir, state)
                checks, _ = plans(run_dir, state)
                item = plan_check(checks, args.area, args.name)
                if item is None:
                    raise ValueError("check is not in the verification plan")
                current_code = code_digest_for(run_dir, state, args.area)
                current_design = design_digest(run_dir, args.area)
                previous = latest_events(state, "check", args.area, "name", args.name,
                                         current_code, current_design)
                previous = [record for record in previous if record.get("plans") == plan_digests(run_dir, state)]
                if args.status == "pass" and previous and previous[-1]["status"] in {"fail", "not-run"}:
                    if not any(event["type"] == "check-resolution" and event["area"] == args.area
                               and event["name"] == args.name and event["digest"] == current_code
                               and event["design_digest"] == current_design
                               and event.get("plans") == plan_digests(run_dir, state)
                               for event in state["events"][state["events"].index(previous[-1]) + 1:]):
                        raise ValueError("resolve-check is required before a passing rerun")
                event = {"type": "check", "area": args.area, "name": args.name,
                         "status": args.status, "digest": current_code,
                         "design_digest": current_design, "plans": plan_digests(run_dir, state),
                         "executed_at": datetime.now(timezone.utc).isoformat()}
                if args.status == "not-run":
                    if not args.reason or not args.reason.strip() or not args.unverified or not args.unverified.strip():
                        raise ValueError("not-run needs reason and unverified scope")
                    event.update({"reason": args.reason.strip(), "unverified": args.unverified.strip()})
                else:
                    if state["version"] >= 5:
                        if not args.manifest_file:
                            raise ValueError("v5 check needs --manifest-file")
                        binding = manifest_binding(run_dir, state, args.area, args.name,
                                                   args.manifest_file, args.status)
                        if any(old.get("attempt_id") == binding["attempt_id"] or
                               old.get("evidence_file") == binding["evidence_file"]
                               for old in state["events"]):
                            raise ValueError("check attempt ID and log must be unique")
                        if args.evidence_file and evidence_digest(run_dir, args.evidence_file)[0] != binding["evidence_file"]:
                            raise ValueError("evidence file differs from runner log")
                        event.update(binding)
                        event["planned_command"] = item["command"]
                        event["actual"] = args.actual.strip() if args.actual else args.status
                    else:
                        if not args.actual or not args.actual.strip() or not args.evidence_file:
                            raise ValueError("executed check needs actual result and local evidence file")
                        path, digest = evidence_digest(run_dir, args.evidence_file)
                        if any(old.get("evidence_file") == path for old in state["events"]):
                            raise ValueError("evidence file must be unique for every attempt")
                        event.update({"actual": args.actual.strip(), "evidence_file": path,
                                      "evidence_digest": digest})
            else:
                if not args.evidence or not args.evidence.strip():
                    raise ValueError("check evidence cannot be empty")
                event = {"type": "check", "area": args.area, "name": args.name,
                         "status": args.status, "evidence": args.evidence}
            if state["version"] == 3:
                event["digest"] = code_digest(Path(state["repo_root"]))
                event["design_digest"] = design_digest(run_dir, args.area)
            state["events"].append(event)
            write_state(run_dir, state)
        elif args.command == "resolve-check":
            if state["version"] < 4 or not args.reference.strip():
                raise ValueError("resolve-check requires a v4 run and a resolution reference")
            require_design_gate(run_dir, state)
            checks, _ = plans(run_dir, state)
            if plan_check(checks, args.area, args.name) is None:
                raise ValueError("check is not in the verification plan")
            current_code = code_digest_for(run_dir, state, args.area)
            current_design = design_digest(run_dir, args.area)
            previous = latest_events(state, "check", args.area, "name", args.name,
                                     current_code, current_design)
            previous = [record for record in previous if record.get("plans") == plan_digests(run_dir, state)]
            if not previous or previous[-1]["status"] not in {"fail", "not-run"}:
                raise ValueError("no current failed or unrun check to resolve")
            state["events"].append({"type": "check-resolution", "area": args.area,
                                    "name": args.name, "digest": current_code,
                                    "design_digest": current_design, "plans": plan_digests(run_dir, state),
                                    "reference": args.reference.strip()})
            write_state(run_dir, state)
        elif args.command == "qa-case":
            if state["version"] < 4:
                raise ValueError("qa-case requires a v4 run")
            require_design_gate(run_dir, state)
            _, cases = plans(run_dir, state)
            scenario = plan_case(cases, args.area, args.scenario_id)
            if scenario is None or scenario["slot"] != args.slot:
                raise ValueError("QA scenario and slot must match the plan")
            if not args.agent_id.strip() or not args.environment.strip() or not args.input.strip() \
                    or not args.actual.strip():
                raise ValueError("QA execution details cannot be empty")
            current_code = code_digest_for(run_dir, state, args.area)
            if args.area == "integration":
                check_v4_checks(run_dir, state, args.area)
                for area in areas_for(state["scope"]):
                    check_v4_checks(run_dir, state, area)
                    check_agent_stage(run_dir, state, "code-review", area, code_digest_for(run_dir, state, area))
                    check_v4_cases(run_dir, state, area)
                    check_agent_stage(run_dir, state, "qa", area, code_digest_for(run_dir, state, area))
            else:
                check_agent_stage(run_dir, state, "code-review", args.area, current_code)
            current_design = design_digest(run_dir, args.area)
            previous = latest_events(state, "qa-case", args.area, "scenario_id", args.scenario_id,
                                     current_code, current_design)
            previous = [record for record in previous if record.get("plans") == plan_digests(run_dir, state)]
            if args.result == "pass" and previous and previous[-1]["result"] in {"fail", "not-run"}:
                if not any(event["type"] == "qa-resolution" and event["area"] == args.area
                           and event["scenario_id"] == args.scenario_id and event["digest"] == current_code
                           and event["design_digest"] == current_design
                           and event.get("plans") == plan_digests(run_dir, state)
                           for event in state["events"][state["events"].index(previous[-1]) + 1:]):
                    raise ValueError("resolve-qa-case is required before a passing rerun")
            event = {"type": "qa-case", "area": args.area, "slot": args.slot,
                     "agent_id": args.agent_id.strip(), "scenario_id": args.scenario_id,
                     "criterion_id": scenario["criterion_id"], "source_area": scenario.get("source_area", args.area),
                     "result": args.result, "environment": args.environment.strip(),
                     "input": args.input.strip(), "expected": scenario["expected"],
                     "actual": args.actual.strip(), "digest": current_code,
                     "design_digest": current_design, "plans": plan_digests(run_dir, state),
                     "executed_at": datetime.now(timezone.utc).isoformat()}
            if args.result == "not-run":
                if not args.reason or not args.reason.strip():
                    raise ValueError("unrun QA case needs a reason")
                event["reason"] = args.reason.strip()
            else:
                if not args.evidence_file:
                    raise ValueError("executed QA case needs a local evidence file")
                path, digest = evidence_digest(run_dir, args.evidence_file)
                if any(old.get("evidence_file") == path for old in state["events"]):
                    raise ValueError("evidence file must be unique for every attempt")
                event.update({"evidence_file": path, "evidence_digest": digest})
            state["events"].append(event)
            write_state(run_dir, state)
        elif args.command == "resolve-qa-case":
            if state["version"] < 4 or not args.reference.strip():
                raise ValueError("resolve-qa-case requires a v4 run and a resolution reference")
            require_design_gate(run_dir, state)
            _, cases = plans(run_dir, state)
            scenario = plan_case(cases, args.area, args.scenario_id)
            if scenario is None or scenario["slot"] != args.slot:
                raise ValueError("QA scenario and slot must match the plan")
            current_code = code_digest_for(run_dir, state, args.area)
            current_design = design_digest(run_dir, args.area)
            previous = latest_events(state, "qa-case", args.area, "scenario_id", args.scenario_id,
                                     current_code, current_design)
            previous = [record for record in previous if record.get("plans") == plan_digests(run_dir, state)]
            if not previous or previous[-1]["result"] not in {"fail", "not-run"}:
                raise ValueError("no current failed or unrun QA case to resolve")
            state["events"].append({"type": "qa-resolution", "area": args.area, "slot": args.slot,
                                    "scenario_id": args.scenario_id, "digest": current_code,
                                    "design_digest": current_design, "plans": plan_digests(run_dir, state),
                                    "reference": args.reference.strip()})
            write_state(run_dir, state)
        else:
            if args.gate == "design" and state["version"] >= 7:
                for area in areas_for(state["scope"]):
                    if code_digest_for(run_dir, state, area) != state["initial_code_digests"][area]:
                        raise ValueError(f"code changed before design gate: {area}")
            (check_design if args.gate == "design" else check_final)(run_dir, state)
            if args.gate == "design" and state["version"] >= 4:
                state["events"].append({"type": "design-gate-passed",
                                        "digests": current_design_digests(run_dir, state),
                                        "plans": plan_digests(run_dir, state)})
                write_state(run_dir, state)
            if args.gate == "final" and state["version"] >= 7:
                state["events"].append({"type": "final-gate-passed",
                                        "binding": current_binding(run_dir, state)})
                write_state(run_dir, state)
            print(f"{args.gate} gate passed")
        return 0
    except (OSError, ValueError, json.JSONDecodeError, KeyError, subprocess.CalledProcessError) as error:
        print(f"workflow harness: {error}", file=sys.stderr)
        return 1
    finally:
        if lock_handle is not None:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
