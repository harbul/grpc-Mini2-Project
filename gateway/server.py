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
import time
import threading

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
        
        # Request tracking for cancellation and status
        self.active_requests = {}  # request_id -> {status, start_time, chunks_sent, cancelled}
        self.request_lock = threading.Lock()
        
        print(f"[{self.process_id}] Initialized as {self.role}")
        print(f"[{self.process_id}] Neighbors: {[n['process_id'] for n in self.neighbors]}")
    
    def Query(self, request, context):
        """
        Handle client query request with progressive chunked streaming
        Returns a stream of QueryResponseChunk messages
        """
        request_id = request.request_id
        start_time = time.time()
        
        print(f"[{self.process_id}] Received query request_id={request_id}")
        print(f"  Query type: {request.query_type}")
        print(f"  Parameters: {list(request.filter.parameters)}")
        print(f"  Chunk size: {request.max_results_per_chunk}")
        
        # Register request
        with self.request_lock:
            self.active_requests[request_id] = {
                'status': 'processing',
                'start_time': start_time,
                'chunks_sent': 0,
                'total_chunks': 0,
                'cancelled': False
            }
        
        try:
            # Forward query to Team Leaders (B and E) and aggregate results
            all_measurements = self.forward_to_team_leaders(request)
            
            # Check if cancelled before streaming
            if self._is_cancelled(request_id):
                print(f"[{self.process_id}] Request {request_id} cancelled before streaming")
                return
            
            print(f"[{self.process_id}] Aggregated {len(all_measurements)} total measurements")
            
            # Split results into chunks
            max_per_chunk = request.max_results_per_chunk if request.max_results_per_chunk > 0 else 1000
            total_results = len(all_measurements)
            total_chunks = (total_results + max_per_chunk - 1) // max_per_chunk if total_results > 0 else 1
            
            # Update total chunks
            with self.request_lock:
                if request_id in self.active_requests:
                    self.active_requests[request_id]['total_chunks'] = total_chunks
            
            if total_results == 0:
                # Return empty result
                chunk = fire_service_pb2.QueryResponseChunk(
                    request_id=request_id,
                    chunk_number=0,
                    is_last_chunk=True,
                    total_chunks=1,
                    total_results=0
                )
                print(f"[{self.process_id}] Sending chunk 0/1 (empty result)")
                yield chunk
                self._update_chunks_sent(request_id, 1)
            else:
                # Send results in chunks
                for chunk_idx in range(total_chunks):
                    # Check for cancellation before each chunk
                    if self._is_cancelled(request_id):
                        print(f"[{self.process_id}] Request {request_id} cancelled at chunk {chunk_idx}/{total_chunks}")
                        break
                    
                    # Check if client disconnected
                    if context.is_active() == False:
                        print(f"[{self.process_id}] Client disconnected for request {request_id}")
                        self._mark_cancelled(request_id)
                        break
                    
                    start_idx = chunk_idx * max_per_chunk
                    end_idx = min(start_idx + max_per_chunk, total_results)
                    chunk_measurements = all_measurements[start_idx:end_idx]
                    
                    chunk = fire_service_pb2.QueryResponseChunk(
                        request_id=request_id,
                        chunk_number=chunk_idx,
                        is_last_chunk=(chunk_idx == total_chunks - 1),
                        total_chunks=total_chunks,
                        total_results=total_results
                    )
                    chunk.measurements.extend(chunk_measurements)
                    
                    print(f"[{self.process_id}] Sending chunk {chunk_idx + 1}/{total_chunks} with {len(chunk_measurements)} measurements")
                    yield chunk
                    self._update_chunks_sent(request_id, chunk_idx + 1)
                    
                    # Small delay to simulate progressive streaming
                    time.sleep(0.01)
            
            # Mark as completed
            elapsed = time.time() - start_time
            print(f"[{self.process_id}] Request {request_id} completed in {elapsed:.2f}s")
            self._mark_completed(request_id)
            
        except Exception as e:
            print(f"[{self.process_id}] Error processing request {request_id}: {e}")
            self._mark_failed(request_id)
            raise
        finally:
            # Cleanup after delay
            threading.Timer(60.0, lambda: self._cleanup_request(request_id)).start()
    
    def forward_to_team_leaders(self, request):
        """
        Forward query to Team Leaders (B and E) and aggregate results
        Returns list of all measurements from both teams
        """
        all_measurements = []
        
        # Create internal query request
        internal_request = fire_service_pb2.InternalQueryRequest(
            request_id=request.request_id,
            original_request_id=str(request.request_id),
            filter=request.filter,
            query_type=request.query_type,
            requesting_process=self.process_id
        )
        
        # Forward to each team leader
        for neighbor in self.neighbors:
            neighbor_id = neighbor['process_id']
            neighbor_address = f"{neighbor['hostname']}:{neighbor['port']}"
            
            print(f"[{self.process_id}] Forwarding query to Team Leader {neighbor_id} at {neighbor_address}")
            
            try:
                # Create channel to team leader with increased message size limits
                options = [
                    ('grpc.max_receive_message_length', 100 * 1024 * 1024),  # 100MB
                    ('grpc.max_send_message_length', 100 * 1024 * 1024),     # 100MB
                ]
                channel = grpc.insecure_channel(neighbor_address, options=options)
                stub = fire_service_pb2_grpc.FireQueryServiceStub(channel)
                
                # Forward the query
                response = stub.InternalQuery(internal_request)
                
                # Collect measurements
                measurements_count = len(response.measurements)
                all_measurements.extend(response.measurements)
                print(f"[{self.process_id}] Received {measurements_count} measurements from {neighbor_id}")
                
                channel.close()
                
            except grpc.RpcError as e:
                print(f"[{self.process_id}] Error contacting {neighbor_id}: {e.code()}: {e.details()}")
        
        return all_measurements
    
    def CancelRequest(self, request, context):
        """Handle request cancellation"""
        request_id = request.request_id
        print(f"[{self.process_id}] Cancel request_id={request_id}")
        
        with self.request_lock:
            if request_id in self.active_requests:
                self.active_requests[request_id]['cancelled'] = True
                self.active_requests[request_id]['status'] = 'cancelled'
                chunks_sent = self.active_requests[request_id]['chunks_sent']
                total_chunks = self.active_requests[request_id]['total_chunks']
                
                print(f"[{self.process_id}] Request {request_id} marked as cancelled ({chunks_sent}/{total_chunks} chunks sent)")
                
                return fire_service_pb2.StatusResponse(
                    request_id=request_id,
                    status="cancelled",
                    chunks_delivered=chunks_sent,
                    total_chunks=total_chunks
                )
            else:
                print(f"[{self.process_id}] Request {request_id} not found (may have already completed)")
                return fire_service_pb2.StatusResponse(
                    request_id=request_id,
                    status="not_found",
                    chunks_delivered=0,
                    total_chunks=0
                )
    
    def GetStatus(self, request, context):
        """Handle status check"""
        request_id = request.request_id
        print(f"[{self.process_id}] Status check for request_id={request_id}")
        
        with self.request_lock:
            if request_id in self.active_requests:
                req_info = self.active_requests[request_id]
                status = req_info['status']
                chunks_sent = req_info['chunks_sent']
                total_chunks = req_info['total_chunks']
                
                print(f"[{self.process_id}] Status: {status}, Progress: {chunks_sent}/{total_chunks}")
                
                return fire_service_pb2.StatusResponse(
                    request_id=request_id,
                    status=status,
                    chunks_delivered=chunks_sent,
                    total_chunks=total_chunks
                )
            else:
                print(f"[{self.process_id}] Request {request_id} not found in active requests")
                return fire_service_pb2.StatusResponse(
                    request_id=request_id,
                    status="not_found",
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
    
    # Helper methods for request tracking
    def _is_cancelled(self, request_id):
        """Check if a request has been cancelled"""
        with self.request_lock:
            if request_id in self.active_requests:
                return self.active_requests[request_id]['cancelled']
        return False
    
    def _mark_cancelled(self, request_id):
        """Mark a request as cancelled"""
        with self.request_lock:
            if request_id in self.active_requests:
                self.active_requests[request_id]['cancelled'] = True
                self.active_requests[request_id]['status'] = 'cancelled'
    
    def _mark_completed(self, request_id):
        """Mark a request as completed"""
        with self.request_lock:
            if request_id in self.active_requests:
                self.active_requests[request_id]['status'] = 'completed'
    
    def _mark_failed(self, request_id):
        """Mark a request as failed"""
        with self.request_lock:
            if request_id in self.active_requests:
                self.active_requests[request_id]['status'] = 'failed'
    
    def _update_chunks_sent(self, request_id, chunks_sent):
        """Update the number of chunks sent"""
        with self.request_lock:
            if request_id in self.active_requests:
                self.active_requests[request_id]['chunks_sent'] = chunks_sent
    
    def _cleanup_request(self, request_id):
        """Remove request from tracking after delay"""
        with self.request_lock:
            if request_id in self.active_requests:
                print(f"[{self.process_id}] Cleaning up request {request_id}")
                del self.active_requests[request_id]


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

