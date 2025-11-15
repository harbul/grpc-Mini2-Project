# Fire Query System Architecture & API Guide

## System Topology
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
| RPC Method      | Direction            | Request Type             | Response Type           | Behavior |
|-----------------|----------------------|--------------------------|-------------------------|----------|
| `Query`         | Client -> Gateway    | `QueryRequest`           | stream `QueryResponseChunk` | Executes filtered query, streams chunked measurements, tracks cancellation and status. |
| `CancelRequest` | Client -> Gateway    | `StatusRequest`          | `StatusResponse`        | Marks request as cancelled; gateway stops streaming. |
| `GetStatus`     | Client -> Gateway    | `StatusRequest`          | `StatusResponse`        | Returns progress (chunks delivered, total chunks, status enum). |
| `InternalQuery` | Process -> Process   | `InternalQueryRequest`   | `InternalQueryResponse` | Used by leaders to query workers; workers respond with aggregated measurements. |
| `Notify`        | Process -> Process   | `InternalQueryRequest`   | `StatusResponse`        | Hook for async notifications (currently returns simple acknowledgment). |

### Message Schemas
- `QueryRequest`: includes `request_id`, `QueryFilter`, `query_type` strings, chunk preferences.
- `QueryFilter`: supports site, agency, parameter lists; bounding boxes; time ranges; concentration/AQI thresholds.
- `QueryResponseChunk`: chunk metadata (`chunk_number`, `is_last_chunk`, counts) and repeated `FireMeasurement` payloads.
- `InternalQueryRequest/Response`: replicate filter info while tracking originating process for routing replies.
- `StatusRequest/Response`: `action` nouns (`cancel`, `status`, etc.) and progress metrics.

## Module, Class, and Function Reference

### proto/fire_service.proto (Contract Source of Truth)
- Declares package `fire_service`, all message types, and the `FireQueryService` RPCs.
- Messages such as `FireMeasurement`, `QueryFilter`, `QueryRequest`, `QueryResponseChunk`, `InternalQueryRequest`, `InternalQueryResponse`, `StatusRequest`, and `StatusResponse` are shared by every process.
- Streaming RPC definitions drive implementation details in both Python and C++ servers.
- Generated files:
    - Python: `proto/fire_service_pb2.py` (messages) and `proto/fire_service_pb2_grpc.py` (service base classes and stubs).
    - C++: `proto/fire_service.pb.h/cc` and `proto/fire_service.grpc.pb.h/cc` (headers/sources compiled into each binary).
- When regenerating stubs, ensure both Python and C++ artifacts are refreshed; stale bindings produce runtime incompatibilities, especially if field numbers change.

### gateway/server.py (Process A Gateway)
**Class `FireQueryServiceImpl`**
- `__init__(config)`: reads runtime metadata (identity, neighbors) and seeds `active_requests` with a thread lock for request lifecycle tracking.
- `Query(request, context)`: orchestrates the entire query lifecycle.
    1. Validates/records the incoming `request_id` in `active_requests` with default status `processing` and `chunks_sent=0`.
    2. Calls `forward_to_team_leaders` to gather full result sets from Teams Green and Pink.
    3. Computes chunk boundaries (defaults to 1000 results when `max_results_per_chunk` is unset) and updates `total_chunks` in the tracking map.
    4. Streams each `QueryResponseChunk`, checking `_is_cancelled` and `context.is_active()` before transmission, sleeping briefly (`0.01s`) to simulate progressive delivery.
    5. On completion, invokes `_mark_completed`; on exception, `_mark_failed`; in both cases schedules `_cleanup_request` via `threading.Timer`.
- `forward_to_team_leaders(request)`: iterates neighbor list (B and E), opens gRPC channels with enlarged message limits, issues `InternalQuery` RPCs, and aggregates `FireMeasurement` payloads.
- `CancelRequest(request, context)`: marks matching `request_id` as cancelled, echoes chunk progress in a `StatusResponse`.
- `GetStatus(request, context)`: reports current state (`processing`, `completed`, etc.) plus chunk counters for an ongoing or completed request.
- `InternalQuery(request, context)`: placeholder for potential use if gateway also held data; currently returns empty completion response.
- `Notify(request, context)`: acknowledges control notifications, useful for future coordination signals.
- `_is_cancelled(request_id)`: reads cancellation flag under lock to decide whether to stop streaming.
- `_mark_cancelled(request_id)`: flips status and cancellation flag post client request or disconnect.
- `_mark_completed(request_id)`: finalizes request status when streaming finishes.
- `_mark_failed(request_id)`: records failure state for error reporting back to clients.
- `_update_chunks_sent(request_id, chunks_sent)`: increments chunk counter so status checks show progress.
- `_cleanup_request(request_id)`: timer-triggered removal to prune finished requests from memory after 60 seconds.

