#pragma once

#include "store.hpp"
#include <charconv>
#include <iomanip>
#include <ostream>
#include <sstream>

template <typename T> T number(const std::string& text) {
    T value{};
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(), value);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        throw std::invalid_argument("Invalid integer: " + text);
    }
    return value;
}

inline std::vector<mini::Row> read_rows(const mini::fs::path& file) {
    std::ifstream input(file);
    if (!input) throw std::runtime_error("Cannot open input TSV");
    std::string line;
    if (!std::getline(input, line)) {
        throw std::runtime_error("Expected TSV header: row_id, key, value");
    }
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line != "row_id\tkey\tvalue") {
        throw std::runtime_error("Expected TSV header: row_id, key, value");
    }
    std::vector<mini::Row> rows;
    while (std::getline(input, line)) {
        std::istringstream fields(line);
        std::string id, key, value, extra;
        if (!(fields >> id >> key >> value) || (fields >> extra)) {
            throw std::runtime_error("Invalid input TSV row");
        }
        rows.push_back({number<std::uint64_t>(id), number<std::int64_t>(key),
                        number<std::int64_t>(value)});
    }
    if (!input.eof()) throw std::runtime_error("Error reading input TSV");
    return rows;
}

inline void output_result(std::ostream& out, const mini::Result& result, bool ids) {
    out << std::setprecision(17)
        << "{\"count\":" << result.count << ",\"sum\":" << result.sum
        << ",\"scanned_rows\":" << result.scanned_rows
        << ",\"requested_read_bytes\":" << result.requested_read_bytes
        << ",\"elapsed_s\":" << result.elapsed_s;
    if (ids) {
        out << ",\"row_ids\":[";
        for (std::size_t i = 0; i < result.ids.size(); ++i) {
            if (i) out << ',';
            out << result.ids[i];
        }
        out << ']';
    }
    out << "}\n";
}
