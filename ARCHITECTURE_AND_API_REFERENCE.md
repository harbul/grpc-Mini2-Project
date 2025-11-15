# Fire Query System Architecture & API Guide

> **New to the project? Start here.** This guide now opens with a plain-language orientation so you can get hands-on quickly before diving into the dense reference material that follows.

## Quick Orientation for New Teammates
- **What we are building**: a gRPC-based info service that answers wildfire air-quality queries by fanning out to multiple Python and C++ microservices. Think of Process A (gateway) as the traffic cop, Processes B/E as regional leads, and Processes C/D/F as data workers.
- **How to get productive in an afternoon**
    1. Skim the "System Topology" diagram below to understand who talks to whom.
    2. Run the end-to-end smoke test: `python client/test_client.py` while the servers are up (see Runbook section for exact commands).
    3. With a debugger or print statements, follow one request through `gateway/server.py` ➜ `team_green/server_b.py` ➜ `team_green/server_c.cpp` to see the full round trip.
    4. Only after that, return to the detailed tables for deeper API semantics.
- **Where to look first**: if you are a Python engineer, prioritize `gateway/server.py` and `team_green/server_b.py`; if you are focusing on C++, head for `team_green/server_c.cpp` and `common/FireColumnModel.cpp`.
- **How this document is structured**: high-level concepts and quickstart steps come first, followed by in-depth API breakdowns, module references, and deployment notes. Whenever the information gets heavy, use the "Learning Roadmap" later in the file as a bookmark trail.
- **Terminology safety net**: unfamiliar names (e.g., *InternalQuery*, *FireColumnModel*) are defined in the Glossary at the end of this orientation. Refer back to it whenever an RPC or data structure is mentioned.

### Glossary (Keep Handy)
- **Gateway (Process A)**: The Python entry point that every client talks to. It fans requests out to the rest of the mesh, slices results into chunks, maintains per-request status, and handles cancellations.
- **Leader**: A Python intermediary (Processes B and E) that both hosts its own data partition and delegates to workers. Think of leaders as regional coordinators.
- **Worker**: A C++ microservice (Processes C, D, F) that holds a specific slice of the dataset and executes `InternalQuery` filters. Workers never speak to clients directly.
- **InternalQuery**: Private RPC used between gateway↔leaders and leaders↔workers. Carries the same filters as client queries but is not exposed outside the cluster.
- **FireColumnModel**: Our in-memory columnar store (Python and C++ versions) that ingests CSV files, maintains aligned vectors for each field, and accelerates lookups using inverted indexes.
- **Chunk**: One streamed `QueryResponseChunk` emitted by the gateway. Chunks let clients start consuming results while the system is still processing later data.
- **Request ID**: A client-assigned integer that threads through every RPC so status calls and cancellations target the correct in-flight query.
- **Data partition**: A set of date-specific directories assigned to one process via the JSON config. Ensures no duplication across services.
- **Active requests**: The gateway’s dictionary that tracks lifecycle state (`pending`, `cancelled`, `completed`) and chunk counters for each `request_id`.
- **StatusResponse**: Unified reply message returned by `CancelRequest`, `GetStatus`, and `Notify`. Contains status text plus chunk counters when relevant.
- **Cancellation**: Client-initiated stop request. The gateway honors it mid-stream; leaders/workers currently log acknowledgements but do not preempt computation.
- **Neighbor**: Another process specified in a config JSON that this service knows how to reach via gRPC. Leaders list their workers; gateway lists leaders.
- **QueryFilter**: Protobuf message defining what the client wants (parameters, AQI range, geo box, datetime window). Passed unchanged through the entire call stack.
- **Runbook**: Documented set of terminal commands in this guide that brings the full system up on a development machine.
- **Smoke test**: Minimal client run (`python client/test_client.py`) that verifies connectivity and basic chunked streaming.

> 💡 **Tip:** If you ever feel lost, jump to "Learning Roadmap" for a curated order of files/scripts to read, or "Environment Setup & Runbook" for concrete commands to run next.

Related reading (open in a second tab):
- `START_HERE.md` for the overall course timeline and expectations.
- `PROJECT_SUMMARY.md` and `WORK_COMPLETE_SUMMARY.md` for milestone context.
- `SINGLE_COMPUTER_COMPLETE.md` and `MULTI_COMPUTER_SETUP.md` for deployment walkthroughs.
- `scripts/README_TESTING.md` for deeper coverage of test scripts referenced later in this guide.

## Visual Cheat Sheet
- **High-level request flow** (follow the arrows to see how a query moves through the system):

```
Client → Gateway (A)
       ↘          ↙
    Leader B      Leader E
     ↓   ↘        ↙   ↓
    Worker C   Worker D   Worker F
```

- **Detailed sequence** (each step maps to a log message you will see during debugging):

```
1. Client sends Query(request_id)
2. Gateway records request_id in active_requests
3. Gateway issues InternalQuery to Leader B and Leader E
4. Leaders run local filters, then fan out to workers with InternalQuery
5. Workers return FireMeasurements to their leader
6. Leaders aggregate and reply to Gateway
7. Gateway slices results into QueryResponseChunk messages
8. Client receives chunks until the stream ends or is cancelled
```

- **Startup order** (keep a copy next to your terminals):

```
[1] ./build/server_c configs/process_c.json    (Worker C)
[2] ./build/server_d configs/process_d.json    (Worker D)
[3] ./build/server_f configs/process_f.json    (Worker F)
[4] python team_green/server_b.py configs/process_b.json  (Leader B)
[5] python team_pink/server_e.py configs/process_e.json    (Leader E)
[6] python gateway/server.py configs/process_a.json        (Gateway A)
```

- **Request lifecycle timeline** (useful when reading logs):

```
┌─────────────────────┬─────────────────────────────┐
│ Time                │ Event                       │
├─────────────────────┼─────────────────────────────┤
│ t0                  │ Gateway<Query> start        │
│ t0 + ε              │ active_requests entry added │
│ t0 + 1 RPC round    │ Leaders + workers respond   │
│ t0 + N chunk loops  │ Gateway streams chunks      │
│ t0 + completion     │ _mark_completed + cleanup   │
└─────────────────────┴─────────────────────────────┘
```

## System Topology
*Skim this diagram before reading code—understanding the traffic pattern makes the rest of the doc easier to digest.*
```
Python/C++ Clients (CLI, scripts)
          |
          v
   Process A Gateway (gateway/server.py, Python)
          |
    -----------------
    |               |
    v               v
Process B Leader   Process E Leader (Python)
(team_green)       (team_pink)
    | \             / |
    |  \           /  |
    v   v         v   v
Process C Worker  Process D Worker (shared)
(team_green, C++) (team_pink, C++)
                  Process F Worker (team_pink, C++)
```
- Clients use gRPC stubs generated from `proto/fire_service.proto`.
- Gateway A streams chunked responses, tracks status, and forwards internal queries to team leaders.
- Team leaders aggregate their own data plus worker responses before returning to the gateway.
- Workers host columnar partitions via `FireColumnModel` (Python or C++ implementations) to satisfy filters.

## Frameworks, Libraries, and Tooling
*Use this as a checklist when your environment fails to build or run; you do not need to memorize every dependency up front.*
- **gRPC + Protocol Buffers**
    - Core contract lives in `proto/fire_service.proto`.
    - Python stubs (`fire_service_pb2.py`, `fire_service_pb2_grpc.py`) are generated via `python -m grpc_tools.protoc` or `make proto`.
    - C++ stubs (`fire_service.grpc.pb.cc`, `fire_service.pb.cc`) are emitted by `protoc` during the CMake build; they work with the synchronous gRPC C++ API.
    - Streaming RPC semantics (`rpc Query(...) returns (stream QueryResponseChunk)`) let the gateway push chunks progressively while clients consume them as generators (`for chunk in stub.Query(...)`) or via `ClientReader::Read`.
- **Python packages** (`requirements.txt`)
    - `grpcio==1.76.0`: runtime transport and gRPC channel/server implementations.
    - `grpcio-tools==1.76.0`: protoc plugin for Python stubs.
    - `protobuf==6.33.0`: message serialization support.
    - `typing_extensions`, `setuptools`: tooling compatibility.
- **C++ third-party libraries**
    - gRPC C++ (linked via CMake) supplies `grpc::ServerBuilder`, `grpc::ServerContext`, and generated stubs.
    - `nlohmann::json`: lightweight JSON parsing for worker configuration files.
    - STL `<filesystem>`, `<chrono>`, `<map>`, `<set>` cover CSV discovery, timing, and indexing.
