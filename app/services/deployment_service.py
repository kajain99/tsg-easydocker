import html
import json
import subprocess
import threading
import time
import uuid


_DEPLOYMENT_RUNS = {}
_DEPLOYMENT_RUNS_LOCK = threading.Lock()
_COMPLETED_RUN_TTL_SECONDS = 3600
_MAX_COMPLETED_RUNS = 25


def _create_run_record(
    deployment_label,
    command_display,
    folder_display,
    services_count,
    ports_display,
    success_result,
    failure_result
):
    return {
        "id": uuid.uuid4().hex,
        "deployment_label": deployment_label,
        "command_display": command_display,
        "folder_display": folder_display,
        "services_count": services_count,
        "ports_display": ports_display,
        "logs": [],
        "complete": False,
        "result": None,
        "condition": threading.Condition(),
        "success_result": success_result,
        "failure_result": failure_result,
        "created_at": time.time(),
        "completed_at": None,
    }


def _append_log(run_record, line):
    with run_record["condition"]:
        run_record["logs"].append(line)
        run_record["condition"].notify_all()


def _finish_run(run_record, result):
    with run_record["condition"]:
        run_record["complete"] = True
        run_record["result"] = result
        run_record["completed_at"] = time.time()
        run_record["condition"].notify_all()


def _cleanup_deployment_runs_locked():
    now = time.time()
    completed_runs = [
        (run_id, record)
        for run_id, record in _DEPLOYMENT_RUNS.items()
        if record.get("complete")
    ]

    expired_ids = [
        run_id
        for run_id, record in completed_runs
        if record.get("completed_at") and now - record["completed_at"] > _COMPLETED_RUN_TTL_SECONDS
    ]
    for run_id in expired_ids:
        _DEPLOYMENT_RUNS.pop(run_id, None)

    remaining_completed = sorted(
        (
            (run_id, record)
            for run_id, record in _DEPLOYMENT_RUNS.items()
            if record.get("complete")
        ),
        key=lambda item: item[1].get("completed_at") or item[1].get("created_at", 0)
    )
    while len(remaining_completed) > _MAX_COMPLETED_RUNS:
        run_id, _ = remaining_completed.pop(0)
        _DEPLOYMENT_RUNS.pop(run_id, None)


def build_deployment_result_payloads(compose_yaml, app_links):
    if compose_yaml:
        compose_preview_html = (
            '<div class="compose-preview-card">'
            f'<pre class="compose-preview">{html.escape(compose_yaml)}</pre>'
            '</div>'
        )
        details_html = (
            '<a class="details-toggle" data-bs-toggle="collapse" href="#technical-details" role="button" '
            'aria-expanded="false" aria-controls="technical-details">Show technical details</a>'
            f'<div id="technical-details" class="collapse technical-details">{compose_preview_html}</div>'
        )
    else:
        details_html = ""

    if app_links:
        action_links = "".join(
            f'<a href="{html.escape(app_link["url"], quote=True)}" target="_blank" rel="noopener noreferrer" class="btn btn-primary">{html.escape(app_link["label"])}</a>'
            for app_link in app_links
        )
        success_actions_html = (
            '<div class="result-actions">'
            f"{action_links}"
            '<a href="/" class="btn btn-outline-secondary">Back to Home</a>'
            "</div>"
        )
    else:
        success_actions_html = (
            '<div class="result-actions"><a href="/" class="btn btn-primary">Back to Home</a></div>'
        )

    return {
        "success": {
            "kind": "success",
            "title": "Deployment completed",
            "message": "Your app is up. Use the action below or open the technical details if you want the generated Compose file.",
            "actions_html": success_actions_html,
            "details_html": details_html,
        },
        "failure": {
            "kind": "failure",
            "title": "Deployment failed",
            "message": "Review the log below, then go back and adjust the configuration if needed.",
            "actions_html": '<div class="result-actions"><a href="/" class="btn btn-primary">Back to Home</a></div>',
            "details_html": details_html,
        },
    }


def _run_compose_command(run_record, command, cwd):
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    if process.stdout:
        for line in iter(process.stdout.readline, ""):
            if not line:
                break
            _append_log(run_record, line.rstrip())
        process.stdout.close()

    return process.wait()


def _execute_deployment(run_record, compose_cmd, app_folder, pull_first):
    try:
        if pull_first:
            _append_log(run_record, "Pulling latest images...")
            pull_code = _run_compose_command(run_record, compose_cmd + ["pull"], app_folder)
            if pull_code != 0:
                _append_log(run_record, f"Pull failed with exit code: {pull_code}")
                result = dict(run_record["failure_result"])
                result["title"] = "Installation Failed"
                result["message"] = "EasyDocker could not pull the latest image. Review the log below and try again."
                _finish_run(run_record, result)
                return

        _append_log(run_record, "Starting deployment...")
        return_code = _run_compose_command(run_record, compose_cmd + ["up", "-d"], app_folder)
        _append_log(run_record, f"Exit code: {return_code}")
        _append_log(
            run_record,
            "Deployment finished successfully." if return_code == 0 else "Deployment failed."
        )

        if return_code == 0:
            _finish_run(run_record, run_record["success_result"])
        else:
            _finish_run(run_record, run_record["failure_result"])
    except Exception as exc:
        _append_log(run_record, f"Execution error: {exc}")
        result = dict(run_record["failure_result"])
        result["title"] = "Deployment failed"
        result["message"] = "EasyDocker hit an execution error while trying to deploy this app."
        _finish_run(run_record, result)


def start_deployment_run(
    compose_cmd,
    app_folder,
    deployment_label,
    command_display,
    folder_display,
    services_count,
    ports_display,
    success_result,
    failure_result,
    pull_first=False
):
    run_record = _create_run_record(
        deployment_label,
        command_display,
        folder_display,
        services_count,
        ports_display,
        success_result,
        failure_result
    )

    with _DEPLOYMENT_RUNS_LOCK:
        _cleanup_deployment_runs_locked()
        _DEPLOYMENT_RUNS[run_record["id"]] = run_record

    worker = threading.Thread(
        target=_execute_deployment,
        args=(run_record, compose_cmd, app_folder, pull_first),
        daemon=True
    )
    worker.start()
    return run_record["id"]


def get_deployment_run(run_id):
    with _DEPLOYMENT_RUNS_LOCK:
        _cleanup_deployment_runs_locked()
        return _DEPLOYMENT_RUNS.get(run_id)


def stream_deployment_events(run_id):
    run_record = get_deployment_run(run_id)
    if not run_record:
        yield "event: done\ndata: " + json.dumps({
            "kind": "failure",
            "title": "Deployment not found",
            "message": "This deployment run is no longer available.",
            "actions_html": '<div class="result-actions"><a href="/" class="btn btn-primary">Back to Home</a></div>',
            "details_html": ""
        }) + "\n\n"
        return

    sent_index = 0
    condition = run_record["condition"]

    while True:
        pending_logs = []
        done_payload = None
        send_keep_alive = False

        with condition:
            while sent_index < len(run_record["logs"]):
                pending_logs.append(run_record["logs"][sent_index])
                sent_index += 1

            if run_record["complete"]:
                done_payload = run_record["result"]
            else:
                condition.wait(timeout=10)
                send_keep_alive = True

        for log_line in pending_logs:
            yield "event: log\ndata: " + json.dumps(log_line) + "\n\n"

        if done_payload is not None:
            yield "event: done\ndata: " + json.dumps(done_payload) + "\n\n"
            return

        if send_keep_alive:
            yield ": keep-alive\n\n"
