// Coaxolotl source for SDR++: an RTL-SDR source that also drives the
// Coaxolotl mast-head box over the same coax.
//
//   down: command frames as short bias-T gaps (coaxlink.hpp, docs/protocol.md)
//   up:   the box answers each frame with a clock beacon at ~36.75 MHz that
//         this dongle hears; the module retunes for ~1.4 s, decodes the
//         reply (ACK/NAK, switch state, VCOAX, LNA current, supercap) and
//         tunes back.
//
// It has to be the source module because only the code that holds the
// rtlsdr_dev_t can switch the bias-T with ms timing and read samples at the
// beacon frequency. The rest of SDR++ sees an ordinary RTL-SDR source.
#include <utils/flog.h>
#include <module.h>
#include <gui/gui.h>
#include <gui/smgui.h>
#include <gui/style.h>
#include <signal_path/signal_path.h>
#include <core.h>
#include <config.h>
#include <rtl-sdr.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <optional>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>

#include "coaxlink.hpp"

#define CONCAT(a, b) ((std::string(a) + b).c_str())

SDRPP_MOD_INFO{
    /* Name:            */ "coaxolotl_source",
    /* Description:     */ "RTL-SDR source with Coaxolotl remote control and telemetry",
    /* Author:          */ "z2labs",
    /* Version:         */ 0, 1, 0,
    /* Max instances    */ 1
};

ConfigManager config;

static const double sampleRates[] = { 1024000, 1536000, 1792000, 1920000, 2048000,
                                      2160000, 2400000, 2560000, 2880000, 3200000 };
static const char* sampleRatesTxt = "1.024 MHz\0" "1.536 MHz\0" "1.792 MHz\0" "1.92 MHz\0" "2.048 MHz\0"
                                    "2.16 MHz\0" "2.4 MHz\0" "2.56 MHz\0" "2.88 MHz\0" "3.2 MHz\0";
static const int NUM_SR = sizeof(sampleRates) / sizeof(sampleRates[0]);

class CoaxolotlSourceModule : public ModuleManager::Instance {
public:
    CoaxolotlSourceModule(std::string name) : name(name) {
        handler.ctx = this;
        handler.selectHandler = menuSelected;
        handler.deselectHandler = menuDeselected;
        handler.menuHandler = menuHandler;
        handler.startHandler = start;
        handler.stopHandler = stop;
        handler.tuneHandler = tune;
        handler.stream = &stream;

        for (const auto& b : coax::branches()) {
            char buf[96];
            snprintf(buf, sizeof buf, "F%d %s (%s)", b.id, b.name, b.filter);
            bankListTxt += buf;
            bankListTxt += '\0';
        }

        loadConfig();
        refresh();
        selectByName(selectedDevName);

        linkThread = std::thread(&CoaxolotlSourceModule::linkWorker, this);
        sigpath::sourceManager.registerSource("Coaxolotl RTL-SDR", &handler);
    }

    ~CoaxolotlSourceModule() {
        stop(this);
        {
            std::lock_guard<std::mutex> lck(jobMtx);
            linkQuit = true;
        }
        jobCv.notify_all();
        if (linkThread.joinable()) { linkThread.join(); }
        sigpath::sourceManager.unregisterSource("Coaxolotl RTL-SDR");
    }

    void postInit() {}
    void enable() { enabled = true; }
    void disable() { enabled = false; }
    bool isEnabled() { return enabled; }

private:
    // ---- jobs for the link thread -------------------------------------------
    enum class JobKind { FRAME, LISTEN };
    struct Job {
        JobKind kind;
        coax::Frame frame;
        bool listen;
    };

    void enqueue(const Job& j) {
        {
            std::lock_guard<std::mutex> lck(jobMtx);
            if (jobs.size() < 4) { jobs.push_back(j); }
        }
        jobCv.notify_all();
    }

    coax::Frame currentFrame(bool clr = false) {
        coax::Frame f;
        f.bank = bank;
        f.lna = lna;
        f.quiet = quiet;
        f.clr = clr;
        return f;
    }

    // ---- device ---------------------------------------------------------------
    void refresh() {
        devNames.clear();
        devListTxt = "";
        devCount = rtlsdr_get_device_count();
        for (int i = 0; i < devCount; i++) {
            char ven[256] = "", prod[256] = "", sn[256] = "", buf[1024];
            int err = rtlsdr_get_device_usb_strings(i, ven, prod, sn);
            snprintf(buf, sizeof buf, "%s %s [%s]", ven[0] ? ven : rtlsdr_get_device_name(i), prod,
                     (!err && sn[0]) ? sn : "No Serial");
            devNames.push_back(buf);
            devListTxt += std::string(buf) + "##" + std::to_string(i);
            devListTxt += '\0';
        }
    }

