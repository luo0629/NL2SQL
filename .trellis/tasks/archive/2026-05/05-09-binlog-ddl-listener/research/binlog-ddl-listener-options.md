# Binlog DDL Listener Options Research

- **Query**: Python libraries and patterns for listening to MySQL binlog DDL events
- **Scope**: external (web search + official docs) + internal (project structure)
- **Date**: 2026-05-09

---

## Library Comparison

### python-mysql-replication (mysql-replication on PyPI)

- **PyPI package**: `mysql-replication` (latest: v1.0.12, Nov 2025)
- **GitHub**: `julien-duponchelle/python-mysql-replication`
- **Python support**: 3.10 - 3.14, PyPy 3.7/3.9
- **MySQL support**: 8.0.14+ (v1.0+), 5.5/5.6/5.7 (v0.1-v0.45)
- **MariaDB**: 10.6
- **Sync only** -- built on top of PyMySQL (synchronous). No native async support.
- **Key class**: `BinLogStreamReader` -- synchronous generator/iterator
- **DDL handling**: DDL statements appear as `QueryEvent` objects with the raw SQL in the `query` attribute. Example output:
  ```
  === QueryEvent ===
  Query: CREATE TABLE test4 (id int NOT NULL AUTO_INCREMENT, ...)
  ```
- **Event filtering**: `only_events=[QueryEvent]` parameter filters at the library level, so only QueryEvents are yielded. This is the efficient way to skip DML row events.
- **Position tracking**: Supports `log_file` / `log_pos` for resume, and `auto_position` for GTID-based replication.
- **Blocking mode**: `blocking=True` makes the reader wait for new events at end of stream.
- **Reconnection**: No built-in auto-reconnection. Must be implemented externally.
- **Gotchas**:
  - Requires `binlog_format = ROW` in MySQL config for DML events, but DDL is always logged as `QueryEvent` regardless of binlog format.
  - For MySQL 8.0.14+, must set `binlog_row_metadata = FULL` and `binlog_row_image = FULL` for correct column resolution after ALTER TABLE.
  - Column schema changes (ALTER TABLE) cause misalignment in cached column metadata; v1.0+ has fixes but requires the above MySQL settings.
  - `freeze_schema=True` disables table metadata lookups (faster but loses column names).

### asyncmy

- **PyPI**: `asyncmy` (latest: v0.2.11, Jan 2026)
- **GitHub**: `long2ice/asyncmy`
- **Native asyncio** -- reuses PyMySQL/aiomysql code with Cython-compiled core for performance.
- **Replication support**: `asyncmy.replication.BinLogStream` -- async iterator using `async for event in stream`.
- **DDL handling**: Same as python-mysql-replication (shared codebase). DDL appears as `QueryEvent`.
- **API**: Nearly identical to python-mysql-replication's `BinLogStreamReader`:
  ```python
  from asyncmy import connect
  from asyncmy.replication import BinLogStream

  conn = await connect()
  ctl_conn = await connect()
  stream = BinLogStream(
      conn, ctl_conn,
      server_id=1,
      master_log_file="binlog.000172",
      master_log_position=2235312,
      resume_stream=True,
      blocking=True,
  )
  async for event in stream:
      print(event)
  ```
- **Reconnection**: No built-in auto-reconnection.
- **Advantage**: Native `async for` integration -- no thread pool or `run_in_executor` needed.
- **Limitation**: Requires two separate connections (one for stream, one for control/metadata queries).

### mysql-event-stream

- **PyPI**: `mysql-event-stream` (latest: v1.1.0)
- **GitHub**: `libraz/mysql-event-stream`
- **Native C++ core** with Python ctypes FFI binding. Claims >100k events/sec.
- **MySQL 8.4+ only** (LTS and Innovation releases).
- **Async native**: `async for event in CdcStream(...)`.
- **Built-in auto-reconnection** with linear backoff.
- **GTID support** natively.
- **DDL handling**: Focused on row-level CDC events (INSERT/UPDATE/DELETE). Does NOT appear to expose DDL/QueryEvent -- its API emits structured row change events only.
- **Gotcha**: MySQL 8.4+ requirement is strict. Does not support MySQL 8.0.x or older. No DDL event support observed.
- **Not suitable** for DDL listening use case.

### aiomysql_replication (abandoned)

- **GitHub**: `jettify/aiomysql_replication`
- **Status**: Abandoned. Uses old Python 3.3/3.4 coroutine syntax (`yield from`).
- **Not viable** for production use.

---

## DDL Event Types

### How DDL appears in binlog

DDL statements (CREATE TABLE, ALTER TABLE, DROP TABLE, etc.) are **always** logged as `QUERY_EVENT` (type code 2) in the binary log, regardless of `binlog_format` setting. They contain the raw SQL text.

From the MySQL source code: "A Query_event is created for each query that modifies the database, unless the query is logged row-based." DDL is never logged in row-based format.

### Key binlog event type codes

