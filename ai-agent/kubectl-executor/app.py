from flask import Flask, request, jsonify
import subprocess, os, re

app = Flask(__name__)

ALLOWLIST_PATTERNS = [
    r"^kubectl get pods( -n \w[\w-]*)?$",
    r"^kubectl describe pod [\w][\w-]* -n \w[\w-]*$",
    r"^kubectl logs [\w][\w-]* -n \w[\w-]*( --tail=\d+)?$",
    r"^kubectl top pods( -n \w[\w-]*)?$",
    r"^kubectl get events -n \w[\w-]*$",
    r"^kubectl rollout status deployment/[\w][\w-]* -n \w[\w-]*$",
]

BEARER_TOKEN = os.environ.get("EXECUTOR_API_KEY", "change-me")


def is_allowed(command: str) -> bool:
    return any(re.match(p, command.strip()) for p in ALLOWLIST_PATTERNS)


@app.route("/exec", methods=["POST"])
def execute():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {BEARER_TOKEN}":
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    command = data.get("command", "").strip()

    if not is_allowed(command):
        return jsonify({"error": f"Command not in allowlist: {command}", "stdout": ""}), 400

    try:
        result = subprocess.run(
            command.split(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return jsonify({
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
            "returncode": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "kubectl timed out after 30s", "stdout": "", "returncode": -1}), 500
    except FileNotFoundError as e:
        return jsonify({"error": f"Executable not found: {e}", "stdout": "", "returncode": -1}), 500
    except Exception as e:
        return jsonify({"error": str(e), "stdout": "", "returncode": -1}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