- **Build & automation**
    - `CMakeLists.txt` wires protobuf/gRPC targets, builds worker binaries (`server_c`, `server_d`, `server_f`) and the C++ client.
    - Root `Makefile` wraps workflows: `make proto`, `make venv`, `make servers`, `make run`, `make test`.
    - Shell scripts under `scripts/` orchestrate integration tests, performance benchmarks, and network smoke tests.
- **Data utilities**
    - Column store lives in `common/fire_column_model.py` and `common/FireColumnModel.cpp`.
    - CSV ingestion uses Python’s built-in `csv` module or the handcrafted C++ `CSVReader` to support multi-line quoted records.
- **Configuration**
    - JSON files in `configs/` (plus `configs/multi_computer/`) define process identity, ports, neighbors, and dataset partitions; no code changes are needed to rewire the topology—edit JSON and restart.

Whenever you modify `fire_service.proto`, rerun `make proto` (or the equivalent `python -m grpc_tools.protoc ...`) before rebuilding Python servers or C++ binaries.

## Process Inventory
| Process | Role            | Language | Module Path                              | Port  | Data Slice                     | Key Responsibilities |
|---------|-----------------|----------|------------------------------------------|-------|--------------------------------|----------------------|
| A       | Gateway leader  | Python   | `gateway/server.py`                      | 50051 | n/a                            | Handle client RPCs, stream chunks, coordinate teams, maintain request state. |
| B       | Team Green lead | Python   | `team_green/server_b.py`                 | 50052 | Aug 10-17 (config partition)   | Query local data, forward to Process C, aggregate totals. |
| C       | Team Green work | C++      | `team_green/server_c.cpp`                | 50053 | Aug 18-26                     | Serve `InternalQuery` using C++ `FireColumnModel`. |
| D       | Shared worker   | C++      | `team_pink/server_d.cpp`                 | 50054 | Aug 27-Sep 4                  | Serve requests from B and E, share partition. |
| E       | Team Pink lead  | Python   | `team_pink/server_e.py`                  | 50055 | Sep 5-13                      | Query local partition, forward to D and F. |
| F       | Team Pink work  | C++      | `team_pink/server_f.cpp`                 | 50056 | Sep 14-24                     | Serve `InternalQuery` for pink leader. |

Configuration JSON files under `configs/` wire process identity, ports, neighbors, and directory partitions.

## RPC API Surface
*Need just the contract? Start here. When you need implementation details, jump down to “API Deep Dive.”*
| RPC Method      | Direction            | Request Type             | Response Type           | Behavior |
|-----------------|----------------------|--------------------------|-------------------------|----------|
| `Query`         | Client -> Gateway    | `QueryRequest`           | stream `QueryResponseChunk` | Executes filtered query, streams chunked measurements, tracks cancellation and status. |
| `CancelRequest` | Client -> Gateway    | `StatusRequest`          | `StatusResponse`        | Marks request as cancelled; gateway stops streaming. |
| `GetStatus`     | Client -> Gateway    | `StatusRequest`          | `StatusResponse`        | Returns progress (chunks delivered, total chunks, status enum). |
| `InternalQuery` | Process -> Process   | `InternalQueryRequest`   | `InternalQueryResponse` | Used by leaders to query workers; workers respond with aggregated measurements. |
| `Notify`        | Process -> Process   | `InternalQueryRequest`   | `StatusResponse`        | Hook for async notifications (currently returns simple acknowledgment). |

### API Execution Flow & Algorithms
- **`Query` (client ➜ gateway)**: *Ingress*—client stubs stream a `QueryRequest` to Process A, which registers the `request_id` in `active_requests` with default status and timestamps. *Aggregation*—gateway forwards the filter to both team leaders via `InternalQuery`; leaders execute `_query_local_data` and call `forward_to_workers` so every worker runs the same filter across its partition-local `FireColumnModel`. *Chunking*—gateway concatenates leader payloads, derives chunk boundaries (`ceil(results / max_results_per_chunk)`), and streams `QueryResponseChunk` messages while `_update_chunks_sent` records progress and `_is_cancelled` plus `context.is_active()` guard against cancellations/disconnects. *Termination*—once chunks finish (or cancellation triggers an exit) the gateway invokes `_mark_completed`/`_mark_cancelled` and schedules `_cleanup_request` via `threading.Timer`.
- **`CancelRequest` (client ➜ gateway)**: *Lookup*—gateway finds the tracking record under `request_lock`. *State flip*—it sets `cancelled=True` and updates `status="cancelled"`, preserving `chunks_sent/total_chunks`; unknown IDs yield `status="not_found"`. *Propagation*—the streaming loop checks `_is_cancelled` before each chunk, aborting promptly while leaving delivered chunks intact.
- **`GetStatus` (client ➜ gateway)**: *Snapshot*—under lock the gateway retrieves `status`, `chunks_sent`, and `total_chunks` and returns them in `StatusResponse`. *Consistency*—because `_update_chunks_sent` runs after every emission, status polling sees monotonic progress; missing IDs respond with `status="not_found"` so clients detect expired handles.
- **`InternalQuery` (process ➜ process)**: *Leader pathway*—leaders accept the call, run `_query_local_data` which unions parameter/site/agency indices, intersects numeric/date/geo bounds, and converts surviving indices into `FireMeasurement` protos. *Worker fan-out*—leaders invoke `forward_to_workers`, where each worker repeats the filtering in C++ and appends its measurements. *Response*—leaders set `responding_process`, append all measurements, mark `is_complete=True`, and return; workers follow the same algorithm without fan-out. *Gateway placeholder*—Process A currently returns an empty response but retains the hook for future caching.
- **`Notify` (process ➜ process)**: Serves as a lightweight control-plane hook; every process logs the sender and replies with `status="acknowledged"`, keeping the RPC available for future health pings or cache invalidations.

## API Deep Dive
*Reference section for when you are modifying behavior—feel free to skim on your first pass and return when implementing changes.*
- **Query**
    - `gateway/server.py`: Registers inbound `request_id`, logs metadata, and stores lifecycle fields (`status`, `chunks_sent`, timers) inside `active_requests` guarded by `request_lock`. The gateway invokes `forward_to_team_leaders`, waits for both responses, flattens the returned `FireMeasurement` protos, and chunks them using the requested `max_results_per_chunk`. During streaming, the gateway enforces cancellation (`_is_cancelled`), client liveness (`context.is_active()`), and updates telemetry counters (`_update_chunks_sent`). Workload: CPU spent on serialization and slicing; memory pressure determined by combined leader payload size (worst-case accumulates all measurements before chunking).
    - `team_green/server_b.py` and `team_pink/server_e.py`: Leaders execute `_query_local_data`, which leverages the Python column store to assemble candidate indices, intersect numeric/date/geospatial constraints, and build `FireMeasurement` protos in-memory. They then run `forward_to_workers`, which issues synchronous `InternalQuery` calls to each neighbor, concatenating payloads and logging per-worker counts. No chunking occurs at this tier; leaders return the entire aggregated list to the gateway. Workload: dominated by filter evaluation and proto construction; latency proportional to worker RPC round-trips.
    - C++ workers (`team_green/server_c.cpp`, `team_pink/server_d.cpp`, `team_pink/server_f.cpp`): `InternalQuery` handler reads the generated `InternalQueryRequest`, performs lookups against unordered_map indices, filters via loops over vectorized columns, and uses generated setters (e.g., `measurement->set_site_name(...)`) to populate responses. Because workers serve a single partition, they do not forward further. Workload: compute-bound filtering plus message serialization; memory reuse through vector storage reduces allocations.
- **CancelRequest**
    - `gateway/server.py`: Only component with actionable cancellation. It acquires `request_lock`, flips `cancelled` and `status`, and reports the latest `chunks_sent`/`total_chunks`. The streaming loop checks `_is_cancelled` prior to yielding each chunk, ensuring minimum extra work after cancellation. Workload: trivial (dictionary mutation + log statements) but critical for responsiveness; designed to be O(1) per call.
    - Leaders/workers: Stubs return canned acknowledgements. Future enhancements could propagate cancellation downstream by interrupting ongoing worker RPCs or bypassing local filtering.
- **GetStatus**
    - `gateway/server.py`: Reads `active_requests` under lock, returning a snapshot of progress fields. Accuracy depends on the streaming path calling `_update_chunks_sent` right after each chunk send. Workload: O(1) lookups; scales with number of concurrent tracked requests.
    - Leaders/workers: Return static responses (`status="untracked"` or similar), signaling readiness to implement richer reporting later without violating proto contracts.
