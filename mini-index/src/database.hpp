#pragma once

#include "cli_support.hpp"
#include <cctype>
#include <sstream>

namespace mini {

inline bool identifier(const std::string& text) {
    const auto letter = [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
    };
    if (text.empty() || !letter(text.front())) return false;
    return std::all_of(text.begin(), text.end(), [&](char c) {
        return letter(c) || (c >= '0' && c <= '9');
    });
}

struct Token {
    std::string text;
    bool quoted = false;
};

inline std::vector<Token> tokenize(const std::string& sql) {
    std::vector<Token> tokens;
    for (std::size_t i = 0; i < sql.size();) {
        const char c = sql[i];
        if (std::isspace(static_cast<unsigned char>(c))) { ++i; continue; }
        if (c == '\'') {
            ++i;
            std::string text;
            bool closed = false;
            while (i < sql.size()) {
                if (sql[i] == '\'') {
                    ++i;
                    if (i < sql.size() && sql[i] == '\'') {
                        text += '\''; ++i;
                    } else { closed = true; break; }
                } else { text += sql[i++]; }
            }
            if (!closed) throw std::invalid_argument("Unterminated quoted path");
            tokens.push_back({text, true});
        } else if (std::isalnum(static_cast<unsigned char>(c)) || c == '_' || c == '-') {
            const auto start = i++;
            while (i < sql.size() &&
                   (std::isalnum(static_cast<unsigned char>(sql[i])) || sql[i] == '_')) ++i;
            tokens.push_back({sql.substr(start, i - start), false});
        } else if (std::string("(),*=<>;").find(c) != std::string::npos) {
            std::string text(1, sql[i++]);
            if (c == '>' && i < sql.size() && sql[i] == '=') text += sql[i++];
            tokens.push_back({text, false});
        } else {
            throw std::invalid_argument("Unsupported SQL character");
        }
    }
    return tokens;
}

enum class Operation { create, show, describe, import, select, explain };
struct Statement {
    Operation operation = Operation::show;
    std::string table;
    fs::path input;
    std::uint64_t granule = 0;
    std::int64_t lower = 0, upper = 0;
    bool equality = false, indexed = true;
};

class Parser {
    std::vector<Token> tokens_;
    std::size_t pos_ = 0;

    bool accept(const std::string& expected) {
        if (pos_ == tokens_.size() || tokens_[pos_].quoted) return false;
        auto text = tokens_[pos_].text;
        for (auto& c : text) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        if (text != expected) return false;
        ++pos_;
        return true;
    }
    void require(const std::string& expected) {
        if (!accept(expected)) throw std::invalid_argument("Expected SQL token: " + expected);
    }
    std::string word() {
        if (pos_ == tokens_.size() || tokens_[pos_].quoted) {
            throw std::invalid_argument("Expected unquoted SQL token");
        }
        return tokens_[pos_++].text;
    }
    std::string table() {
        const auto name = word();
        if (!identifier(name)) throw std::invalid_argument("Invalid table name");
        return name;
    }

public:
    explicit Parser(const std::string& sql) : tokens_(tokenize(sql)) {}
    Statement parse() {
        Statement s;
        if (accept("CREATE")) {
            require("TABLE"); s.operation = Operation::create; s.table = table();
        } else if (accept("SHOW")) {
            require("TABLES"); s.operation = Operation::show;
        } else if (accept("DESCRIBE")) {
            s.operation = Operation::describe; s.table = table();
        } else if (accept("IMPORT")) {
            s.operation = Operation::import; s.table = table(); require("FROM");
            if (pos_ == tokens_.size() || !tokens_[pos_].quoted || tokens_[pos_].text.empty()) {
                throw std::invalid_argument("IMPORT requires a nonempty single-quoted path");
            }
            s.input = tokens_[pos_++].text;
            require("GRANULE"); s.granule = number<std::uint64_t>(word());
            if (!s.granule) throw std::invalid_argument("Granule size must be positive");
        } else {
            s.operation = accept("EXPLAIN") ? Operation::explain : Operation::select;
            require("SELECT"); require("COUNT"); require("("); require("*"); require(")");
            require(","); require("SUM"); require("("); require("VALUE"); require(")");
            require("FROM"); s.table = table(); require("WHERE"); require("KEY");
            if (accept("=")) {
                s.equality = true; s.lower = s.upper = number<std::int64_t>(word());
            } else {
                require(">="); s.lower = number<std::int64_t>(word());
                require("AND"); require("KEY"); require("<");
                s.upper = number<std::int64_t>(word());
                if (s.lower > s.upper) throw std::invalid_argument("Reversed range");
            }
            if (accept("USING")) {
                if (accept("FULL")) s.indexed = false;
                else require("INDEX");
            }
        }
        accept(";");
        if (pos_ != tokens_.size()) throw std::invalid_argument("Unexpected trailing SQL tokens");
        return s;
    }
};

class Database {
    fs::path root_;
    static constexpr const char* catalog_header = "MINI_DB 1";
    static constexpr const char* schema_header = "MINI_TABLE 1 row_id:u64 key:i64 value:i64";

