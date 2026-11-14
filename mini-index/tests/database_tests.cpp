#include "database.hpp"
#include <iostream>
#include <random>

void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

template <typename F> void rejects(F action, const std::string& label) {
    bool rejected = false;
    try { action(); } catch (const std::exception&) { rejected = true; }
    require(rejected, "Expected rejection: " + label);
}

std::string execute(mini::Database& db, const std::string& sql) {
    std::ostringstream out;
    db.execute(sql, out);
    return out.str();
}

int main(int argc, char** argv) {
    try {
        require(argc == 3, "Expected create|verify ROOT");
        const mini::fs::path root = argv[2];
        const std::string mode = argv[1];
        require(mode == "create" || mode == "verify", "Invalid test mode");
        if (mode == "create") {
            require(!mini::fs::exists(root), "Use a fresh fixture directory");
            mini::Database db(root);
            execute(db, "CREATE TABLE demo;");
            execute(db, "CREATE TABLE empty;");
            execute(db, "CREATE TABLE pending;");
            const auto input = root / "input.tsv";
            std::ofstream file(input);
            file << "row_id\tkey\tvalue\r\n0\t-9223372036854775808\t-4\r\n";
            for (unsigned i = 1; i <= 30; ++i) file << i << '\t' << i / 4 << '\t' << i << '\n';
            file << "31\t9223372036854775807\t9\n";
            file.close();
            require(static_cast<bool>(file), "Failed writing input");
            execute(db, "IMPORT demo FROM '" + input.string() + "' GRANULE 3;");
            std::ofstream empty(root / "empty.tsv");
            empty << "row_id\tkey\tvalue\n";
            empty.close();
            execute(db, "IMPORT empty FROM '" + (root / "empty.tsv").string() + "' GRANULE 512;");
            rejects([&] { execute(db, "IMPORT pending FROM 'missing.tsv' GRANULE 3;"); },
                    "missing import");
            require(!mini::fs::exists(root / "pending" / ".import"), "Missing input published staging");
            std::cout << "PASS catalog creation and imports\n";
            return 0;
        }
        mini::Database db(root);
        require(execute(db, "show tables") == "{\"tables\":[\"demo\",\"empty\",\"pending\"]}\n",
                "Catalog reload");
        require(execute(db, "DESCRIBE demo").find("\"rows\":32,\"granule\":3,\"groups\":11") !=
                std::string::npos, "Schema reload");
        require(execute(db, "DESCRIBE pending").find("\"state\":\"created\"") != std::string::npos,
                "Pending table state");
        mini::Store store(root / "demo" / "data");
        const auto rows = read_rows(root / "input.tsv");
        unsigned checked = 0;
        const auto check = [&](std::int64_t lo, std::int64_t hi, bool eq) {
            std::uint64_t count = 0;
            std::int64_t sum = 0;
            std::vector<std::uint64_t> ids;
            for (const auto& row : rows) {
                if (eq ? row.key == lo : row.key >= lo && row.key < hi) {
                    ++count; sum += row.value; ids.push_back(row.id);
                }
            }
            std::sort(ids.begin(), ids.end());
            for (const bool indexed : {false, true}) {
                const auto plan = store.plan(lo, hi, eq, indexed);
                auto result = store.query(lo, hi, eq, indexed);
                std::sort(result.ids.begin(), result.ids.end());
                require(result.ids == ids && result.count == count && result.sum == sum,
                        "Independent oracle mismatch");
                require(plan.end >= plan.begin && plan.end <= store.rows() &&
                        result.scanned_rows == plan.end - plan.begin &&
                        result.requested_read_bytes == (plan.end - plan.begin) * 24,
                        "Shared scan plan mismatch");
                const auto predicate = eq ? "key = " + std::to_string(lo)
                    : "key >= " + std::to_string(lo) + " AND key < " + std::to_string(hi);
                const auto sql = "SELECT COUNT(*), SUM(value) FROM demo WHERE " + predicate +
                    (indexed ? " USING INDEX;" : " USING FULL;");
                const auto output = execute(db, sql);
                const auto expected = "{\"count\":" + std::to_string(count) +
                    ",\"sum\":" + std::to_string(sum) + ",\"scanned_rows\":" +
                    std::to_string(result.scanned_rows) + ",";
                require(output.compare(0, expected.size(), expected) == 0, "SQL result mismatch");
                const auto explanation = execute(db, "EXPLAIN " + sql);
                require(explanation.find("\"estimated_scanned_rows\":" +
                        std::to_string(result.scanned_rows) + ",") != std::string::npos,
                        "EXPLAIN versus execution mismatch");
            }
            ++checked;
        };
        std::mt19937 random(20260930);
        for (unsigned i = 0; i < 200; ++i) {
            auto lo = static_cast<std::int64_t>(random() % 30) - 10;
            auto hi = static_cast<std::int64_t>(random() % 30) - 10;
            if (lo > hi) std::swap(lo, hi);
            check(lo, hi, false);
            check(lo, lo, true);
        }
        check(std::numeric_limits<std::int64_t>::min(), 0, false);
        check(std::numeric_limits<std::int64_t>::min(), 0, true);
        check(std::numeric_limits<std::int64_t>::max(), 0, true);
        check(1, 1, false);
        require(store.plan(1, 1, false, true).candidate_groups == 0, "Empty plan groups");
        require(store.plan(3, 3, true, true).candidate_groups < store.groups(), "No sparse pruning");
        for (const auto* method : {"FULL", "INDEX"}) {
            require(execute(db, std::string("SELECT COUNT(*), SUM(value) FROM empty WHERE key = 0 USING ") +
                            method).find("{\"count\":0,\"sum\":0,\"scanned_rows\":0,") == 0,
                    "Empty table query");
        }
        for (const std::string sql : {
            "CREATE TABLE demo", "CREATE TABLE ../bad", "DESCRIBE absent",
            "IMPORT demo FROM 'missing' GRANULE 3",
            "SELECT COUNT(*), SUM(value) FROM pending WHERE key = 1",
            "SELECT COUNT(*), SUM(value) FROM demo WHERE key >= 5 AND key < 2",
            "SELECT COUNT(*), SUM(value) FROM demo WHERE key = 9223372036854775808",
            "SELECT COUNT(*), SUM(value) FROM demo WHERE key = 1 OR key = 2",
            "SELECT COUNT(*) FROM demo WHERE key = 1",
            "CREATE TABLE newtable; SHOW TABLES", "IMPORT pending FROM 'bad' GRANULE 0",
            "IMPORT pending FROM 'unclosed GRANULE 1", "DROP TABLE demo"}) {
            rejects([&] { execute(db, sql); }, sql);
        }
        require(!mini::fs::exists(root / "newtable"), "Trailing statement mutated catalog");
        require(mini::split_statements("IMPORT t FROM 'a;it''s.tsv' GRANULE 1;SHOW TABLES;").size() == 2,
                "Quoted statement boundaries");
        const auto parsed = mini::Parser("IMPORT t FROM 'a;it''s.tsv' GRANULE 1").parse();
        require(parsed.input.string() == "a;it's.tsv", "Quoted path escaping");
        rejects([] { mini::split_statements(" ; \n"); }, "empty script");
        rejects([] { mini::split_statements("IMPORT t FROM 'x"); }, "unterminated script");
        std::ofstream duplicate(root / "duplicate.tsv");
        duplicate << "row_id\tkey\tvalue\n1\t0\t2\n1\t2\t3\n"; duplicate.close();
        rejects([&] { execute(db, "IMPORT pending FROM '" + (root / "duplicate.tsv").string() +
                                 "' GRANULE 2"); }, "duplicate import");
        require(!mini::fs::exists(root / "pending" / "data"), "Failed import published data");
        require(!mini::fs::exists(root / "pending" / ".import"), "Duplicate input created staging");
        execute(db, "CREATE TABLE overflow");
        std::ofstream overflow(root / "overflow.tsv");
        overflow << "row_id\tkey\tvalue\n0\t0\t9223372036854775807\n1\t0\t1\n";
        overflow.close();
        execute(db, "IMPORT overflow FROM '" + (root / "overflow.tsv").string() + "' GRANULE 1");
        rejects([&] { execute(db, "SELECT COUNT(*), SUM(value) FROM overflow WHERE key = 0"); },
                "SQL aggregate overflow");
        mini::fs::create_directory(root / "pending" / ".import");
        rejects([&] { execute(db, "IMPORT pending FROM '" + (root / "input.tsv").string() +
                                 "' GRANULE 2"); }, "unfinished import");
        rejects([&] { execute(db, "SELECT COUNT(*), SUM(value) FROM pending WHERE key = 0"); },
                "unfinished import not visible");
        std::ofstream corrupt(root / "pending" / "schema.txt", std::ios::trunc);
        corrupt << "bad\n"; corrupt.close();
        rejects([&] { execute(db, "DESCRIBE pending"); }, "corrupt schema");
        rejects([&] { execute(db, "SHOW TABLES"); }, "corrupt catalog entry");
        std::cout << "PASS separate-process reload; " << checked
                  << " predicates, full/index SQL/oracle/EXPLAIN parity and failures\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