- **InternalQuery**
    - `gateway/server.py`: Currently a placeholder returning an empty response; serves as hook for potential gateway-side caching or loopback queries when Process A hosts data.
    - Leaders: Translate the filter, perform local aggregation, then iterate over workers. They set `responding_process`, `is_complete`, and attach every measurement gathered. Leaders effectively serve as reducers in a map-reduce pattern, shouldering the workload of combining multiple worker payloads.
    - Workers: Pure filter engines. They do not maintain per-request state beyond the call stack, enabling high concurrency via gRPC thread pools. Returning full batches keeps logic simple but requires the gateway to manage chunking.
- **Notify**
    - All processes: Log the notification (useful for debugging) and return `status="acknowledged"`. With no additional logic, the workload is negligible. The RPC is intentionally lightweight so future adoption (e.g., cache invalidations, health heartbeats, or back-pressure signals) can reuse the channel without interface changes.

### Message Schemas
- `QueryRequest`: includes `request_id`, `QueryFilter`, `query_type` strings, chunk preferences.
- `QueryFilter`: supports site, agency, parameter lists; bounding boxes; time ranges; concentration/AQI thresholds.
- `QueryResponseChunk`: chunk metadata (`chunk_number`, `is_last_chunk`, counts) and repeated `FireMeasurement` payloads.
- `InternalQueryRequest/Response`: replicate filter info while tracking originating process for routing replies.
- `StatusRequest/Response`: `action` nouns (`cancel`, `status`, etc.) and progress metrics.

## Module, Class, and Function Reference
*Treat this like an encyclopedia—look up a module when you need specifics rather than reading linearly.*

### proto/fire_service.proto (Contract Source of Truth)
- Declares package `fire_service`, all message types, and the `FireQueryService` RPCs.
- Messages such as `FireMeasurement`, `QueryFilter`, `QueryRequest`, `QueryResponseChunk`, `InternalQueryRequest`, `InternalQueryResponse`, `StatusRequest`, and `StatusResponse` are shared by every process.
- Streaming RPC definitions drive implementation details in both Python and C++ servers.
- Generated files:
    - Python: `proto/fire_service_pb2.py` (messages) and `proto/fire_service_pb2_grpc.py` (service base classes and stubs).
    - C++: `proto/fire_service.pb.h/cc` and `proto/fire_service.grpc.pb.h/cc` (headers/sources compiled into each binary).
- When regenerating stubs, ensure both Python and C++ artifacts are refreshed; stale bindings produce runtime incompatibilities, especially if field numbers change.

### proto/fire_service_pb2.py and fire_service_pb2_grpc.py (Generated Python Bindings)
- `fire_service_pb2.py`: Auto-generated dataclasses for every message in the proto. Encodes/decodes protobuf wire format, exposes typed accessors (e.g., `QueryRequest.filter`) and helper constructors. No business logic—treat as serialization backbone accessed by all Python services and clients.
- `fire_service_pb2_grpc.py`: Supplies base service classes (`FireQueryServiceServicer`) and client stubs (`FireQueryServiceStub`). Gateway/leaders inherit from the servicer to implement RPC bodies; clients instantiate the stub to invoke RPCs. Includes registration helpers (`add_FireQueryServiceServicer_to_server`). Regenerated alongside `fire_service_pb2.py`.

### proto/fire_service.pb.h/.cc and fire_service.grpc.pb.h/.cc (Generated C++ Bindings)
- `fire_service.pb.h/.cc`: Provide C++ message classes, getters/setters, serialization methods, and type registration. C++ workers and client link against these to build `QueryRequest`, `FireMeasurement`, etc.
- `fire_service.grpc.pb.h/.cc`: Define the C++ service interface (`FireQueryService::Service`) and client stub (`FireQueryService::Stub`). Workers derive from the service to implement RPC handlers; the C++ client uses the stub to issue synchronous calls. Rebuild via CMake after proto changes to keep signatures aligned.

### proto/__init__.py
- Declares the `proto` directory as a Python package and optionally re-exports generated modules. Keeps `import proto.fire_service_pb2` working across the repo. Update exports if new protos are added.

### gateway/server.py (Process A Gateway)
**Class `FireQueryServiceImpl`**
- `__init__(config)`: Flow—loads JSON config, extracts identity/role/neighbor list, and initializes `active_requests` (dict) plus `request_lock` (threading.Lock). Algorithm—store per-request lifecycle template (`status='idle'`, counters zeroed) and pre-log topology to aid debugging. Structures—`active_requests` maps `request_id -> {status, start_time, chunks_sent, total_chunks, cancelled}`.
- `Query(request, context)`: Flow—register request, fetch measurements, stream chunks, tear down state. Algorithm—(1) create tracking entry; (2) call `forward_to_team_leaders` to collect all measurements, catching exceptions and early-cancels; (3) compute `max_per_chunk` with safe default, derive `total_chunks` via ceiling division; (4) in a for-loop per chunk: check `_is_cancelled`, ensure `context.is_active()`, slice measurement list, build `QueryResponseChunk`, send via `yield`, call `_update_chunks_sent`, sleep 10 ms to showcase chunking; (5) mark success/failure and spawn timer for cleanup. Structures—relies on Python lists for aggregated measurements and repeated protobuf fields for chunk payloads.
- `forward_to_team_leaders(request)`: Flow—iterate neighbors, dial gRPC channel, send `InternalQuery`, collect responses. Algorithm—build `InternalQueryRequest` mirroring client filter, set 100 MB gRPC limits, call `stub.InternalQuery`, extend `all_measurements` with returned list, log counts, close channel, swallow `grpc.RpcError` after logging to maintain partial progress. Structures—uses neighbor descriptors (`hostname`, `port`, `process_id`) from config.
- `CancelRequest(request, context)`: Flow—lookup tracking entry under lock, toggle cancellation, respond with metrics. Algorithm—set `cancelled=True`, `status='cancelled'`, preserve `chunks_sent`/`total_chunks` for client telemetry; if missing, respond `status='not_found'`. Structures—mutates same dict entry used by `Query` and `GetStatus`.
- `GetStatus(request, context)`: Flow—read tracking entry and surface progress. Algorithm—under lock, fetch `status`, `chunks_sent`, `total_chunks`; default to `not_found` when entry absent (e.g., already cleaned). Structures—read-only access to `active_requests` snapshot.
- `InternalQuery(request, context)`: Flow—log caller and respond immediately. Algorithm—construct empty `InternalQueryResponse`, copy IDs, set `responding_process=self.process_id`, `is_complete=True`; intended to be fleshed out when Process A owns data. Structures—no local data scanned today.
- `Notify(request, context)`: Flow—log sender, return acknowledgement. Algorithm—build `StatusResponse(status='acknowledged')`; lightweight placeholder for future orchestration signals.
- `_is_cancelled(request_id)`: Flow—look up flag under lock and return boolean. Algorithm—ensures streaming loop exits quickly without mutating state; returns False if request missing (already cleaned/completed).
- `_mark_cancelled`, `_mark_completed`, `_mark_failed`: Flow—update `status` field safely. Algorithm—each helper checks entry existence before mutating; called from streaming/cancellation paths to keep status consistent for `GetStatus`.
- `_update_chunks_sent(request_id, chunks_sent)`: Flow—store last emitted chunk index. Algorithm—single dictionary assignment under lock so status polling reflects progress even mid-stream.
- `_cleanup_request(request_id)`: Flow—scheduled via `threading.Timer(60s)` to evict completed/cancelled entries. Algorithm—under lock, delete dict key if still present; logs cleanup action to aid tracing.

**Module-level helpers**
- `load_config(config_path)`: Flow—open file, `json.load`, return dict. Algorithm—raises on IO/parse error, letting caller fail fast; centralizes config parsing for reuse in tests.
- `serve(config_path)`: Flow—load config, instantiate service, create `grpc.server` with `ThreadPoolExecutor(max_workers=10)`, register servicer, bind to host:port, start, block on `wait_for_termination`. Algorithm—ensures service lifetime managed via gRPC server object; logs startup metadata. Structures—uses generated registration helper.
- `main`: Flow—validate CLI args, call `serve`, handle usage errors. Algorithm—expects exactly one argument (config path); prints usage and exits non-zero otherwise.
- Runtime environment: uses `grpc.server(futures.ThreadPoolExecutor(max_workers=10))`, so each incoming RPC runs on its own thread—hence the need for `request_lock` when touching `active_requests`.

### team_green/server_b.py (Process B Leader)
**Class `FireQueryServiceImpl`**
- `__init__(config)`
    - Flow—log identity, neighbors, and partition settings; instantiate `FireColumnModel`; call `read_from_directory` with `config['data_partition']['directories']` (if enabled); emit local measurement counts so the gateway can anticipate load.
    - Algorithm—builds a neighbor cache directly from config (no network discovery) and reuses the shared column model ingestion pipeline.
    - Work—single-shot boot action; failure to locate data still results in a serving process but with empty results.
