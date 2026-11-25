"""下发工作台 HTTP 接口。

主前缀为 ``/api/dispatch``，同时保留 ``/api/groups``、``/api/corrections``
等简短别名，方便不同调用约定。
"""
from flask import Blueprint, current_app, jsonify, request

from app.dispatch import service

dispatch_api = Blueprint("dispatch_api", __name__)


def store():
    return current_app.extensions["dispatch"]


def _body():
    data = request.get_json(silent=True)
    if isinstance(data, list):
        return {"groups": data}
    return dict(data) if isinstance(data, dict) else {}


def _board_id(data=None):
    data = data or {}
    run_id = (data.get("run_id") if isinstance(data, dict) else None) or request.args.get("run_id")
    if run_id and not (data.get("board_id") if isinstance(data, dict) else None):
        return f"run:{run_id}"
    return (
        (data.get("board_id") if isinstance(data, dict) else None)
        or request.args.get("board_id")
        or request.headers.get("X-Board-Id")
        or current_app.config.get("DISPATCH_BOARD_ID")
        or "default"
    )


def _board_response(board_id):
    """读取 board；run 作用域首次访问时从已签发/已创建批次同步。"""
    board = service.get_board(store(), board_id)
    if not board["groups"] and board_id.startswith("run:"):
        run_id = board_id.split(":", 1)[1]
        row = current_app.extensions["runs"].get(run_id)
        if row is not None:
            from app.review.service import serialize
            board = service.sync_run(store(), serialize(row), board_id)
    return board


def _idempotency(data):
    key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
    if key and not data.get("idempotency_key"):
        data["idempotency_key"] = key
    return data


# ---------------------------------------------------------------- board/views

@dispatch_api.get("/api/dispatch")
@dispatch_api.get("/api/dispatch/board")
@dispatch_api.get("/api/dispatch/boards")
@dispatch_api.get("/api/dispatch/eligibility")
@dispatch_api.get("/api/eligibility")
@dispatch_api.get("/api/eligibility/board")
@dispatch_api.get("/api/workbench/dispatch")
@dispatch_api.get("/api/board")
@dispatch_api.get("/api/dispatch-board")
def board_route():
    board_id = request.args.get("board_id") or _board_id()
    return jsonify(_board_response(board_id))


@dispatch_api.get("/api/dispatch/boards/<board_id>")
def named_board_route(board_id):
    return jsonify(_board_response(board_id))


@dispatch_api.post("/api/dispatch/demo")
@dispatch_api.post("/api/dispatch/seed")
@dispatch_api.post("/api/dispatch/load-sample")
@dispatch_api.post("/api/dispatch/sample")
@dispatch_api.post("/api/dispatch/sample/clean")
def demo_route():
    data = _body()
    return jsonify(service.demo(store(), _board_id(data)))


@dispatch_api.post("/api/dispatch/reset")
def reset_route():
    data = _body()
    return jsonify(service.reset_board(store(), _board_id(data)))


# ------------------------------------------------------------------- groups

@dispatch_api.get("/api/dispatch/groups")
@dispatch_api.get("/api/dispatch/claim-groups")
@dispatch_api.get("/api/dispatch/eligibility/groups")
@dispatch_api.get("/api/eligibility/groups")
@dispatch_api.get("/api/groups")
@dispatch_api.get("/api/claim-groups")
def list_groups_route():
    board = _board_response(_board_id())
    return jsonify(groups=board["groups"], summary=board["summary"],
                   dispatchable_group_ids=board["dispatchable_group_ids"])


@dispatch_api.get("/api/dispatch/groups/<path:group_id>")
@dispatch_api.get("/api/groups/<path:group_id>")
def group_route(group_id):
    board_id = _board_id()
    _board_response(board_id)
    return jsonify(service.get_group(store(), group_id, board_id))


