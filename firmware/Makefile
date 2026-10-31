# Coaxolotl firmware
#
#   make              EFM8BB21 (QFN20) image, Snooze build -> build/coaxolotl.hex
#   make MCU=bb52     EFM8BB52F32G (QFN32) image
#   make SNOOZE=0     bring-up build (Timer2/Idle only) -> build/coaxolotl.hex
#   make BEACON=0     no back-channel replies
#   make test         host unit tests (gcc)
#   make sim          host simulator  -> build/sim
#   make bandplan     regenerate src/bandplan.h from ../bandplan.json
#
# Flash with Simplicity Commander, efm8load (UART bootloader) or any C2
# programmer, e.g.:  commander flash build/coaxolotl.hex --device EFM8BB21F16G
#                    commander flash build/coaxolotl.hex --device EFM8BB52F32G

MCU      ?= bb21

SNOOZE   ?= 1
BEACON   ?= 1
SDCC     ?= sdcc
PACKIHX  ?= packihx
CC_HOST  ?= cc
BUILD    := build
OBJ      := $(BUILD)/$(MCU)

SRC_COMMON := src/decoder.c src/app.c src/panel.c src/beacon.c

ifeq ($(MCU),bb52)
# EFM8BB52F32G: 30 kB code in 2 kB pages (0x0000-0x77FF), the last one is the
# panel-mode log (NV_PAGE); lock byte and factory bootloader sit in the
# separate security page. 256 B IRAM, 2 kB XRAM.
HAL      := src/hal_efm8bb52.c
MCU_FLAGS := --code-size 0x7000 -DMCU_BB52=1 -DNV_PAGE=0x7000u -DNV_PAGE_SIZE=2048u
else ifeq ($(MCU),bb21)
# EFM8BB21F16G: 16 kB flash (last page holds the lock byte, the page below it
# is the panel-mode log, NV_PAGE), 256 B IRAM, 2 kB XRAM.
HAL      := src/hal_efm8bb2.c
MCU_FLAGS := --code-size 0x3C00 -DMCU_BB21=1
else
$(error MCU must be bb21 or bb52)
endif
SRC_TARGET := src/main.c $(HAL) $(SRC_COMMON)

# Large model: state lives in XRAM, IRAM stays free for the stack.
SDCC_FLAGS := -mmcs51 --model-large --opt-code-size --std-sdcc11 \
              --iram-size 256 --xram-size 2048 $(MCU_FLAGS) \
              -Isrc -Iinclude -DCFG_SNOOZE=$(SNOOZE) -DCFG_BEACON=$(BEACON)

HOST_FLAGS := -std=c99 -O2 -Wall -Wextra -Wno-unused-function -Isrc -Itest -DCFG_SNOOZE=1

RELS := $(patsubst src/%.c,$(OBJ)/%.rel,$(SRC_TARGET))

# build/coaxolotl.hex is always the image of the MCU just built
all: $(OBJ)/coaxolotl.hex
	cp $< $(BUILD)/coaxolotl.hex
	@grep -E "^ *(ROM|Stack)" $(OBJ)/coaxolotl.mem 2>/dev/null || true

$(BUILD) $(OBJ):
	mkdir -p $@

$(OBJ)/%.rel: src/%.c src/*.h include/*.h | $(OBJ)
	$(SDCC) $(SDCC_FLAGS) -c $< -o $@

$(OBJ)/coaxolotl.ihx: $(RELS)
	$(SDCC) $(SDCC_FLAGS) $(RELS) -o $@

$(OBJ)/coaxolotl.hex: $(OBJ)/coaxolotl.ihx
	$(PACKIHX) $< > $@

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