- `Query(request, context)`
    - Flow—acts as a defensive RPC when a client bypasses the gateway; yields one `QueryResponseChunk` with zero results and `is_last_chunk=True`.
    - Algorithm—no filtering; returns immediately while still speaking the streaming API the proto requires.
    - Work—constant time regardless of dataset size.
- `InternalQuery(request, context)`
    - Flow—gateway fan-out entry point; logs request metadata, calls `_query_local_data` to cover Process B’s partition, then invokes `forward_to_workers` to fan the query to C (and any future neighbors) before combining all measurements into a single `InternalQueryResponse`.
    - Algorithm—performs sequential aggregation: local evaluation, remote fetch loop, metadata population (`responding_process`, `is_complete=True`).
    - Work—dominant cost is the column scan plus network time; response size scales with total matches.
- `_query_local_data(request)`
    - Flow—interprets the `QueryFilter`: seed with union of parameter/site hits (OR semantics), then narrow using AQI thresholds (AND semantics). When no filter is provided it returns the entire partition.
    - Algorithm—leverages column-model inverted indexes for parameter/site lookup, then iterates candidate indices to enforce numeric predicates before materializing `FireMeasurement` protos.
    - Work—bounded by candidate count; uses Python list iteration but avoids duplicate conversions by set union.
- `forward_to_workers(request)`
    - Flow—walks neighbor list from config (currently C), constructs gRPC channels with 100 MB send/receive caps, calls each worker’s `InternalQuery`, concatenates returned measurements, and closes the channel.
    - Algorithm—serial RPC loop with best-effort error handling (logs failures, continues to next neighbor).
    - Work—O(number of neighbors) network round-trips plus proto merges; no threading.
- `CancelRequest`, `GetStatus`, `Notify`
    - Flow—surface-level control plane hooks that respond with canned `StatusResponse` objects; they keep the proto contract even though Process B does not yet track per-request state.
    - Algorithm—straight-line construction of acknowledgements.
    - Work—constant time handlers suitable for future extension.

**Module helpers**
- `load_config(config_path)` is a straightforward JSON loader mirroring the gateway utility.
- `serve(config_path)` boots a `ThreadPoolExecutor` gRPC server, registers `FireQueryServiceImpl`, binds to the configured address, and blocks on `wait_for_termination` while printing lifecycle hints.
- `main` validates CLI arguments and delegates to `serve`.
- gRPC client channels consistently set `grpc.max_receive_message_length` and `grpc.max_send_message_length` to 100 MB so large measurement batches never truncate mid-flight.

### team_pink/server_e.py (Process E Leader)
Python twin of Process B adapted for the pink partition.
- `__init__(config)`
    - Flow—load Sep 5–13 directories into `FireColumnModel`, capture neighbor metadata (shared worker D, exclusive worker F), and print dataset inventory for observability.
    - Algorithm—identical ingestion path to Process B but isolates a different whitelist of directories.
    - Work—startup cost proportional to pink data volume.
- `Query` mirrors the defensive behavior of Process B, yielding an empty chunk when invoked directly.
- `InternalQuery`
    - Flow—perform `_query_local_data`, then branch to `forward_to_workers` which iterates over both neighbors; combines local + D + F results and marks response metadata.
    - Algorithm—same sequential aggregation pattern as Process B but with two remote hops.
    - Work—scales with pink partition size plus results from both workers.
- `_query_local_data` interprets filters identically to Process B but operates on pink data files.
- `forward_to_workers`
    - Flow—handles neighbor list in config order (D then F), creates oversized gRPC channels, invokes each `InternalQuery`, merges results, and logs per-neighbor counts while allowing subsequent neighbors to proceed even after individual RPC failures.
    - Algorithm—serial RPC loop with try/except around each call.
    - Work—O(number of neighbors) RPC effort; duplicates suppressed earlier by relying on underlying worker partitions.
- `CancelRequest`, `GetStatus`, `Notify`, `load_config`, `serve`, `main`, and the 100 MB gRPC option policy are all equivalent to Process B.

### team_green/server_c.cpp (Process C Worker)
**Class `FireQueryServiceImpl`**
- Constructor
    - Flow—parse JSON config, record identity/role/team, build list of allowed subdirectories, and call `FireColumnModel::readFromDirectory` to load the worker’s partition.
    - Algorithm—relies on `nlohmann::json` accessors and the C++ column model ingestion path; emits measurement counts for confirmation.
    - Work—startup-only cost dominated by CSV ingestion.
- `Status Query(...)`
    - Flow—acts as a defensive stub when a client calls a worker directly; streams one empty `QueryResponseChunk` via `ServerWriter`.
    - Algorithm—populate chunk fields with zeros and `is_last_chunk=true`, return `Status::OK`.
    - Work—constant time regardless of dataset.
- `Status InternalQuery(...)`
    - Flow—log request metadata, build candidate indices using parameter/site OR semantics, narrow with AQI bounds, then append each matching row to `InternalQueryResponse::add_measurements`; set request/response identifiers and mark `is_complete=true`.
    - Algorithm—uses `std::set` to deduplicate parameter lookups, vector iteration for AQI filters, and direct column accessors to populate protos.
    - Work—bounded by number of candidate indices; avoids duplicate allocations by reserving inside the protobuf repeated field.
- `Status CancelRequest(...)`, `Status GetStatus(...)`, `Status Notify(...)`
    - Flow—emit canned `StatusResponse` acknowledgements to preserve API coverage.
    - Algorithm—straight-line assignments; Notify simply logs sender identity before acknowledging.
    - Work—constant-time guardrails for future lifecycle tracking.

**Supporting functions**
- `load_config` wraps `std::ifstream` + `nlohmann::json` parsing, throwing on missing files to fail fast.
- `RunServer` constructs `FireQueryServiceImpl`, registers it with `ServerBuilder`, binds to the configured host:port, and blocks on `server->Wait()` while printing startup hints.
- `main` handles CLI validation and prints runtime errors before exiting non-zero.

### team_pink/server_d.cpp (Process D Worker)
Shared C++ worker consumed by both teams.
- Constructor loads the Aug 27–Sep 4 slice into `FireColumnModel`, remembers both Process B and Process E as neighbors (for logging), and reports readiness.
- `InternalQuery`
    - Flow—identical to Process C’s filtering pipeline; populates `responding_process="D"` so leaders can attribute results when aggregating shared data.
    - Algorithm—delegates to the same column-model helpers; agnostic to the caller.
    - Work—scales with the shared partition size.
- `Query`, `CancelRequest`, `GetStatus`, `Notify` replicate Process C’s defensive stubs to keep the gRPC surface uniform.
- `RunServer`, `load_config`, `main` reuse the same wiring; only default config paths differ.

### team_pink/server_f.cpp (Process F Worker)
Exclusive C++ worker for Process E.
- Constructor loads Sep 14–24 directories and records that only Process E is a neighbor, enabling minimal logging while still printing measurement inventory.
- `InternalQuery`
    - Flow—same filtering stages as Processes C/D but returns `responding_process="F"` to keep leader attribution accurate.
    - Algorithm—reuses column-model accessors; no special casing beyond metadata.
    - Work—bounded by F’s data partition size.
- Remaining RPCs (`Query`, `CancelRequest`, `GetStatus`, `Notify`) mirror Process C’s guards, ensuring API parity.
- Support functions (`load_config`, `RunServer`, `main`) stay copy-equivalent aside from config defaults, delivering operational parity across workers.

