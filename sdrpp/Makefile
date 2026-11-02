# Coaxolotl SDR++ module - standalone tests of the link code (no SDR++ needed)
#
#   make test     build and run build/test_coaxlink
#
# The module itself is built inside an SDR++ source tree, see README.md.

CXX      ?= c++
BUILD    := build
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Icoaxolotl_source/src

$(BUILD)/test_coaxlink: test/test_coaxlink.cpp coaxolotl_source/src/coaxlink.cpp \
                        coaxolotl_source/src/coaxlink.hpp coaxolotl_source/src/bandplan.inc
	mkdir -p $(BUILD)
	$(CXX) $(CXXFLAGS) test/test_coaxlink.cpp coaxolotl_source/src/coaxlink.cpp -o $@ -lpthread

test: $(BUILD)/test_coaxlink
	./$(BUILD)/test_coaxlink

clean:
	rm -rf $(BUILD)

.PHONY: test clean