**Module-level helpers**
- `load_config(config_path)`: loads JSON configuration for the process.
- `serve(config_path)`: creates a thread pool gRPC server, registers `FireQueryServiceImpl`, binds to configured host:port, and blocks on `wait_for_termination`.
- `main`: parses CLI arguments and dispatches to `serve`.
- Runtime environment: uses `grpc.server(futures.ThreadPoolExecutor(max_workers=10))`, so each incoming RPC runs on its own thread—hence the need for `request_lock` when touching `active_requests`.

### team_green/server_b.py (Process B Leader)
**Class `FireQueryServiceImpl`**
- `__init__(config)`: logs topology details, instantiates Python `FireColumnModel`, optionally loading only whitelisted directories from `data_partition`; prints measurement counts to confirm data coverage.
- `Query(request, context)`: safety handler for direct client access; returns empty result chunk while keeping server compliant with service definition.
- `InternalQuery(request, context)`: main entry point for Process A; retrieves local matches via `_query_local_data`, forwards residual work to neighbors (Process C), aggregates responses, and constructs `InternalQueryResponse`.
- `_query_local_data(request)`: applies parameter OR filtering, site-based lookup, and AQI range AND filtering against the Python column model; converts indices into `FireMeasurement` messages.
- `forward_to_workers(request)`: loops over neighbors (C), making `InternalQuery` calls and concatenating results.
- `CancelRequest(request, context)`, `GetStatus(request, context)`, `Notify(request, context)`: placeholder implementations returning canned responses for possible future use.

**Module-level helpers**
- `load_config(config_path)`: JSON loader mirroring gateway.
- `serve(config_path)`: spins up gRPC server for Process B using `ThreadPoolExecutor`.
- `main`: command-line interface.
- When forwarding to workers, gRPC channels are created with `grpc.max_receive_message_length` and `grpc.max_send_message_length` set to 100 MB—mirrored in the gateway—to accommodate large measurement batches.

### team_pink/server_e.py (Process E Leader)
Mirror of Process B with pink-specific partitions.
- `__init__`: loads partitions for Sep 5-13 and neighbors D/F.
- `Query`: fallback empty chunk for unexpected client calls.
- `InternalQuery`: queries local `FireColumnModel`, forwards to D and F, aggregates all measurements, and responds to gateway.
- `_query_local_data`: identical filtering flow as Process B, operating on pink partition.
- `forward_to_workers`: fans out to D and F through gRPC.
- `CancelRequest`, `GetStatus`, `Notify`: stub responses.
- `load_config`, `serve`, `main`: same patterns as other Python servers.
- Uses the same 100 MB gRPC message limits as Process B to prevent truncation when aggregating Team Pink data.

### team_green/server_c.cpp (Process C Worker)
**Class `FireQueryServiceImpl`**
- Constructor: parses JSON config, logs identity, loads allowed directories into C++ `FireColumnModel` to back queries.
- `Status Query(...)`: handles direct client calls with an empty chunk to remain protocol compliant.
- `Status InternalQuery(...)`: receives filters from Process B, performs parameter OR, site lookup, and AQI range filtering using C++ model getters, fills `InternalQueryResponse` with matching rows, and marks response complete.
- `Status CancelRequest(...)`: returns a `StatusResponse` indicating cancellation acknowledgement for monitoring hooks.
- `Status GetStatus(...)`: echoes a pending status for diagnostics.
- `Status Notify(...)`: acknowledges notifications from leaders.

**Supporting functions**
- `load_config(config_path)`: reads JSON config via `nlohmann::json`.
- `RunServer(config_path)`: builds gRPC server, registers service, prints status, and blocks until shutdown.
- `main`: CLI entrypoint verifying arguments and running server.

### team_pink/server_d.cpp (Process D Worker)
Functionality mirrors Process C while serving a different partition and accepting `InternalQuery` from both leaders B and E. Each method implements the same logic paths with shared C++ model accessors.

### team_pink/server_f.cpp (Process F Worker)
Identical structure to Process C and D but serving Sep 14-24 data partition exclusively for Team Pink leader E.

