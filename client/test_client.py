#!/usr/bin/env python3
"""
Test Client for Process A (Gateway)
Simple Python client to verify server is working
"""

import grpc
import sys
import os

# Add proto directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'proto'))

import fire_service_pb2
import fire_service_pb2_grpc


def test_query(stub):
    """Test the Query RPC method"""
    print("\n=== Testing Query RPC ===")
    
    # Create a query request
    query_filter = fire_service_pb2.QueryFilter(
        parameters=["PM2.5", "PM10"],
        min_aqi=0,
        max_aqi=100
    )
    
    request = fire_service_pb2.QueryRequest(
        request_id=12345,
        filter=query_filter,
        query_type="filter",
        require_chunked=True,
        max_results_per_chunk=100
    )
    
    print(f"Sending query request_id={request.request_id}")
    print(f"  Parameters: {list(request.filter.parameters)}")
    print(f"  AQI range: {request.filter.min_aqi} - {request.filter.max_aqi}")
    
    # Send request and receive streaming response
    try:
        for chunk in stub.Query(request):
            print(f"\nReceived chunk #{chunk.chunk_number}")
            print(f"  Request ID: {chunk.request_id}")
            print(f"  Measurements: {len(chunk.measurements)}")
            print(f"  Total results: {chunk.total_results}")
            print(f"  Total chunks: {chunk.total_chunks}")
            print(f"  Is last chunk: {chunk.is_last_chunk}")
    except grpc.RpcError as e:
        print(f"Error: {e.code()}: {e.details()}")


def test_get_status(stub):
    """Test the GetStatus RPC method"""
    print("\n=== Testing GetStatus RPC ===")
    
    request = fire_service_pb2.StatusRequest(
        request_id=12345,
        action="status"
    )
    
    print(f"Checking status for request_id={request.request_id}")
    
    try:
        response = stub.GetStatus(request)
        print(f"\nStatus response:")
        print(f"  Request ID: {response.request_id}")
        print(f"  Status: {response.status}")
        print(f"  Chunks delivered: {response.chunks_delivered}/{response.total_chunks}")
    except grpc.RpcError as e:
        print(f"Error: {e.code()}: {e.details()}")


def test_cancel_request(stub):
    """Test the CancelRequest RPC method"""
    print("\n=== Testing CancelRequest RPC ===")
    
    request = fire_service_pb2.StatusRequest(
        request_id=12345,
        action="cancel"
    )
    
    print(f"Cancelling request_id={request.request_id}")
    
    try:
        response = stub.CancelRequest(request)
        print(f"\nCancel response:")
        print(f"  Request ID: {response.request_id}")
        print(f"  Status: {response.status}")
    except grpc.RpcError as e:
        print(f"Error: {e.code()}: {e.details()}")


def main():
    """Main test function"""
    # Server address (Process A)
    server_address = "localhost:50051"
    
    print(f"Connecting to gateway server at {server_address}...")
    
    # Create a channel
    channel = grpc.insecure_channel(server_address)
    
    # Create a stub
    stub = fire_service_pb2_grpc.FireQueryServiceStub(channel)
    
    print("Connected successfully!\n")
    
    # Run tests
    test_query(stub)
    test_get_status(stub)
    test_cancel_request(stub)
    
    # Close channel
    channel.close()
    print("\n=== All tests completed ===")


if __name__ == '__main__':
    try:
        main()
    except grpc.RpcError as e:
        print(f"\nFailed to connect: {e.code()}: {e.details()}")
        print("\nMake sure the gateway server is running:")
        print("  cd gateway")
        print("  python server.py ../configs/process_a.json")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)

