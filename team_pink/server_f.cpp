/**
 * @file server_f.cpp
 * @brief Process F - Team Pink Worker Server (C++)
 * 
 * Worker process that handles queries for Team Pink data subset
 */

#include <iostream>
#include <memory>
#include <string>
#include <fstream>
#include <set>
#include <vector>
#include <grpcpp/grpcpp.h>
#include <nlohmann/json.hpp>

#include "../proto/fire_service.grpc.pb.h"
#include "FireColumnModel.hpp"

using grpc::Server;
using grpc::ServerBuilder;
using grpc::ServerContext;
using grpc::Status;
using fire_service::FireQueryService;
using fire_service::QueryRequest;
using fire_service::QueryResponseChunk;
using fire_service::InternalQueryRequest;
using fire_service::InternalQueryResponse;
using fire_service::StatusRequest;
using fire_service::StatusResponse;
using fire_service::FireMeasurement;
using json = nlohmann::json;

/**
 * @class FireQueryServiceImpl
 * @brief Implementation of FireQueryService for Process F (Worker)
 */
class FireQueryServiceImpl final : public FireQueryService::Service {
private:
    std::string process_id_;
    std::string role_;
    std::string team_;
    int port_;
    
    // FireColumnModel for local data storage
    FireColumnModel data_model_;

public:
    FireQueryServiceImpl(const json& config) {
        process_id_ = config["identity"];
        role_ = config["role"];
        team_ = config["team"];
        port_ = config["port"];
        
        std::cout << "[" << process_id_ << "] Initialized as " << role_ 
                  << " for Team " << team_ << std::endl;
        
        // Initialize FireColumnModel with Team Pink data subset
        std::vector<std::string> allowed_dirs;
        if (config.contains("data_partition") && config["data_partition"]["enabled"]) {
            for (const auto& dir : config["data_partition"]["directories"]) {
                allowed_dirs.push_back(dir.get<std::string>());
            }
            std::cout << "[" << process_id_ << "] Loading partitioned data from " 
                      << allowed_dirs.size() << " subdirectories..." << std::endl;
        } else {
            std::cout << "[" << process_id_ << "] Loading all data from data/ directory..." << std::endl;
        }
        data_model_.readFromDirectory("data/", allowed_dirs);
        std::cout << "[" << process_id_ << "] Data model initialized with " 
                  << data_model_.measurementCount() << " measurements" << std::endl;
    }
    
    /**
     * Query RPC - Workers typically don't receive direct client queries
     */
    Status Query(ServerContext* context, 
                 const QueryRequest* request,
                 grpc::ServerWriter<QueryResponseChunk>* writer) override {
        std::cout << "[" << process_id_ << "] Received direct query request_id=" 
                  << request->request_id() << std::endl;
        
        // Return empty response
        QueryResponseChunk chunk;
        chunk.set_request_id(request->request_id());
        chunk.set_chunk_number(0);
        chunk.set_is_last_chunk(true);
        chunk.set_total_chunks(1);
        chunk.set_total_results(0);
        
        writer->Write(chunk);
        return Status::OK;
    }
    
