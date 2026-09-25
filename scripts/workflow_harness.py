#!/usr/bin/env python3
"""Keep implementation evidence local and check design/QA stage gates."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

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
    if state.get("version") not in (2, 3, 4, 5, 6, 7):
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
    if not model or not model.strip() or effort not in {"low", "medium", "high", "xhigh", "max", "ultra"} \
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
                     expected_digest: str | None = None) -> dict:
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
    if state["version"] >= 7 and (manifest.get("schema_version") != 2
            or manifest.get("run_id") != state["run_id"]
            or manifest.get("checkout_id") != state["checkout_ids"][area]
            or manifest.get("design_digest") != design_digest(run_dir, area)
            or manifest.get("plan_digests") != plan_digests(run_dir)):
        raise ValueError("check manifest differs from the current run, design, or plan")
    return {"manifest_file": path, "manifest_digest": digest,
            "attempt_id": manifest["attempt_id"],
            "evidence_file": manifest["log_file"], "evidence_digest": manifest["log_sha256"]}


def verify_manifest_binding(run_dir: Path, state: dict, event: dict) -> None:
    if any(key not in event for key in ("manifest_file", "manifest_digest", "attempt_id")):
        raise ValueError("v5 check event needs runner manifest")
    if not event.get("planned_command"):
        raise ValueError("v5 check event lacks its historical planned command")
    actual = manifest_binding(run_dir, state, event["area"], event["name"],
                              str(run_dir / event["manifest_file"]), event["status"],
                              expected_command=event["planned_command"],
                              expected_digest=event["digest"])
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


def plans(run_dir: Path, state: dict) -> tuple[dict, dict]:
    for name in ("verification-plan.json", "qa-plan.json"):
        require_artifact(run_dir, name)
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


def plan_digests(run_dir: Path) -> dict[str, str]:
    names = ["verification-plan.json", "qa-plan.json"]
    if (run_dir / "impact-map.json").exists():
        names.append("impact-map.json")
    return {name: artifact_digest(run_dir, name) for name in names}


def current_design_digests(run_dir: Path, state: dict) -> dict[str, str]:
    return {area: design_digest(run_dir, area) for area in areas_for(state["scope"])}


def require_design_gate(run_dir: Path, state: dict) -> None:
    checks, cases = plans(run_dir, state)
    del checks, cases
    expected_design = current_design_digests(run_dir, state)
    expected_plans = plan_digests(run_dir)
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
            verify_manifest_binding(run_dir, state, event)
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
        required_plans = (plan_digests(run_dir) if finding["digest"] == current_digest
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
            if state["version"] >= 4 and event.get("plans") != plan_digests(run_dir):
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
        if state["version"] >= 4 and event.get("plans") != plan_digests(run_dir):
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
    current_plans = plan_digests(run_dir)
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
    current_plans = plan_digests(run_dir)
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
            "plans": plan_digests(run_dir),
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


def workflow_status(run_dir: Path, state: dict) -> dict:
    if completed_run(run_dir, state):
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
    initialize.add_argument("--scope", choices=("frontend", "backend", "both"), required=True)
    initialize.add_argument("--review-mode", choices=("immediate", "user-review"), required=True)
    initialize.add_argument("--mode-reference", required=True,
                            help="Reference to the user's answer for this implementation request")
    initialize.add_argument("--review-depth", choices=("light", "full"), default="full")
    initialize.add_argument("--risk-reason", default="")
    initialize.add_argument("--run-dir", type=Path)
    initialize.add_argument("--parent-dir", type=Path,
                            help="Existing branch or work folder under ~/Documents/docs")
    initialize.add_argument("--work-item", help="Stable name for the current work item")
    for command in ("present", "review", "approve", "accept-design", "agent-result", "resolve-agent",
                    "check-record", "resolve-check", "qa-case", "resolve-qa-case", "check", "status"):
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
    args = parser.parse_args()

    lock_handle = None
    try:
        if args.command == "init":
            if not args.mode_reference.strip():
                raise ValueError("--mode-reference must identify the user's answer for this request")
            if args.scope == "both" and args.review_depth == "light":
                raise ValueError("light review needs one area and a risk reason")
            if args.run_dir and args.parent_dir:
                raise ValueError("--run-dir and --parent-dir cannot be used together")
            if args.work_item is not None and args.run_dir:
                raise ValueError("--work-item cannot be combined with --run-dir")
            if args.work_item is not None and (not args.work_item.strip()
                    or args.work_item in {".", ".."}
                    or any(separator in args.work_item for separator in ("/", "\\"))):
                raise ValueError("--work-item must be a single nonempty folder name")
            selected = {}
            if args.scope == "both":
                if args.repo and not args.frontend_repo and not args.backend_repo:
                    selected = {"frontend": args.repo, "backend": args.repo}
                elif not args.frontend_repo or not args.backend_repo:
                    raise ValueError("both scope requires --frontend-repo and --backend-repo")
                else:
                    selected = {"frontend": args.frontend_repo, "backend": args.backend_repo}
            else:
                path = args.frontend_repo if args.scope == "frontend" else args.backend_repo
                path = path or args.repo
                if path is None:
                    raise ValueError("single-area scope requires --repo or its area repository")
                selected = {args.scope: path}
            selected = {area: path.resolve() for area, path in selected.items()}
            selected["integration"] = (args.integration_repo or selected.get("backend")
                                       or selected.get("frontend")).resolve()
            checkout_ids = {}
            repo_root = selected.get("backend") or selected["frontend"]
            if args.parent_dir and not args.parent_dir.is_dir():
                raise ValueError("--parent-dir must be an existing folder")
            run_dir = args.run_dir or default_run_dir(repo_root, args.work_item, args.parent_dir)
            for area in review_areas(args.scope):
                ensure_local_run_dir(run_dir, selected[area])
            for area in review_areas(args.scope):
                checkout_ids[area] = checkout_identity(selected[area])
            run_dir = lexical_absolute(run_dir)
            parent = run_dir.parent
            with file_lock(parent / ".workflow-init.lock"):
                if args.work_item and not args.new_run:
                    candidates = []
                    for path in parent.glob("run-*/state.json"):
                        try:
                            summary = json.loads(path.read_text(encoding="utf-8"))
                        except (OSError, ValueError, json.JSONDecodeError):
                            continue
                        if summary.get("version") != 7 or summary.get("work_item") != args.work_item:
                            continue
                        try:
                            previous = read_state(path.parent)
                        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
                            raise ValueError(f"matching work item cannot be resumed: {error}") from error
                        if previous.get("scope") != args.scope or previous.get("repo_roots") != {
                                area: str(root) for area, root in selected.items()} or previous.get("review_mode") != args.review_mode \
                                or previous.get("review_depth") != args.review_depth:
                            raise ValueError("existing work item has conflicting workflow settings")
                        if not completed_run(path.parent, previous):
                            candidates.append(path.parent)
                    if len(candidates) > 1:
                        raise ValueError("multiple unfinished runs match this work item")
                    if candidates:
                        print(candidates[0])
                        return 0
                state = {"version": 7, "run_id": uuid.uuid4().hex,
                         "work_item": args.work_item, "repo_root": str(repo_root),
                         "repo_roots": {area: str(root) for area, root in selected.items()},
                         "checkout_ids": checkout_ids, "scope": args.scope,
                         "review_mode": args.review_mode, "mode_reference": args.mode_reference,
                         "review_depth": args.review_depth,
                         "risk_reason": args.risk_reason.strip(), "events": []}
                state["base_commits"] = {}
                for area in review_areas(args.scope):
                    result = subprocess.run(["git", "-C", str(selected[area]), "rev-parse",
                                             "--verify", "HEAD"], capture_output=True, text=True)
                    if result.returncode == 0:
                        state["base_commits"][area] = result.stdout.strip()
                    else:
                        state["base_commits"][area] = subprocess.run(
                            ["git", "-C", str(selected[area]), "mktree"], input=b"",
                            check=True, capture_output=True).stdout.decode().strip()
                risk_guard(state)
                state["initial_code_digests"] = {
                    area: code_digest_for(run_dir, state, area) for area in areas_for(args.scope)}
                run_dir.mkdir(parents=True, exist_ok=False)
                run_dir.chmod(0o700)
                write_state(run_dir, state)
            print(run_dir)
            return 0

        run_dir = lexical_absolute(args.run_dir)
        state = read_state(run_dir)
        lock_handle = (run_dir / ".workflow-state.lock").open("a+b")
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        state = read_state(run_dir)
        if args.command == "status":
            print(json.dumps(workflow_status(run_dir, state), ensure_ascii=False))
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
                    event["plans"] = plan_digests(run_dir)
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
                                     "plans": plan_digests(run_dir),
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
                event["plans"] = plan_digests(run_dir)
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
                previous = [record for record in previous if record.get("plans") == plan_digests(run_dir)]
                if args.status == "pass" and previous and previous[-1]["status"] in {"fail", "not-run"}:
                    if not any(event["type"] == "check-resolution" and event["area"] == args.area
                               and event["name"] == args.name and event["digest"] == current_code
                               and event["design_digest"] == current_design
                               and event.get("plans") == plan_digests(run_dir)
                               for event in state["events"][state["events"].index(previous[-1]) + 1:]):
                        raise ValueError("resolve-check is required before a passing rerun")
                event = {"type": "check", "area": args.area, "name": args.name,
                         "status": args.status, "digest": current_code,
                         "design_digest": current_design, "plans": plan_digests(run_dir),
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
            previous = [record for record in previous if record.get("plans") == plan_digests(run_dir)]
            if not previous or previous[-1]["status"] not in {"fail", "not-run"}:
                raise ValueError("no current failed or unrun check to resolve")
            state["events"].append({"type": "check-resolution", "area": args.area,
                                    "name": args.name, "digest": current_code,
                                    "design_digest": current_design, "plans": plan_digests(run_dir),
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
            previous = [record for record in previous if record.get("plans") == plan_digests(run_dir)]
            if args.result == "pass" and previous and previous[-1]["result"] in {"fail", "not-run"}:
                if not any(event["type"] == "qa-resolution" and event["area"] == args.area
                           and event["scenario_id"] == args.scenario_id and event["digest"] == current_code
                           and event["design_digest"] == current_design
                           and event.get("plans") == plan_digests(run_dir)
                           for event in state["events"][state["events"].index(previous[-1]) + 1:]):
                    raise ValueError("resolve-qa-case is required before a passing rerun")
            event = {"type": "qa-case", "area": args.area, "slot": args.slot,
                     "agent_id": args.agent_id.strip(), "scenario_id": args.scenario_id,
                     "criterion_id": scenario["criterion_id"], "source_area": scenario.get("source_area", args.area),
                     "result": args.result, "environment": args.environment.strip(),
                     "input": args.input.strip(), "expected": scenario["expected"],
                     "actual": args.actual.strip(), "digest": current_code,
                     "design_digest": current_design, "plans": plan_digests(run_dir),
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
            previous = [record for record in previous if record.get("plans") == plan_digests(run_dir)]
            if not previous or previous[-1]["result"] not in {"fail", "not-run"}:
                raise ValueError("no current failed or unrun QA case to resolve")
            state["events"].append({"type": "qa-resolution", "area": args.area, "slot": args.slot,
                                    "scenario_id": args.scenario_id, "digest": current_code,
                                    "design_digest": current_design, "plans": plan_digests(run_dir),
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
                                        "plans": plan_digests(run_dir)})
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
