# Logging Guidelines

> Logging configuration and usage for the backend.

---

## Configuration

- **Startup**: `configure_logging()` in `app/core/logging.py` is invoked from FastAPI lifespan in `app/main.py`.
- **Format**: `%(asctime)s | %(levelname)s | %(name)s | %(message)s`
- **Default level**: `INFO` via `logging.basicConfig`.

---

## Usage Conventions

- Use module-level loggers: `logging.getLogger(__name__)` in each module where logging is added.
- Log operational events (request boundaries, agent phase outcomes, execution summaries) at **INFO**.
- Guard sensitive data: never log full API keys, database passwords, or raw PII from query results at default levels.

---

## Forbidden Patterns

- Calling `logging.basicConfig` from multiple modules (breaks centralized config).
- Printing debug state with `print()` in production paths.

---

## Extensions (Future)

- Structured JSON logging for centralized collectors.
- Correlate logs with request IDs from middleware (`app/core/middleware.py`) when tracing is introduced.
