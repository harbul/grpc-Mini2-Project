# Presentation Iteration 1 - Bug Fixes & Issues


## Status: Issues Identified & Partially Fixed

---

## 🐛 **Issue #1: Query Filter Combination Bug**

### **What Happened:**
Queries with multiple filters (e.g., `parameters=["PM2.5", "PM10"]` AND `AQI range: 0-100`) were returning **0 results** even though data existed.

### **Why It Happened:**
The query filter logic in all servers used `if/elif/elif` statements, which meant:
- Only the **first matching condition** was checked
- When both `parameters` and `AQI range` were specified, only parameters were checked
- The AQI filter was completely ignored
- Also, only the **first parameter** was used (`parameters[0]`), so `PM10` was ignored

**Example of broken code:**
```python
if len(filter_obj.parameters) > 0:
    param = filter_obj.parameters[0]  # Only uses first parameter!
    matching_indices = self.data_model.get_indices_by_parameter(param)
elif filter_obj.min_aqi > 0 or filter_obj.max_aqi > 0:  # Never reached!
    # AQI filtering code
```

### **How We Fixed It:**
1. **Changed filter logic** to combine filters with AND logic:
   - First, filter by parameters (OR logic for multiple parameters)
   - Then, apply AQI range filter (AND logic)
2. **Handle multiple parameters** by iterating through all parameters and combining results
3. **Applied fix to all 5 servers:**
   - `team_green/server_b.py` (Python)
   - `team_pink/server_e.py` (Python)
   - `team_green/server_c.cpp` (C++)
   - `team_pink/server_d.cpp` (C++)
   - `team_pink/server_f.cpp` (C++)

**Fixed code example:**
```python
# Start with parameter filtering (OR logic)
if len(filter_obj.parameters) > 0:
    all_param_indices = set()
    for param in filter_obj.parameters:  # Handle ALL parameters
        param_indices = self.data_model.get_indices_by_parameter(param)
        all_param_indices.update(param_indices)
    matching_indices = list(all_param_indices)

# Then apply AQI filter (AND logic)
if filter_obj.min_aqi > 0 or filter_obj.max_aqi > 0:
    filtered_indices = []
    for idx in matching_indices:
        aqi = self.data_model.aqis[idx]
        if ((filter_obj.min_aqi == 0 or aqi >= filter_obj.min_aqi) and
            (filter_obj.max_aqi == 0 or aqi <= filter_obj.max_aqi)):
            filtered_indices.append(idx)
    matching_indices = filtered_indices
```

### **Result:**
✅ Filter combination now works correctly
✅ Multiple parameters are handled (OR logic)
✅ AQI filtering is applied after parameter filtering (AND logic)

---

## 🚨 **Issue #2: gRPC Message Size Limit**

### **What Happened:**
After fixing Issue #1, queries with large result sets (e.g., `parameters=["PM2.5", "PM10"]` with `AQI 0-100`) still returned **0 results**, but queries with smaller results worked fine.

### **Why It Happened:**
gRPC has a **default maximum message size of 4MB**. When servers tried to send large result sets through `InternalQuery` responses:
- Server B tried to send ~7.8MB to Gateway A → **RESOURCE_EXHAUSTED error**
- Server E tried to send ~12.9MB to Gateway A → **RESOURCE_EXHAUSTED error**
- Gateway A received errors and returned 0 results

**Error from logs:**
```
[A] Error contacting B: StatusCode.RESOURCE_EXHAUSTED: CLIENT: Received message larger than max (7872772 vs. 4194304)
[A] Error contacting E: StatusCode.RESOURCE_EXHAUSTED: CLIENT: Received message larger than max (12942162 vs. 4194304)
```

### **Why Some Queries Worked:**
- Test 4 (small chunks, no filters) worked because it returned fewer results (~26K measurements)
- The chunked streaming to the **client** works fine (that's already implemented)
- The problem is in the **internal server-to-server** communication using `InternalQuery`

### **How to Fix It:**
Increase the gRPC message size limits when creating channels between servers.

**Files that need changes:**
1. `gateway/server.py` - When creating channels to Team Leaders (B, E)
2. `team_green/server_b.py` - When creating channels to Workers (C)
3. `team_pink/server_e.py` - When creating channels to Workers (D, F)

**Python fix:**
```python
# Instead of:
channel = grpc.insecure_channel(neighbor_address)

# Use:
options = [
    ('grpc.max_receive_message_length', 100 * 1024 * 1024),  # 100MB
    ('grpc.max_send_message_length', 100 * 1024 * 1024),     # 100MB
]
channel = grpc.insecure_channel(neighbor_address, options=options)
```

**C++ fix (if needed):**
```cpp
grpc::ChannelArguments args;
args.SetMaxReceiveMessageSize(100 * 1024 * 1024);  // 100MB
args.SetMaxSendMessageSize(100 * 1024 * 1024);     // 100MB
auto channel = grpc::CreateCustomChannel(
    server_address,
    grpc::InsecureChannelCredentials(),
    args
);
```

### **Status:**
✅ **FIXED** - Implemented in all servers (gateway, team leaders)

---

## 📊 **Test Results Summary**

### **Before Fixes:**
- All queries with filters: **0 results** ❌

### **After Filter Fix (Issue #1):**
- Queries with small result sets: **Working** ✅ (26,269 measurements returned)
- Queries with large result sets: **Still failing** ❌ (gRPC message size limit)

### **After Fixing Issue #2:**
- All queries should work regardless of result size ✅
- Performance testing in progress to verify

---

## 🔍 **Key Learnings**

1. **Filter Logic:** Always use AND/OR logic explicitly, not `if/elif` chains
2. **gRPC Limits:** Default 4MB message size can be a bottleneck for large datasets
3. **Error Handling:** Check server logs for `RESOURCE_EXHAUSTED` errors when debugging
4. **Testing:** Test with both small and large result sets to catch size-related issues

---

## 📝 **Next Steps**

1. ✅ Fix filter combination logic (DONE)
2. ✅ Increase gRPC message size limits (DONE)
3. 🔄 Re-test all query combinations (IN PROGRESS)
4. 🔄 Verify performance with large result sets (IN PROGRESS)

---

## 🔧 **Technical Details**

### **Files Modified for Issue #1:**
- `team_green/server_b.py` - Lines 99-136 (filter logic)
- `team_pink/server_e.py` - Lines 99-136 (filter logic)
- `team_green/server_c.cpp` - Lines 105-166 (filter logic, added `<set>` header)
- `team_pink/server_d.cpp` - Lines 107-168 (filter logic, added `<set>` header)
- `team_pink/server_f.cpp` - Lines 106-167 (filter logic, added `<set>` header)

### **Build Commands:**
```bash
make servers  # Rebuilds C++ servers after code changes
```

### **Test Commands:**
```bash
./test_phase2.sh  # Runs full end-to-end test
```

---

## 📈 **Impact**

- **Issue #1:** Affected all queries with multiple filters - **CRITICAL**
- **Issue #2:** Affects queries returning >4MB of data - **HIGH PRIORITY**

Both issues prevent the system from returning correct results for common query patterns.

---

**Note:** The chunked streaming to clients works perfectly - the issue is only in internal server-to-server communication via `InternalQuery`.