| Event Type | Code | DDL Relevant? | Description |
|---|---|---|---|
| `QUERY_EVENT` | 2 | **YES -- primary DDL carrier** | Text query: DDL and statement-based DML |
| `XID_EVENT` | 16 | Indirect | Transaction commit marker (DDL with XID uses `Q_DDL_LOGGED_WITH_XID`) |
| `TABLE_MAP_EVENT` | 19 | Indirect | Table metadata before row events |
| `WRITE_ROWS_EVENT` | 30 | No | Row-level INSERT |
| `UPDATE_ROWS_EVENT` | 31 | No | Row-level UPDATE |
| `DELETE_ROWS_EVENT` | 32 | No | Row-level DELETE |
| `GTID_LOG_EVENT` | 33 | Indirect | GTID marker before transaction |
| `ROTATE_EVENT` | 4 | No | Binlog file rotation |
| `FORMAT_DESCRIPTION_EVENT` | 15 | No | Binlog format descriptor |

### Filtering strategy for DDL-only

The most efficient approach is to use the library's built-in event type filtering:

```python
from pymysqlreplication.event import QueryEvent

stream = BinLogStreamReader(
    connection_settings=mysql_settings,
    server_id=100,
    only_events=[QueryEvent],  # Only yield QueryEvent objects
    blocking=True,
)
```

This filters at the packet level inside `BinLogStreamReader._allowed_event_list()`. The library still needs `TableMapEvent` and `RotateEvent` internally for bookkeeping, but they are consumed silently and not yielded to the caller.

### Distinguishing DDL from DML in QueryEvent

Not all `QueryEvent` objects are DDL. Statement-based DML (INSERT/UPDATE/DELETE when `binlog_format=STATEMENT`) also appears as QueryEvent. To filter DDL specifically, parse the query text:

```python
DDL_KEYWORDS = {"CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME", "GRANT", "REVOKE"}

def is_ddl(event: QueryEvent) -> bool:
    if not hasattr(event, "query") or not event.query:
        return False
    first_word = event.query.strip().split()[0].upper()
    return first_word in DDL_KEYWORDS
```

Note: Under `binlog_format=ROW` (the recommended setting), DML does NOT produce QueryEvents -- only DDL does. So if ROW format is confirmed, every QueryEvent is DDL or administrative.

---

## Resilience Patterns

### Reconnection on network failure

**python-mysql-replication** and **asyncmy** do NOT have built-in auto-reconnection. The stream iterator raises an exception on connection loss. Pattern:

```python
import asyncio
import logging

async def resilient_binlog_listener(settings, server_id, on_event):
    while True:
        try:
            conn = await connect(**settings)
            ctl_conn = await connect(**settings)
            stream = BinLogStream(
                conn, ctl_conn, server_id,
                master_log_file=last_file,
                master_log_position=last_pos,
                resume_stream=True, blocking=True,
            )
            async for event in stream:
                last_file = event.packet.log_file  # or however position is exposed
                last_pos = event.packet.log_pos
                await on_event(event)
        except Exception as e:
            logging.warning(f"Binlog connection lost: {e}, reconnecting in 5s...")
            await asyncio.sleep(5)
```

**mysql-event-stream** has built-in auto-reconnection with linear backoff -- a significant advantage for production, but it lacks DDL event support.

### Position tracking for resume after restart

Two approaches:

1. **File + Position (traditional)**: Track `(log_file, log_pos)` tuple. Persist to disk/database after each event. Resume with `BinLogStreamReader(log_file=..., log_pos=..., resume_stream=True)`.

2. **GTID (recommended for MySQL 5.6+)**: Track GTID set as a string (e.g., `"server-uuid:1-100"`). Resume with `BinLogStreamReader(auto_position=gtid_set)`. GTID is position-independent and survives failover scenarios.

For this project, GTID is preferred because:
- It works across binlog file rotations automatically
- It is more resilient to failover/replication topology changes
- MySQL 8.0 has GTID enabled by default

Persistence options for position state:
- Simple file (JSON with `{"gtid_set": "...", "log_file": "...", "log_pos": ...}`)
- Database table (single row updated after each event batch)
- Redis (for high-throughput scenarios)

---

## Async Integration Patterns

### Pattern 1: asyncmy BinLogStream as background task (recommended)

Since asyncmy provides native `async for`, it integrates directly with FastAPI's async lifecycle:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
import asyncio

binlog_task = None

async def binlog_listener():
    """Run as a long-lived background coroutine."""
    while True:
        try:
            conn = await asyncmy.connect(**db_settings)
            ctl_conn = await asyncmy.connect(**db_settings)
            stream = BinLogStream(
                conn, ctl_conn, server_id=1,
                resume_stream=True, blocking=True,
                only_events=[QueryEvent],
            )
            async for event in stream:
                if is_ddl(event):
                    await handle_ddl(event)
        except Exception as e:
            logger.error(f"Binlog listener error: {e}")
            await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global binlog_task
    binlog_task = asyncio.create_task(binlog_listener())
    yield
    binlog_task.cancel()
    try:
        await binlog_task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)
