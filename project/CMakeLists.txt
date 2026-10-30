# ffxiii2 - ReXGlue Recompiled Project (FFXIII-2 test build)
#
# SDK boilerplate lives in generated/rexglue.cmake.

cmake_minimum_required(VERSION 3.25)
project(ffxiii2 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(generated/rexglue.cmake)

# Sources
set(FFXIII2_SOURCES
    src/main.cpp
    src/x2_hooks.cpp
    src/x2_movies.cpp
    src/x2_story_recaps.cpp
    src/x2_settings.cpp
    src/x2_config_menu.cpp
    src/x2_perf.cpp
    src/x2_xbox_texts.cpp
    src/x2_save_delete.cpp
    src/x2_steam_saves.cpp
)

if(WIN32)
    add_executable(ffxiii2 WIN32 ${FFXIII2_SOURCES})
else()
    add_executable(ffxiii2 ${FFXIII2_SOURCES})
endif()

if(WIN32 AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/src/ffxiii2.rc")
    enable_language(RC)
    target_sources(ffxiii2 PRIVATE src/ffxiii2.rc)
endif()

rexglue_setup_target(ffxiii2)

# As in the Lightning Returns port: the recompiled code is built for the CPU of the PC that
# builds it; -ffp-contract=off keeps float results exactly as with the SSE4.1 build.
option(LR_NATIVE_CPU "Build the recompiled code for this PC's CPU" ON)
if(LR_NATIVE_CPU AND NOT MSVC AND CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64")
    target_compile_options(ffxiii2_recomp PRIVATE -march=native -ffp-contract=off)
    target_compile_options(ffxiii2 PRIVATE -march=native -ffp-contract=off)
endif()