    void selectByName(const std::string& n) {
        for (int i = 0; i < devCount; i++) {
            if (devNames[i] == n) {
                selectById(i);
                return;
            }
        }
        if (devCount > 0) { selectById(0); }
    }

    void selectById(int id) {
        rtlsdr_dev_t* dev;
        if (rtlsdr_open(&dev, id) < 0) {
            selectedDevName = "";
            flog::error("Coaxolotl: could not open RTL-SDR {0}", id);
            return;
        }
        devId = id;
        selectedDevName = devNames[id];
        int g[256];
        int n = rtlsdr_get_tuner_gains(dev, g);
        gainList.assign(g, g + std::max(0, n));
        std::sort(gainList.begin(), gainList.end());
        rtlsdr_close(dev);
        if (gainList.empty()) { gainList.push_back(0); }
        gainId = std::clamp(gainId, 0, (int)gainList.size() - 1);
        updateGainTxt();
    }

    void updateGainTxt() {
        snprintf(gainTxt, sizeof gainTxt, "%.1f dB", gainList[gainId] / 10.0f);
    }

    void applyGain() {
        rtlsdr_set_tuner_gain_mode(dev, tunerAgc ? 0 : 1);
        if (!tunerAgc) { rtlsdr_set_tuner_gain(dev, gainList[gainId]); }
    }

    static void menuSelected(void* ctx) {
        auto* _this = (CoaxolotlSourceModule*)ctx;
        core::setInputSampleRate(_this->sampleRate);
    }

    static void menuDeselected(void*) {}

    static void start(void* ctx) {
        auto* _this = (CoaxolotlSourceModule*)ctx;
        if (_this->running) { return; }
        if (_this->selectedDevName.empty()) {
            flog::error("Coaxolotl: no device selected");
            return;
        }
        if (rtlsdr_open(&_this->dev, _this->devId) < 0) {
            flog::error("Coaxolotl: could not open RTL-SDR");
            return;
        }
        rtlsdr_set_sample_rate(_this->dev, (uint32_t)_this->sampleRate);
        rtlsdr_set_center_freq(_this->dev, (uint32_t)_this->freq);
        rtlsdr_set_freq_correction(_this->dev, _this->ppm);
        rtlsdr_set_tuner_bandwidth(_this->dev, 0);
        rtlsdr_set_agc_mode(_this->dev, 0);
        _this->applyGain();
        rtlsdr_set_bias_tee(_this->dev, _this->biasT);

        _this->asyncCount = (int)roundf(_this->sampleRate / (200 * 512)) * 512;
        _this->running = true;
        _this->workerThread = std::thread([_this]() {
            rtlsdr_reset_buffer(_this->dev);
            rtlsdr_read_async(_this->dev, asyncHandler, _this, 0, _this->asyncCount);
        });
        flog::info("Coaxolotl: started at {0} S/s", _this->sampleRate);
    }

    static void stop(void* ctx) {
        auto* _this = (CoaxolotlSourceModule*)ctx;
        if (!_this->running) { return; }
        _this->running = false;                        // aborts a transaction in flight
        std::lock_guard<std::mutex> lck(_this->devMtx);
        _this->stream.stopWriter();
        rtlsdr_cancel_async(_this->dev);
        if (_this->workerThread.joinable()) { _this->workerThread.join(); }
        _this->stream.clearWriteStop();
        rtlsdr_close(_this->dev);
        _this->dev = nullptr;
    }

    static void tune(double freq, void* ctx) {
        auto* _this = (CoaxolotlSourceModule*)ctx;
        _this->freq = freq;
        _this->tunedAt = std::chrono::steady_clock::now();
        // while the link thread listens on the beacon it owns the tuner;
        // it tunes back to _this->freq when done
        if (_this->running && !_this->listening) {
            rtlsdr_set_center_freq(_this->dev, (uint32_t)freq);
        }
    }

