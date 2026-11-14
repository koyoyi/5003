#include "store.hpp"
#include <iostream>
#include <random>

using mini::Row;
using mini::Result;
using mini::Store;
namespace fs = std::filesystem;

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

std::vector<Row> dataset(unsigned kind) {
    std::vector<Row> rows;
    std::mt19937_64 random(20260930);
    const unsigned n = kind == 0 ? 0 : kind == 1 ? 1 : kind == 2 ? 33 : 1000;
    for (unsigned i = 0; i < n; ++i) {
        const auto key = kind == 2 ? 7 : static_cast<std::int64_t>(random() % 101) - 50;
        rows.push_back({i, key, static_cast<std::int64_t>(random() % 201) - 100});
    }
    if (kind == 4) {
        rows[0].key = std::numeric_limits<std::int64_t>::min();
        rows[1].key = std::numeric_limits<std::int64_t>::max();
    }
    return rows;
}

void same(Result a, Result b, const std::string& label) {
    std::sort(a.ids.begin(), a.ids.end());
    std::sort(b.ids.begin(), b.ids.end());
    require(a.ids == b.ids && a.count == b.count && a.sum == b.sum,
            "Result mismatch: " + label);
}

int main(int argc, char** argv) {
    try {
        require(argc == 3, "Usage: offline_tests create|verify DIRECTORY");
        const std::string mode = argv[1];
        require(mode == "create" || mode == "verify", "Unknown mode");
        const fs::path root = argv[2];
        if (mode == "create") {
            require(!fs::exists(root), "Fixture directory exists; use a fresh build directory");
        }
        std::uint64_t queries = 0;
        std::ofstream log;
        if (mode == "verify") {
            log.open(root / "correctness.csv");
            require(static_cast<bool>(log), "Cannot open correctness log");
            log << "dataset,granule,lower,upper,equality,method,matched_rows,sum,"
                   "scanned_rows,requested_read_bytes,index_bytes,elapsed_s,validation_status\n";
        }
        for (unsigned kind = 0; kind < 5; ++kind) {
            for (const unsigned granule : {1u, 16u, 512u, 2048u, 8192u}) {
                const auto name = std::to_string(kind) + "-" + std::to_string(granule);
                const auto path = root / name;
                const auto rows = dataset(kind);
                if (mode == "create") {
                    Store::build(path, rows, granule);
                    continue;
                }
                Store store(path);
                require(store.rows() == rows.size(), "Reloaded row count mismatch");
                require(store.groups() == rows.size() / granule + (rows.size() % granule != 0),
                        "Granule count mismatch");
                require(store.index_bytes() == store.groups() * 24, "Index bytes mismatch");
                auto check = [&](std::int64_t lower, std::int64_t upper, bool equality) {
                    Result expected;
                    for (const auto& row : rows) {
                        if (equality ? row.key == lower : row.key >= lower && row.key < upper) {
                            mini::add(expected, row);
                        }
                    }
                    const auto full = store.query(lower, upper, equality, false);
                    const auto sparse = store.query(lower, upper, equality, true);
                    same(expected, full, name + " oracle/full");
                    same(full, sparse, name + " full/index");
                    require(sparse.scanned_rows <= full.scanned_rows, "Invalid scanned row count");
                    for (const auto indexed : {false, true}) {
                        const auto& result = indexed ? sparse : full;
                        require(result.requested_read_bytes == result.scanned_rows * 24,
                                "Read byte accounting mismatch");
                        log << name << ',' << granule << ',' << lower << ',' << upper << ','
                            << equality << ',' << (indexed ? "sparse_index" : "full_scan") << ','
                            << result.count << ',' << result.sum << ',' << result.scanned_rows << ','
                            << result.requested_read_bytes << ',' << store.index_bytes() << ','
                            << result.elapsed_s << ",passed\n";
                    }
                    ++queries;
                };
                for (std::int64_t key = -52; key <= 52; ++key) {
                    check(key, key, true);
                    check(key, key, false);
                    check(key, key + 1, false);
                }
                check(-100, 100, false);
                const auto min = std::numeric_limits<std::int64_t>::min();
                const auto max = std::numeric_limits<std::int64_t>::max();
                check(min, min, true);
                check(max, max, true);
                check(min, max, false);
                std::mt19937_64 random(20260930);
                for (unsigned i = 0; i < 200; ++i) {
                    auto lower = static_cast<std::int64_t>(random() % 141) - 70;
                    auto upper = static_cast<std::int64_t>(random() % 141) - 70;
                    if (lower > upper) std::swap(lower, upper);
                    check(lower, upper, i % 2 == 0);
                }
                bool rejected = false;
                try { store.query(10, 0, false, true); }
                catch (const std::invalid_argument&) { rejected = true; }
                require(rejected, "Reversed range was accepted");
            }
        }
        if (mode == "verify") {
            log.close();
            require(static_cast<bool>(log), "Failed writing correctness log");
        }
        std::cout << "PASS mode=" << mode << " datasets=25 queries=" << queries
                  << " seed=20260930\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
