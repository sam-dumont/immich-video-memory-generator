# HTTP exception handlers and BaseHTTPRequestHandler callbacks are framework
# entry points. Vulture cannot see their callers inside Starlette/http.server.
dead-code:
	uvx vulture src/ $(SERVICE_TREES) vulture-whitelist.py --min-confidence 60 \
		--ignore-names 'model_config,do_GET,log_message' \
		--ignore-decorators '@register_preset,@*.command,@*.group,@ui.page,@app.middleware,@app.get,@app.post,@app.exception_handler,@field_validator,@model_validator,@field_serializer'

arch-check:
	PYTHONPATH=services/inference:services/render-worker uv run lint-imports