    static void asyncHandler(unsigned char* buf, uint32_t len, void* ctx) {
        auto* _this = (CoaxolotlSourceModule*)ctx;
        int n = len / 2;
        bool mute = _this->listening && !_this->showBeacon;
        for (int i = 0; i < n; i++) {
            float re = ((float)buf[2 * i] - 127.4f) / 128.0f;
            float im = ((float)buf[2 * i + 1] - 127.4f) / 128.0f;
            _this->stream.writeBuf[i].re = mute ? 0.0f : re;
            _this->stream.writeBuf[i].im = mute ? 0.0f : im;
        }
        if (_this->capturing) { _this->captureChunk(buf, n); }
        _this->stream.swap(n);
    }

    void captureChunk(const unsigned char* buf, int n) {
        std::lock_guard<std::mutex> lck(capMtx);
        int i = 0;
        if (capSkip > 0) {                              // stale USB buffers + PLL settle
            i = (int)std::min<size_t>(capSkip, (size_t)n);
            capSkip -= i;
        }
        for (; i < n && cap.size() < capWant; i++) {
            cap.emplace_back(((float)buf[2 * i] - 127.4f) / 128.0f, ((float)buf[2 * i + 1] - 127.4f) / 128.0f);
        }
        if (cap.size() >= capWant) {
            capturing = false;
            capCv.notify_all();
        }
    }

    // ---- link thread ----------------------------------------------------------
    void linkWorker() {
        std::unique_lock<std::mutex> lck(jobMtx);
        while (!linkQuit) {
            jobCv.wait_for(lck, std::chrono::milliseconds(250));
            if (linkQuit) { break; }
            if (jobs.empty() && autoBand && running) { autoBandCheck(); }
            if (jobs.empty()) { continue; }
            Job j = jobs.front();
            jobs.pop_front();
            inFlight = true;
            lck.unlock();
            transaction(j);
            inFlight = false;
            lck.lock();
        }
    }

    void autoBandCheck() {
        int want = coax::bankFor(freq);
        auto quiet_for = std::chrono::steady_clock::now() - tunedAt;
        if (want == lastSentBank || quiet_for < std::chrono::milliseconds(1500)) { return; }
        bank = want;
        saveConfig();
        jobs.push_back({ JobKind::FRAME, currentFrame(), listenReply });
    }

    void setStatus(const std::string& s) {
        std::lock_guard<std::mutex> lck(statusMtx);
        status = s;
    }

    void transaction(const Job& j) {
        std::lock_guard<std::mutex> dl(devMtx);
        if (!running) {
            setStatus("start the source first");
            return;
        }
        if (j.kind == JobKind::FRAME) {
            char buf[96];
            snprintf(buf, sizeof buf, "keying F%d%s%s%s ...", j.frame.bank, j.frame.lna ? " LNA" : "",
                     j.frame.quiet ? " QUIET" : "", j.frame.clr ? " CLR" : "");
            setStatus(buf);
            auto e = coax::edges(j.frame, timing);
            auto late = coax::play(e, [this](bool on) { rtlsdr_set_bias_tee(dev, on); },
                                   [this]() { return !running; });
            biasT = true;                               // the box stays powered
            if (late.size() != e.size()) {
                setStatus("keying aborted");
                return;
            }
            auto mm = std::minmax_element(late.begin(), late.end());
            {
                std::lock_guard<std::mutex> lck(statusMtx);
                jitterMs = *mm.second - *mm.first;
            }
            lastSentBank = j.frame.bank;
            saveConfig();
            if (!j.listen) {
                setStatus(jitterMs > 6 ? "sent (jitter > 6 ms!)" : "sent, not listening");
                return;
            }
        }
        listen(j.kind == JobKind::LISTEN ? 3.0 : (coax::REPLY_DELAY_MS + coax::REPLY_MS + 300) / 1000.0);
    }

    void listen(double seconds) {
        setStatus("listening for the reply ...");
        {
            std::lock_guard<std::mutex> lck(capMtx);
            cap.clear();
            capSkip = (size_t)(sampleRate * 0.1);
            capWant = (size_t)(sampleRate * seconds);
            cap.reserve(capWant);
        }
        listening = true;
        rtlsdr_set_center_freq(dev, (uint32_t)beaconHz);
        capturing = true;
        {
            std::unique_lock<std::mutex> lck(capMtx);
            capCv.wait_for(lck, std::chrono::duration<double>(seconds + 1.5),
                           [this]() { return !capturing || !running; });
        }
        capturing = false;
        if (running) { rtlsdr_set_center_freq(dev, (uint32_t)freq); }
        listening = false;

        std::vector<std::complex<float>> iq;
        {
            std::lock_guard<std::mutex> lck(capMtx);
            iq.swap(cap);
        }
        coax::Heard h = coax::demodulate(iq, sampleRate);
        {
            std::lock_guard<std::mutex> lck(statusMtx);
            heard = h;
            heardAt = std::chrono::steady_clock::now();
            haveHeard = true;
            if (h.reply) {
                lastReply = h.reply;
                status = std::string("reply: ") + h.reply->kindName();
            }
            else {
                status = "no reply: " + h.note;
            }
        }
    }

