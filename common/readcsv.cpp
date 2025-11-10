#include "readcsv.hpp"

#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

struct CSVReader::Impl {
    std::ifstream ifs;
    std::string path;
    char delim;
    char quote;
    char comment;

    Impl(const std::string& p, char d, char q, char c)
        : path(p), delim(d), quote(q), comment(c) {}
};

CSVReader::CSVReader(const std::string& path, char delimiter, char quote, char comment)
    : pimpl(new Impl(path, delimiter, quote, comment)) {}

CSVReader::~CSVReader() {
    close();
    delete pimpl;
}

void CSVReader::open() {
    pimpl->ifs.open(pimpl->path);
    if (!pimpl->ifs.is_open()) throw std::runtime_error("Failed to open CSV file: " + pimpl->path);
}

void CSVReader::close() {
    if (pimpl && pimpl->ifs.is_open()) pimpl->ifs.close();
}

// Helper to read logical record
static bool readPhysicalRecord(std::ifstream& ifs, std::string& out, char quote, char comment) {
    out.clear();
    std::string line;
    bool first = true;
    int quote_count = 0;

    while (std::getline(ifs, line)) {
        if (first) {
            std::size_t i = 0;
            while (i < line.size() && (line[i] == ' ' || line[i] == '\t')) ++i;
            if (i < line.size() && line[i] == comment) { first = true; continue; }
        }

        if (!out.empty()) out.push_back('\n');
        out += line;
        for (char c : line) if (c == quote) ++quote_count;
        if ((quote_count % 2) == 0) return true;
        first = false;
    }

    return !out.empty();
}

// Helper to split record
static void splitRecord(const std::string& record, std::vector<std::string>& out, char delim, char quote) {
    out.clear();
    std::string cur;
    enum State { Unquoted, Quoted } state = Unquoted;

    for (std::size_t i = 0; i < record.size(); ++i) {
        char c = record[i];
        if (state == Unquoted) {
            if (c == delim) {
                out.push_back(cur);
                cur.clear();
            } else if (c == quote) {
                state = Quoted;
            } else {
                cur.push_back(c);
            }
        } else {
            if (c == quote) {
                if (i + 1 < record.size() && record[i+1] == quote) {
                    cur.push_back(quote);
                    ++i;
                } else {
                    state = Unquoted;
                }
            } else {
                cur.push_back(c);
            }
        }
    }

    out.push_back(cur);
}

bool CSVReader::readRow(std::vector<std::string>& out) {
    if (!pimpl) return false;
    if (!pimpl->ifs.is_open()) return false;
    std::string raw;
    if (!readPhysicalRecord(pimpl->ifs, raw, pimpl->quote, pimpl->comment)) return false;
    splitRecord(raw, out, pimpl->delim, pimpl->quote);
    return true;
}

