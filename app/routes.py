from flask import Blueprint, abort, jsonify, request, send_file

from app.catalog import SPEC
from app.config import SAMPLES
from app.errors import DomainIssue
from app.evidence.render import render
from app.review.service import approve, create_run, serialize
from app.service import pipeline
from app.dispatch import service as dispatch_service


api = Blueprint("api", __name__)
STAGE_INDEX = {"intake": 1, "prepare": 2, "calculate": 3, "decision": 4, "reconcile": 5}


def store():
    from flask import current_app
    return current_app.extensions["runs"]


def dispatch_store():
    from flask import current_app
    return current_app.extensions["dispatch"]


def _run_or_404(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return run


def _run_board_id(run_id):
    return f"run:{run_id}"


@api.get("/api/catalog")
def catalog():
    return jsonify(code=SPEC["code"], name=SPEC["name"], actor=SPEC["actor"], rule=SPEC["rule"],
                   rule_short=SPEC["rule_short"],
                   stages=[{"id": item, "name": name} for item, name in zip(
                       ["intake", "prepare", "calculate", "decision", "reconcile", "review", "evidence"],
                       SPEC["stages"])],
                   chart=SPEC["chart"], result_columns=SPEC["result_columns"],
                   columns=SPEC["columns"], samples=["dirty", "clean"])


@api.post("/api/stages/<stage_id>")
def stage(stage_id):
    if stage_id not in STAGE_INDEX:
        raise DomainIssue("STAGE_UNKNOWN", 404)
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    upto = SPEC["stages"][STAGE_INDEX[stage_id] - 1]
    return jsonify(pipeline(profile, upto=upto))


@api.post("/api/runs")
def create():
    data = request.get_json(silent=True) or {}
    profile = data.get("profile", "dirty")
    payload = pipeline(profile)
    run = create_run(store(), payload)
    # 预建按成员+服务日聚合的下发资格台账；失败不应影响原批次创建。
    try:
        board = dispatch_service.sync_run(dispatch_store(), run, f"run:{run['run_id']}")
        run["dispatch_board_id"] = f"run:{run['run_id']}"
        run["dispatch_summary"] = board["summary"]
        run["dispatchable_group_ids"] = board["dispatchable_group_ids"]
    except Exception:
        pass
    return jsonify(run)


@api.get("/api/runs/<run_id>")
def get_run(run_id):
    run = serialize(store().get(run_id))
    if not run:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    board_id = _run_board_id(run_id)
    board = dispatch_service.get_board(dispatch_store(), board_id)
    if board["groups"]:
        run["dispatch_board_id"] = board_id
        run["dispatch_summary"] = board["summary"]
        run["dispatchable_group_ids"] = board["dispatchable_group_ids"]
    return jsonify(run)


@api.post("/api/runs/<run_id>/review")
def review(run_id):
    data = request.get_json(silent=True) or {}
    if "decision" not in data or "expected_revision" not in data:
        raise DomainIssue("BAD_REQUEST")
    return jsonify(approve(store(), run_id, data["decision"], data.get("comment", ""), int(data["expected_revision"])))


@api.get("/api/runs/<run_id>/evidence")
def evidence(run_id):
    run = serialize(store().get(run_id))
    return jsonify(render(run))


@api.get("/api/examples/<profile>")
def example(profile):
    if profile not in {"clean", "dirty"}:
        abort(404)
    return send_file(SAMPLES / f"{profile}.csv", as_attachment=True)


# ---------------------------------------------------------------------------
# 已签发批次的下发资格视图。这些接口是 /api/dispatch 的 run 作用域便捷别名。
# ---------------------------------------------------------------------------

@api.get("/api/runs/<run_id>/dispatch")
@api.get("/api/runs/<run_id>/distribution")
@api.get("/api/runs/<run_id>/issuance")
@api.get("/api/runs/<run_id>/dispatch/board")
@api.get("/api/runs/<run_id>/dispatch-board")
@api.get("/api/runs/<run_id>/eligibility")
@api.get("/api/runs/<run_id>/groups")
def run_dispatch_board(run_id):
    run = _run_or_404(run_id)
    board_id = _run_board_id(run_id)
    board = dispatch_service.get_board(dispatch_store(), board_id)
    if not board["groups"]:
        board = dispatch_service.sync_run(dispatch_store(), run, board_id)
    return jsonify(board)


@api.post("/api/runs/<run_id>/dispatch")
@api.post("/api/runs/<run_id>/distribution")
@api.post("/api/runs/<run_id>/issuance")
@api.post("/api/runs/<run_id>/dispatch/sync")
def run_dispatch_sync(run_id):
    run = _run_or_404(run_id)
    board = dispatch_service.sync_run(dispatch_store(), run, _run_board_id(run_id))
    return jsonify(board)


@api.post("/api/runs/<run_id>/source-updates")
@api.post("/api/runs/<run_id>/source-update")
@api.post("/api/runs/<run_id>/source-updated")
@api.post("/api/runs/<run_id>/source")
@api.post("/api/runs/<run_id>/dispatch/source-updates")
def run_source_update(run_id):
    run = _run_or_404(run_id)
    data = request.get_json(silent=True) or {}
    dstore = dispatch_store()
    board_id = _run_board_id(run_id)
    if not dstore.load(board_id).get("groups"):
        dispatch_service.sync_run(dstore, run, board_id)
    return jsonify(dispatch_service.source_update(dstore, data, board_id))


@api.post("/api/runs/<run_id>/corrections")
@api.post("/api/runs/<run_id>/correct")
@api.post("/api/runs/<run_id>/amendments")
@api.post("/api/runs/<run_id>/dispatch/corrections")
def run_correction(run_id):
    run = _run_or_404(run_id)
    data = request.get_json(silent=True) or {}
    dstore = dispatch_store()
    board_id = _run_board_id(run_id)
    if not dstore.load(board_id).get("groups"):
        dispatch_service.sync_run(dstore, run, board_id)
    return jsonify(dispatch_service.correction(dstore, data, board_id))


@api.post("/api/runs/<run_id>/corrections/<correction_id>/withdraw")
@api.post("/api/runs/<run_id>/corrections/<correction_id>/cancel")
@api.post("/api/runs/<run_id>/dispatch/corrections/<correction_id>/withdraw")
def run_withdraw_correction(run_id, correction_id):
    _run_or_404(run_id)
    data = request.get_json(silent=True) or {}
    return jsonify(dispatch_service.withdraw(dispatch_store(), correction_id, data, _run_board_id(run_id)))
