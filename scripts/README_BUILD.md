# Building the C++ Client

## ✅ Build Complete!

The C++ client has been successfully built using the Makefile.

## Build Instructions

### Quick Build

```bash
cd /Users/indraneelsarode/Desktop/mini-2-grpc
make
```

### Clean and Rebuild

```bash
make clean && make
```

## Running the Client

### With Python Gateway Running

1. **Start Process A (Gateway) in Terminal 1:**
```bash
cd /Users/indraneelsarode/Desktop/mini-2-grpc/gateway
source ../venv/bin/activate
python server.py ../configs/process_a.json
```

2. **Run C++ Client in Terminal 2:**
```bash
cd /Users/indraneelsarode/Desktop/mini-2-grpc
./build/fire_client
```

### With Custom Server Address

```bash
./build/fire_client <hostname:port>

# Example:
./build/fire_client localhost:50051
./build/fire_client 192.168.1.100:50051
```

## What the Client Does

The C++ client:
1. Connects to Process A (Gateway) at the specified address
2. Sends a Query request for PM2.5 and PM10 data (AQI 0-100)
3. Receives streaming QueryResponseChunk responses
4. Tests GetStatus RPC
5. Tests CancelRequest RPC
6. Prints all results in human-readable format

## Expected Output

```
Fire Query Service C++ Client
==============================
Connecting to: localhost:50051

=== Sending Query ===
Request ID: 12345
Parameters: PM2.5 PM10 
AQI range: 0 - 100

--- Received Chunk #0 ---
  Measurements in chunk: 0
  Total results: 0
  Total chunks: 1
  Is last chunk: Yes

✓ Query completed successfully
Total measurements received: 0

=== Checking Status ===
Request ID: 12345
Status: pending
Chunks delivered: 0/0

=== Cancelling Request ===
Request ID: 12345
Status: cancelled

=== All tests completed ===
```

## Build System

### Why Makefile Instead of CMake?

We use a simple Makefile because:
- ✅ Avoids CMake protobuf/gRPC version conflicts
- ✅ Simple and explicit
- ✅ Auto-detects all abseil libraries
- ✅ Works reliably on macOS with Homebrew

### Makefile Features

- Automatically finds all abseil libraries
- Links gRPC++, protobuf, re2, and all dependencies
- Creates `build/` directory automatically
- Clean target for rebuilding
- Shows build status and usage instructions

## Troubleshooting

### Build Errors

**Error: Cannot find grpc++ or protobuf**
```bash
brew install grpc protobuf
```

**Error: clang++ not found**
```bash
xcode-select --install
```

### Runtime Errors

**Connection refused**
- Make sure Process A (Gateway) is running on port 50051

**Library not found (macOS)**
```bash
export DYLD_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_LIBRARY_PATH
./build/fire_client
```

## Next Steps

Once the C++ client is working:
1. ✅ Test with all 6 Python servers running
2. ✅ Implement C++ servers (required by assignment)
3. ✅ Load FireColumnModel data into processes
4. ✅ Test with actual fire data queries

## Files

- `Makefile` - Build configuration
- `client/client.cpp` - C++ client source code
- `build/fire_client` - Compiled executable (923KB)
- `proto/*.pb.cc` - Generated protobuf code
- `proto/*.grpc.pb.cc` - Generated gRPC code

