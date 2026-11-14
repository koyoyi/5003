#include "store.hpp"
#include <iostream>

template <typename Function> void rejects(Function action, const std::string& name) {
    bool rejected = false;
    try { action(); }
    catch (const std::exception&) { rejected = true; }
    if (!rejected) throw std::runtime_error("Expected rejection: " + name);
}

int main(int argc, char** argv) {
    try {
        if (argc != 2) throw std::invalid_argument("Expected fresh fixture directory");
        const mini::fs::path root = argv[1];
        if (mini::fs::exists(root)) throw std::runtime_error("Fixture directory already exists");
        mini::fs::create_directories(root);
        const std::vector<mini::Row> rows{{0, -2, 10}, {1, 0, -3}, {2, 2, 5}};
        rejects([&] { mini::Store::build(root / "zero", rows, 0); }, "zero granule");
        rejects([&] { mini::Store::build(root / "duplicates", {{0, 0, 1}, {0, 1, 2}}, 1); },
                "duplicate row IDs");
        mini::Store::build(root / "valid", rows, 2);
        rejects([&] { mini::Store::build(root / "valid", rows, 2); }, "overwrite");
        mini::Store valid(root / "valid");
        for (const bool indexed : {false, true}) {
            auto ids = valid.query(-2, 3, false, indexed);
            auto aggregate = valid.query(-2, 3, false, indexed, false);
            if (!aggregate.ids.empty() || aggregate.count != ids.count ||
                aggregate.sum != ids.sum || ids.count != 3 || ids.sum != 12) {
                throw std::runtime_error("Aggregate mode mismatch");
            }
            rejects([&] { valid.query(3, -2, false, indexed); }, "reversed range");
        }
        for (const auto* name : {"key.bin", "index.bin", "manifest.txt"}) {
            const auto path = root / name;
            mini::Store::build(path, rows, 2);
            std::ofstream broken(path / name, std::ios::binary | std::ios::trunc);
            broken.put('X');
            broken.close();
            rejects([&] { mini::Store invalid(path); }, name);
        }
        const auto index_path = root / "bad-index";
        mini::Store::build(index_path, rows, 2);
        std::fstream index(index_path / "index.bin", std::ios::binary | std::ios::in | std::ios::out);
        mini::write_word(index, 123);
        index.close();
        rejects([&] { mini::Store invalid(index_path); }, "incorrect index key");
        const auto overflow_path = root / "overflow";
        mini::Store::build(overflow_path, {{0, 0, std::numeric_limits<std::int64_t>::max()},
                                         {1, 0, 1}}, 1);
        mini::Store overflow(overflow_path);
        rejects([&] { overflow.query(0, 0, true, false); }, "sum overflow");
        const auto underflow_path = root / "underflow";
        mini::Store::build(underflow_path, {{0, 0, std::numeric_limits<std::int64_t>::min()},
                                          {1, 0, -1}}, 1);
        mini::Store underflow(underflow_path);
        rejects([&] { underflow.query(0, 0, true, true); }, "sum underflow");
        std::cout << "PASS invalid inputs, aggregate modes, overflow and corruption\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
