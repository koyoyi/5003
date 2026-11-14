# CTest-only setup: each invocation may replace one of these four generated
# fixture directories, never user datasets or directories outside this build.
foreach(required BINARY_ROOT ROOT EXECUTABLE)
  if(NOT DEFINED ${required} OR "${${required}}" STREQUAL "")
    message(FATAL_ERROR "Missing fixture-runner argument: ${required}")
  endif()
endforeach()

get_filename_component(binary_real "${BINARY_ROOT}" REALPATH)
get_filename_component(fixture_name "${ROOT}" NAME)
if(NOT fixture_name MATCHES "^(fixtures|invalid-fixtures|database-fixtures|database-cli-fixtures)$")
  message(FATAL_ERROR "Unexpected generated fixture name: ${fixture_name}")
endif()
get_filename_component(fixture_real "${ROOT}" REALPATH)
set(expected_root "${binary_real}/${fixture_name}")
if(NOT fixture_real STREQUAL expected_root)
  message(FATAL_ERROR "Fixture must be a direct, non-redirected child of the build directory: ${ROOT}")
endif()
if(IS_SYMLINK "${ROOT}")
  message(FATAL_ERROR "Refusing to remove a symlink fixture: ${ROOT}")
endif()

if(EXISTS "${fixture_real}")
  file(REMOVE_RECURSE "${fixture_real}")
endif()

if(DEFINED TEST_SCRIPT)
  execute_process(COMMAND "${CMAKE_COMMAND}" "-DEXECUTABLE=${EXECUTABLE}"
    "-DROOT=${fixture_real}" -P "${TEST_SCRIPT}" RESULT_VARIABLE status)
elseif(DEFINED MODE)
  execute_process(COMMAND "${EXECUTABLE}" "${MODE}" "${fixture_real}"
    RESULT_VARIABLE status)
else()
  execute_process(COMMAND "${EXECUTABLE}" "${fixture_real}" RESULT_VARIABLE status)
endif()
if(NOT "${status}" STREQUAL "0")
  message(FATAL_ERROR "Fixture test failed with status: ${status}")
endif()
