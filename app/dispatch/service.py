"""下发工作台应用服务：HTTP 与持久化 store 之间的编排。"""
from __future__ import annotations

from app.dispatch.ledger import (
    DispatchError,
    apply_source_update,
    archive_credential,
    board_view,
    create_correction,
    dispatch_group,
    ensure_group,
    get_group_view,
    issue_batch,
    make_credential,
    new_board,
    recheck_group,
    withdraw_correction,
    withdraw_source_update,
)
from app.errors import DomainIssue


def _domain_issue(exc: DispatchError) -> DomainIssue:
    status = int(getattr(exc, "status", 400) or 400)
    return DomainIssue(exc.code, status, str(exc.detail) if exc.detail is not None else None)


def get_board(store, board_id: str = "default"):
    return board_view(store.load(board_id))


def reset_board(store, board_id: str = "default"):
    return board_view(store.reset(board_id))


def issue(store, data: dict, board_id: str = "default"):
    data = data or {}
    bid = str(data.get("board_id") or board_id or "default")

    def mutation(board):
        try:
            result = issue_batch(
                board,
                batch_id=data.get("batch_id") or data.get("id"),
                claims=data.get("claims") or data.get("rows") or data.get("tickets"),
                groups=data.get("groups") or data.get("group_ids"),
                issued_at=data.get("issued_at"),
            )
        except DispatchError as exc:
            raise _domain_issue(exc)
        return board, result

    store.transact(bid, mutation)
    return get_board(store, bid)


def register_credential(store, data: dict, board_id: str = "default"):
    data = data or {}
    bid = str(data.get("board_id") or board_id)
    issuance = data.get("issuance") or {
        key: data.get(key) for key in ("group_id", "member_id", "service_date", "claims", "claim_ids")
        if data.get(key) is not None
    }
    if not issuance.get("group_id") and data.get("group_id"):
        issuance["group_id"] = data["group_id"]

    def mutation(board):
        group_id = issuance.get("group_id")
        if group_id:
            ensure_group(board, group_id, issuance.get("member_id"), issuance.get("service_date"))
        credential = make_credential(
            issuance,
            batch_id=data.get("batch_id"),
            issued_at=data.get("issued_at"),
            schema=data.get("schema", "dispatch-credential/v1"),
        )
        archive_credential(board, credential)
        if group_id:
            group = board["groups"][group_id]
            group.setdefault("credential_ids", []).append(credential["credential_id"])
            if group.get("original_credential") is None:
                group["original_credential"] = credential
            group["credential"] = group["original_credential"]
        return board, credential

    return store.transact(bid, mutation)


def demo(store, board_id: str = "default"):
    claims = [
        {"claim_id": "K1", "member_id": "M1", "service_date": "2026-11-01", "payer": "MEDICARE",
         "billed_cents": 10000, "approved_cents": 8000, "deductible_cents": 1000},
        {"claim_id": "K2", "member_id": "M1", "service_date": "2026-11-01", "payer": "PRIVATE",
         "billed_cents": 6000, "approved_cents": 2000, "deductible_cents": 0},
        {"claim_id": "K3", "member_id": "M2", "service_date": "2026-11-02", "payer": "PRIVATE",
         "billed_cents": 5000, "approved_cents": 4500, "deductible_cents": 500},
        {"claim_id": "K4", "member_id": "M3", "service_date": "2026-11-03", "payer": "MEDICARE",
         "billed_cents": 8000, "approved_cents": 5000, "deductible_cents": 1000},
        {"claim_id": "K5", "member_id": "M3", "service_date": "2026-11-03", "payer": "SELF",
         "billed_cents": 8000, "approved_cents": 3000, "deductible_cents": 0},
    ]
    return issue(store, {"batch_id": "DEMO-BATCH", "claims": claims}, board_id)