```

Key points:
- Use `asyncio.create_task()` to launch as a concurrent coroutine (not `BackgroundTasks` -- those are for one-shot post-response tasks).
- Use FastAPI's `lifespan` context manager for startup/shutdown lifecycle.
- The listener runs concurrently with the HTTP server on the same event loop.
- No thread pool needed.

### Pattern 2: python-mysql-replication via run_in_executor

If using the synchronous `python-mysql-replication`, wrap in a thread:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

executor = ThreadPoolExecutor(max_workers=1)

def sync_binlog_listener():
    stream = BinLogStreamReader(
        connection_settings=mysql_settings,
        server_id=100,
        only_events=[QueryEvent],
        blocking=True,
    )
    for event in stream:
        # Can't directly call async code here
        # Use asyncio.run_coroutine_threadsafe() or a queue
        pass

async def start_listener():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, sync_binlog_listener)
```

This is more complex because you need inter-thread communication (e.g., `asyncio.Queue`) to pass events from the sync thread to the async handler.

### Pattern 3: Dedicated listener service

For production, consider running the binlog listener as a separate process/service that communicates with the main app via a message queue (Redis pub/sub, NATS, etc.). This provides:
- Process isolation (listener crash does not affect API)
- Independent scaling
- Easier restart/resume semantics

---

## Recommendation

### Best approach for this project

**Primary choice: `asyncmy` with `BinLogStream`**

Reasons:
1. **Native async** -- integrates cleanly with the existing FastAPI backend (`backend/app/main.py` runs on uvicorn/asyncio). No thread pool gymnastics needed.
2. **Same API surface** as python-mysql-replication -- all the event types, filtering, GTID support are the same.
3. **DDL events are `QueryEvent`** -- use `only_events=[QueryEvent]` to skip all DML row events efficiently.
4. **Actively maintained** -- v0.2.11 released Jan 2026.
5. **Lightweight** -- the listener only needs to parse DDL text from QueryEvent, no heavy row parsing.

### Fallback choice: `python-mysql-replication` with `run_in_executor`

If asyncmy has compatibility issues with the target MySQL version or environment, the synchronous library works via thread pool wrapping.

### What to avoid

- **mysql-event-stream**: No DDL event support; MySQL 8.4+ only.
- **aiomysql_replication**: Abandoned.
- **Polling INFORMATION_SCHEMA**: Works but has latency (polling interval) and misses transient DDL (CREATE + DROP between polls). Binlog is the authoritative source.

### Integration into existing architecture

Based on the project structure (`backend/app/services/`, `backend/app/database/`):

- New service: `backend/app/services/binlog_listener_service.py` -- owns the listener lifecycle
- New module: `backend/app/binlog/` -- event handler, DDL parser, position persistence
- Startup hook: register as a lifespan task in `backend/app/main.py`
- Schema refresh: on DDL event detected, trigger RAG service schema cache invalidation (`backend/app/services/rag_service.py`)

### MySQL server prerequisites

```ini
[mysqld]
server-id                = 1
log_bin                   = /var/log/mysql/mysql-bin.log
binlog_expire_logs_seconds = 864000
max_binlog_size           = 100M
binlog_format             = ROW
binlog_row_metadata       = FULL
binlog_row_image          = FULL
gtid_mode                 = ON
enforce_gtid_consistency  = ON
```

Replication user needs: `REPLICATION SLAVE`, `REPLICATION CLIENT` privileges.

---

## External References

- [python-mysql-replication docs](https://python-mysql-replication.readthedocs.io/en/latest/) -- BinLogStreamReader API, event types
- [python-mysql-replication GitHub](https://github.com/julien-duponchelle/python-mysql-replication) -- source, examples, issues
- [asyncmy GitHub](https://github.com/long2ice/asyncmy) -- asyncio MySQL driver with replication support
- [asyncmy PyPI](https://pypi.org/project/asyncmy/0.2.11/) -- v0.2.11
- [mysql-event-stream](https://pypi.org/project/mysql-event-stream/) -- C++ CDC engine (no DDL support)
- [MySQL Binlog Event Reference](https://dev.mysql.com/doc/dev/mysql-server/latest/page_protocol_replication_binlog_event.html) -- official event type codes and structure
- [MySQL Query_event docs](https://dev.mysql.com/doc/dev/mysql-server/8.0.46/classbinary__log_1_1Query__event.html) -- DDL logging behavior
- [FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/) -- for one-shot tasks (not long-lived listeners)
- [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/) -- proper startup/shutdown hooks for long-lived coroutines

---

## Caveats / Not Found

- asyncmy's `BinLogStream` does not expose `only_events` filtering in the same way as python-mysql-replication's `BinLogStreamReader`. Need to verify the exact parameter name in asyncmy's source (it may use `ignored_events` or accept the same kwargs). If not available, filter in the `async for` loop body.
- The `is_ddl()` text-parsing approach is a heuristic. Edge cases include: comments before DDL keywords, `PREPARE` statements containing DDL text, `CALL` to stored procedures that perform DDL. For this project (teaching/enterprise skeleton), the simple keyword check is sufficient.
- No Python library was found that provides structured DDL parsing (extracting table name, column changes, etc.) from binlog events. The raw SQL text from `QueryEvent.query` must be parsed separately (e.g., with a SQL parser like `sqlparse` or `sqlglot`).