    // ---- GUI ----------------------------------------------------------------------
    static void menuHandler(void* ctx) {
        auto* _this = (CoaxolotlSourceModule*)ctx;
        _this->drawDevice();
        _this->drawLink();
    }

    void drawDevice() {
        if (running) { SmGui::BeginDisabled(); }
        SmGui::FillWidth();
        SmGui::ForceSync();
        if (SmGui::Combo(CONCAT("##_coax_dev_", name), &devId, devListTxt.c_str())) {
            selectById(devId);
            saveConfig();
        }
        if (SmGui::Combo(CONCAT("##_coax_sr_", name), &srId, sampleRatesTxt)) {
            sampleRate = sampleRates[srId];
            core::setInputSampleRate(sampleRate);
            saveConfig();
        }
        SmGui::SameLine();
        SmGui::FillWidth();
        SmGui::ForceSync();
        if (SmGui::Button(CONCAT("Refresh##_coax_refr_", name))) {
            refresh();
            selectByName(selectedDevName);
        }
        if (running) { SmGui::EndDisabled(); }

        SmGui::LeftLabel("PPM");
        SmGui::FillWidth();
        if (SmGui::InputInt(CONCAT("##_coax_ppm_", name), &ppm, 1, 10)) {
            ppm = std::clamp(ppm, -1000, 1000);
            if (running) { rtlsdr_set_freq_correction(dev, ppm); }
            saveConfig();
        }
        if (tunerAgc) { SmGui::BeginDisabled(); }
        SmGui::LeftLabel("Gain");
        SmGui::FillWidth();
        if (SmGui::SliderInt(CONCAT("##_coax_gain_", name), &gainId, 0, (int)gainList.size() - 1,
                             SmGui::FMT_STR_NONE)) {
            updateGainTxt();
            if (running) { applyGain(); }
            saveConfig();
        }
        if (tunerAgc) { SmGui::EndDisabled(); }
        SmGui::SameLine();
        SmGui::Text(gainTxt);
        if (SmGui::Checkbox(CONCAT("Tuner AGC##_coax_agc_", name), &tunerAgc)) {
            if (running) { applyGain(); }
            saveConfig();
        }
        if (SmGui::Checkbox(CONCAT("Bias-T (box power)##_coax_bt_", name), &biasT)) {
            if (running) { rtlsdr_set_bias_tee(dev, biasT); }
            saveConfig();
        }
    }

    void drawLink() {
        SmGui::Text("Coaxolotl");
        SmGui::LeftLabel("Branch");
        SmGui::FillWidth();
        if (SmGui::Combo(CONCAT("##_coax_bank_", name), &bank, bankListTxt.c_str())) { saveConfig(); }
        const coax::Branch& br = coax::branches()[bank];
        if (SmGui::Checkbox(CONCAT("LNA##_coax_lna_", name), &lna)) { saveConfig(); }
        SmGui::SameLine();
        if (SmGui::Checkbox(CONCAT("Quiet##_coax_q_", name), &quiet)) { saveConfig(); }
        SmGui::SameLine();
        if (SmGui::Checkbox(CONCAT("Read reply##_coax_rr_", name), &listenReply)) { saveConfig(); }
        if (lna && !br.lna_ok) {
            SmGui::TextColored(ImVec4(1.0f, 0.75f, 0.2f, 1.0f), "this branch forbids the LNA (AUTO bypass)");
        }
        if (SmGui::Checkbox(CONCAT("Follow tuning##_coax_auto_", name), &autoBand)) { saveConfig(); }

        bool busy = inFlight;
        {
            std::lock_guard<std::mutex> lck(jobMtx);
            busy = busy || !jobs.empty();
        }
        if (!running || busy) { SmGui::BeginDisabled(); }
        if (SmGui::Button(CONCAT("Send##_coax_send_", name))) {
            enqueue({ JobKind::FRAME, currentFrame(), listenReply });
        }
        SmGui::SameLine();
        if (SmGui::Button(CONCAT("Poll##_coax_poll_", name))) {
            // re-sending the current state is harmless and always answered
            enqueue({ JobKind::FRAME, currentFrame(), true });
        }
        SmGui::SameLine();
        if (SmGui::Button(CONCAT("Clear##_coax_clr_", name))) {
            enqueue({ JobKind::FRAME, currentFrame(true), true });
        }
        SmGui::SameLine();
        if (SmGui::Button(CONCAT("Listen##_coax_lis_", name))) {
            enqueue({ JobKind::LISTEN, currentFrame(), true });
        }
        if (!running || busy) { SmGui::EndDisabled(); }

        SmGui::LeftLabel("Beacon MHz");
        SmGui::FillWidth();
        if (SmGui::SliderFloatWithSteps(CONCAT("##_coax_bcn_", name), &beaconMHz, 24.0f, 120.0f, 0.05f,
                                        SmGui::FMT_STR_FLOAT_THREE_DECIMAL)) {
            beaconHz = beaconMHz * 1e6;
            saveConfig();
        }
        if (SmGui::Checkbox(CONCAT("Show beacon on waterfall##_coax_sb_", name), &showBeacon)) { saveConfig(); }

        drawTelemetry();
    }

