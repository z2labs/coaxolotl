cmake_minimum_required(VERSION 3.13)
project(coaxolotl_source)

# Built inside an SDR++ source tree (see ../README.md): SDR++'s top-level
# CMakeLists sets SDRPP_MODULE_CMAKE and the core target.
file(GLOB SRC "src/*.cpp")

include(${SDRPP_MODULE_CMAKE})

if (MSVC)
    target_link_directories(coaxolotl_source PRIVATE "C:/Program Files/PothosSDR/bin/")
    target_link_libraries(coaxolotl_source PRIVATE rtlsdr)
else (MSVC)
    find_package(PkgConfig)
    pkg_check_modules(LIBRTLSDR REQUIRED librtlsdr)
    target_include_directories(coaxolotl_source PRIVATE ${LIBRTLSDR_INCLUDE_DIRS})
    target_link_directories(coaxolotl_source PRIVATE ${LIBRTLSDR_LIBRARY_DIRS})
    target_link_libraries(coaxolotl_source PRIVATE ${LIBRTLSDR_LIBRARIES})
endif ()