    /**
     * InternalQuery RPC - Main method for workers
     * Receives queries from team leader (Process E)
     */
    Status InternalQuery(ServerContext* context,
                        const InternalQueryRequest* request,
                        InternalQueryResponse* response) override {
        std::cout << "[" << process_id_ << "] Internal query from " 
                  << request->requesting_process() << std::endl;
        std::cout << "  Request ID: " << request->request_id() << std::endl;
        std::cout << "  Original request: " << request->original_request_id() << std::endl;
        std::cout << "  Query type: " << request->query_type() << std::endl;
        
        // Query local FireColumnModel data
        std::vector<std::size_t> matching_indices;
        
        if (request->has_filter()) {
            const auto& filter = request->filter();
            
            // Start with parameter or site filtering (OR logic for multiple parameters)
            if (filter.parameters_size() > 0) {
                // Handle multiple parameters (OR logic - match any parameter)
                std::set<std::size_t> all_param_indices;
                for (int i = 0; i < filter.parameters_size(); ++i) {
                    const std::string& param = filter.parameters(i);
                    auto param_indices = data_model_.getIndicesByParameter(param);
                    all_param_indices.insert(param_indices.begin(), param_indices.end());
                }
                matching_indices.assign(all_param_indices.begin(), all_param_indices.end());
                std::cout << "  Filtered by " << filter.parameters_size() << " parameter(s): " 
                         << matching_indices.size() << " matches" << std::endl;
            }
            // Filter by site name - use first site if specified
            else if (filter.site_names_size() > 0) {
                const std::string& site = filter.site_names(0);
                matching_indices = data_model_.getIndicesBySite(site);
                std::cout << "  Filtered by site '" << site 
                         << "': " << matching_indices.size() << " matches" << std::endl;
            }
            else {
                // No parameter/site filter - start with all measurements
                for (std::size_t i = 0; i < data_model_.measurementCount(); ++i) {
                    matching_indices.push_back(i);
                }
            }
            
            // Apply AQI range filter (AND logic - must also match AQI range)
            if (filter.min_aqi() > 0 || filter.max_aqi() > 0) {
                std::vector<std::size_t> filtered_indices;
                const auto& all_aqis = data_model_.aqis();
                for (std::size_t idx : matching_indices) {
                    int aqi = all_aqis[idx];
                    bool matches = true;
                    
                    if (filter.min_aqi() > 0 && aqi < filter.min_aqi()) {
                        matches = false;
                    }
                    if (filter.max_aqi() > 0 && aqi > filter.max_aqi()) {
                        matches = false;
                    }
                    
                    if (matches) {
                        filtered_indices.push_back(idx);
                    }
                }
                matching_indices = filtered_indices;
                std::cout << "  After AQI filter [" << filter.min_aqi() << "-" << filter.max_aqi() 
                         << "]: " << matching_indices.size() << " matches" << std::endl;
            }
        } else {
            // No filter - return all measurements
            for (std::size_t i = 0; i < data_model_.measurementCount(); ++i) {
                matching_indices.push_back(i);
            }
        }
        
        // Convert results to FireMeasurement proto messages
        for (std::size_t idx : matching_indices) {
            auto* measurement = response->add_measurements();
            measurement->set_latitude(data_model_.latitudes()[idx]);
            measurement->set_longitude(data_model_.longitudes()[idx]);
            measurement->set_datetime(data_model_.datetimes()[idx]);
            measurement->set_parameter(data_model_.parameters()[idx]);
            measurement->set_concentration(data_model_.concentrations()[idx]);
            measurement->set_unit(data_model_.units()[idx]);
            measurement->set_raw_concentration(data_model_.rawConcentrations()[idx]);
            measurement->set_aqi(data_model_.aqis()[idx]);
            measurement->set_category(data_model_.categories()[idx]);
            measurement->set_site_name(data_model_.siteNames()[idx]);
            measurement->set_agency_name(data_model_.agencyNames()[idx]);
            measurement->set_aqs_code(data_model_.aqsCodes()[idx]);
            measurement->set_full_aqs_code(data_model_.fullAqsCodes()[idx]);
        }
        
        response->set_request_id(request->request_id());
        response->set_original_request_id(request->original_request_id());
        response->set_is_complete(true);
        response->set_responding_process(process_id_);
        
        std::cout << "[" << process_id_ << "] Returning response with " 
                  << response->measurements_size() << " measurements" << std::endl;
        
        return Status::OK;
    }
    
    /**
     * CancelRequest RPC
     */
    Status CancelRequest(ServerContext* context,
                        const StatusRequest* request,
                        StatusResponse* response) override {
        std::cout << "[" << process_id_ << "] Cancel request_id=" 
                  << request->request_id() << std::endl;
        
        response->set_request_id(request->request_id());
        response->set_status("cancelled");
        response->set_chunks_delivered(0);
        response->set_total_chunks(0);
        
        return Status::OK;
    }
    
    /**
     * GetStatus RPC
     */
    Status GetStatus(ServerContext* context,
                    const StatusRequest* request,
                    StatusResponse* response) override {
        std::cout << "[" << process_id_ << "] Status request_id=" 
                  << request->request_id() << std::endl;
        
        response->set_request_id(request->request_id());
        response->set_status("pending");
        response->set_chunks_delivered(0);
        response->set_total_chunks(0);
        
        return Status::OK;
    }
    
    /**
     * Notify RPC
     */
    Status Notify(ServerContext* context,
                 const InternalQueryRequest* request,
                 StatusResponse* response) override {
        std::cout << "[" << process_id_ << "] Notification from " 
                  << request->requesting_process() << std::endl;
        
        response->set_request_id(request->request_id());
        response->set_status("acknowledged");
        
        return Status::OK;
    }
};

/**
 * Load configuration from JSON file
 */
json load_config(const std::string& config_path) {
    std::ifstream file(config_path);
    if (!file.is_open()) {
        throw std::runtime_error("Could not open config file: " + config_path);
    }
    
    json config;
    file >> config;
    return config;
}

/**
 * Start the gRPC server
 */
void RunServer(const std::string& config_path) {
    // Load configuration
    json config = load_config(config_path);
    std::string process_id = config["identity"];
    std::string hostname = config["hostname"];
    int port = config["port"];
    
    // Build server address
    std::string server_address = hostname + ":" + std::to_string(port);
    
    // Create service implementation
    FireQueryServiceImpl service(config);
    
    // Build and start server
    ServerBuilder builder;
    builder.AddListeningPort(server_address, grpc::InsecureServerCredentials());
    builder.RegisterService(&service);
    
    std::unique_ptr<Server> server(builder.BuildAndStart());
    std::cout << "[" << process_id << "] Server started on " << server_address << std::endl;
    std::cout << "[" << process_id << "] Press Ctrl+C to stop" << std::endl;
    
    // Wait for server shutdown
    server->Wait();
}

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "Usage: " << argv[0] << " <config_file>" << std::endl;
        std::cerr << "Example: " << argv[0] << " ../configs/process_f.json" << std::endl;
        return 1;
    }
    
    try {
        RunServer(argv[1]);
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << std::endl;
        return 1;
    }
    
    return 0;
}