### common/fire_column_model.py (Python Column Model)
**Class `FireColumnModel`**
- `__init__`: Flow—set up empty Python lists for every measurement column (site, parameter, concentration, longitude, etc.), initialize lookup dicts mapping keys to list of row indices, and seed metadata (min/max lat/lon, datetime bounds) with sentinel state. Algorithm—ensures every column stays position-aligned (index `i` across all lists represents one measurement). Structures—lists for each attribute plus dicts keyed by `site_name`, `parameter`, `aqs_code`, `agency`, enabling O(1) index lookups.
- `read_from_directory(directory_path, allowed_subdirs=None)`: Flow—invoke `_get_csv_files` to enumerate CSVs (respecting optional whitelist), iterate files, call `read_from_csv` for each, accumulate counts. Algorithm—wraps IO errors per file to keep ingestion resilient; returns aggregate inserted row count for logging. Structures—delegates file discovery to `_get_csv_files` which returns deterministic sorted list.
- `read_from_csv(filename)`: Flow—open CSV, skip header, for each row parse into typed fields, call `insert_measurement`; count successes/failures. Algorithm—casts numeric fields (`float`, `int`) with fallback, normalizes strings (strip whitespace), and guards against malformed rows using try/except. Structures—local tuple representing measurement passed to `insert_measurement`.
- `insert_measurement(...)`: Flow—append column values to parallel lists, update lookup dicts, recompute metadata. Algorithm—push value into each list, call `_update_indices` to maintain inverted indexes, `_update_geographic_bounds` for lat/lon, `_update_datetime_range` for time, update `unique_*` sets. Structures—`self.index` increments sequentially, ensuring consistent row IDs returned to query evaluators.
- `get_indices_by_site(site_name)`, `get_indices_by_parameter(parameter)`, `get_indices_by_aqs_code(aqs_code)`: Flow—retrieve list of row indices for the key or empty list. Algorithm—dict lookups with `.get(key, [])` to avoid KeyError; supports OR semantics by concatenating results. Structures—lists are stored as direct references so no copying occurs unless caller modifies them.
- `measurement_count()`, `site_count()`, `unique_sites()`, `unique_parameters()`, `unique_agencies()`, `datetime_range()`, `geographic_bounds()`: Flow—return snapshot metrics derived from cached fields. Algorithm—constant-time operations; no recomputation. Structures—`datetime_range` returns tuple `(min_datetime, max_datetime)`, geospatial bounds return dictionary or tuple (depending on implementation) reflecting latest min/max.
- `_update_indices(index)`: Flow—append newly inserted index to all relevant lookup dicts (`site`, `parameter`, `aqs`, `agency`). Algorithm—`dict.setdefault(key, []).append(index)` pattern ensures creation on first occurrence. Structures—keeps inverted indexes in sync with column vectors.
- `_update_geographic_bounds(latitude, longitude)`: Flow—compare new coordinate against stored min/max floats, expand bounds when necessary. Algorithm—handles first insert by assigning values when sentinel `None` present; subsequent inserts cast to float for comparisons. Structures—stores bounds as `[min_lat, max_lat, min_lon, max_lon]` (exact format as in code).
- `_update_datetime_range(datetime)`: Flow—update earliest/latest timestamp strings using lexical comparisons (ISO 8601 ensures lexical order equals chronological order). Algorithm—if stored min/max are `None` or new value < min / > max, assign accordingly.
- `_get_csv_files(...)`: Flow—walk directory tree (using `os.walk`), optionally filter by `allowed_subdirs`, collect `*.csv`, sort stable order, return list. Algorithm—logs missing directories/warnings, filters hidden files if needed, ensures deterministic ingestion order for reproducibility.

### common/FireColumnModel.cpp and FireColumnModel.hpp (C++ Column Model)
- Constructor/Destructor: Flow—allocate vectors (`sites_`, `parameters_`, `aqi_`, etc.), `std::unordered_map` indexes for site/parameter/AQS, and metadata containers (min/max lat/lon). Algorithm—sets sentinel flags (e.g., `boundsInitialized_ = false`) to guard future updates; destructor relies on RAII for container cleanup.
- `readFromDirectory(const std::string& directoryPath, const std::vector<std::string>& allowedSubdirs)`: Flow—call `getCSVFiles` to enumerate allowed CSV paths, iterate list, call `readFromCSV` for each, track total inserted rows. Algorithm—short-circuits on missing directory by returning 0 and logging; ensures deterministic processing order by sorting paths inside `getCSVFiles`.
- `readFromCSV(const std::string& filename)`: Flow—instantiate `CSVReader`, open file, consume rows until EOF, transform each row into typed fields (dates, floats, ints), call `insertMeasurement`. Algorithm—skips header line, uses helper conversions (`parseLongOrZero`) to tolerate malformed numbers, wraps per-row parse in try/catch to continue on errors.
- `insertMeasurement(...)`: Flow—append every value to associated `std::vector`, update inverted indexes via `updateIndices`, refresh metadata using `updateGeographicBounds`/`updateDatetimeRange`, and increment aggregate counters. Algorithm—keeps all vectors the same length to maintain positional indexing; uses emplace_back for minimal copies.
- `getIndicesBySite`, `getIndicesByParameter`, `getIndicesByAqsCode`: Flow—return const reference to vector of indices for given key or static empty vector when key absent. Algorithm—avoids allocations by returning `static const std::vector<int64_t> empty;` reference; callers treat as read-only to preserve integrity.
- `getGeographicBounds(double& minLat, double& maxLat, double& minLon, double& maxLon)`: Flow—if bounds initialized, copy stored doubles into out params and return `true`; otherwise leave params untouched and return `false`. Algorithm—supports worker RPC path that needs to guard usage when dataset empty.
- `getDatetimeRange(std::string& minTs, std::string& maxTs)`: (if present) Flow—similar to geographic bounds; copies ISO strings into output arguments.
- `updateIndices`, `updateGeographicBounds`, `updateDatetimeRange`, `getCSVFiles`: Flow—internal helpers called from ingestion. Algorithm—`updateIndices` pushes new row index into all relevant unordered_map vectors; `updateGeographicBounds` toggles `boundsInitialized_` and adjusts min/max; `updateDatetimeRange` compares lexicographically; `getCSVFiles` recurses directories using `std::filesystem::recursive_directory_iterator`, filters by whitelist, collects `.csv`, sorts for deterministic order.

### common/utils.hpp / utils.cpp
- `parseLongOrZero(const std::string& s)`: Flow—attempt to convert string to `long long` using `std::stoll`, catch `std::invalid_argument`/`std::out_of_range`, and return 0 on failure. Algorithm—supports tolerant parsing during CSV ingestion; avoids throwing exceptions outward.
- `timeCall(std::function<void()> f)`: Flow—capture `start = high_resolution_clock::now()`, execute `f()`, capture end time, return duration in microseconds. Algorithm—provides lightweight instrumentation for worker hot paths without external dependencies.
- `timeCallMulti(std::function<void()> f, int runs)`: Flow—loop `runs` times, call `timeCall(f)` each iteration, push result into vector, return vector. Algorithm—enables callers to compute aggregates (mean, std dev) for benchmarking; gracefully handles `runs <= 0` by returning empty vector.
- `mean(const std::vector<double>& v)`: Flow—sum elements using `std::accumulate`, divide by `v.size()` when non-zero. Algorithm—returns 0.0 when vector empty to sidestep divide-by-zero; used in performance reporting.

### common/readcsv.hpp / readcsv.cpp
 `CSVReader::CSVReader(path, delimiter, quote, comment)`: Flow—store file path, delimiter characters, quote character, and optional comment prefix; initialize internal buffers. Algorithm—supports configurable CSV dialects; defaults match dataset format.
 `open()`: Flow—attempt to open `std::ifstream` with `std::ios::in`, throw descriptive `std::runtime_error` if open fails. Algorithm—called per file before streaming rows; ensures exceptions caught by caller.
 `close()`: Flow—check `ifstream::is_open`, close if true. Algorithm—allows deterministic resource cleanup.
 `readRow(std::vector<std::string>& out)`: Flow—clear `out`, call `readPhysicalRecord` to assemble a full logical line (merging multi-line quoted text), then `splitRecord` to tokenize fields respecting quotes and comment markers; return `false` on EOF. Algorithm—handles escaped delimiters inside quotes, strips trailing carriage returns, skips comment-only lines by recursing until a data row is found.
 `readPhysicalRecord` and `splitRecord`: Flow—`readPhysicalRecord` appends lines until balanced quote counts achieved; `splitRecord` iterates characters, tracking quote state, building current field, and pushing to `out` when delimiter or end-of-line encountered. Algorithm—ensures multi-line values and embedded delimiters are parsed correctly—a necessity for the provided EPA datasets.

### client/test_client.py
- `test_query(stub)`: Flow—assemble `QueryRequest` with PM2.5 filter, invoke `stub.Query`, iterate generator, print chunk metadata. Algorithm—counts records per chunk, samples first measurement to validate schema, catches `grpc.RpcError` for diagnostics. Structures—uses deterministic `request_id` to match later status calls.
- `test_get_status(stub)`: Flow—reuse `request_id`, send `StatusRequest(action='status')`, print returned `status`, `chunks_delivered`, `total_chunks`. Algorithm—demonstrates expectation that gateway computes chunk totals lazily; handles missing request by reporting `not_found`.
- `test_cancel_request(stub)`: Flow—issue `CancelRequest` via `StatusRequest(action='cancel')`, print status. Algorithm—intended to be run while `test_query` in progress; shows cancellation acknowledgment path even if request already complete.
- `main()`: Flow—open insecure channel to `localhost:50051`, instantiate `FireQueryServiceStub`, sequentially call `test_query`, `test_get_status`, `test_cancel_request`. Algorithm—wraps in `try/except grpc.RpcError` to surface connection failures; demonstrates proper channel cleanup via `channel.close()` on exit.
- Demonstrates idiomatic Python gRPC client usage (channel creation, stub instantiation, consuming streaming responses); treat it as the minimal reproducible example when onboarding new developers.

