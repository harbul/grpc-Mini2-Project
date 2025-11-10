#include "FireColumnModel.hpp"
#include "utils.hpp"
#include "readcsv.hpp"
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <algorithm>
#include <filesystem>
#include <iostream>
#include <iomanip>

FireColumnModel::FireColumnModel() 
    : _min_latitude(0.0), _max_latitude(0.0), _min_longitude(0.0), _max_longitude(0.0),
      _bounds_initialized(false) {
    _datetime_range.resize(2);
}

FireColumnModel::~FireColumnModel() = default;

void FireColumnModel::readFromDirectory(const std::string& directoryPath) {
    auto csvFiles = getCSVFiles(directoryPath);
    
    if (csvFiles.empty()) {
        std::cout << "[FireColumnModel] No CSV files found in: " << directoryPath << std::endl;
        return;
    }
    
    std::cout << "[FireColumnModel] Processing " << csvFiles.size() << " CSV files..." << std::endl;
    
    for (const auto& file : csvFiles) {
        try {
            readFromCSV(file);
        } catch (const std::exception& e) {
            std::cerr << "[FireColumnModel] Error processing " << file << ": " << e.what() << std::endl;
        }
    }
    
    std::cout << "[FireColumnModel] Loaded " << measurementCount() << " measurements from " 
              << siteCount() << " sites" << std::endl;
}

void FireColumnModel::readFromCSV(const std::string& filename) {
    CSVReader reader(filename);
    
    try {
        reader.open();
    } catch (const std::exception& e) {
        throw std::runtime_error("Failed to open CSV file " + filename + ": " + e.what());
    }
    
    std::vector<std::string> row;
    bool headerSkipped = false;
    
    while (reader.readRow(row)) {
        if (!headerSkipped) {
            headerSkipped = true;
            continue;
        }
        
        if (row.size() < 13) {
            continue;
        }
        
        try {
            double latitude = std::stod(row[0]);
            double longitude = std::stod(row[1]);
            std::string datetime = row[2];
            std::string parameter = row[3];
            double concentration = std::stod(row[4]);
            std::string unit = row[5];
            double raw_concentration = std::stod(row[6]);
            int aqi = std::stoi(row[7]);
            int category = std::stoi(row[8]);
            std::string site_name = row[9];
            std::string agency_name = row[10];
            std::string aqs_code = row[11];
            std::string full_aqs_code = row[12];
            
            insertMeasurement(latitude, longitude, datetime, parameter, concentration,
                            unit, raw_concentration, aqi, category, site_name,
                            agency_name, aqs_code, full_aqs_code);
            
        } catch (const std::exception& e) {
            continue;
        }
    }
    
    reader.close();
}

void FireColumnModel::insertMeasurement(double latitude, double longitude, const std::string& datetime,
                                       const std::string& parameter, double concentration, const std::string& unit,
                                       double raw_concentration, int aqi, int category,
                                       const std::string& site_name, const std::string& agency_name,
                                       const std::string& aqs_code, const std::string& full_aqs_code) {
    _latitudes.push_back(latitude);
    _longitudes.push_back(longitude);
    _datetimes.push_back(datetime);
    _parameters.push_back(parameter);
    _concentrations.push_back(concentration);
    _units.push_back(unit);
    _raw_concentrations.push_back(raw_concentration);
    _aqis.push_back(aqi);
    _categories.push_back(category);
    _site_names.push_back(site_name);
    _agency_names.push_back(agency_name);
    _aqs_codes.push_back(aqs_code);
    _full_aqs_codes.push_back(full_aqs_code);
    
    std::size_t newIndex = _latitudes.size() - 1;
    updateIndices(newIndex);
    updateGeographicBounds(latitude, longitude);
    updateDatetimeRange(datetime);
    
    _unique_sites.insert(site_name);
    _unique_parameters.insert(parameter);
    _unique_agencies.insert(agency_name);
}

std::vector<std::size_t> FireColumnModel::getIndicesBySite(const std::string& siteName) const {
    auto it = _site_indices.find(siteName);
    return (it != _site_indices.end()) ? it->second : std::vector<std::size_t>{};
}

std::vector<std::size_t> FireColumnModel::getIndicesByParameter(const std::string& parameter) const {
    auto it = _parameter_indices.find(parameter);
    return (it != _parameter_indices.end()) ? it->second : std::vector<std::size_t>{};
}

std::vector<std::size_t> FireColumnModel::getIndicesByAqsCode(const std::string& aqsCode) const {
    auto it = _aqs_indices.find(aqsCode);
    return (it != _aqs_indices.end()) ? it->second : std::vector<std::size_t>{};
}

void FireColumnModel::getGeographicBounds(double& min_lat, double& max_lat, 
                                         double& min_lon, double& max_lon) const {
    if (_bounds_initialized) {
        min_lat = _min_latitude;
        max_lat = _max_latitude;
        min_lon = _min_longitude;
        max_lon = _max_longitude;
    } else {
        min_lat = max_lat = min_lon = max_lon = 0.0;
    }
}

void FireColumnModel::updateIndices(std::size_t index) {
    if (index >= _site_names.size()) return;
    
    _site_indices[_site_names[index]].push_back(index);
    _parameter_indices[_parameters[index]].push_back(index);
    _aqs_indices[_aqs_codes[index]].push_back(index);
}

void FireColumnModel::updateGeographicBounds(double latitude, double longitude) {
    if (!_bounds_initialized) {
        _min_latitude = _max_latitude = latitude;
        _min_longitude = _max_longitude = longitude;
        _bounds_initialized = true;
    } else {
        _min_latitude = std::min(_min_latitude, latitude);
        _max_latitude = std::max(_max_latitude, latitude);
        _min_longitude = std::min(_min_longitude, longitude);
        _max_longitude = std::max(_max_longitude, longitude);
    }
}

void FireColumnModel::updateDatetimeRange(const std::string& datetime) {
    if (_datetime_range[0].empty() || datetime < _datetime_range[0]) {
        _datetime_range[0] = datetime;
    }
    if (_datetime_range[1].empty() || datetime > _datetime_range[1]) {
        _datetime_range[1] = datetime;
    }
}

std::vector<std::string> FireColumnModel::getCSVFiles(const std::string& directoryPath) const {
    std::vector<std::string> csvFiles;
    
    try {
        for (const auto& entry : std::filesystem::recursive_directory_iterator(directoryPath)) {
            if (entry.is_regular_file()) {
                const std::string filename = entry.path().string();
                if (filename.size() >= 4 && 
                    filename.substr(filename.size() - 4) == ".csv") {
                    csvFiles.push_back(filename);
                }
            }
        }
    } catch (const std::filesystem::filesystem_error& e) {
        std::cerr << "[FireColumnModel] Error accessing directory " << directoryPath << ": " << e.what() << std::endl;
    }
    
    std::sort(csvFiles.begin(), csvFiles.end());
    return csvFiles;
}