    void drawTelemetry() {
        const ImVec4 red(1.0f, 0.3f, 0.3f, 1.0f), green(0.4f, 1.0f, 0.4f, 1.0f),
            amber(1.0f, 0.75f, 0.2f, 1.0f), grey(0.6f, 0.6f, 0.6f, 1.0f);
        std::lock_guard<std::mutex> lck(statusMtx);
        char buf[256];
        SmGui::Text(("Link: " + status).c_str());
        if (jitterMs > 0) {
            snprintf(buf, sizeof buf, "keying jitter %.1f ms", jitterMs);
            SmGui::TextColored(jitterMs > 6 ? amber : grey, buf);
        }
        if (!lastReply) {
            SmGui::TextColored(grey, "no reply yet - Poll asks the box");
            return;
        }
        const coax::Reply& r = *lastReply;
        double age = std::chrono::duration<double>(std::chrono::steady_clock::now() - heardAt).count();
        if (r.kind == coax::Kind::NAK) {
            snprintf(buf, sizeof buf, "NAK: %s error (%.0f s ago)", r.errName(), age);
            SmGui::TextColored(red, buf);
        }
        else {
            snprintf(buf, sizeof buf, "%s seq %d (%.0f s ago)", r.kindName(), r.seq, age);
            SmGui::TextColored(green, buf);
        }
        snprintf(buf, sizeof buf, "box: F%d %s %s%s%s%s", r.bank, coax::branches()[r.bank].name,
                 r.lnaPath ? "LNA" : "bypass", r.autoByp ? " (AUTO)" : "", r.fmn ? " +FM notch" : "",
                 r.quiet ? " quiet" : "");
        SmGui::TextColored(r.bank == lastSentBank || lastSentBank < 0 ? green : amber, buf);

        snprintf(buf, sizeof buf, "Vcoax %.2f V", r.vcoax_v);
        SmGui::TextColored(r.low ? red : green, buf);
        SmGui::SameLine();
        snprintf(buf, sizeof buf, "I_lna %d mA", r.i_lna_ma);
        bool iok = !r.lnaEn || (r.i_lna_ma >= 60 && r.i_lna_ma <= 130);
        SmGui::TextColored(r.trip ? red : iok ? green : amber, buf);
        SmGui::SameLine();
        snprintf(buf, sizeof buf, "Vscap %.2f V", r.vscap_v);
        SmGui::TextColored(r.hold ? green : amber, buf);

        const char* names[] = { "TRIP", "LOW", "DROP", "GLT", "RST" };
        bool on[] = { r.trip, r.low, r.drop, r.glt, r.rst };
        for (int i = 0; i < 5; i++) {
            if (i) { SmGui::SameLine(); }
            SmGui::TextColored(on[i] ? red : grey, names[i]);
        }
        if (haveHeard) {
            double f = beaconHz + heard.offset_hz;
            snprintf(buf, sizeof buf, "beacon %.4f MHz (osc %+.2f %%), on/off %.0f dB, chip %.2f ms", f / 1e6,
                     (f / coax::BEACON_HZ - 1) * 100, heard.snr_db, heard.chip_ms);
            SmGui::TextColored(grey, buf);
        }
    }