### common/fire_column_model.py (Python Column Model)
**Class `FireColumnModel`**
- `__init__`: initializes column arrays, dictionaries for quick lookup, metadata sets, datetime bounds, and geographic bounds.
- `read_from_directory(directory_path, allowed_subdirs=None)`: walks directory tree, filtering optional partitions, assembles CSV file list, and delegates to `read_from_csv`, logging successes.
- `read_from_csv(filename)`: parses each CSV row without headers, converts to typed values, and calls `insert_measurement`; skips malformed rows.
- `insert_measurement(...)`: appends values into column lists, updates lookup indices, metadata (unique sets), geospatial bounds, and datetime range. This function is the *only* mutation entry point—every query relies on the invariants maintained here (`_site_indices`, `_parameter_indices`, `_aqs_indices` all remain synchronized with column vectors).
- `get_indices_by_site(site_name)`, `get_indices_by_parameter(parameter)`, `get_indices_by_aqs_code(aqs_code)`: expose fast lookups using pre-built dictionaries.
- `measurement_count()`, `site_count()`, `unique_sites()`, `unique_parameters()`, `unique_agencies()`, `datetime_range()`, `geographic_bounds()`: report summary statistics.
- `_update_indices(index)`: internal helper to populate lookup dictionaries with new index.
- `_update_geographic_bounds(latitude, longitude)`: grows min/max geospatial bounds as data loads.
- `_update_datetime_range(datetime)`: tracks earliest and latest timestamp strings.
- `_get_csv_files(...)`: returns sorted list of CSV paths respecting partition whitelist and handling errors gracefully.

### common/FireColumnModel.cpp and FireColumnModel.hpp (C++ Column Model)
- Constructor/Destructor: initialize bounds and metadata containers.
- `readFromDirectory(directoryPath, allowedSubdirs)`: enumerates CSVs via `getCSVFiles`, invokes `readFromCSV` per file, and logs totals.
- `readFromCSV(filename)`: uses `CSVReader` for safe parsing, skips headers, converts strings to typed values, and feeds `insertMeasurement`.
- `insertMeasurement(...)`: appends to column vectors, calls `updateIndices`, `updateGeographicBounds`, `updateDatetimeRange`, and updates metadata sets. As in Python, this centralizes state mutation, ensuring indexes remain aligned.
- `getIndicesBySite`, `getIndicesByParameter`, `getIndicesByAqsCode`: fetch index vectors from maps to serve worker filters.
- `getGeographicBounds`: copies stored min/max values if initialized.
- Private helpers `updateIndices`, `updateGeographicBounds`, `updateDatetimeRange`, `getCSVFiles`: maintain internal structures and enforce partition filtering with `<filesystem>`.

### common/utils.hpp / utils.cpp
- `parseLongOrZero(s)`: wraps `std::stoll`, returning 0 on failure for resilient parsing.
- `timeCall(f)`: measures microseconds taken by invoking function `f` using `high_resolution_clock`.
- `timeCallMulti(f, runs)`: repeats `f` and records runtime per invocation.
- `mean(v)`: computes average of a vector of doubles.

### common/readcsv.hpp / readcsv.cpp
- `CSVReader::CSVReader(path, delimiter, quote, comment)`: constructs reader with configurable delimiters.
- `open()`: opens underlying `ifstream`, throwing on failure.
- `close()`: closes file if open.
- `readRow(out)`: reads logical CSV records (honoring quotes and comments) and splits them into fields.
- Internal helpers `readPhysicalRecord` and `splitRecord`: manage multi-line quoted records and delimiter-aware parsing.

### client/test_client.py
- `test_query(stub)`: crafts sample filter, issues `Query`, renders chunk progress bar, and prints sample measurements.
- `test_get_status(stub)`: sends `GetStatus` RPC to gateway and prints stats.
- `test_cancel_request(stub)`: exercises `CancelRequest` to validate control path.
- `main()`: establishes gRPC channel to gateway, instantiates stub, and sequentially runs test trio.
- Demonstrates idiomatic Python gRPC client usage (channel creation, stub instantiation, consuming streaming responses); treat it as the minimal reproducible example when onboarding new developers.

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
- Class `ProgressTracker`: tracks chunk counts, total results, elapsed time, and prints textual progress bars.
- `test_chunked_streaming(stub, chunk_size)`: fetches PM2.5 data with moderate AQI filter to highlight chunk delivery.
- `test_cancellation(stub, chunk_size, cancel_after_chunks)`: triggers cancellation mid-stream and confirms server response.
- `test_status_tracking(stub)`: spawns status polling thread while streaming and logs snapshots.
- `test_small_chunks(stub)`: runs query with small chunk size to emphasize progressive streaming.
- `main()`: orchestrates connection, runs all scenarios with pauses, and summarizes features demonstrated.

### client/client.cpp
- `FireQueryClient` constructor: binds to gRPC channel and creates C++ stub.
- `Query(request_id, parameters, min_aqi, max_aqi)`: builds `QueryRequest`, streams results, prints chunk metadata, and sample measurements.
- `GetStatus(request_id)`: calls `GetStatus` RPC and prints chunk counters.
- `CancelRequest(request_id)`: invokes cancellation RPC for demo.
- `main(argc, argv)`: configures server address (optional CLI override), instantiates `FireQueryClient`, and runs sequential query/status/cancel demonstration.

