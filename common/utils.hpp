#pragma once

#include <string>
#include <vector>
#include <functional>
#include <chrono>

/**
 * @file utils.hpp
 * @brief Common utility functions for timing, statistics, and data parsing
 */

namespace Utils {
    /**
     * @brief Parse a string to long long, returning 0 on any error
     */
    long long parseLongOrZero(const std::string& s);

    // === Timing Utilities ===
    
    /// High-resolution clock type for consistent timing measurements
    using Clock = std::chrono::high_resolution_clock;
    
    /**
     * @brief Time the execution of a function and return elapsed time in microseconds
     */
    double timeCall(const std::function<void()>& f);
    
    /**
     * @brief Run a function multiple times and return vector of elapsed times
     */
    std::vector<double> timeCallMulti(const std::function<void()>& f, int runs);

    // === Statistical Utilities ===
    
    /**
     * @brief Calculate mean (average) of a vector of values
     */
    double mean(const std::vector<double>& v);
}

