// Coaxolotl link, host side: command frames down the bias-T, replies back
// on the coax. Plain C++17, no SDR++ or librtlsdr dependency, so the same
// code is unit-tested on its own (sdrpp/test) and used by the SDR++ module.
//
// Mirrors host/coaxolotl.py (frames) and host/backchannel.py (replies);
// the wire formats are defined in docs/protocol.md and firmware/src/*.h.
#pragma once

#include <array>
#include <complex>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace coax {

// ---- command frames (host -> box) -------------------------------------

struct Timing {
    double gap0 = 30.0;   // ms, bias off for a 0
    double gap1 = 70.0;   // ms, bias off for a 1
    double on = 100.0;    // ms, bias on between bits
    double pre = 400.0;   // ms, steady bias before the frame
    bool valid() const;
};

struct Frame {
    int bank = 0;         // 0..7, bandplan.json
    bool lna = false;
    bool quiet = false;
    bool clr = false;
    uint8_t payload() const;
    std::array<int, 15> bits() const;            // sync 101 | payload | ~payload
};

struct Edge {
    double t_ms;
    bool on;
};

// power on, idle, one gap per bit; ends powered
std::vector<Edge> edges(const Frame& f, const Timing& t = Timing());

// Drive the bias-T on an absolute schedule (sleep, then spin the last
// 2 ms). Returns the lateness of each edge in ms. `abort` is polled.
std::vector<double> play(const std::vector<Edge>& e, const std::function<void(bool)>& setBias,
                         const std::function<bool()>& abort = nullptr);

// ---- replies (box -> host) --------------------------------------------

constexpr double CARRIER_HZ = 24.5e6 / 2.0;       // PCA CEX0 at SYSCLK / 2
constexpr int HARMONIC = 3;
constexpr double BEACON_HZ = CARRIER_HZ * HARMONIC;  // 36.75 MHz nominal
constexpr double CHIP_MS = 5.0;
constexpr double REPLY_DELAY_MS = 300.0;          // after the host's last gap
constexpr int PKT_LEN = 7;
constexpr uint8_t SYNC = 0x2D;
constexpr int PRE_BITS = 8;
constexpr int NCHIPS = (PRE_BITS + 8 + 8 * PKT_LEN) * 2 + 1;
constexpr double REPLY_MS = NCHIPS * CHIP_MS;

enum class Kind { HELLO = 1, ACK = 2, NAK = 3 };

uint8_t crc8(const uint8_t* p, int n);
std::vector<int> chips(const std::array<uint8_t, PKT_LEN>& pkt);
std::array<uint8_t, PKT_LEN> pack(int kind, int err, int seq, uint8_t state, uint8_t flags,
                                  int vcoax_mv, int i_ma, int vscap_mv);

struct Reply {
    Kind kind;
    int err;              // ERR_* (decoder.h), 0 unless NAK
    int seq;              // good frames applied, mod 8
    int bank;
    bool lnaReq, lnaPath, fmn, autoByp, quiet;
    bool trip, low, hold, drop, glt, rst, lnaEn;
    float vcoax_v, vscap_v;
    int i_lna_ma;
    std::array<uint8_t, PKT_LEN> raw;

    static std::optional<Reply> parse(const std::array<uint8_t, PKT_LEN>& pkt);
    const char* kindName() const;
    const char* errName() const;
    std::string summary() const;
};

struct Heard {
    std::optional<Reply> reply;
    double offset_hz = 0;  // carrier relative to the tuned centre
    double snr_db = 0;     // on/off ratio in the beacon bin
    double chip_ms = 0;    // measured chip length
    double t_ms = 0;       // start of the reply in the capture
    std::string note;
};

// Find and decode one reply in a complex baseband capture. The carrier
// may sit anywhere in the band (MCU oscillator +/-2 %), chips +/-10 %.
Heard demodulate(const std::vector<std::complex<float>>& iq, double fs,
                 double chip_ms = CHIP_MS, double min_snr_db = 15.0);

// ---- band plan -----------------------------------------------------------

struct Branch {
    int id;
    const char* name;
    const char* filter;
    double lo_hz, hi_hz;
    bool lna_ok, fm_notch;
};

const std::vector<Branch>& branches();
int bankFor(double freq_hz);   // narrowest branch that covers freq, else 0 (THRU)

}  // namespace coax
