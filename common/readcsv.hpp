#pragma once

#include <string>
#include <vector>

// CSVReader: declaration-only header. Implementation lives in readcsv.cpp
class CSVReader {
public:
	explicit CSVReader(const std::string& path, char delimiter = ',', char quote = '"', char comment = '#');
	CSVReader(const CSVReader&) = delete;
	CSVReader& operator=(const CSVReader&) = delete;
	void open();
	void close();
	// Read next CSV row. Returns true if a row was read, false on EOF.
	bool readRow(std::vector<std::string>& out);
	~CSVReader();
private:
	// private implementation details are in the cpp file
	struct Impl;
	Impl* pimpl{nullptr};
};

