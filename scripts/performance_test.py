#!/usr/bin/env python3
"""
Performance Testing Script for Fire Query System
Tests various aspects of system performance on single computer
"""

import grpc
import sys
import os
import time
import json
import random
import threading
from datetime import datetime
from typing import List, Dict, Any
import statistics

# Add proto directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'proto'))

import fire_service_pb2
import fire_service_pb2_grpc


class PerformanceMetrics:
    """Track performance metrics for a query"""
    def __init__(self, test_name: str):
        self.test_name = test_name
        self.start_time = None
        self.first_chunk_time = None
        self.end_time = None
        self.chunk_times = []
        self.total_measurements = 0
        self.total_chunks = 0
        self.errors = []
    
    def start(self):
        self.start_time = time.time()
    
    def record_first_chunk(self):
        if self.first_chunk_time is None:
            self.first_chunk_time = time.time()
    
    def record_chunk(self, chunk_number: int, measurements_count: int):
        self.chunk_times.append(time.time())
        self.total_chunks += 1
        self.total_measurements += measurements_count
    
    def finish(self):
        self.end_time = time.time()
    
    def get_results(self) -> Dict[str, Any]:
        """Calculate and return performance metrics"""
        if not self.start_time or not self.end_time:
            return {"error": "Incomplete test"}
        
        total_time = self.end_time - self.start_time
        time_to_first_chunk = self.first_chunk_time - self.start_time if self.first_chunk_time else 0
        
        # Calculate chunk delivery intervals
        chunk_intervals = []
        if len(self.chunk_times) > 1:
            for i in range(1, len(self.chunk_times)):
                chunk_intervals.append(self.chunk_times[i] - self.chunk_times[i-1])
        
        results = {
            "test_name": self.test_name,
            "total_time": round(total_time, 3),
            "time_to_first_chunk": round(time_to_first_chunk, 3),
            "total_measurements": self.total_measurements,
            "total_chunks": self.total_chunks,
            "throughput": round(self.total_measurements / total_time, 2) if total_time > 0 else 0,
            "avg_chunk_time": round(statistics.mean(chunk_intervals), 4) if chunk_intervals else 0,
            "median_chunk_time": round(statistics.median(chunk_intervals), 4) if chunk_intervals else 0,
            "errors": self.errors
        }
        
        return results


def run_query_test(stub, test_name: str, query_filter, chunk_size: int) -> PerformanceMetrics:
    """Run a single query test and collect metrics"""
    print(f"\n{'='*60}")
    print(f"Running: {test_name}")
    print(f"{'='*60}")
    
    metrics = PerformanceMetrics(test_name)
    request_id = random.randint(10000, 99999)
    
    request = fire_service_pb2.QueryRequest(
        request_id=request_id,
        filter=query_filter,
        query_type="filter",
        require_chunked=True,
        max_results_per_chunk=chunk_size
    )
    
    metrics.start()
    
    try:
        for chunk in stub.Query(request):
            if metrics.first_chunk_time is None:
                metrics.record_first_chunk()
                print(f"  First chunk received: {time.time() - metrics.start_time:.3f}s")
            
            metrics.record_chunk(chunk.chunk_number, len(chunk.measurements))
            
            # Progress indicator
            if chunk.total_chunks > 0:
                progress = (chunk.chunk_number + 1) / chunk.total_chunks * 100
                print(f"\r  Progress: {progress:5.1f}% | Chunks: {chunk.chunk_number+1}/{chunk.total_chunks} | "
                      f"Results: {metrics.total_measurements:,}", end='', flush=True)
        
        print()  # New line after progress
        metrics.finish()
        print(f"✓ Test completed in {metrics.end_time - metrics.start_time:.2f}s")
        
    except grpc.RpcError as e:
        metrics.finish()
        metrics.errors.append(f"{e.code()}: {e.details()}")
        print(f"✗ Error: {e.code()}: {e.details()}")
    
    return metrics


