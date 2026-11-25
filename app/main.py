import os

from flask import Flask, jsonify, render_template
from werkzeug.exceptions import RequestEntityTooLarge

from app.catalog import SPEC
from app.config import MAX_FILE, ROOT
from app.errors import DomainIssue, error_response
from app.review.store import Store
from app.routes import api
from app.dispatch.routes import dispatch_api
from app.dispatch.store import DispatchStore


def create_app(database=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_FILE + 64 * 1024
    db_path = database or os.getenv("WORKBENCH_DB", str(ROOT / "instance" / "workbench.sqlite3"))
    app.extensions["runs"] = Store(db_path)
    dispatch_store = DispatchStore(db_path)
    app.extensions["dispatch"] = dispatch_store
    app.extensions["dispatch_store"] = dispatch_store
    app.extensions["dispatch_board"] = dispatch_store
    app.extensions["eligibility"] = dispatch_store
    app.register_error_handler(DomainIssue, error_response)
    app.register_error_handler(RequestEntityTooLarge, lambda _: error_response(DomainIssue("FILE_TOO_LARGE", 413)))
    app.register_blueprint(api)
    app.register_blueprint(dispatch_api)

    @app.get("/")
    def home():
        return render_template("index.html", p=SPEC)

    @app.get("/dispatch")
    def dispatch_workbench():
        return render_template("dispatch.html", p=SPEC)

    @app.get("/healthz")
    def health():
        return jsonify(status="ok")

    return app


app = create_app()
