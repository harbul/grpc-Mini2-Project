#!/usr/bin/env python3
"""
Process A - Gateway Server
Entry point for client queries, coordinates Team Green and Team Pink
"""

import json
import grpc
from concurrent import futures
import sys
import os

# Add proto directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'proto'))

import fire_service_pb2
import fire_service_pb2_grpc


class FireQueryServiceImpl(fire_service_pb2_grpc.FireQueryServiceServicer):
    """Implementation of FireQueryService for Process A (Gateway)"""
    
    def __init__(self, config):
        self.config = config
        self.process_id = config['identity']
        self.role = config['role']
        self.neighbors = config['neighbors']
        print(f"[{self.process_id}] Initialized as {self.role}")
        print(f"[{self.process_id}] Neighbors: {[n['process_id'] for n in self.neighbors]}")
    
    def Query(self, request, context):
        """
        Handle client query request
        Returns a stream of QueryResponseChunk messages
        """
        print(f"[{self.process_id}] Received query request_id={request.request_id}")
        
        # TODO: Forward query to Team Leaders (B and E)
        # TODO: Aggregate results from teams
        # TODO: Split results into chunks
        
        # For now, return a dummy response
        chunk = fire_service_pb2.QueryResponseChunk(
            request_id=request.request_id,
            chunk_number=0,
            is_last_chunk=True,
            total_chunks=1,
            total_results=0
        )
        yield chunk
    
    def CancelRequest(self, request, context):
        """Handle request cancellation"""
        print(f"[{self.process_id}] Cancel request_id={request.request_id}")
        
        # TODO: Cancel ongoing query and notify downstream processes
        
        return fire_service_pb2.StatusResponse(
            request_id=request.request_id,
            status="cancelled",
            chunks_delivered=0,
            total_chunks=0
        )
    
    def GetStatus(self, request, context):
        """Handle status check"""
        print(f"[{self.process_id}] Status request_id={request.request_id}")
        
        # TODO: Check actual status from request tracking
        
        return fire_service_pb2.StatusResponse(
            request_id=request.request_id,
            status="pending",
            chunks_delivered=0,
            total_chunks=0
        )
    
    def InternalQuery(self, request, context):
        """Handle internal queries from other processes"""
        print(f"[{self.process_id}] Internal query from {request.requesting_process}")
        
        # TODO: Query local FireColumnModel data
        # TODO: Return matching measurements
        
        return fire_service_pb2.InternalQueryResponse(
            request_id=request.request_id,
            original_request_id=request.original_request_id,
            is_complete=True,
            responding_process=self.process_id
        )
    
    def Notify(self, request, context):
        """Handle notifications from other processes"""
        print(f"[{self.process_id}] Notification from {request.requesting_process}")
        
        return fire_service_pb2.StatusResponse(
            request_id=request.request_id,
            status="acknowledged"
        )


def load_config(config_path):
    """Load configuration from JSON file"""
    with open(config_path, 'r') as f:
        return json.load(f)


def serve(config_path):
    """Start the gRPC server"""
    # Load configuration
    config = load_config(config_path)
    process_id = config['identity']
    hostname = config['hostname']
    port = config['port']
    
    # Create server
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    
    # Add service implementation
    service_impl = FireQueryServiceImpl(config)
    fire_service_pb2_grpc.add_FireQueryServiceServicer_to_server(service_impl, server)
    
    # Bind to address
    server_address = f"{hostname}:{port}"
    server.add_insecure_port(server_address)
    
    # Start server
    server.start()
    print(f"[{process_id}] Server started on {server_address}")
    print(f"[{process_id}] Press Ctrl+C to stop")
    
    # Keep server running
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        print(f"\n[{process_id}] Shutting down...")
        server.stop(0)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python server.py <config_file>")
        print("Example: python server.py ../configs/process_a.json")
        sys.exit(1)
    
    config_path = sys.argv[1]
    serve(config_path)