def source_update(store, data: dict, board_id: str = "default"):
    bid = str((data or {}).get("board_id") or board_id)

    def mutation(board):
        try:
            record, changed = apply_source_update(board, data)
        except DispatchError as exc:
            raise _domain_issue(exc)
        record["idempotent_hit"] = not changed
        return board, record

    return store.transact(bid, mutation)


def withdraw_source(store, update_id: str, data: dict | None = None, board_id: str = "default"):
    data = data or {}
    bid = str(data.get("board_id") or board_id)

    def mutation(board):
        try:
            record = withdraw_source_update(board, update_id)
        except DispatchError as exc:
            raise _domain_issue(exc)
        return board, record

    return store.transact(bid, mutation)


def correction(store, data: dict, board_id: str = "default"):
    bid = str((data or {}).get("board_id") or board_id)

    def mutation(board):
        try:
            record, changed = create_correction(board, data)
        except DispatchError as exc:
            raise _domain_issue(exc)
        record["idempotent_hit"] = not changed
        return board, record

    return store.transact(bid, mutation)


def get_correction(store, correction_id: str, board_id: str = "default"):
    board = store.load(board_id)
    record = board.get("corrections", {}).get(str(correction_id))
    if record is None:
        raise DomainIssue("CORRECTION_NOT_FOUND", 404)
    return record


def withdraw(store, correction_id: str, data: dict | None = None, board_id: str = "default"):
    data = data or {}
    bid = str(data.get("board_id") or board_id)

    def mutation(board):
        try:
            record, changed = withdraw_correction(board, correction_id, **data)
        except DispatchError as exc:
            raise _domain_issue(exc)
        record["idempotent_hit"] = not changed
        return board, record

    return store.transact(bid, mutation)


def recheck(store, group_id: str, data: dict | None = None, board_id: str = "default"):
    data = data or {}
    bid = str(data.get("board_id") or board_id)

    def mutation(board):
        try:
            view = recheck_group(board, group_id, ok=bool(data.get("ok", True)),
                                 correction_id=data.get("correction_id"))
        except DispatchError as exc:
            raise _domain_issue(exc)
        return board, view

    return store.transact(bid, mutation)


def dispatch(store, group_id: str, data: dict | None = None, board_id: str = "default"):
    data = data or {}
    bid = str(data.get("board_id") or board_id)

    def mutation(board):
        try:
            view = dispatch_group(board, group_id, dispatch_id=data.get("dispatch_id") or data.get("idempotency_key"),
                                  idempotency_key=data.get("idempotency_key"))
        except DispatchError as exc:
            raise _domain_issue(exc)
        return board, view

    return store.transact(bid, mutation)


def get_group(store, group_id: str, board_id: str = "default"):
    try:
        return get_group_view(store.load(board_id), group_id)
    except DispatchError as exc:
        raise _domain_issue(exc)


def sync_run(store, run: dict, board_id: str | None = None):
    """把已签发理赔批次中的成员+服务日组同步为下发组。"""
    if run is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    bid = board_id or f"run:{run['run_id']}"
    claims = []
    try:
        from app.catalog import SPEC
        from app.service import pipeline
        prepared = pipeline(run.get("profile", "clean"), upto=SPEC["stages"][1])
        claims = prepared.get("rows", [])
    except Exception:
        # 即使样例/文件不可用，也可使用运行结果中的 label 建组。
        claims = []
    if not claims:
        for row in run.get("rows", []):
            label = row.get("label") or row.get("group_id")
            if label and "|" in str(label):
                member, day = str(label).split("|", 1)
                claims.append({"claim_id": row.get("claim_id") or f"{member}-{day}-{row.get('payer', 'G')}",
                               "member_id": member, "service_date": day,
                               "payer": row.get("payer", "SELF"),
                               "billed_cents": row.get("billed", 0),
                               "approved_cents": row.get("paid", 0),
                               "deductible_cents": 0})
    return issue(store, {"batch_id": run["run_id"], "claims": claims}, bid)
