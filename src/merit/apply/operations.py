"""Ten staging-only operation handlers. Semantic rules remain numbered checks."""

import datetime
import re

from merit import model
from merit.anchor import anchor_evidence
from merit.config import load_config
from merit.errors import UsageError


def allocate_id(root, kind):
    prefix = {"requirement": "REQ-" + load_config(root).id_prefix,
              "candidate": "CAN", "issue": "ISS", "decision": "DEC",
              "supersession": "SUP"}[kind]
    directory = root / model.ARTEFACT_DIRS[kind]
    numbers = [int(p.stem.rsplit("-", 1)[1]) for p in directory.glob("*.yaml")
               if re.fullmatch(re.escape(prefix) + r"-[0-9]{4}", p.stem)]
    number = max(numbers, default=0) + 1
    if number > 9999:
        raise UsageError(f"{prefix} ID space exhausted", fix="merit validate")
    return f"{prefix}-{number:04d}"


def _write(root, kind, artefact):
    path = root / model.ARTEFACT_DIRS[kind] / (artefact.id + ".yaml")
    model.save_artefact(artefact, path)
    return path


def _load(root, kind, identity):
    path = root / model.ARTEFACT_DIRS[kind] / (identity + ".yaml")
    if not path.is_file():
        raise UsageError(f"unknown {kind} {identity}", fix="merit validate")
    return model.load_artefact(path, kind)


def _evidence(root, data):
    return anchor_evidence(model.Evidence(**data), root)


def _add(root, op, kind, defaults=None):
    data = {**(defaults or {}), **{k: v for k, v in op.items() if k != "op"}}
    data.update(schema_version=1, id=allocate_id(root, kind))
    if "evidence" in data:
        data["evidence"] = [_evidence(root, e) for e in data["evidence"]]
    for field in ("record", "confirmation_record"):
        if field in data:
            data[field] = _evidence(root, data[field])
    artefact = model.ARTEFACT_CLASSES[kind](**data)
    return artefact, _write(root, kind, artefact)


def add_requirement(root, op):
    return [_add(root, op, "requirement", {"status": "draft"})[1]]


def add_candidate(root, op):
    return [_add(root, op, "candidate", {"status": "open", "raised_by": "merit",
                 "raised_on": datetime.date.today().isoformat()})[1]]


def add_issue(root, op):
    return [_add(root, op, "issue", {"status": "open"})[1]]


def add_decision(root, op):
    decision, path = _add(root, op, "decision")
    paths = [path]
    for identity in decision.resolves:
        # Unknown issue ownership belongs to V9 in the complete check run.
        if (root / "issues" / (identity + ".yaml")).is_file():
            issue = _load(root, "issue", identity)
            issue.status, issue.resolved_by = "resolved", decision.id
            paths.append(_write(root, "issue", issue))
    return paths


def confirm_decision(root, op):
    decision = _load(root, "decision", op["id"])
    decision.confirmed_by = op["confirmed_by"]
    decision.confirmation_record = _evidence(root, op["confirmation_record"])
    return [_write(root, "decision", decision)]


def promote_candidate(root, op):
    candidate = _load(root, "candidate", op["id"])
    if candidate.status != "open":
        raise UsageError(f"candidate {candidate.id} is not open", fix="merit validate")
    data = {k: v for k, v in op.items() if k not in ("op", "id")}
    data.update(statement=candidate.statement, promoted_from=candidate.id,
                op="add_requirement")
    requirement, path = _add(root, data, "requirement", {"status": "draft"})
    candidate.status, candidate.promoted_to = "promoted", requirement.id
    return [path, _write(root, "candidate", candidate)]


def set_status(root, op):
    requirement = _load(root, "requirement", op["id"])
    requirement.status = op["status"]
    if requirement.status in ("withdrawn", "deferred"):
        setattr(requirement, requirement.status + "_reason", op.get("reason"))
    return [_write(root, "requirement", requirement)]


def dismiss_issue(root, op):
    issue = _load(root, "issue", op["id"])
    issue.status, issue.dismissed_reason = "dismissed", op["reason"]
    return [_write(root, "issue", issue)]


def add_acceptance_criterion(root, op):
    requirement = _load(root, "requirement", op["id"])
    requirement.acceptance_criteria.append(op["criterion"])
    return [_write(root, "requirement", requirement)]


def record_supersession(root, op):
    return [_add(root, op, "supersession")[1]]


HANDLERS = {handler.__name__: handler for handler in (
    add_requirement, add_candidate, add_issue, add_decision, confirm_decision,
    promote_candidate, set_status, dismiss_issue, add_acceptance_criterion,
    record_supersession)}