@dispatch_api.post("/api/dispatch/groups/<path:group_id>/issue")
@dispatch_api.post("/api/groups/<path:group_id>/issue")
@dispatch_api.post("/api/dispatch/groups")
@dispatch_api.post("/api/groups")
@dispatch_api.post("/api/dispatch/issue")
@dispatch_api.post("/api/dispatch/board")
@dispatch_api.post("/api/dispatch/batches")
@dispatch_api.post("/api/dispatch")
def issue_route(group_id=None):
    data = _idempotency(_body())
    if group_id:
        data.setdefault("groups", [])
        if isinstance(data["groups"], list):
            data["groups"].append(group_id)
        else:
            data["group_id"] = group_id
    return jsonify(service.issue(store(), data, _board_id(data)))


@dispatch_api.post("/api/dispatch/groups/<path:group_id>/dispatch")
@dispatch_api.post("/api/groups/<path:group_id>/dispatch")
def dispatch_group_route(group_id):
    data = _idempotency(_body())
    return jsonify(service.dispatch(store(), group_id, data, _board_id(data)))


@dispatch_api.post("/api/dispatch/groups/<path:group_id>/recheck")
@dispatch_api.post("/api/groups/<path:group_id>/recheck")
@dispatch_api.post("/api/dispatch/recheck/<path:group_id>")
def recheck_group_route(group_id):
    data = _body()
    return jsonify(service.recheck(store(), group_id, data, _board_id(data)))


# ------------------------------------------------------------ source updates

def _source_update(data=None, group_id=None):
    data = _idempotency(data if data is not None else _body())
    if group_id:
        data.setdefault("group_id", group_id)
        data.setdefault("affected_group_ids", [group_id])
    return jsonify(service.source_update(store(), data, _board_id(data)))


@dispatch_api.post("/api/dispatch/source-updates")
@dispatch_api.post("/api/dispatch/source_updates")
@dispatch_api.post("/api/dispatch/source-data-updates")
@dispatch_api.post("/api/dispatch/updates")
@dispatch_api.post("/api/dispatch/invalidate")
@dispatch_api.post("/api/source-updates")
@dispatch_api.post("/api/source_updates")
@dispatch_api.post("/api/source-data-updates")
@dispatch_api.post("/api/ticket-source-updates")
@dispatch_api.post("/api/tickets/source-updates")
@dispatch_api.post("/api/claims/source-updates")
def source_update_route():
    return _source_update()


@dispatch_api.post("/api/source-updates/<update_id>/withdraw")
@dispatch_api.post("/api/dispatch/source-updates/<update_id>/withdraw")
def withdraw_source_update_route(update_id):
    data = _body()
    return jsonify(service.withdraw_source(store(), update_id, data, _board_id(data)))


@dispatch_api.post("/api/dispatch/groups/<path:group_id>/source-update")
@dispatch_api.post("/api/dispatch/groups/<path:group_id>/source-updated")
@dispatch_api.post("/api/dispatch/groups/<path:group_id>/invalidate")
@dispatch_api.post("/api/groups/<path:group_id>/source-update")
@dispatch_api.post("/api/groups/<path:group_id>/invalidate")
def group_source_update_route(group_id):
    return _source_update(group_id=group_id)


# --------------------------------------------------------------- corrections

@dispatch_api.post("/api/dispatch/corrections")
@dispatch_api.post("/api/dispatch/groups/<path:group_id>/corrections")
@dispatch_api.post("/api/groups/<path:group_id>/corrections")
@dispatch_api.post("/api/claim-groups/<path:group_id>/corrections")
@dispatch_api.post("/api/corrections")
@dispatch_api.post("/api/amendments")
@dispatch_api.post("/api/rectifications")
def correction_route(group_id=None):
    data = _idempotency(_body())
    if group_id:
        data.setdefault("group_id", group_id)
    return jsonify(service.correction(store(), data, _board_id(data)))
    data = _idempotency(_body())
    return jsonify(service.correction(store(), data, _board_id(data)))


