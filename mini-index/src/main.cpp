#include "cli_support.hpp"
#include <iostream>

void output(const mini::Result& result, bool ids) {
    output_result(std::cout, result, ids);
}

int main(int argc, char** argv) {
    try {
        if (argc < 2) throw std::invalid_argument(
            "Commands: build INPUT.tsv STORE G | query STORE full|index eq|range L U ids|aggregate"
            " | benchmark STORE QUERIES.tsv OUTPUT.csv");
        const std::string command = argv[1];
        if (command == "build" && argc == 5) {
            const auto stats = mini::Store::build(argv[3], read_rows(argv[2]),
                                                  number<std::uint64_t>(argv[4]));
            std::cout << std::setprecision(17) << "{\"sort_s\":" << stats.sort_s
                      << ",\"data_write_s\":" << stats.data_write_s
                      << ",\"index_write_s\":" << stats.index_write_s << "}\n";
        } else if (command == "query" && argc == 8) {
            const std::string method = argv[3], predicate = argv[4], mode = argv[7];
            if ((method != "full" && method != "index") ||
                (predicate != "eq" && predicate != "range") ||
                (mode != "ids" && mode != "aggregate")) {
                throw std::invalid_argument("Invalid method, predicate or output mode");
            }
            mini::Store store(argv[2]);
            output(store.query(number<std::int64_t>(argv[5]), number<std::int64_t>(argv[6]),
                               predicate == "eq", method == "index", mode == "ids"),
                   mode == "ids");
        } else if (command == "benchmark" && argc == 5) {
            if (mini::fs::exists(argv[4])) throw std::runtime_error("Output already exists");
            mini::Store store(argv[2]);
            std::ifstream queries(argv[3]);
            std::string line;
            if (!std::getline(queries, line) || line != "query_id\tlower\tupper") {
                throw std::runtime_error("Invalid query TSV header");
            }
            std::ofstream log(argv[4]);
            if (!log) throw std::runtime_error("Cannot open benchmark output");
            log << "query_id,lower_bound,upper_bound,method,run_no,phase,elapsed_s,"
                   "matched_rows,scanned_rows,requested_read_bytes,index_bytes,"
                   "result_count,result_sum,validation_status\n" << std::setprecision(17);
            unsigned query_count = 0;
            while (std::getline(queries, line)) {
                std::istringstream fields(line);
                std::string id_text, lo_text, hi_text, extra;
                if (!(fields >> id_text >> lo_text >> hi_text) || (fields >> extra)) {
                    throw std::runtime_error("Invalid query TSV row");
                }
                const auto id = number<unsigned>(id_text);
                const auto lower = number<std::int64_t>(lo_text);
                const auto upper = number<std::int64_t>(hi_text);
                auto full = store.query(lower, upper, false, false);
                auto sparse = store.query(lower, upper, false, true);
                if (full.ids != sparse.ids || full.count != sparse.count || full.sum != sparse.sum) {
                    throw std::runtime_error("Full row_id validation failed");
                }
                for (unsigned run = 0; run <= 5; ++run) {
                    for (unsigned slot = 0; slot < 2; ++slot) {
                        const bool indexed = (slot + run) % 2 != 0;
                        const auto result = store.query(lower, upper, false, indexed, false);
                        if (result.count != full.count || result.sum != full.sum) {
                            throw std::runtime_error("Timed aggregate validation failed");
                        }
                        log << id << ',' << lower << ',' << upper << ','
                            << (indexed ? "sparse_index" : "full_scan") << ',' << run << ','
                            << (run == 0 ? "warmup" : "measurement") << ',' << result.elapsed_s
                            << ',' << result.count << ',' << result.scanned_rows << ','
                            << result.requested_read_bytes << ',' << store.index_bytes() << ','
                            << result.count << ',' << result.sum << ",passed\n";
                    }
                }
                ++query_count;
            }
            if (!queries.eof() || query_count != 4) {
                throw std::runtime_error("Benchmark requires exactly four queries");
            }
            log.close();
            if (!log) throw std::runtime_error("Failed writing benchmark output");
            std::cout << "{\"validated_queries\":" << query_count << ",\"samples\":48}\n";
        } else {
            throw std::invalid_argument("Unknown command or incorrect argument count");
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
