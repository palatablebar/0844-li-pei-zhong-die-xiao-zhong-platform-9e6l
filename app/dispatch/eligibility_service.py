from app.dispatch.ledger import *  # noqa: F401,F403

def evaluate_eligibility(board, group_id):
    return get_group_view(board, group_id)

def refresh_all(board):
    for gid in list(board.get('groups', {})):
        recompute_group(board, gid)
    return board_view(board, include_events=False)
