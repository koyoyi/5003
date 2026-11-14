#pragma once

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace mini {
namespace fs = std::filesystem;
struct Row {
    std::uint64_t id;
    std::int64_t key;
    std::int64_t value;
};
struct Mark {
    std::int64_t key;
    std::uint64_t group;
    std::uint64_t offset;
};
struct Result {
    std::vector<std::uint64_t> ids;
    std::uint64_t count = 0;
    std::int64_t sum = 0;
    std::uint64_t scanned_rows = 0;
    std::uint64_t requested_read_bytes = 0;
    double elapsed_s = 0;
};

inline void write_word(std::ostream& out, std::uint64_t word) {
    for (unsigned i = 0; i < 8; ++i) {
        out.put(static_cast<char>((word >> (8 * i)) & 255));
    }
    if (!out) throw std::runtime_error("Failed writing binary data");
}
inline std::uint64_t read_word(std::istream& in) {
    std::uint64_t word = 0;
    for (unsigned i = 0; i < 8; ++i) {
        const auto c = in.get();
        if (c == std::char_traits<char>::eof()) {
            throw std::runtime_error("Truncated binary data");
        }
        word |= static_cast<std::uint64_t>(static_cast<unsigned char>(c)) << (8 * i);
    }
    return word;
}
inline std::int64_t signed_word(std::uint64_t word) {
    if (word <= static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max())) {
        return static_cast<std::int64_t>(word);
    }
    return -1 - static_cast<std::int64_t>(~word);
}
inline void add(Result& result, const Row& row, bool collect_ids = true) {
    const auto min = std::numeric_limits<std::int64_t>::min();
    const auto max = std::numeric_limits<std::int64_t>::max();
    if ((row.value > 0 && result.sum > max - row.value) ||
        (row.value < 0 && result.sum < min - row.value)) {
        throw std::overflow_error("Query sum exceeds int64");
    }
    if (collect_ids) result.ids.push_back(row.id);
    ++result.count;
    result.sum += row.value;
}

class Store {
    fs::path directory_;
    std::uint64_t rows_ = 0;
    std::uint64_t granule_ = 0;
    std::vector<Mark> marks_;
    mutable std::ifstream ids_, keys_, values_;

public:
    struct ScanPlan {
        std::uint64_t begin;
        std::uint64_t end;
        std::uint64_t candidate_groups;
    };
    struct BuildStats {
        double sort_s = 0;
        double data_write_s = 0;
        double index_write_s = 0;
    };
    static BuildStats build(const fs::path& directory, std::vector<Row> rows,
                      std::uint64_t granule) {
        if (!granule) throw std::invalid_argument("Granule size must be positive");
        if (fs::exists(directory)) throw std::runtime_error("Refusing to overwrite dataset");
        std::unordered_set<std::uint64_t> ids_seen;
        for (const auto& row : rows) {
            if (!ids_seen.insert(row.id).second) {
                throw std::invalid_argument("Duplicate row_id");
            }
        }
        BuildStats stats;
        auto started = std::chrono::steady_clock::now();
        std::sort(rows.begin(), rows.end(), [](const Row& a, const Row& b) {
            return a.key < b.key || (a.key == b.key && a.id < b.id);
        });
        stats.sort_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        fs::create_directories(directory);
        started = std::chrono::steady_clock::now();
        std::ofstream ids(directory / "row_id.bin", std::ios::binary);
        std::ofstream keys(directory / "key.bin", std::ios::binary);
        std::ofstream values(directory / "value.bin", std::ios::binary);
        for (std::size_t i = 0; i < rows.size(); ++i) {
            write_word(ids, rows[i].id);
            write_word(keys, static_cast<std::uint64_t>(rows[i].key));
            write_word(values, static_cast<std::uint64_t>(rows[i].value));
        }
        for (auto* stream : {&ids, &keys, &values}) {
            stream->close();
            if (!*stream) throw std::runtime_error("Failed closing dataset file");
        }
        stats.data_write_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        started = std::chrono::steady_clock::now();
        std::ofstream index(directory / "index.bin", std::ios::binary);
        for (std::size_t i = 0; i < rows.size(); ++i) {
            if (i % granule == 0) {
                write_word(index, static_cast<std::uint64_t>(rows[i].key));
                write_word(index, i / granule);
                write_word(index, i);
            }
        }
        index.close();
        if (!index) throw std::runtime_error("Failed closing index file");
        stats.index_write_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        std::ofstream manifest(directory / "manifest.txt");
        manifest << "MINI_INDEX 1 LE u64 i64 i64\n" << rows.size() << ' ' << granule << '\n';
        manifest.close();
        if (!manifest) throw std::runtime_error("Failed writing manifest");
        return stats;
    }

