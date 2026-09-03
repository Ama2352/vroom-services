"""HTTP boundary for the clean incident-diagnosis agent."""

from __future__ import annotations

from flask import Flask, jsonify, request

from config import Settings


def create_app(settings: Settings | None = None, *, investigation_service=None, incident_store=None, knowledge_store=None) -> Flask:
    """Create the Flask application and allow tests to replace its workflow service."""
    app = Flask(__name__)
    active_settings = settings or Settings.from_environment()

    @app.get("/health")
    def health():
        # This reports configuration, not live network connectivity.
        return jsonify(
            {
                "status": "ok",
                "dependencies": {
                    "redis": "configured",
                    "llm": "configured" if active_settings.llm_configured else "unconfigured",
                },
            }
        )

    @app.post("/investigate")
    def investigate():
        """Validate the external alert before passing it to the workflow service."""
        nonlocal investigation_service
        alert = request.get_json(silent=True)
        if not isinstance(alert, dict) or not str(alert.get("service", "")).strip():
            return jsonify({"error": "service is required"}), 400
        if investigation_service is None:
            # Health must not require Redis or model artifacts to be reachable.
            from runtime import build_investigation_service

            investigation_service = build_investigation_service(active_settings)
        return jsonify(investigation_service.investigate(alert))

    @app.get("/incidents")
    def incidents():
        store = incident_store or getattr(investigation_service, "incident_store", None)
        return jsonify(store.list() if store else [])

    @app.get("/incidents/<incident_id>")
    def incident(incident_id):
        store = incident_store or getattr(investigation_service, "incident_store", None)
        record = store.get(incident_id) if store else None
        return (jsonify(record), 200) if record else (jsonify({"error": "incident not found"}), 404)

    @app.get("/knowledge")
    def knowledge():
        return jsonify(knowledge_store.load() if knowledge_store else {"families": [], "examples": [], "hints": []})

    @app.put("/knowledge")
    def replace_knowledge():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not all(isinstance(body.get(key), list) for key in ("families", "examples", "hints")):
            return jsonify({"error": "families, examples, and hints must be lists"}), 400
        if knowledge_store is None:
            return jsonify({"error": "knowledge store is unavailable"}), 503
        knowledge_store.save(body)
        return jsonify(body)

    return app
