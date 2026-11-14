cmake_minimum_required(VERSION 3.16)
project(dsc5003_clickhouse_repro VERSION 0.1.0 LANGUAGES CXX)

# Register tests at the repository root so `ctest --test-dir build/...` works.
include(CTest)
add_subdirectory(mini-index)