### client/README_CPP_CLIENT.md
- Step-by-step instructions for configuring a C++ build environment, running `cmake`/`make`, and executing the compiled client example. Serves as the companion document for developers extending the native client.

### configs/process_a.json … process_f.json (Single-Computer Topology)
- Structure: Each JSON describes a process with keys `identity`, `role`, `team`, `hostname`, `port`, `neighbors` (array of `{process_id, hostname, port}`), and `data_partition` specifying directories to load. Flow—process loads config at startup via `load_config`, binds to declared host:port, and records neighbor endpoints for RPC fan-out. Algorithm—data partitions enforce disjoint directory ownership (e.g., B: `20200810-20200817`, C: `20200818-20200826`). Gateway (`process_a.json`) omits `data_partition` because it holds no data.
- Usage: Update hostnames/ports when deploying across machines, keeping neighbor IDs consistent. Workers/leaders rely on identical structure, allowing homogeneous loader functions (Python & C++).

### configs/multi_computer/*.json (Deployment Templates)
- `two_computer_template.json` / `three_computer_template.json`: Provide example mappings of processes to physical hosts with placeholder hostnames (`MACHINE_1`, etc.) and port allocations. Flow—copy template, adjust hostnames/IPs per target environment, then distribute tailored configs to each machine. Algorithm—preserves neighbor graph while modifying transport endpoints, ensuring cross-host gRPC connections remain valid. Useful for staging distributed deployments without editing single-machine configs in-place.

## Supported Query Filters & Data Partitioning

### Query Capabilities
Client requests populate `QueryFilter` (defined in `fire_service.proto`), enabling combinations of:
- **Parameters** (`parameters` repeated field): OR semantics—results include measurements whose pollutant/metric matches any supplied parameter (e.g., `PM2.5`, `PM10`, `OZONE`, `NO2`, `SO2`, `CO`).
- **Site names** (`site_names` repeated field): OR semantics—restricts results to specific monitoring locations.
- **AQS codes / Agency names** (`aqs_codes`, `agency_names`): additional identifiers for site-based filtering.
- **Geospatial bounds** (`min_latitude`, `max_latitude`, `min_longitude`, `max_longitude`): rectangular bounding box on latitude/longitude.
- **Datetime window** (`min_datetime`, `max_datetime`): ISO timestamp strings bounding measurement timestamps.
- **Concentration/AQI ranges** (`min_concentration`, `max_concentration`, `min_aqi`, `max_aqi`): numeric lower/upper bounds; filters combine with other criteria using AND semantics.

Workers interpret filters as follows:
1. Start from parameter/site list (if provided) to build candidate indices (OR logic within the list).
2. Apply numeric/date/geographic bounds (AND logic) to refine matches.
3. Construct `FireMeasurement` protos for indices that satisfy all provided constraints.

Leaders aggregate local matches using the same logic before forwarding to workers, and the gateway preserves `query_type` for future extension (current implementation treats non-empty filters uniformly).

### Data Separation Across Processes
CSV data resides under `data/` and is partitioned by date-oriented subdirectories (e.g., `20200810` through `20200924`). Each process loads a distinct slice via `data_partition.directories` in its config:

| Process | Directories (inclusive) | Date Range |
|---------|-------------------------|------------|
| B (Team Green leader) | `20200810` – `20200817` | Aug 10–17 |
| C (Team Green worker) | `20200818` – `20200826` | Aug 18–26 |
| D (Shared worker)     | `20200827` – `20200904` | Aug 27–Sep 4 |
| E (Team Pink leader)  | `20200905` – `20200913` | Sep 5–13 |
| F (Team Pink worker)  | `20200914` – `20200924` | Sep 14–24 |

- **No overlap**: Partitions are mutually exclusive, ensuring each measurement is owned by exactly one process.
- **Leaders act as workers**: Processes B and E load their own subsets and can satisfy queries without contacting downstream workers when filters stay within their date ranges.
- **Gateway**: Process A does not load data; it coordinates requests/responses and can be extended to cache results if needed.
- **Multi-machine deployment**: These partitions inform which directories must be present on each host when distributing processes across computers.

### Example Query Scenarios
```python
# Python client: moderate AQI PM2.5 query with chunking
request = fire_service_pb2.QueryRequest(
    request_id=12345,
    filter=fire_service_pb2.QueryFilter(
        parameters=["PM2.5"],
        min_aqi=0,
        max_aqi=100
    ),
    query_type="filter",
    require_chunked=True,
    max_results_per_chunk=500
)
for chunk in stub.Query(request):
    print(f"Chunk {chunk.chunk_number + 1}/{chunk.total_chunks} -> {len(chunk.measurements)} measurements")
```

```python
# Python client: geographic + datetime window across teams
request = fire_service_pb2.QueryRequest(
    request_id=67890,
    filter=fire_service_pb2.QueryFilter(
        min_latitude=37.0,
        max_latitude=39.0,
        min_longitude=-123.0,
        max_longitude=-121.0,
        min_datetime="2020-09-01T00:00:00",
        max_datetime="2020-09-10T23:59:59"
    ),
    query_type="filter",
    require_chunked=True,
    max_results_per_chunk=1000
)
response_stream = stub.Query(request)
```

```cpp
// C++ client: combine parameter and site filters
QueryRequest request;
request.set_request_id(22222);
request.set_query_type("filter");
request.set_require_chunked(true);
request.set_max_results_per_chunk(250);

QueryFilter* filter = request.mutable_filter();
filter->add_parameters("OZONE");
filter->add_parameters("NO2");
filter->add_site_names("Oakland West");
filter->set_min_aqi(50);
filter->set_max_aqi(150);

ClientContext context;
auto reader = stub_->Query(&context, request);
QueryResponseChunk chunk;
while (reader->Read(&chunk)) {
    std::cout << "Received chunk " << chunk.chunk_number() + 1
              << " with " << chunk.measurements_size() << " records\n";
}
```

```bash
# CLI: run performance test with specific chunk size
python scripts/performance_test.py --server 192.168.1.10:50051 --output results/scenario_pm25.json
```

### client/advanced_client.py
- Class `ProgressTracker`: Flow—initialized with `request_id`, records `start_time`, counters, flags. Algorithm—`update(chunk)` increments chunk counters and measurement tally; `display()` renders 40-character progress bar with Unicode blocks, deriving percent complete from `chunks_received/total_chunks`; `finish()` calls `display()` then prints newline. Structures—stores aggregate metrics to summarize stream after completion/cancellation.
- `test_chunked_streaming(stub, chunk_size)`: Flow—build filtered `QueryRequest`, instantiate `ProgressTracker`, iterate `stub.Query`, update tracker, sleep 50 ms per chunk for readability, and conclude with summary. Algorithm—validates progressive delivery by tracking chunk and measurement counts; handles `grpc.RpcError` to report transport failures.
- `test_cancellation(stub, chunk_size, cancel_after_chunks)`: Flow—issue broad query (no filters) to maximize results, update tracker per chunk, invoke `CancelRequest` once `chunks_received` hits threshold, set `tracker.cancelled`, and break loop. Algorithm—demonstrates control path by verifying gateway acknowledges cancellation and halts stream; prints pre-cancel progress metrics.
- `test_status_tracking(stub)`: Flow—spawn daemon thread polling `GetStatus` every 0.5 s while main thread consumes stream; store snapshots in list, join thread after streaming completes. Algorithm—shows asynchronous status monitoring by correlating `status_resp.chunks_delivered` with tracker metrics; handles thread shutdown via `Event`.
- `test_small_chunks(stub)`: Flow—construct request with tiny `max_results_per_chunk` (100), iterate stream with small sleep, emphasize high chunk counts, and print totals afterward. Algorithm—stress-tests chunk boundary logic and ensures tracker handles many small responses.
- `main()`: Flow—connect to gateway (`localhost:50051`), create stub, call each test with 1-second pauses, close channel, summarize outcomes. Algorithm—wraps invocation in `try/except` to surface connectivity issues, prints deployment checklist on failure, handles `KeyboardInterrupt` gracefully.