def test_small_query(stub, chunk_size: int) -> PerformanceMetrics:
    """Test: Small query (single parameter, narrow AQI range)"""
    query_filter = fire_service_pb2.QueryFilter(
        parameters=["PM2.5"],
        min_aqi=0,
        max_aqi=50
    )
    return run_query_test(stub, f"Small Query (chunk_size={chunk_size})", query_filter, chunk_size)


def test_medium_query(stub, chunk_size: int) -> PerformanceMetrics:
    """Test: Medium query (2 parameters, moderate AQI range)"""
    query_filter = fire_service_pb2.QueryFilter(
        parameters=["PM2.5", "PM10"],
        min_aqi=0,
        max_aqi=100
    )
    return run_query_test(stub, f"Medium Query (chunk_size={chunk_size})", query_filter, chunk_size)


def test_large_query(stub, chunk_size: int) -> PerformanceMetrics:
    """Test: Large query (all parameters, wide AQI range)"""
    query_filter = fire_service_pb2.QueryFilter(
        parameters=["PM2.5", "PM10", "OZONE", "NO2", "SO2", "CO"],
        min_aqi=0,
        max_aqi=500
    )
    return run_query_test(stub, f"Large Query (chunk_size={chunk_size})", query_filter, chunk_size)


def test_no_filter_query(stub, chunk_size: int) -> PerformanceMetrics:
    """Test: No filter (all data)"""
    query_filter = fire_service_pb2.QueryFilter()
    return run_query_test(stub, f"No Filter Query (chunk_size={chunk_size})", query_filter, chunk_size)


def concurrent_query_worker(stub, test_name: str, query_filter, chunk_size: int, 
                            results: List, worker_id: int):
    """Worker function for concurrent query testing"""
    metrics = run_query_test(stub, f"{test_name} (Worker {worker_id})", query_filter, chunk_size)
    results.append(metrics)


def test_concurrent_queries(server_address: str, num_clients: int, chunk_size: int) -> List[PerformanceMetrics]:
    """Test concurrent queries from multiple clients"""
    print(f"\n{'='*60}")
    print(f"Concurrent Query Test: {num_clients} clients")
    print(f"{'='*60}")
    
    # Medium query for concurrent testing
    query_filter = fire_service_pb2.QueryFilter(
        parameters=["PM2.5", "PM10"],
        min_aqi=0,
        max_aqi=100
    )
    
    results = []
    threads = []
    
    start_time = time.time()
    
    for i in range(num_clients):
        # Each client gets its own channel
        channel = grpc.insecure_channel(server_address)
        stub = fire_service_pb2_grpc.FireQueryServiceStub(channel)
        
        thread = threading.Thread(
            target=concurrent_query_worker,
            args=(stub, f"Concurrent Query", query_filter, chunk_size, results, i+1)
        )
        threads.append(thread)
        thread.start()
    
    # Wait for all threads
    for thread in threads:
        thread.join()
    
    total_time = time.time() - start_time
    print(f"\n✓ All {num_clients} concurrent queries completed in {total_time:.2f}s")
    
    return results