@dispatch_api.get("/api/dispatch/credentials")
@dispatch_api.get("/api/credentials")
@dispatch_api.get("/api/certificates")
@dispatch_api.get("/api/vouchers")
def credentials_route():
    board = _board_response(_board_id())
    return jsonify(credentials=board["credentials"], certificates=board["certificates"],
                   archived_credentials=board["credentials"])


@dispatch_api.get("/api/dispatch/credentials/<credential_id>")
@dispatch_api.get("/api/credentials/<credential_id>")
def credential_detail_route(credential_id):
    board = store().load(_board_id())
    cert = board.get("certificate_by_id", {}).get(credential_id)
    if cert is None:
        from app.errors import DomainIssue
        raise DomainIssue("CREDENTIAL_NOT_FOUND", 404)
    return jsonify(cert)


@dispatch_api.post("/api/dispatch/credentials")
@dispatch_api.post("/api/credentials")
@dispatch_api.post("/api/certificates")
@dispatch_api.post("/api/vouchers")
def create_credential_route():
    data = _body()
    if any(key in data for key in ("claims", "groups", "group_ids")):
        return issue_route()
    return jsonify(service.register_credential(store(), data, _board_id(data)))


@dispatch_api.get("/api/dispatch/corrections/<correction_id>")
@dispatch_api.get("/api/corrections/<correction_id>")
def get_correction_route(correction_id):
    return jsonify(service.get_correction(store(), correction_id, _board_id()))


def _withdraw(correction_id, data=None):
    data = data if data is not None else _body()
    return jsonify(service.withdraw(store(), correction_id, data, _board_id(data)))


@dispatch_api.post("/api/dispatch/corrections/<correction_id>/withdraw")
@dispatch_api.post("/api/dispatch/corrections/<correction_id>/withdrawal")
@dispatch_api.post("/api/dispatch/corrections/<correction_id>/withdrawn")
@dispatch_api.post("/api/dispatch/corrections/<correction_id>/revoke")
@dispatch_api.post("/api/dispatch/corrections/<correction_id>/cancel")
@dispatch_api.post("/api/corrections/<correction_id>/withdraw")
@dispatch_api.post("/api/corrections/<correction_id>/withdrawal")
@dispatch_api.post("/api/corrections/<correction_id>/revoke")
@dispatch_api.post("/api/corrections/<correction_id>/cancel")
def withdraw_correction_route(correction_id):
    return _withdraw(correction_id, _body())


@dispatch_api.post("/api/dispatch/corrections/<correction_id>")
@dispatch_api.post("/api/corrections/<correction_id>")
@dispatch_api.delete("/api/dispatch/corrections/<correction_id>")
@dispatch_api.delete("/api/corrections/<correction_id>")
@dispatch_api.patch("/api/dispatch/corrections/<correction_id>")
@dispatch_api.patch("/api/corrections/<correction_id>")
@dispatch_api.put("/api/corrections/<correction_id>/status")
def delete_correction_route(correction_id):
    data = _body()
    status = str(data.get("status") or data.get("action") or "").upper()
    if request.method in {"PATCH", "PUT"} and status and status not in {
        "WITHDRAWN", "CANCELLED", "CANCELED", "REVOKED", "DELETE", "DELETED"
    }:
        return jsonify(service.get_correction(store(), correction_id, _board_id(data)))
    return _withdraw(correction_id, data)


@dispatch_api.post("/api/dispatch/corrections/<correction_id>/recheck")
@dispatch_api.post("/api/corrections/<correction_id>/recheck")
def recheck_correction_route(correction_id):
    data = _body()
    group_id = data.get("group_id")
    if not group_id:
        correction = service.get_correction(store(), correction_id, _board_id(data))
        groups = correction.get("affected_group_ids", [])
        if len(groups) == 1:
            group_id = groups[0]
        else:
            results = []
            for gid in groups:
                results.append(service.recheck(store(), gid, {**data, "correction_id": correction_id}, _board_id(data)))
            return jsonify(results=results)
    data["correction_id"] = correction_id
    return jsonify(service.recheck(store(), group_id, data, _board_id(data)))