    // ---- config -------------------------------------------------------------------
    void loadConfig() {
        config.acquire();
        json& c = config.conf;
        if (c.contains("device")) { selectedDevName = c["device"]; }
        if (c.contains("sampleRate")) {
            double sr = c["sampleRate"];
            for (int i = 0; i < NUM_SR; i++) {
                if (sampleRates[i] == sr) { srId = i; }
            }
        }
        sampleRate = sampleRates[srId];
        if (c.contains("ppm")) { ppm = c["ppm"]; }
        if (c.contains("gain")) { gainId = c["gain"]; }
        if (c.contains("tunerAgc")) { tunerAgc = c["tunerAgc"]; }
        if (c.contains("biasT")) { biasT = c["biasT"]; }
        if (c.contains("bank")) { bank = std::clamp((int)c["bank"], 0, 7); }
        if (c.contains("lna")) { lna = c["lna"]; }
        if (c.contains("quiet")) { quiet = c["quiet"]; }
        if (c.contains("listenReply")) { listenReply = c["listenReply"]; }
        if (c.contains("autoBand")) { autoBand = c["autoBand"]; }
        if (c.contains("beaconHz")) { beaconHz = c["beaconHz"]; }
        if (c.contains("showBeacon")) { showBeacon = c["showBeacon"]; }
        config.release();
        beaconMHz = (float)(beaconHz / 1e6);
    }

    void saveConfig() {
        config.acquire();
        json& c = config.conf;
        c["device"] = selectedDevName;
        c["sampleRate"] = sampleRate;
        c["ppm"] = ppm;
        c["gain"] = gainId;
        c["tunerAgc"] = (bool)tunerAgc;
        c["biasT"] = (bool)biasT;
        c["bank"] = (int)bank;
        c["lna"] = (bool)lna;
        c["quiet"] = (bool)quiet;
        c["listenReply"] = (bool)listenReply;
        c["autoBand"] = (bool)autoBand;
        c["beaconHz"] = (double)beaconHz;
        c["showBeacon"] = (bool)showBeacon;
        config.release(true);
    }

    // ---- state --------------------------------------------------------------------
    std::string name;
    bool enabled = true;
    dsp::stream<dsp::complex_t> stream;
    SourceManager::SourceHandler handler;

    rtlsdr_dev_t* dev = nullptr;
    std::atomic<bool> running{ false };
    std::thread workerThread;
    std::mutex devMtx;                  // held by a transaction; stop() waits for it
    int asyncCount = 0;
    double freq = 100e6;
    std::chrono::steady_clock::time_point tunedAt = std::chrono::steady_clock::now();

    std::vector<std::string> devNames;
    std::string devListTxt, selectedDevName, bankListTxt;
    int devCount = 0, devId = 0, srId = 6;
    double sampleRate = 2400000;
    int ppm = 0, gainId = 0;
    std::vector<int> gainList{ 0 };
    char gainTxt[32] = "--";
    bool tunerAgc = false;
    bool biasT = false;

    // link settings (GUI thread writes, link thread reads; ints/bools only)
    int bank = 0;
    bool lna = false, quiet = false, listenReply = true, autoBand = false, showBeacon = true;
    double beaconHz = coax::BEACON_HZ;
    float beaconMHz = (float)(coax::BEACON_HZ / 1e6);
    coax::Timing timing;
    std::atomic<int> lastSentBank{ -1 };

    // link thread
    std::thread linkThread;
    std::mutex jobMtx;
    std::condition_variable jobCv;
    std::deque<Job> jobs;
    bool linkQuit = false;
    std::atomic<bool> inFlight{ false };

    // reply capture (async handler fills, link thread decodes)
    std::atomic<bool> listening{ false }, capturing{ false };
    std::mutex capMtx;
    std::condition_variable capCv;
    std::vector<std::complex<float>> cap;
    size_t capSkip = 0, capWant = 0;

    // shown in the GUI
    std::mutex statusMtx;
    std::string status = "idle";
    double jitterMs = 0;
    std::optional<coax::Reply> lastReply;
    coax::Heard heard;
    bool haveHeard = false;
    std::chrono::steady_clock::time_point heardAt;
};

MOD_EXPORT void _INIT_() {
    json def = json({});
    config.setPath(core::args["root"].s() + "/coaxolotl_source_config.json");
    config.load(def);
    config.enableAutoSave();
}

MOD_EXPORT ModuleManager::Instance* _CREATE_INSTANCE_(std::string name) {
    return new CoaxolotlSourceModule(name);
}

MOD_EXPORT void _DELETE_INSTANCE_(ModuleManager::Instance* instance) {
    delete (CoaxolotlSourceModule*)instance;
}

MOD_EXPORT void _END_() {
    config.disableAutoSave();
    config.save();
}
