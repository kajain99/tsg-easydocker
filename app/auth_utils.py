import os
import secrets
from urllib.parse import unquote, urlsplit

from flask import abort, redirect, render_template, request, session, url_for

from app_config import EASYDOCKER_PASSWORD, EASYDOCKER_USERNAME, EASYDOCKER_VERSION


def configure_app(app):
    app.secret_key = os.environ.get("EASYDOCKER_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax"
    )


def get_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def is_logged_in():
    return session.get("authenticated") is True


def normalize_next_url(next_url):
    candidate = next_url or "/"
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return "/"

    decoded_path = unquote(parsed.path or "")
    if not decoded_path.startswith("/"):
        return "/"

    if decoded_path.startswith("//") or decoded_path.startswith("/\\") or decoded_path.startswith("/%5c"):
        return "/"

    return candidate


def register_security(app):
    @app.context_processor
    def inject_csrf_token():
        return {
            "csrf_token": get_csrf_token,
            "easydocker_version": EASYDOCKER_VERSION,
        }

    @app.before_request
    def protect_state_changing_requests():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return

        submitted_token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        expected_token = session.get("csrf_token")
        if not submitted_token or not expected_token or not secrets.compare_digest(submitted_token, expected_token):
            abort(400, description="Invalid or expired form token. Please go back, refresh the page, and try again.")

        session["csrf_token"] = secrets.token_urlsafe(32)

    @app.before_request
    def require_login():
        if request.endpoint in {"login", "static"}:
            return
        if is_logged_in():
            return
        next_url = request.full_path if request.query_string else request.path
        return redirect(url_for("login", next=next_url))

    @app.after_request
    def disable_html_caching(response):
        content_type = (response.content_type or "").lower()
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


def login_view():
    error = None
    next_url = normalize_next_url(request.args.get("next") or request.form.get("next") or "/")

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        valid_username = secrets.compare_digest(username, EASYDOCKER_USERNAME)
        valid_password = secrets.compare_digest(password, EASYDOCKER_PASSWORD)

        if valid_username and valid_password:
            session.clear()
            session["authenticated"] = True
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(next_url)

        error = "Invalid username or password."

    return render_template(
        "login.html",
        error=error,
        next_url=next_url
    )


def logout_view():
    session.clear()
    return redirect(url_for("login"))