### client/client.cpp
- `FireQueryClient` constructor: Flow—store channel pointer, create synchronous stub via `FireQueryService::NewStub(channel)`. Structures—owns `std::unique_ptr<FireQueryService::Stub>` used by all member methods.
- `Query(int64_t request_id, const std::vector<std::string>& parameters, int min_aqi, int max_aqi)`: Flow—populate `QueryRequest` (add parameters, set AQI bounds, request chunking), call `stub_->Query` to obtain `std::unique_ptr<ClientReader<QueryResponseChunk>>`, loop on `reader->Read(&chunk)` to stream results. Algorithm—tracks chunk index, prints metadata and first measurement from each chunk, handles end-of-stream by calling `reader->Finish()` and reporting grpc status.
- `GetStatus(int64_t request_id)`: Flow—populate `StatusRequest` with `action="status"`, call `stub_->GetStatus`, print status text and chunk counters. Algorithm—demonstrates synchronous unary RPC invocation, handling `grpc::Status` failure by logging error code/message.
- `CancelRequest(int64_t request_id)`: Flow—send `StatusRequest(action="cancel")`, print gateway response. Algorithm—mirrors Python demo, capturing partial progress metrics if available.
- `main(int argc, char** argv)`: Flow—parse optional CLI arg for server address (default `localhost:50051`), instantiate `FireQueryClient`, call `Query`, `GetStatus`, `CancelRequest` in sequence. Algorithm—wraps each call in try/catch for `std::exception`, logs errors, returns non-zero on failure; ensures gRPC channel created via `grpc::CreateChannel` with insecure credentials.

### scripts/performance_test.py
- Class `PerformanceMetrics`: wraps timing bookkeeping; records wall-clock start/stop, track per-chunk arrival durations, measurement totals, and computes derived stats (`chunks_per_second`, etc.) via `get_results`.
- `run_query_test(stub, test_name, query_filter, chunk_size)`: central harness that issues a query, feeds each chunk into `PerformanceMetrics.record_chunk`, prints progress, and returns the metrics structure.
- `test_small_query`, `test_medium_query`, `test_large_query`, `test_no_filter_query`: convenience wrappers that build representative `QueryFilter` objects (scoped parameters, date windows, or wide-open) before delegating to `run_query_test`.
- `concurrent_query_worker(...)`: worker runnable executed in separate threads; each worker opens its own channel, runs `run_query_test`, and appends results into a thread-safe queue.
- `test_concurrent_queries(server_address, num_clients, chunk_size)`: spawns multiple workers, waits for them to finish, and aggregates the collected metrics to report concurrency behaviour.
- `run_all_tests(server_address)`: orchestrates the entire suite—individual chunk sizes, varied query scopes, and the concurrent stress test—returning a nested dictionary keyed by scenario name.
- `save_results(results, output_file)`: serializes metrics into JSON and writes them to disk for later analysis.
- `print_summary(results)`: pretty-prints key metrics to stdout so CI or humans can quickly inspect throughput and latency data.
- `main()`: parses CLI arguments (`--server`, `--chunk`, `--output`, `--clients`), wires logging, invokes `run_all_tests`, prints the summary, and persists the report.

### scripts/build_cpp_client.sh
- Checks/creates `build` directory, runs CMake configuration, invokes `make` to compile C++ client and servers, prints post-build instructions.

### scripts/test_network.sh
- Prints reminders for starting all six servers, pauses for confirmation, activates Python virtual environment, and runs `client/test_client.py` to validate network overlay.

### test_phase2.sh
- Validates existence of compiled C++ binaries, launches all six servers with log files, records PIDs, and sets `trap` for cleanup.
- After initialization wait, tail-checks log files for health, runs `client/test_client.py` and `client/advanced_client.py` sequentially, summarizes results, and waits for user input before shutting down processes.

### Build and Project Files
- `CMakeLists.txt`: configures gRPC/protobuf dependencies, defines build targets for C++ workers and client, and links generated proto sources.
- `Makefile`: wraps common tasks (`make proto`, `make servers`, `make clients`, etc.), sets up Python virtual environment target, and bundles testing commands.
- `requirements.txt`: lists Python packages required for gRPC services and scripts.
- Documentation markdown files (`START_HERE.md`, `PROJECT_SUMMARY.md`, etc.) track project milestones and guidance.

## Configuration & Data Partitioning
- Each `configs/process_*.json` defines `identity`, `role`, `team`, network endpoint, neighbor list, and `data_partition.directories` specifying subfolders under `data/` for that process.
- `configs/multi_computer/*.json` provides templates for distributing processes across multiple machines (see `MULTI_COMPUTER_SETUP.md` for annotated deployment examples).
- Gateway uses only neighbor connections; leaders/workers rely on partition filtering to avoid duplicate reads.

## Learning Roadmap
1. **Start with the protocol**: Read `proto/fire_service.proto` to internalize message types and RPC surfaces, then inspect generated stubs (`proto/fire_service_pb2*.py`, `.pb.h`) to see field access patterns.
2. **Trace a request**: Follow `client/test_client.py -> gateway/server.py -> team_* leader -> team_* worker` to understand call stack and data flow.
3. **Explore data model**: Step through `FireColumnModel` insert/query paths (Python then C++) with a debugger or print statements to see indices in action.
4. **Run end-to-end scripts**: Use `test_phase2.sh` or `scripts/test_network.sh` to watch logs and chunked streaming in real time; capture metrics with `scripts/performance_test.py`.
5. **Experiment with filters**: Modify client filters (parameters, AQI bounds, geographic boxes) to observe partition coverage and chunk counts.
6. **Inspect concurrency**: Enable multiple client threads via `performance_test.py` to measure throughput and adjust `max_results_per_chunk`.
7. **Extend APIs**: Prototype new RPCs by updating the proto, regenerating stubs (`make proto`), then implementing corresponding methods in gateway/leaders/workers.

## Operational Notes
- **Chunk sizing**: Gateway defaults to 1000 unless `QueryRequest.max_results_per_chunk` overrides; leaders/workers return full result sets which gateway partitions.
- **Cancellation**: Gateway honors cancel flag per chunk and cleans up request state after 60 seconds via `threading.Timer`.
- **Client disconnects**: `context.is_active()` guard stops streaming immediately if client drops.
- **Large payloads**: Inter-process gRPC options raise message limits to 100 MB to accommodate sizeable measurement sets.
- **Testing**: `scripts/performance_test.py` writes JSON summary to `results/single_computer.json`; markdown under `results/` documents findings.

Use this guide as the canonical map of the codebase: each module summary above enumerates the exported functions or methods, while the API reference grounds how processes communicate. Combining request tracing with the learning roadmap will fast-track familiarity with the entire assignment implementation.

## End-to-End Request Walkthrough
1. **Client call** – a client (Python or C++) instantiates a stub against `localhost:50051` and calls `Query` with a `QueryRequest` that includes `request_id`, a `QueryFilter`, and streaming hints.
2. **Gateway registration** – `gateway/server.py::FireQueryServiceImpl.Query` logs the request, records metadata in `active_requests`, and invokes `forward_to_team_leaders`.
3. **Leader aggregation** – each leader (`server_b.py` for Team Green, `server_e.py` for Team Pink) handles `InternalQuery`, first pulling matches from its local `FireColumnModel`, then forwarding to neighbors using `forward_to_workers`.
4. **Worker filtering** – C++ workers (`server_c.cpp`, `server_d.cpp`, `server_f.cpp`) apply the filter: they union parameter hits, intersect optional AQI constraints, and repack each matching row into a `FireMeasurement` message.
5. **Leader response** – leaders merge worker payloads with their local results and return an `InternalQueryResponse` up to the gateway.
6. **Gateway chunking** – back in `Query`, the gateway composes the complete result set, slices it according to `max_results_per_chunk`, and streams each `QueryResponseChunk` downstream. Before emitting a chunk, it checks for cancellation (`_is_cancelled`) and client connectivity (`context.is_active()`).
7. **Status/Cancellation** – meanwhile, clients can call `GetStatus` or `CancelRequest`; these RPCs operate on the same `active_requests` structure the streaming handler updates.
8. **Cleanup** – once streaming finishes or is cancelled, `_mark_completed`/`_mark_cancelled` update status, and `_cleanup_request` prunes the entry a minute later.

Tracing this flow in logs while running `client/advanced_client.py` or `test_phase2.sh` gives a tangible view of how every component cooperates.