    static void check_header(const fs::path& file, const std::string& expected) {
        std::ifstream in(file);
        std::string text;
        if (!std::getline(in, text) || text != expected) {
            throw std::runtime_error("Invalid metadata: " + file.string());
        }
        in >> std::ws;
        if (!in.eof()) throw std::runtime_error("Trailing metadata: " + file.string());
    }
    static void write_header(const fs::path& file, const std::string& header) {
        std::ofstream out(file);
        out << header << '\n';
        out.close();
        if (!out) throw std::runtime_error("Failed writing metadata: " + file.string());
    }
    fs::path table_path(const std::string& name) const {
        if (!identifier(name)) throw std::invalid_argument("Invalid table name");
        const auto path = root_ / name;
        if (fs::is_symlink(path)) throw std::runtime_error("Symlink tables are unsupported");
        check_header(path / "schema.txt", schema_header);
        return path;
    }
    static bool ready(const fs::path& path) {
        const auto data = path / "data";
        if (fs::is_symlink(data)) throw std::runtime_error("Symlink data is unsupported");
        return fs::exists(data);
    }

public:
    explicit Database(fs::path root) : root_(std::move(root)) {
        if (fs::is_symlink(root_)) throw std::runtime_error("Symlink database roots are unsupported");
        if (!fs::exists(root_)) {
            fs::create_directories(root_);
            write_header(root_ / "catalog.txt", catalog_header);
        }
        check_header(root_ / "catalog.txt", catalog_header);
    }

    void execute(const std::string& sql, std::ostream& out) {
        const auto s = Parser(sql).parse();
        if (s.operation == Operation::create) {
            const auto path = root_ / s.table;
            const auto staging = root_ / (".create-" + s.table);
            if (fs::exists(path) || fs::exists(staging)) {
                throw std::runtime_error("Table or unfinished CREATE already exists");
            }
            fs::create_directory(staging);
            write_header(staging / "schema.txt", schema_header);
            fs::rename(staging, path);
            out << "{\"created\":\"" << s.table << "\"}\n";
        } else if (s.operation == Operation::show) {
            std::vector<std::string> names;
            for (const auto& entry : fs::directory_iterator(root_)) {
                const auto name = entry.path().filename().string();
                if (identifier(name) && (entry.is_directory() || entry.is_symlink())) {
                    table_path(name);
                    names.push_back(name);
                }
            }
            std::sort(names.begin(), names.end());
            out << "{\"tables\":[";
            for (std::size_t i = 0; i < names.size(); ++i) {
                if (i) out << ',';
                out << '"' << names[i] << '"';
            }
            out << "]}\n";
        } else {
            const auto path = table_path(s.table);
            if (s.operation == Operation::import) {
                const auto staging = path / ".import";
                if (ready(path) || fs::exists(staging)) {
                    throw std::runtime_error("Table is already imported or has unfinished IMPORT");
                }
                Store::build(staging, read_rows(s.input), s.granule);
                {
                    Store verified(staging);
                }
                fs::rename(staging, path / "data");
                out << "{\"imported\":\"" << s.table << "\"}\n";
            } else if (s.operation == Operation::describe) {
                const bool imported = ready(path);
                out << "{\"table\":\"" << s.table
                    << "\",\"schema\":{\"row_id\":\"UInt64\",\"key\":\"Int64\",\"value\":\"Int64\"}"
                    << ",\"state\":\"" << (imported ? "ready" : "created") << '"';
                if (imported) {
                    Store store(path / "data");
                    out << ",\"rows\":" << store.rows() << ",\"granule\":" << store.granule()
                        << ",\"groups\":" << store.groups();
                }
                out << "}\n";
            } else {
                if (!ready(path)) throw std::runtime_error("Table must be imported before querying");
                Store store(path / "data");
                if (s.operation == Operation::explain) {
                    const auto plan = store.plan(s.lower, s.upper, s.equality, s.indexed);
                    out << "{\"table\":\"" << s.table << "\",\"access\":\""
                        << (s.indexed ? "sparse_index" : "full_scan")
                        << "\",\"predicate\":\"" << (s.equality ? "eq" : "range")
                        << "\",\"lower\":" << s.lower << ",\"upper\":" << s.upper
                        << ",\"total_groups\":" << store.groups()
                        << ",\"candidate_groups\":" << plan.candidate_groups
                        << ",\"begin_row\":" << plan.begin << ",\"end_row\":" << plan.end
                        << ",\"estimated_scanned_rows\":" << plan.end - plan.begin
                        << ",\"requested_read_bytes\":" << (plan.end - plan.begin) * 24 << "}\n";
                } else {
                    output_result(out, store.query(s.lower, s.upper, s.equality, s.indexed, false),
                                  false);
                }
            }
        }
        if (!out) throw std::runtime_error("Failed writing command output");
    }
};

inline std::vector<std::string> split_statements(const std::string& script) {
    std::vector<std::string> statements;
    bool quoted = false;
    std::size_t start = 0;
    for (std::size_t i = 0; i < script.size(); ++i) {
        if (script[i] == '\'') {
            if (quoted && i + 1 < script.size() && script[i + 1] == '\'') ++i;
            else quoted = !quoted;
        } else if (script[i] == ';' && !quoted) {
            const auto text = script.substr(start, i - start + 1);
            if (tokenize(text).size() > 1) statements.push_back(text);
            start = i + 1;
        }
    }
    if (quoted) throw std::invalid_argument("Unterminated quoted path");
    const auto tail = script.substr(start);
    if (!tokenize(tail).empty()) statements.push_back(tail);
    if (statements.empty()) throw std::invalid_argument("No SQL statements supplied");
    return statements;
}
} // namespace mini
