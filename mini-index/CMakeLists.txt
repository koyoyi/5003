cmake_minimum_required(VERSION 3.16)
project(mini_index LANGUAGES CXX)

# Keep this directory independently buildable for the experiment snapshot runner.
include(CTest)

# Named component targets describe the existing header-only implementation.
add_library(mini_index_core INTERFACE)
add_library(DSC5003::IndexCore ALIAS mini_index_core)
target_include_directories(mini_index_core INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_compile_features(mini_index_core INTERFACE cxx_std_17)
target_sources(mini_index_core INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/src/store.hpp")

add_library(mini_index_cli INTERFACE)
add_library(DSC5003::CLI ALIAS mini_index_cli)
target_link_libraries(mini_index_cli INTERFACE mini_index_core)
target_sources(mini_index_cli INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/src/cli_support.hpp")

add_library(mini_database INTERFACE)
add_library(DSC5003::Database ALIAS mini_database)
target_link_libraries(mini_database INTERFACE mini_index_cli)
target_sources(mini_database INTERFACE "${CMAKE_CURRENT_SOURCE_DIR}/src/database.hpp")

add_executable(mini_index src/main.cpp)
add_executable(mini_db src/database_main.cpp)
target_link_libraries(mini_index PRIVATE mini_index_cli)
target_link_libraries(mini_db PRIVATE mini_database)

set(mini_index_executables mini_index mini_db)
if(BUILD_TESTING)
  add_executable(offline_tests tests/offline_tests.cpp)
  add_executable(failure_tests tests/failure_tests.cpp)
  add_executable(database_tests tests/database_tests.cpp)
  target_link_libraries(offline_tests PRIVATE mini_index_core)
  target_link_libraries(failure_tests PRIVATE mini_index_core)
  target_link_libraries(database_tests PRIVATE mini_database)
  list(APPEND mini_index_executables offline_tests failure_tests database_tests)
endif()

foreach(target IN LISTS mini_index_executables)
  set_target_properties(${target} PROPERTIES CXX_EXTENSIONS OFF)
  if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endif()
endforeach()

if(BUILD_TESTING)
  set(fixture_runner "${CMAKE_CURRENT_SOURCE_DIR}/tests/run_fixture.cmake")
  add_test(NAME persist COMMAND ${CMAKE_COMMAND}
    "-DEXECUTABLE=$<TARGET_FILE:offline_tests>" "-DMODE=create"
    "-DBINARY_ROOT=${CMAKE_CURRENT_BINARY_DIR}"
    "-DROOT=${CMAKE_CURRENT_BINARY_DIR}/fixtures" -P "${fixture_runner}")
  set_tests_properties(persist PROPERTIES FIXTURES_SETUP persisted_data)
  add_test(NAME reload_and_query COMMAND offline_tests verify "${CMAKE_CURRENT_BINARY_DIR}/fixtures")
  set_tests_properties(reload_and_query PROPERTIES FIXTURES_REQUIRED persisted_data)
  add_test(NAME invalid_inputs COMMAND ${CMAKE_COMMAND}
    "-DEXECUTABLE=$<TARGET_FILE:failure_tests>"
    "-DBINARY_ROOT=${CMAKE_CURRENT_BINARY_DIR}"
    "-DROOT=${CMAKE_CURRENT_BINARY_DIR}/invalid-fixtures" -P "${fixture_runner}")
  add_test(NAME database_create COMMAND ${CMAKE_COMMAND}
    "-DEXECUTABLE=$<TARGET_FILE:database_tests>" "-DMODE=create"
    "-DBINARY_ROOT=${CMAKE_CURRENT_BINARY_DIR}"
    "-DROOT=${CMAKE_CURRENT_BINARY_DIR}/database-fixtures" -P "${fixture_runner}")
  set_tests_properties(database_create PROPERTIES FIXTURES_SETUP database_data)
  add_test(NAME database_reload COMMAND database_tests verify "${CMAKE_CURRENT_BINARY_DIR}/database-fixtures")
  set_tests_properties(database_reload PROPERTIES FIXTURES_REQUIRED database_data)
  add_test(NAME database_cli COMMAND ${CMAKE_COMMAND}
    "-DEXECUTABLE=$<TARGET_FILE:mini_db>"
    "-DTEST_SCRIPT=${CMAKE_CURRENT_SOURCE_DIR}/tests/database_cli.cmake"
    "-DBINARY_ROOT=${CMAKE_CURRENT_BINARY_DIR}"
    "-DROOT=${CMAKE_CURRENT_BINARY_DIR}/database-cli-fixtures" -P "${fixture_runner}")
  set_tests_properties(persist reload_and_query invalid_inputs database_create
    database_reload database_cli PROPERTIES TIMEOUT 120)
endif()