## Key Runtime Data Structures
- **`active_requests` (gateway/server.py)**: dictionary keyed by `request_id` storing `status`, `start_time`, `chunks_sent`, `total_chunks`, and `cancelled` flag. All status/cancel calls, as well as chunk streaming, read/write this structure under `request_lock` to guarantee thread safety.
- **`FireColumnModel` column arrays**: parallel vectors holding each measurement attribute (latitudes, parameters, AQI scores, etc.). Index alignment across arrays is guaranteed, so a single index `i` represents a complete `FireMeasurement`.
- **`_site_indices`, `_parameter_indices`, `_aqs_indices`**: maps from site/parameter/AQS code to integer lists; act as inverted indexes allowing leaders and workers to answer lookup filters in O(k) time (k = matches) rather than scanning entire datasets.
- **`QueryFilter` protobuf**: packed structure containing repeated fields for OR logic (`parameters`, `site_names`), scalar bounds for AND logic (`min_aqi`, `max_aqi`, lat/lon/datetime ranges), and optional combinations thereof. Workers treat unspecified bounds as wildcards.
- **Configuration JSONs**: each process consumes its JSON file to determine network neighbors, identity, and the subset of directories it must load. This keeps dataset partitioning declarative and easily adjustable.
- **Client progress trackers**: Python advanced client maintains chunk counts and total result counters to showcase incremental delivery; these classes demonstrate expected semantics for clients you may write.

## Environment Setup & Runbook
*Copy/paste these commands the first time you spin up the system; iterate from there once the basics work.*
1. **Create Python environment**
    - `make venv` to create `.venv` or `python3 -m venv venv`; `source venv/bin/activate`.
    - `pip install -r requirements.txt` to pull gRPC tooling.
2. **Generate protobuf bindings**
    - `make proto` (or `python -m grpc_tools.protoc -I proto --python_out=proto --grpc_python_out=proto proto/fire_service.proto`). See `scripts/README_BUILD.md` if you need a deeper walkthrough of the build targets.
    - Ensures both Python and C++ generated code are up to date before builds.
3. **Build C++ binaries**
    - `make servers` (wraps CMake configure + build), or run `scripts/build_cpp_client.sh`.
    - Outputs live in `build/` (e.g., `build/server_c`).
4. **Launch processes (single machine)**
    - In six terminals: start C++ workers (`build/server_c configs/process_c.json`, etc.), Python leaders (`python team_green/server_b.py configs/process_b.json`), and gateway (`python gateway/server.py configs/process_a.json`).
    - Alternatively execute `test_phase2.sh` to spin everything up automatically.
5. **Run clients**
    - Basic sanity: `python client/test_client.py` (documented in `scripts/README_TESTING.md`).
    - Feature demo: `python client/advanced_client.py` (also covered in `scripts/README_TESTING.md`).
    - C++ example: `build/client localhost:50051` (after compiling).
6. **Shutdown**
    - Use Ctrl+C in each terminal or press Enter when prompted by `test_phase2.sh`; processes will stop and clean logs. Reference `scripts/README_TESTING.md` for log locations and troubleshooting tips.

## Testing & Diagnostics Checklist
- `scripts/test_network.sh`: manual smoke test to ensure RPC pathways are healthy (quickstart in `scripts/README_TESTING.md`).
- `test_phase2.sh`: orchestrated integration test covering chunked streaming, cancellation, and status tracking; collects logs under `/tmp/server_*.log` for inspection (see the same README for expected output snippets).
- `scripts/performance_test.py`: benchmark suite producing `results/single_computer.json` and enabling chunk-size/concurrency experimentation (scenario breakdowns are documented in `scripts/README_TESTING.md`).
- Logging tips:
  - Gateway logs chunk transmission counts; confirm progressive streaming and cancellation events here.
  - Team leaders log local match counts and worker contributions; use these to verify partition coverage.
  - Workers log request details (requesting process, filters) for troubleshooting missing data.

## Alignment With Mini 2 Requirements
- **Overlay topology**: `configs/process_a.json` … `process_f.json` implement the required A↔(B,E), B→C, and E→(D,F) connections with disjoint partitions; identities/hosts remain in JSON, not hardcoded.
- **Chunked request control**: Gateway `FireQueryServiceImpl.Query` streams multi-part results, exposes cancellation and status RPCs, and checks client disconnects—meeting the segmented-delivery objective.
- **Data partitioning**: Leaders and workers load non-overlapping directory sets via `data_partition.directories`, honoring the “no sharing, no replication” rule.
- **Language/tooling constraints**: Python gateway/leaders, C++ workers/client, CMake builds, and JSON-driven config satisfy the specified technology stack.
- **Testing infrastructure**: Provided scripts (`test_phase2.sh`, `scripts/test_network.sh`, `scripts/performance_test.py`) echo the assignment’s call for validation harnesses.

## Open Gaps and Improvement Opportunities
1. **Gateway `InternalQuery` TODO**: Currently returns an empty response; implement local filtering to keep topology flexible.
2. **Cancellation propagation**: Leaders (`server_b.py`, `server_e.py`) still mark TODOs—forward cancellation signals to workers to prevent wasted effort.
3. **Fairness/back-pressure**: Introduce simple scheduling (queues, round-robin, rate limits) so the system can demonstrate balanced request handling under load.
4. **Caching/anticipation**: Add optional result caching or speculative prefetching to better reflect “request/cache control” ambitions.
5. **Failure handling**: Add retries/timeouts when inter-process RPCs fail, and surface errors to the gateway/client.
6. **Multi-machine validation**: Documentation still flags distributed testing as pending—complete the run and capture findings.

## Two-Machine Deployment Procedure
_Assume Machine 1 hosts {A,B,D}; Machine 2 hosts {C,E,F}; both reachable on the same LAN._

1. **Network preparation**
    - Assign static IPs (e.g., Machine1 `192.168.1.10`, Machine2 `192.168.1.11`).
    - Open/forward TCP ports `50051-50056` on firewalls/router for cross-host RPC traffic.
2. **Code and dependency setup (both machines)**
    - Clone repository or sync latest code.
    - `make venv && source venv/bin/activate && pip install -r requirements.txt`.
    - `make proto` to regenerate protobuf bindings, then `make servers` (or `cmake -S . -B build && cmake --build build`).
3. **Update configuration JSONs**
    - Edit each `configs/process_*.json`: set `hostname` to the actual machine IP, leave `port` values unique per process, verify neighbor edges match overlay.
4. **Launch services**
    - Machine 2: start worker `server_c`, worker `server_f`, and leader `server_e` using their configs.
    - Machine 1: start worker `server_d`, leader `server_b`, then gateway `server.py`.
    - Watch logs for “Server started” and successful `InternalQuery` responses to confirm connectivity.
5. **Validate end-to-end**
    - Run `python client/test_client.py --gateway 192.168.1.10:50051` (or similar) to confirm streaming across machines.
    - Exercise `client/advanced_client.py` and optionally `scripts/performance_test.py --server 192.168.1.10:50051` to capture metrics.
6. **Operational hygiene**
    - If traversing NAT, configure router port forwarding or establish VPN/SSH tunnels; for public exposure, layer TLS or secure tunnels.
    - Document host/IP mapping and startup order in `MULTI_COMPUTER_SETUP.md`; consider automation (systemd units, Ansible, or scripts) for repeatable deployments.

## Documentation Map
- `START_HERE.md`: quick-start instructions for the overall project timeline.
- `README.md`: high-level summary and build/run guidance (use alongside this reference).
- `PROJECT_SUMMARY.md`, `WORK_COMPLETE_SUMMARY.md`: milestone retrospectives useful for context.
- `PHASE1_DATA_PARTITIONING_COMPLETE.md`, `PHASE2_CHUNKED_STREAMING_COMPLETE.md`, `SINGLE_COMPUTER_COMPLETE.md`: detailed reports explaining deliverables per phase.
- `MULTI_COMPUTER_SETUP.md`: notes on distributing processes across machines using `configs/multi_computer/` templates.
- `presentation-iteration-1.md`, `WHAT_REMAINS.md`: planning artefacts; review for roadmap and outstanding work.

## Frequently Used Command Snippets
- Regenerate stubs: ``make proto``
- Build C++ targets: ``cmake -S . -B build && cmake --build build``
- Run gateway: ``python gateway/server.py configs/process_a.json``
- Run Team Green leader: ``python team_green/server_b.py configs/process_b.json``
- Run Team Pink leader: ``python team_pink/server_e.py configs/process_e.json``
- Start worker C: ``./build/server_c configs/process_c.json``
- Execute advanced client: ``python client/advanced_client.py``
- Launch performance tests: ``python scripts/performance_test.py --server localhost:50051``

Keep this cheat sheet near your terminal—most development tasks can be reduced to these commands.
