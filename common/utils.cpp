#include "utils.hpp"
#include <stdexcept>
#include <algorithm>
#include <cmath>

namespace Utils {
    long long parseLongOrZero(const std::string& s) {
        try {
            size_t idx = 0;
            long long v = std::stoll(s, &idx);
            return v;
        } catch (...) {
            return 0;
        }
    }

    double timeCall(const std::function<void()>& f) {
        auto t0 = Clock::now();
        f();
        auto t1 = Clock::now();
        
        std::chrono::duration<double, std::micro> d = t1 - t0;
        return d.count();
    }

    std::vector<double> timeCallMulti(const std::function<void()>& f, int runs) {
        std::vector<double> res;
        res.reserve(static_cast<std::size_t>(runs));
        
        for (int i = 0; i < runs; ++i) {
            res.push_back(timeCall(f));
        }
        return res;
    }

    double mean(const std::vector<double>& v) {
        if (v.empty()) return 0.0;
        double sum = 0.0;
        for (double x : v) sum += x;
        return sum / v.size();
    }
}