def run_all_tests(server_address: str = "localhost:50051") -> Dict[str, Any]:
    """Run all performance tests"""
    print("\n" + "="*80)
    print("FIRE QUERY SYSTEM - PERFORMANCE TEST SUITE")
    print("="*80)
    print(f"Server: {server_address}")
    print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*80)
    
    # Create channel
    channel = grpc.insecure_channel(server_address)
    stub = fire_service_pb2_grpc.FireQueryServiceStub(channel)
    
    all_results = {
        "metadata": {
            "server": server_address,
            "timestamp": datetime.now().isoformat(),
            "deployment": "single_computer"
        },
        "tests": {}
    }
    
    # Test 1: Different chunk sizes
    print("\n" + "#"*80)
    print("# TEST SUITE 1: Chunk Size Optimization")
    print("#"*80)
    
    chunk_sizes = [100, 500, 1000, 5000]
    all_results["tests"]["chunk_size_comparison"] = []
    
    for chunk_size in chunk_sizes:
        print(f"\n--- Testing with chunk_size={chunk_size} ---")
        metrics = test_medium_query(stub, chunk_size)
        all_results["tests"]["chunk_size_comparison"].append(metrics.get_results())
    
    # Test 2: Query complexity
    print("\n" + "#"*80)
    print("# TEST SUITE 2: Query Complexity")
    print("#"*80)
    
    chunk_size = 1000  # Standard chunk size
    all_results["tests"]["query_complexity"] = []
    
    # Small query
    metrics = test_small_query(stub, chunk_size)
    all_results["tests"]["query_complexity"].append(metrics.get_results())
    
    # Medium query
    metrics = test_medium_query(stub, chunk_size)
    all_results["tests"]["query_complexity"].append(metrics.get_results())
    
    # Large query
    metrics = test_large_query(stub, chunk_size)
    all_results["tests"]["query_complexity"].append(metrics.get_results())
    
    # No filter
    metrics = test_no_filter_query(stub, chunk_size)
    all_results["tests"]["query_complexity"].append(metrics.get_results())
    
    # Test 3: Concurrent clients
    print("\n" + "#"*80)
    print("# TEST SUITE 3: Concurrent Client Testing")
    print("#"*80)
    
    channel.close()  # Close the main channel
    
    all_results["tests"]["concurrent_clients"] = {}
    
    for num_clients in [1, 2, 5]:
        print(f"\n--- Testing with {num_clients} concurrent client(s) ---")
        concurrent_results = test_concurrent_queries(server_address, num_clients, 1000)
        
        # Aggregate results
        all_results["tests"]["concurrent_clients"][f"{num_clients}_clients"] = {
            "num_clients": num_clients,
            "results": [m.get_results() for m in concurrent_results],
            "total_measurements": sum(m.total_measurements for m in concurrent_results),
            "avg_time": statistics.mean([m.end_time - m.start_time for m in concurrent_results]),
            "total_time": max(m.end_time for m in concurrent_results) - min(m.start_time for m in concurrent_results)
        }
    
    print("\n" + "="*80)
    print("ALL TESTS COMPLETED")
    print("="*80)
    
    return all_results


def save_results(results: Dict[str, Any], output_file: str):
    """Save results to JSON file"""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n✓ Results saved to: {output_file}")


def print_summary(results: Dict[str, Any]):
    """Print a summary of the test results"""
    print("\n" + "="*80)
    print("PERFORMANCE TEST SUMMARY")
    print("="*80)
    
    # Chunk size comparison
    print("\n1. Chunk Size Optimization:")
    print("-" * 60)
    for test in results["tests"]["chunk_size_comparison"]:
        chunk_match = test["test_name"].split("chunk_size=")[1].split(")")[0]
        print(f"  Chunk Size {chunk_match:>5}: "
              f"{test['total_time']:6.2f}s | "
              f"{test['throughput']:8.0f} measurements/s | "
              f"First chunk: {test['time_to_first_chunk']:.3f}s")
    
    # Query complexity
    print("\n2. Query Complexity:")
    print("-" * 60)
    for test in results["tests"]["query_complexity"]:
        test_type = test["test_name"].split(" Query")[0]
        print(f"  {test_type:12}: "
              f"{test['total_time']:6.2f}s | "
              f"{test['total_measurements']:7,} results | "
              f"{test['throughput']:8.0f} measurements/s")
    
    # Concurrent clients
    print("\n3. Concurrent Client Performance:")
    print("-" * 60)
    for key, data in results["tests"]["concurrent_clients"].items():
        num = data["num_clients"]
        print(f"  {num} client(s): "
              f"Avg {data['avg_time']:6.2f}s | "
              f"Total measurements: {data['total_measurements']:,}")
    
    print("\n" + "="*80)


def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Performance testing for Fire Query System')
    parser.add_argument('--server', default='localhost:50051', help='Server address')
    parser.add_argument('--output', default='results/single_computer.json', help='Output file for results')
    args = parser.parse_args()
    
    try:
        # Run all tests
        results = run_all_tests(args.server)
        
        # Print summary
        print_summary(results)
        
        # Save results
        save_results(results, args.output)
        
        print("\n✓ Performance testing complete!")
        
    except KeyboardInterrupt:
        print("\n\nTesting interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