    explicit Store(fs::path directory) : directory_(std::move(directory)) {
        std::ifstream manifest(directory_ / "manifest.txt");
        std::string header;
        std::getline(manifest, header);
        if (header != "MINI_INDEX 1 LE u64 i64 i64" ||
            !(manifest >> rows_ >> granule_) || !granule_) {
            throw std::runtime_error("Invalid manifest");
        }
        manifest >> std::ws;
        if (!manifest.eof()) throw std::runtime_error("Trailing manifest data");
        const auto max_rows = static_cast<std::uint64_t>(
            std::numeric_limits<std::streamoff>::max()) / 24;
        if (rows_ > max_rows) throw std::runtime_error("Dataset too large");
        for (const auto* name : {"row_id.bin", "key.bin", "value.bin"}) {
            if (fs::file_size(directory_ / name) != rows_ * 8) {
                throw std::runtime_error(std::string("Invalid column size: ") + name);
            }
        }
        const auto groups = rows_ / granule_ + (rows_ % granule_ != 0);
        if (fs::file_size(directory_ / "index.bin") != groups * 24) {
            throw std::runtime_error("Invalid index size");
        }
        std::ifstream index(directory_ / "index.bin", std::ios::binary);
        std::ifstream keys(directory_ / "key.bin", std::ios::binary);
        for (std::uint64_t g = 0; g < groups; ++g) {
            Mark mark{signed_word(read_word(index)), read_word(index), read_word(index)};
            if (mark.group != g || mark.offset != g * granule_ ||
                (!marks_.empty() && mark.key < marks_.back().key)) {
                throw std::runtime_error("Invalid index entry");
            }
            keys.seekg(static_cast<std::streamoff>(mark.offset * 8));
            if (signed_word(read_word(keys)) != mark.key) {
                throw std::runtime_error("Index and key column disagree");
            }
            marks_.push_back(mark);
        }
        ids_.open(directory_ / "row_id.bin", std::ios::binary);
        keys_.open(directory_ / "key.bin", std::ios::binary);
        values_.open(directory_ / "value.bin", std::ios::binary);
        if (!ids_ || !keys_ || !values_) throw std::runtime_error("Cannot open column files");
    }

    std::uint64_t rows() const { return rows_; }
    std::uint64_t granule() const { return granule_; }
    std::uint64_t groups() const { return marks_.size(); }
    std::uint64_t index_bytes() const { return fs::file_size(directory_ / "index.bin"); }

    ScanPlan plan(std::int64_t lower, std::int64_t upper, bool equality,
                  bool indexed) const {
        if (!equality && lower > upper) throw std::invalid_argument("Reversed range");
        std::uint64_t begin = 0, end = rows_;
        if ((!equality && lower == upper) || !rows_) {
            end = 0;
        } else if (indexed) {
            auto first = std::lower_bound(marks_.begin(), marks_.end(), lower,
                [](const Mark& m, std::int64_t key) { return m.key < key; });
            // The predecessor can contain duplicates of the lower bound.
            if (first != marks_.begin()) --first;
            begin = first->offset;
            const auto last = equality
                ? std::upper_bound(marks_.begin(), marks_.end(), lower,
                    [](std::int64_t key, const Mark& m) { return key < m.key; })
                : std::lower_bound(marks_.begin(), marks_.end(), upper,
                    [](const Mark& m, std::int64_t key) { return m.key < key; });
            end = last == marks_.end() ? rows_ : last->offset;
        }
        const auto groups = end > begin
            ? (end - 1) / granule_ - begin / granule_ + 1 : 0;
        return {begin, end, groups};
    }

    Result query(std::int64_t lower, std::int64_t upper, bool equality, bool indexed,
                 bool collect_ids = true) const {
        const auto started = std::chrono::steady_clock::now();
        const auto scan = plan(lower, upper, equality, indexed);
        const auto begin = scan.begin, end = scan.end;
        Result result;
        for (auto* stream : {&ids_, &keys_, &values_}) {
            stream->clear();
            stream->seekg(static_cast<std::streamoff>(begin * 8));
            if (!*stream) throw std::runtime_error("Failed opening/seeking column");
        }
        constexpr std::uint64_t block_rows = 4096;
        std::array<unsigned char, block_rows * 8> id_buffer{}, key_buffer{}, value_buffer{};
        const auto decode = [](const auto& buffer, std::size_t offset) {
            std::uint64_t word = 0;
            for (unsigned b = 0; b < 8; ++b) {
                word |= static_cast<std::uint64_t>(buffer[offset * 8 + b]) << (8 * b);
            }
            return word;
        };
        for (auto pos = begin; pos < end;) {
            const auto count = std::min(block_rows, end - pos);
            const auto bytes = static_cast<std::streamsize>(count * 8);
            ids_.read(reinterpret_cast<char*>(id_buffer.data()), bytes);
            keys_.read(reinterpret_cast<char*>(key_buffer.data()), bytes);
            values_.read(reinterpret_cast<char*>(value_buffer.data()), bytes);
            if (!ids_ || !keys_ || !values_) throw std::runtime_error("Truncated column read");
            result.scanned_rows += count;
            result.requested_read_bytes += count * 24;
            for (std::size_t i = 0; i < count; ++i) {
                Row row{decode(id_buffer, i), signed_word(decode(key_buffer, i)),
                        signed_word(decode(value_buffer, i))};
                if (equality ? row.key == lower : row.key >= lower && row.key < upper) {
                    add(result, row, collect_ids);
                }
            }
            pos += count;
        }
        result.elapsed_s = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started).count();
        return result;
    }
};
} // namespace mini
