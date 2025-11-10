#pragma once

#include <string>
#include <vector>
#include <functional>
#include <unordered_map>
#include <unordered_set>

/**
 * @file FireColumnModel.hpp
 * @brief Column-oriented fire air quality data model (Simplified for mini2 - no OpenMP)
 */

class FireColumnModel {
private:
    // Columnar storage
    std::vector<double> _latitudes;
    std::vector<double> _longitudes;
    std::vector<std::string> _datetimes;
    std::vector<std::string> _parameters;
    std::vector<double> _concentrations;
    std::vector<std::string> _units;
    std::vector<double> _raw_concentrations;
    std::vector<int> _aqis;
    std::vector<int> _categories;
    std::vector<std::string> _site_names;
    std::vector<std::string> _agency_names;
    std::vector<std::string> _aqs_codes;
    std::vector<std::string> _full_aqs_codes;

    // Index structures for fast lookups
    std::unordered_map<std::string, std::vector<std::size_t>> _site_indices;
    std::unordered_map<std::string, std::vector<std::size_t>> _parameter_indices;
    std::unordered_map<std::string, std::vector<std::size_t>> _aqs_indices;
    
    // Metadata tracking
    std::unordered_set<std::string> _unique_sites;
    std::unordered_set<std::string> _unique_parameters;
    std::unordered_set<std::string> _unique_agencies;
    std::vector<std::string> _datetime_range;
    
    // Geographic bounds tracking
    double _min_latitude, _max_latitude;
    double _min_longitude, _max_longitude;
    bool _bounds_initialized;

public:
    FireColumnModel();
    ~FireColumnModel();

    // Data Loading Methods (simplified - no OpenMP)
    void readFromDirectory(const std::string& directoryPath);
    void readFromCSV(const std::string& filename);
    
    void insertMeasurement(double latitude, double longitude, const std::string& datetime,
                          const std::string& parameter, double concentration, const std::string& unit,
                          double raw_concentration, int aqi, int category,
                          const std::string& site_name, const std::string& agency_name,
                          const std::string& aqs_code, const std::string& full_aqs_code);

    // Query Methods
    std::vector<std::size_t> getIndicesBySite(const std::string& siteName) const;
    std::vector<std::size_t> getIndicesByParameter(const std::string& parameter) const;
    std::vector<std::size_t> getIndicesByAqsCode(const std::string& aqsCode) const;

    // Accessors for Columnar Data
    const std::vector<double>& latitudes() const noexcept { return _latitudes; }
    const std::vector<double>& longitudes() const noexcept { return _longitudes; }
    const std::vector<std::string>& datetimes() const noexcept { return _datetimes; }
    const std::vector<std::string>& parameters() const noexcept { return _parameters; }
    const std::vector<double>& concentrations() const noexcept { return _concentrations; }
    const std::vector<std::string>& units() const noexcept { return _units; }
    const std::vector<double>& rawConcentrations() const noexcept { return _raw_concentrations; }
    const std::vector<int>& aqis() const noexcept { return _aqis; }
    const std::vector<int>& categories() const noexcept { return _categories; }
    const std::vector<std::string>& siteNames() const noexcept { return _site_names; }
    const std::vector<std::string>& agencyNames() const noexcept { return _agency_names; }
    const std::vector<std::string>& aqsCodes() const noexcept { return _aqs_codes; }
    const std::vector<std::string>& fullAqsCodes() const noexcept { return _full_aqs_codes; }

    // Metadata and Statistics
    std::size_t measurementCount() const noexcept { return _latitudes.size(); }
    std::size_t siteCount() const noexcept { return _unique_sites.size(); }
    const std::unordered_set<std::string>& uniqueSites() const noexcept { return _unique_sites; }
    const std::unordered_set<std::string>& uniqueParameters() const noexcept { return _unique_parameters; }
    const std::unordered_set<std::string>& uniqueAgencies() const noexcept { return _unique_agencies; }
    const std::vector<std::string>& datetimeRange() const noexcept { return _datetime_range; }
    void getGeographicBounds(double& min_lat, double& max_lat, 
                            double& min_lon, double& max_lon) const;

private:
    void updateIndices(std::size_t index);
    void updateGeographicBounds(double latitude, double longitude);
    void updateDatetimeRange(const std::string& datetime);
    std::vector<std::string> getCSVFiles(const std::string& directoryPath) const;
};

