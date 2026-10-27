# Coaxolotl firmware
#
#   make              EFM8BB2 image, Snooze build      -> build/coaxolotl.hex
#   make SNOOZE=0     bring-up build (Timer2/Idle only) -> build/coaxolotl.hex
#   make test         host unit tests (gcc)
#   make sim          host simulator  -> build/sim
#   make bandplan     regenerate src/bandplan.h from ../bandplan.json
#
# Flash with Simplicity Commander, efm8load (UART bootloader) or any C2
# programmer, e.g.:  commander flash build/coaxolotl.hex --device EFM8BB21F16G

SNOOZE   ?= 1
SDCC     ?= sdcc
PACKIHX  ?= packihx
CC_HOST  ?= cc
BUILD    := build

SRC_COMMON := src/decoder.c src/app.c src/panel.c
SRC_TARGET := src/main.c src/hal_efm8bb2.c $(SRC_COMMON)

# EFM8BB21F16G: 16 kB flash (last page holds the lock byte, the page below it
# is the panel-mode log, NV_PAGE), 256 B IRAM, 2 kB XRAM.
# Large model: state lives in XRAM, IRAM stays free for the stack.
SDCC_FLAGS := -mmcs51 --model-large --opt-code-size --std-sdcc11 \
              --code-size 0x3C00 --iram-size 256 --xram-size 2048 \
              -Isrc -Iinclude -DCFG_SNOOZE=$(SNOOZE)

HOST_FLAGS := -std=c99 -O2 -Wall -Wextra -Wno-unused-function -Isrc -Itest -DCFG_SNOOZE=1

RELS := $(patsubst src/%.c,$(BUILD)/%.rel,$(SRC_TARGET))

all: $(BUILD)/coaxolotl.hex

$(BUILD):
	mkdir -p $@

$(BUILD)/%.rel: src/%.c src/*.h include/*.h | $(BUILD)
	$(SDCC) $(SDCC_FLAGS) -c $< -o $@

$(BUILD)/coaxolotl.ihx: $(RELS)
	$(SDCC) $(SDCC_FLAGS) $(RELS) -o $@

$(BUILD)/coaxolotl.hex: $(BUILD)/coaxolotl.ihx
	$(PACKIHX) $< > $@
	@grep -E "^(ROM|Name|---|CODE|DATA|XDATA|ISEG|Stack)" $(BUILD)/coaxolotl.mem 2>/dev/null || true

$(BUILD)/test_decoder: test/test_decoder.c test/harness.h $(SRC_COMMON) src/*.h | $(BUILD)
	$(CC_HOST) $(HOST_FLAGS) test/test_decoder.c $(SRC_COMMON) -o $@

$(BUILD)/sim: test/sim.c test/harness.h $(SRC_COMMON) src/*.h | $(BUILD)
	$(CC_HOST) $(HOST_FLAGS) test/sim.c $(SRC_COMMON) -o $@

test: $(BUILD)/test_decoder
	./$(BUILD)/test_decoder

sim: $(BUILD)/sim

bandplan:
	python3 ../tools/gen_bandplan.py

clean:
	rm -rf $(BUILD)

.PHONY: all test sim bandplan clean