### scripts/performance_test.py
- Class `PerformanceMetrics`: captures start/end time, chunk timings, measurement counts, computes throughput and chunk statistics via `get_results`.
- `run_query_test(stub, test_name, query_filter, chunk_size)`: core runner that streams results while tracking metrics and printing progress.
- `test_small_query`, `test_medium_query`, `test_large_query`, `test_no_filter_query`: convenience wrappers passing different filter profiles.
- `concurrent_query_worker(...)`: thread worker function to execute `run_query_test` concurrently.
- `test_concurrent_queries(server_address, num_clients, chunk_size)`: spawns multiple threads (each with its own channel) to stress concurrency and aggregates metrics.
- `run_all_tests(server_address)`: orchestrates chunk-size suite, query-complexity suite, and concurrent suite; collects results into structured dict.
- `save_results(results, output_file)`: writes JSON summary to disk.
- `print_summary(results)`: formats key metrics in human-readable summary.
- `main()`: parses CLI arguments, runs full suite, prints summary, saves results, and handles exceptions.

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
- `configs/multi_computer/*.json` provides templates for distributing processes across multiple machines.
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

## Onboarding Study Plan
1. **`proto/fire_service.proto`** – internalize the RPC contract and message schemas first; keep it open as a reference.
2. **`ARCHITECTURE_AND_API_REFERENCE.md` (this file)** – skim topology and request walkthrough to build a mental model.
3. **`gateway/server.py`** – read the `FireQueryServiceImpl` class top-to-bottom; this is the critical coordinator that exercises every concept in the assignment. Pay special attention to `Query` and `forward_to_team_leaders`.
4. **`team_green/server_b.py`** – study how a leader queries its local data and workers; compare with `team_pink/server_e.py` for symmetry.
5. **`team_green/server_c.cpp`** – review the C++ worker implementation to understand how the columnar model is used in a lower-level language; follow up with `server_d.cpp` and `server_f.cpp` (differences are partitions and neighbor interactions).
6. **`common/fire_column_model.py` and `common/FireColumnModel.cpp`** – learn the column store internals, indexing strategies, and CSV ingestion logic.
7. **Client tools** – run through `client/test_client.py` to see simple usage, then `client/advanced_client.py` for Phase 2 features, and finally `client/client.cpp` if you intend to extend the C++ ecosystem.
8. **Automation scripts** – inspect `scripts/performance_test.py`, `test_phase2.sh`, and `scripts/test_network.sh` to understand testing, diagnostics, and orchestration patterns.
9. **Configs and build assets** – glance over `configs/*.json`, `CMakeLists.txt`, and `Makefile` to see how the environment is wired together.

Following this order ensures that new developers first grasp the shared protocol, then the orchestration layer, then the data/worker internals, and finally the tooling that exercises the system.

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
1. **Create Python environment**
    - `make venv` to create `.venv` or `python3 -m venv venv`; `source venv/bin/activate`.
    - `pip install -r requirements.txt` to pull gRPC tooling.
2. **Generate protobuf bindings**
    - `make proto` (or `python -m grpc_tools.protoc -I proto --python_out=proto --grpc_python_out=proto proto/fire_service.proto`).
    - Ensures both Python and C++ generated code are up to date before builds.
3. **Build C++ binaries**
    - `make servers` (wraps CMake configure + build), or run `scripts/build_cpp_client.sh`.
    - Outputs live in `build/` (e.g., `build/server_c`).
4. **Launch processes (single machine)**
    - In six terminals: start C++ workers (`build/server_c configs/process_c.json`, etc.), Python leaders (`python team_green/server_b.py configs/process_b.json`), and gateway (`python gateway/server.py configs/process_a.json`).
    - Alternatively execute `test_phase2.sh` to spin everything up automatically.
5. **Run clients**
    - Basic sanity: `python client/test_client.py`.
    - Feature demo: `python client/advanced_client.py`.
    - C++ example: `build/client localhost:50051` (after compiling).
6. **Shutdown**
    - Use Ctrl+C in each terminal or press Enter when prompted by `test_phase2.sh`; processes will stop and clean logs.

## Testing & Diagnostics Checklist
- `scripts/test_network.sh`: manual smoke test to ensure RPC pathways are healthy.
- `test_phase2.sh`: orchestrated integration test covering chunked streaming, cancellation, and status tracking; collects logs under `/tmp/server_*.log` for inspection.
- `scripts/performance_test.py`: benchmark suite producing `results/single_computer.json` and enabling chunk-size/concurrency experimentation.
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
