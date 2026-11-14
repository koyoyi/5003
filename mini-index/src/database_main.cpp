#include "database.hpp"
#include <iostream>
#include <iterator>

int main(int argc, char** argv) {
    try {
        if (argc != 2 && argc != 4) {
            throw std::invalid_argument("Usage: mini_db ROOT [--execute SQL | --file SCRIPT.sql]");
        }
        std::string script;
        if (argc == 4) {
            const std::string option = argv[2];
            if (option == "--execute") {
                const auto statement = mini::Parser(argv[3]).parse();
                (void)statement;
                script = argv[3];
            } else if (option == "--file") {
                std::ifstream in(argv[3]);
                if (!in) throw std::runtime_error("Cannot open SQL script");
                script.assign(std::istreambuf_iterator<char>(in), {});
                if (in.bad()) throw std::runtime_error("Failed reading SQL script");
            } else {
                throw std::invalid_argument("Unknown mini_db option");
            }
        } else {
            script.assign(std::istreambuf_iterator<char>(std::cin), {});
            if (std::cin.bad()) throw std::runtime_error("Failed reading SQL input");
        }
        const auto statements = mini::split_statements(script);
        for (const auto& sql : statements) {
            const auto statement = mini::Parser(sql).parse();
            (void)statement;
        }
        mini::Database db(argv[1]);
        for (const auto& sql : statements) db.execute(sql, std::cout);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
        return 1;
    }
}
